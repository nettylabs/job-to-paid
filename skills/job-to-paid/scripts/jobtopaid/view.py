"""Build a display-ready view of a document. Both renderers (PDF and HTML)
draw from this, so wording and numbers are identical in every format.

Internal numbers (your costs, markups, margins) never enter the view, so they
can't leak onto anything a customer sees.
"""

from __future__ import annotations

import datetime as _dt

from . import profile as profile_mod
from .money import PRICED_KINDS, format_money, format_qty, format_rate
from .util import from_iso, to_decimal

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def format_date(day: _dt.date | None, fmt: str | None) -> str:
    if not day:
        return ""
    fmt = fmt or "D MMM YYYY"
    if fmt == "D MMM YYYY":
        return f"{day.day} {MONTHS[day.month - 1]} {day.year}"
    if fmt == "DD/MM/YYYY":
        return day.strftime("%d/%m/%Y")
    if fmt == "MM/DD/YYYY":
        return day.strftime("%m/%d/%Y")
    if fmt == "DD.MM.YYYY":
        return day.strftime("%d.%m.%Y")
    if fmt == "DD-MM-YYYY":
        return day.strftime("%d-%m-%Y")
    return day.isoformat()


class _Ctx:
    def __init__(self, store):
        self.store = store
        self.profile = store.profile()
        self.labels = profile_mod.labels(self.profile)
        self.disp = store.money()
        self.date_fmt = self.profile["locale"].get("date_format")

    def money(self, value, extra=0, code=False) -> str:
        text = format_money(to_decimal(value), self.disp, extra_places=extra)
        return f"{text} {self.disp['code']}" if code and self.disp.get("show_code") else text

    def date(self, text) -> str:
        return format_date(from_iso(text) if isinstance(text, str) else text, self.date_fmt)


def _business(ctx: _Ctx) -> dict:
    biz = ctx.profile["business"]
    lines = list(biz.get("address") or [])
    contact = [x for x in (biz.get("phone"), biz.get("email"), biz.get("website")) if x]
    registration = [
        f"{r.get('label', '')} {r.get('value', '')}".strip()
        for r in biz.get("registration_numbers") or []
        if r.get("value")
    ]
    return {
        "name": biz.get("name") or "",
        "legal_name": biz.get("legal_name")
        if biz.get("legal_name") and biz.get("legal_name") != biz.get("name")
        else "",
        "lines": lines,
        "contact": contact,
        "registration": registration,
        "logo": biz.get("logo_path") or "",
        "color": biz.get("brand_color") or "#0F766E",
    }


def _customer(ctx: _Ctx, customer_id: str, heading: str) -> dict:
    try:
        cust = ctx.store.customer(customer_id)
    except Exception:
        cust = {"name": "Sample Customer" if customer_id == "SAMPLE" else customer_id}
    lines = [cust.get("name", "")]
    if cust.get("company"):
        lines.append(cust["company"])
    lines += list(cust.get("address") or [])
    for key in ("email", "phone"):
        if cust.get(key):
            lines.append(cust[key])
    if cust.get("tax_id"):
        lines.append(cust["tax_id"])
    return {"heading": heading, "lines": [ln for ln in lines if ln], "address": list(cust.get("address") or [])}


def _norm(text: str) -> str:
    return " ".join(text.replace(",", " ").lower().split())


def _site(ctx: _Ctx, job: dict | None, customer: dict) -> dict | None:
    if not job or not job.get("site_address"):
        return None
    site = _norm(job["site_address"])
    address = customer.get("address") or []
    if site in (_norm(", ".join(address)), _norm(" ".join(address))) or any(site == _norm(a) for a in address):
        return None
    return {"heading": ctx.labels["job_site"], "lines": [job["site_address"]]}


def _rows(ctx: _Ctx, lines: list) -> list:
    rows = []
    for ln in lines:
        kind = ln.get("kind")
        if kind == "text":
            rows.append({"desc": ln.get("description", ""), "qty": "", "unit_price": "", "amount": "", "style": "text"})
            continue
        if kind == "discount":
            rows.append(
                {
                    "desc": ln.get("description", ""),
                    "qty": "",
                    "unit_price": "",
                    "amount": ctx.money(ln.get("amount", "0")),
                    "style": "discount",
                }
            )
            continue
        if kind not in PRICED_KINDS:
            continue
        show_qty = ln.get("display_qty", True)
        qty = ""
        unit_price = ""
        if show_qty:
            qty = format_qty(to_decimal(ln["qty"]), ctx.disp)
            unit = (ln.get("unit") or "").strip()
            if unit and unit.lower() not in ("ea", "each", "item", "job"):
                qty = f"{qty} {unit}"
            unit_price = ctx.money(ln["unit_price"], extra=2)
        desc = ln.get("description", "")
        if ln.get("estimate"):
            desc = f"{desc} ({ctx.labels['estimate']})"
        rows.append(
            {
                "desc": desc,
                "qty": qty,
                "unit_price": unit_price,
                "amount": ctx.money(ln["amount"]),
                "style": "item",
            }
        )
    return rows


def _sections_from_lines(ctx: _Ctx, lines: list) -> list:
    sections = []
    current_key = object()
    for ln in lines:
        key = ln.get("section")
        if key != current_key:
            sections.append({"heading": key, "subheading": "", "badge": "", "rows": [], "totals": None})
            current_key = key
        sections[-1]["rows"].extend(_rows(ctx, [ln]))
    return sections


def _totals_rows(ctx: _Ctx, totals: dict, final_label: str | None = None) -> list:
    rows = []
    labels = ctx.labels
    has_discount = to_decimal(totals.get("discount", "0")) != 0
    inclusive = totals.get("prices_include_tax")
    if inclusive:
        if has_discount:
            rows.append({"label": labels["subtotal"], "value": ctx.money(totals["subtotal"]), "strong": False})
            rows.append({"label": labels["discount"], "value": ctx.money(totals["discount"]), "strong": False})
        rows.append(
            {
                "label": final_label or labels["total_incl_tax"],
                "value": ctx.money(totals["total"], code=True),
                "strong": True,
            }
        )
        for tax in totals.get("taxes", []):
            rows.append(
                {
                    "label": f"{labels['includes_tax']} {tax['name']} ({format_rate(to_decimal(tax['rate']))})",
                    "value": ctx.money(tax["amount"]),
                    "strong": False,
                    "muted": True,
                }
            )
        return rows
    if has_discount or totals.get("taxes"):
        rows.append({"label": labels["subtotal"], "value": ctx.money(totals["subtotal"]), "strong": False})
    if has_discount:
        rows.append({"label": labels["discount"], "value": ctx.money(totals["discount"]), "strong": False})
    for tax in totals.get("taxes", []):
        rows.append(
            {
                "label": f"{tax['name']} ({format_rate(to_decimal(tax['rate']))})",
                "value": ctx.money(tax["amount"]),
                "strong": False,
            }
        )
    rows.append(
        {"label": final_label or labels["total"], "value": ctx.money(totals["total"], code=True), "strong": True}
    )
    return rows


def payment_lines(profile: dict, number: str = "") -> list:
    """The owner's payment methods as printable lines; {number} becomes the document number."""
    methods = []
    for method in profile["payment"].get("methods") or []:
        name = method.get("name", "")
        instructions = (method.get("instructions") or "").replace("{number}", number or "")
        methods.append(f"{name}: {instructions}" if name and instructions else name or instructions)
    return methods


def _payment_block(ctx: _Ctx, link: str = "", number: str = "") -> dict:
    methods = payment_lines(ctx.profile, number)
    return {"heading": ctx.labels["how_to_pay"], "lines": methods, "link": link, "link_label": ctx.labels["pay_online"]}


def _base(ctx: _Ctx, doc: dict, title_key: str) -> dict:
    return {
        "doc_type": doc["type"],
        "title": ctx.labels[title_key],
        "number": doc["id"],
        "business": _business(ctx),
        "labels": ctx.labels,
        "paper": ctx.profile["locale"].get("paper_size", "letter"),
        "language": ctx.profile["locale"].get("language") or "en",
        "currency": ctx.disp["code"],
        "stamp": "",
        "meta": [],
        "subject": doc.get("title", ""),
        "intro": doc.get("intro", ""),
        "sections": [],
        "summary": None,
        "totals": [],
        "balance": None,
        "deposit": "",
        "payment": None,
        "acceptance": None,
        "notes": doc.get("notes", ""),
        "terms": doc.get("terms", ""),
        "tax_note": ctx.profile["tax"].get("note", ""),
        "thank_you": "",
    }


def _signed_off(ctx: _Ctx, template: str, record: dict, customer: dict) -> str:
    """'Approved by Jane Doe on 15 Oct 2026 (text message)' from a label like 'Approved by {name} on {date}'."""
    name = record.get("by") or (customer.get("lines") or [""])[0]
    text = template.replace("{name}", name).replace("{date}", ctx.date(record.get("date")))
    return f"{text} ({record['method']})" if record.get("method") else text


def build_view(store, doc: dict) -> dict:
    view = _build_view(store, doc)
    # A preview has no real job number yet, so leave that row off.
    view["meta"] = [(label, value) for label, value in view["meta"] if value not in ("PREVIEW", "J-DRAFT")]
    return view


def _build_view(store, doc: dict) -> dict:
    ctx = _Ctx(store)
    labels = ctx.labels
    job = None
    if doc.get("job_id"):
        try:
            job = store.job(doc["job_id"])
        except Exception:
            job = None

    if doc["type"] == "quote":
        view = _base(ctx, doc, "quote")
        if doc.get("revision", 1) > 1:
            view["revision"] = f"{labels['revision']} {doc['revision']}"
        view["meta"] = [
            (labels["date"], ctx.date(doc["date"])),
            (labels["valid_until"], ctx.date(doc["valid_until"])),
            (labels["job"], doc["job_id"]),
        ]
        view["customer"] = _customer(ctx, doc["customer_id"], labels["prepared_for"])
        view["site"] = _site(ctx, job, view["customer"])
        options = doc["options"]
        if len(options) == 1:
            view["sections"] = _sections_from_lines(ctx, options[0]["lines"])
            view["totals"] = _totals_rows(ctx, options[0]["totals"])
            if options[0].get("deposit_due"):
                view["deposit"] = f"{labels['deposit_required']}: {ctx.money(options[0]['deposit_due'])}"
        else:
            chosen = (doc.get("accepted") or {}).get("option") if doc["status"] == "accepted" else None
            picked = next((o for o in options if o["key"] == chosen), None)
            with_deposit = [o for o in options if o.get("deposit_due")]
            without = [o["key"] for o in options if not o.get("deposit_due")]
            if picked:
                if picked.get("deposit_due"):
                    view["deposit"] = f"{labels['deposit_required']}: {ctx.money(picked['deposit_due'])}"
            elif with_deposit:
                parts = " · ".join(f"{o['key']} {ctx.money(o['deposit_due'])}" for o in with_deposit)
                view["deposit"] = f"{labels['deposit_required']}: {parts}"
                if without:
                    view["deposit"] += f" ({labels['no_deposit_for']} {', '.join(without)})"
            view["summary"] = {"heading": labels["your_options"], "rows": []}
            for option in options:
                heading = f"{labels['option']} {option['key']} – {option['name']}"
                section = {
                    "heading": heading,
                    "subheading": option.get("description", ""),
                    "badge": labels["recommended"] if option.get("recommended") else "",
                    "rows": _rows(ctx, option["lines"]),
                    "totals": _totals_rows(ctx, option["totals"]),
                }
                view["sections"].append(section)
                view["summary"]["rows"].append(
                    {
                        "name": heading,
                        "total": ctx.money(option["totals"]["total"], code=True),
                        "badge": section["badge"],
                        "deposit": ctx.money(option["deposit_due"]) if option.get("deposit_due") else "",
                    }
                )
        if view["deposit"] and doc["status"] not in ("declined", "expired", "superseded", "void"):
            block = _payment_block(ctx, number=doc["id"])
            if block["lines"]:
                block["heading"] = labels["how_to_pay_deposit"]
                view["payment"] = block
        view["acceptance"] = {
            "heading": labels["accept_heading"],
            "text": labels["accept_text"] if len(options) > 1 else labels["accept_text_single"],
            "signature": doc["status"] != "accepted",
        }
        if doc["status"] == "accepted" and doc.get("accepted"):
            acc = doc["accepted"]
            text = _signed_off(ctx, labels["accepted_by"], acc, view["customer"])
            view["acceptance"]["text"] = f"{labels['option']} {acc['option']}: {text}" if len(options) > 1 else text
        if doc["status"] == "void":
            view["stamp"] = labels["void_stamp"]
        elif doc["status"] == "superseded":
            view["stamp"] = labels["superseded_stamp"]
        if doc["id"] == "PREVIEW":
            view["stamp"] = labels["draft_stamp"]
        return view

    if doc["type"] == "change_order":
        view = _base(ctx, doc, "change_order")
        meta = [(labels["date"], ctx.date(doc["date"])), (labels["job"], doc["job_id"])]
        if doc.get("quote_id"):
            meta.append((labels["reference"], doc["quote_id"]))
        view["meta"] = meta
        view["customer"] = _customer(ctx, doc["customer_id"], labels["prepared_for"])
        view["site"] = _site(ctx, job, view["customer"])
        if doc.get("reason"):
            view["intro"] = f"{labels['change_reason']}: {doc['reason']}"
        view["sections"] = _sections_from_lines(ctx, doc["lines"])
        view["totals"] = _totals_rows(ctx, doc["totals"])
        if doc["status"] == "approved" and doc.get("approval"):
            appr = doc["approval"]
            view["acceptance"] = {
                "heading": labels["accept_heading"],
                "signature": False,
                "text": _signed_off(ctx, labels["approved_by"], appr, view["customer"]),
            }
        else:
            view["acceptance"] = {
                "heading": labels["accept_heading"],
                "text": labels["approval_text"],
                "signature": True,
            }
        if doc["status"] == "void":
            view["stamp"] = labels["void_stamp"]
        if doc["id"] == "PREVIEW":
            view["stamp"] = labels["draft_stamp"]
        return view

    if doc["type"] == "invoice":
        view = _base(ctx, doc, "invoice")
        meta = []
        if doc.get("date"):
            meta.append((labels["date"], ctx.date(doc["date"])))
        if doc.get("due_date"):
            meta.append((labels["due_date"], ctx.date(doc["due_date"])))
        meta.append((labels["job"], doc["job_id"]))
        if (doc.get("source") or {}).get("quote"):
            meta.append((labels["reference"], doc["source"]["quote"]))
        view["meta"] = meta
        view["customer"] = _customer(ctx, doc["customer_id"], labels["bill_to"])
        view["site"] = _site(ctx, job, view["customer"])
        view["sections"] = _sections_from_lines(ctx, doc["lines"])
        view["totals"] = _totals_rows(ctx, doc["totals"])
        from .documents import invoice_state  # avoid import cycle at module load

        state = invoice_state(doc, store.today())
        deposits = [p for p in doc.get("payments", []) if p.get("from_deposit")]
        held = sum((to_decimal(p["amount"]) for p in deposits), to_decimal(0))
        paid = sum((to_decimal(p["amount"]) for p in doc.get("payments", [])), to_decimal(0)) - held
        credits = sum((to_decimal(c["amount"]) for c in doc.get("credits", [])), to_decimal(0))
        if held:
            label = labels["deposit_received"]
            if len({p["date"] for p in deposits}) == 1:
                label += f" ({ctx.date(deposits[0]['date'])})"
            view["totals"].append({"label": label, "value": ctx.money(-held), "strong": False})
        if paid:
            view["totals"].append({"label": labels["payments_received"], "value": ctx.money(-paid), "strong": False})
        if credits:
            view["totals"].append({"label": labels["credits"], "value": ctx.money(-credits), "strong": False})
        view["balance"] = {"label": labels["balance_due"], "value": ctx.money(max(state["balance"], 0), code=True)}
        prev = (doc.get("source") or {}).get("previously_billed_percent")
        if prev:
            view["intro"] = (
                view["intro"] + "\n" if view["intro"] else ""
            ) + f"{labels['previously_invoiced']}: {prev}%"
        if doc["status"] != "void" and state["balance"] > 0:
            view["payment"] = _payment_block(ctx, doc.get("payment_link") or "", doc["id"])
        view["thank_you"] = labels["thank_you"]
        if doc["status"] == "draft":
            view["stamp"] = labels["draft_stamp"]
        elif doc["status"] == "void":
            view["stamp"] = labels["void_stamp"]
        elif state["balance"] <= 0:
            view["stamp"] = labels["paid_stamp"]
        return view

    if doc["type"] == "credit_note":
        view = _base(ctx, doc, "credit_note")
        view["meta"] = [
            (labels["date"], ctx.date(doc["date"])),
            (labels["for_invoice"], doc["invoice_id"]),
            (labels["job"], doc["job_id"]),
        ]
        view["customer"] = _customer(ctx, doc["customer_id"], labels["bill_to"])
        view["site"] = None
        view["subject"] = f"{labels['credit_for']} {doc['invoice_id']}"
        if doc.get("reason"):
            view["intro"] = f"{labels['reason']}: {doc['reason']}"
        view["sections"] = _sections_from_lines(ctx, doc["lines"])
        view["totals"] = _totals_rows(ctx, doc["totals"])
        view["terms"] = ""
        if doc["id"] == "PREVIEW":
            view["stamp"] = labels["draft_stamp"]
        return view

    raise ValueError(f"Can't render a {doc.get('type')}")


def build_receipt_view(store, payment: dict, invoice: dict | None, job: dict) -> dict:
    ctx = _Ctx(store)
    labels = ctx.labels
    fake = {"type": "receipt", "id": payment["id"], "title": job.get("title", ""), "notes": payment.get("note", "")}
    view = _base(ctx, fake, "receipt")
    view["terms"] = ""
    meta = [(labels["date"], ctx.date(payment["date"]))]
    if payment.get("method"):
        meta.append((labels["payment_method"], payment["method"]))
    if payment.get("reference"):
        meta.append((labels["reference"], payment["reference"]))
    if invoice:
        meta.append((labels["for_invoice"], invoice["id"]))
    meta.append((labels["job"], job["id"]))
    view["meta"] = meta
    view["customer"] = _customer(ctx, job["customer_id"], labels["received_from"])
    view["site"] = None
    if invoice:
        desc = f"{labels['payment_received']} - {labels['for_invoice']} {invoice['id']}: {invoice.get('title', '')}"
    else:
        desc = f"{labels['deposit_for']}: {job.get('title', '')}"
    view["sections"] = [
        {
            "heading": None,
            "subheading": "",
            "badge": "",
            "totals": None,
            "simple": True,  # description and amount only
            "rows": [
                {"desc": desc, "qty": "", "unit_price": "", "amount": ctx.money(payment["amount"]), "style": "item"}
            ],
        }
    ]
    view["totals"] = [
        {"label": labels["amount_paid"], "value": ctx.money(payment["amount"], code=True), "strong": True}
    ]
    if invoice:
        from .documents import invoice_state

        state = invoice_state(invoice, store.today())
        view["balance"] = {"label": labels["balance_due"], "value": ctx.money(max(state["balance"], 0), code=True)}
    view["stamp"] = labels["paid_stamp"]
    view["thank_you"] = labels["thank_you"]
    return view
