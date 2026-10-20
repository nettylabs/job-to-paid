"""Quotes, change orders, invoices, credit notes and payments.

Rules that keep the books trustworthy:
  * numbers are sequential and never reused (the newest draft can be deleted,
    anything older is voided instead, so there are no gaps)
  * issued invoices are locked; corrections are made with credit notes
  * a quote's discount only applies to the quote's own lines
  * every total comes from money.compute_totals, never from the agent
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import secrets
from collections import OrderedDict, defaultdict
from decimal import Decimal

from . import confirm
from . import profile as profile_mod
from .money import (
    HUNDRED,
    PRICED_KINDS,
    ZERO,
    allocate,
    compute_totals,
    format_money,
    format_total,
    format_qty,
    line_tax_breakdown,
    q,
)
from .pricing import lines_as_spec, normalize_lines
from .records import job_create
from .util import (
    JTPError,
    dec_str,
    fold_letters,
    from_iso,
    iso,
    now_stamp,
    parse_date,
    to_decimal,
    trim_decimal,
    zone_name,
)

QUOTE_STATUSES = ("draft", "sent", "accepted", "declined", "expired", "superseded", "void")
OPEN_QUOTE = ("draft", "sent", "expired")
CO_STATUSES = ("draft", "sent", "approved", "declined", "void")
INVOICE_STATUSES = ("draft", "issued", "paid", "void")
CHANNELS = ("email", "sms", "whatsapp", "phone", "in_person", "portal", "other")
PREVIEW_ID = "PREVIEW"
SAMPLE_CUSTOMER = "SAMPLE"


def customer_label(store, customer_id) -> str:
    """The customer as the owner knows them: "Company (Name)", or whichever of the two is set."""
    if customer_id == SAMPLE_CUSTOMER:
        return "Sample Customer"
    try:
        cust = store.customer(customer_id)
    except JTPError:
        return customer_id
    company, name = cust.get("company"), cust.get("name")
    if company and name and company.lower() != name.lower():
        return f"{company} ({name})"
    return name or company or customer_id


def priced(lines: list) -> list:
    return [ln for ln in lines if ln.get("kind") in PRICED_KINDS]


def build_lines(store, specs, prefix: str) -> tuple:
    profile = store.profile()
    lines, warnings, suggestions = normalize_lines(profile, store.pricebook(), specs, store.places, prefix)
    totals = compute_totals(lines, profile["tax"], store.places)
    return lines, totals, warnings, suggestions


def _channel(value: str | None) -> str:
    channel = (value or "email").lower().replace("-", "_").replace(" ", "_")
    if channel not in CHANNELS:
        raise JTPError(f"Unknown channel {value!r}.", hint=f"Use one of: {', '.join(CHANNELS)}")
    return channel


def _require_ready(store, what: str) -> None:
    profile = store.profile()
    if not profile["business"].get("name"):
        raise JTPError(
            f"Set your business name before creating a {what}.",
            hint='jtp profile set business.name "Your Business Name"',
            code="setup_needed",
        )
    if not profile["tax"].get("confirmed"):
        raise JTPError(
            f"Tax setup isn't confirmed yet, so a {what} could show the wrong tax.",
            hint="Ask the owner which taxes they charge (or confirm none), save them, then: "
            "jtp profile set tax.confirmed true",
            code="setup_needed",
        )


def _shown_date(store, text) -> str:
    """A date as the owner writes it (locale.date_format, e.g. 18 Oct 2026), for messages."""
    from .view import format_date  # local import, as in reports.py

    return format_date(from_iso(text), store.profile()["locale"].get("date_format")) if text else ""


def _resolve_job(store, job_id, customer_id, title, site, dry_run, tags=None):
    if job_id:
        return store.job(job_id), False
    if not customer_id and dry_run:
        # A preview with no customer yet (e.g. showing a new owner what customers will see).
        return {
            "id": PREVIEW_ID,
            "customer_id": SAMPLE_CUSTOMER,
            "title": title or "Sample job",
            "site_address": site or "",
            "log": [],
            "quotes": [],
            "invoices": [],
            "change_orders": [],
            "payments": [],
            "status": "lead",
        }, False
    if not customer_id:
        raise JTPError(
            "Say which job (--job) or which customer (--customer) this is for.",
            hint="Find them with: jtp job list --search <words> / jtp customer list --search <name>",
        )
    job = job_create(store, customer_id, title or "Job", site=site, tags=tags, save=not dry_run)
    job["auto_created"] = True
    return job, True


def _drop_empty_auto_job(store, job: dict) -> bool:
    """Remove a job that was only created to hold a just-deleted draft."""
    if (
        not job.get("auto_created")
        or job.get("quotes")
        or job.get("invoices")
        or job.get("change_orders")
        or job.get("log")
        or job.get("payments")
    ):
        return False
    path = store.job_path(job["id"])
    if path.exists():
        path.unlink()
    store.release_last_id("job", job["id"])
    store.log("job.remove_empty", job["id"])
    return True


def _deposit_from(spec: dict, profile: dict, places: int) -> dict | None:
    if spec.get("deposit_amount") not in (None, "", 0, "0"):
        amount = q(to_decimal(spec["deposit_amount"], "deposit_amount"), places)
        return {"amount": str(amount)} if amount > 0 else None
    pct_raw = spec.get("deposit_percent")
    if pct_raw in (None, ""):
        pct_raw = profile["terms"].get("deposit_percent") or "0"
    pct = to_decimal(pct_raw, "deposit_percent")
    if pct < 0 or pct > 100:
        raise JTPError("deposit_percent must be between 0 and 100.")
    return {"percent": trim_decimal(pct)} if pct > 0 else None


def deposit_due(deposit: dict | None, total: Decimal, places: int) -> Decimal | None:
    if not deposit:
        return None
    if "percent" in deposit:
        return q(total * to_decimal(deposit["percent"]) / HUNDRED, places)
    return min(to_decimal(deposit["amount"]), total)


def _terms_text(*parts) -> str:
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


# ===================================================================== quotes


def _quote_content(store, spec: dict, base_title: str) -> tuple:
    profile = store.profile()
    places = store.places
    title = (spec.get("title") or base_title or "Quote").strip()
    options_spec = spec.get("options")
    if options_spec is None:
        if not spec.get("lines"):
            raise JTPError(
                "A quote needs lines (or options with lines).",
                hint='{"title":"...","lines":[{"kind":"labour","description":"Install","qty":3}]}',
            )
        options_spec = [{"name": title, "lines": spec["lines"]}]
    if not isinstance(options_spec, list) or not options_spec:
        raise JTPError("options must be a non-empty list.")
    if len(options_spec) > 6:
        raise JTPError("Keep it to 6 options or fewer; 3 (good / better / best) works best.")
    letters = "ABCDEF"
    options, warnings, suggestions, seen = [], [], [], set()
    for index, ospec in enumerate(options_spec):
        key = str(ospec.get("key") or letters[index]).upper()
        if key in seen:
            raise JTPError(f"Option key {key} is used twice.")
        seen.add(key)
        lines, totals, warn, sugg = build_lines(store, ospec.get("lines"), prefix=key)
        warnings += [f"Option {key}: {w}" for w in warn] if len(options_spec) > 1 else warn
        suggestions += sugg
        options.append(
            {
                "key": key,
                "name": str(ospec.get("name") or f"Option {key}"),
                "description": str(ospec.get("description") or ""),
                "recommended": bool(ospec.get("recommended")),
                "lines": lines,
                "totals": totals,
            }
        )
    deposit = _deposit_from(spec, profile, places)
    explicit = any(spec.get(k) not in (None, "") for k in ("deposit_percent", "deposit_amount"))
    threshold = to_decimal(profile["terms"].get("deposit_min_total") or "0", "deposit_min_total")
    for option in options:
        total = to_decimal(option["totals"]["total"])
        due = deposit_due(deposit, total, places)
        if due is not None and not explicit and threshold > 0 and total < threshold:
            due = None  # the owner only takes deposits on bigger jobs
        option["deposit_due"] = str(due) if due is not None else None
    if deposit and not any(o["deposit_due"] for o in options):
        deposit = None
    valid_days = int(spec.get("valid_days") or profile["terms"].get("quote_valid_days") or 30)
    day = store.today()
    content = {
        "title": title,
        "intro": str(spec.get("intro") or ""),
        "notes": str(spec.get("notes") or ""),
        "terms": str(spec["terms"])
        if spec.get("terms") is not None
        else _terms_text(profile["terms"].get("quote_terms"), profile["terms"].get("warranty")),
        "date": iso(day),
        "valid_until": iso(day + _dt.timedelta(days=valid_days)),
        "options": options,
        "deposit": deposit,
    }
    return content, warnings, _dedupe(suggestions)


def _dedupe(items: list) -> list:
    seen, out = set(), []
    for item in items:
        key = item["name"].lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def quote_create(store, spec: dict, job_id=None, customer_id=None, dry_run=False) -> dict:
    if not dry_run:
        _require_ready(store, "quote")
    job, new_job = _resolve_job(
        store, job_id, customer_id, spec.get("title"), spec.get("site_address"), dry_run, spec.get("tags")
    )
    content, warnings, suggestions = _quote_content(store, spec, job["title"])
    if new_job or (dry_run and not job_id and customer_id):
        others = [
            d for d in store.docs("quote") if d["customer_id"] == job["customer_id"] and d["status"] in OPEN_QUOTE
        ]
        if others:
            listed = ", ".join(f"{d['id']} ({d['title']}, job {d['job_id']})" for d in others)
            warnings.append(
                f"This customer already has open quotes: {listed}. If this one replaces one of them, create it "
                "with --job <that job> instead (the old one is retired when this one is accepted), or revise "
                "the old quote, so the customer doesn't get follow-ups for both."
            )
    doc = {
        "id": PREVIEW_ID if dry_run else store.next_id("quote"),
        "type": "quote",
        "revision": 1,
        "status": "draft",
        "job_id": job["id"],
        "customer_id": job["customer_id"],
        **content,
        "accepted": None,
        "sent": [],
        "followups": [],
        "history": [],
        "created_at": now_stamp(),
    }
    if not dry_run:
        store.save_doc(doc)
        job["quotes"].append(doc["id"])
        store.save_job(job)
        store.log("quote.create", doc["id"], job=job["id"], options=len(doc["options"]))
    return {
        "quote": doc,
        "job_created": new_job and not dry_run,
        "warnings": warnings,
        "pricebook_suggestions": suggestions,
    }


def quote_revise(store, ident: str, spec: dict) -> dict:
    doc = store.doc(ident, "quote")
    if doc["status"] in ("accepted", "declined", "void", "superseded"):
        raise JTPError(
            f"{doc['id']} is {doc['status']} and can't be changed.",
            hint="For extra work on an accepted job use a change order: jtp co create --job "
            f"{doc['job_id']} --spec <file>. Otherwise create a new quote.",
        )
    content, warnings, suggestions = _quote_content(store, spec, doc["title"])
    if doc["status"] != "draft":
        snapshot = {
            k: doc.get(k)
            for k in (
                "revision",
                "status",
                "title",
                "intro",
                "notes",
                "terms",
                "date",
                "valid_until",
                "options",
                "deposit",
                "sent",
                "followups",
            )
        }
        doc["history"].append(snapshot)
        doc["revision"] = doc.get("revision", 1) + 1
        doc["status"] = "draft"
        doc["sent"] = []
        doc["followups"] = []
    doc.update(content)
    store.save_doc(doc)
    store.log("quote.revise", doc["id"], revision=doc["revision"])
    return {
        "quote": doc,
        "warnings": warnings,
        "pricebook_suggestions": suggestions,
        "note": "Revised. Send it again so the customer has the latest version." if doc["revision"] > 1 else "",
    }


def _record_send(doc: dict, channel: str, to: str | None, day: _dt.date) -> bool:
    record = {"date": iso(day), "channel": channel, "to": to or ""}
    if doc.get("revision"):
        record["revision"] = doc["revision"]
    if record in doc.get("sent", []):
        return False
    doc.setdefault("sent", []).append(record)
    return True


def _replaced_error(doc: dict) -> JTPError:
    by = (doc.get("superseded") or {}).get("by")
    return JTPError(
        f"{doc['id']} was replaced" + (f" by {by}, which the customer accepted." if by else "."),
        hint=f"Work with {by} instead." if by else "Create a new quote instead.",
    )


def quote_mark_sent(store, ident: str, channel: str | None, to: str | None, date=None) -> dict:
    doc = store.doc(ident, "quote")
    if doc["status"] == "superseded":
        raise _replaced_error(doc)
    if doc["status"] in ("accepted", "declined", "void"):
        raise JTPError(f"{doc['id']} is already {doc['status']}.")
    if doc["status"] == "expired":
        raise JTPError(f"{doc['id']} has expired.", hint=f"Extend it first: jtp quote extend {doc['id']} --days 14")
    day = parse_date(date or "today", store.today())
    added = _record_send(doc, _channel(channel), to, day)
    doc["status"] = "sent"
    store.save_doc(doc)
    job = store.job(doc["job_id"])
    if job["status"] == "lead":
        job["status"] = "quoted"
        store.save_job(job)
    if added:
        store.log("quote.sent", doc["id"], channel=channel, to=to)
    return {"quote": doc, "already_recorded": not added}


def quote_accept(
    store, ident: str, option: str | None, by: str | None, method: str | None, note: str | None, date=None
) -> dict:
    doc = store.doc(ident, "quote")
    keys = [o["key"] for o in doc["options"]]
    if option is None and len(keys) == 1:
        option = keys[0]
    if option is None:
        raise JTPError(
            f"{doc['id']} has options {', '.join(keys)}. Which one did the customer pick?",
            hint=f"jtp quote accept {doc['id']} --option {keys[0]}",
        )
    option = option.upper()
    if option not in keys:
        raise JTPError(f"{doc['id']} has no option {option}.", hint=f"Options: {', '.join(keys)}")
    if doc["status"] == "accepted":
        if doc["accepted"]["option"] == option:
            return {"quote": doc, "already_recorded": True}
        raise JTPError(
            f"{doc['id']} was already accepted with option {doc['accepted']['option']}.",
            hint="If the customer changed their mind, use a change order for the difference.",
        )
    if doc["status"] == "superseded":
        raise _replaced_error(doc)
    if doc["status"] in ("declined", "void"):
        raise JTPError(f"{doc['id']} is {doc['status']}.", hint="Create a new quote instead.")
    job = store.job(doc["job_id"])
    earlier = job.get("accepted_quote")
    if earlier and earlier != doc["id"] and store.doc(earlier, "quote")["status"] == "accepted":
        raise JTPError(
            f"{job['id']} already has an accepted quote ({earlier}), and a job bills one quote.",
            hint=f"Price extra or changed work as a change order: jtp co create --job {job['id']} --spec ... "
            "If this quote is for separate work, it needs its own job.",
        )
    warnings = []
    day = parse_date(date or "today", store.today())
    if doc["valid_until"] and day > from_iso(doc["valid_until"]):
        warnings.append(
            f"Accepted after the quote's valid-until date ({_shown_date(store, doc['valid_until'])}). "
            "Check your prices still hold before booking the work."
        )
    if doc["status"] == "draft":
        warnings.append("This quote was never marked as sent. Accepted anyway.")
    doc["status"] = "accepted"
    doc["accepted"] = {"option": option, "by": by or "", "method": method or "", "note": note or "", "date": iso(day)}
    store.save_doc(doc)
    # Other open quotes on this job were alternatives; stop them getting follow-ups.
    replaced = []
    for other_id in job.get("quotes", []):
        other = store.doc(other_id, "quote") if other_id != doc["id"] else None
        if other and other["status"] in OPEN_QUOTE:
            _supersede(store, other, doc["id"], day)
            replaced.append(other["id"])
    elsewhere = [
        d
        for d in store.docs("quote")
        if d["customer_id"] == doc["customer_id"] and d["job_id"] != job["id"] and d["status"] in OPEN_QUOTE
    ]
    job["pricing"] = "fixed"
    job["accepted_quote"] = doc["id"]
    job["accepted_option"] = option
    if job["status"] in ("lead", "quoted", "lost"):
        job["status"] = "won"
    store.save_job(job)
    chosen = next(o for o in doc["options"] if o["key"] == option)
    store.log("quote.accept", doc["id"], option=option, by=by)
    return {
        "quote": doc,
        "job": job["id"],
        "accepted_total": chosen["totals"]["total"],
        "deposit_due": chosen.get("deposit_due"),
        "replaced_quotes": replaced,
        "other_open_quotes": [
            {"id": d["id"], "title": d["title"], "job": d["job_id"], "status": d["status"]} for d in elsewhere
        ],
        "warnings": warnings,
    }


def _supersede(store, doc: dict, by: str | None, day: _dt.date) -> None:
    doc["status"] = "superseded"
    doc["superseded"] = {"by": by or "", "date": iso(day)}
    store.save_doc(doc)
    store.log("quote.superseded", doc["id"], by=by)


def quote_supersede(store, ident: str, by: str | None = None, date=None) -> dict:
    """Mark an open quote as replaced by another one (no more follow-ups, not counted as lost)."""
    doc = store.doc(ident, "quote")
    if doc["status"] not in OPEN_QUOTE:
        raise JTPError(f"{doc['id']} is {doc['status']}; only open quotes can be marked as replaced.")
    by_id = store.doc(by, "quote")["id"] if by else None
    if by_id == doc["id"]:
        raise JTPError("A quote can't replace itself.")
    _supersede(store, doc, by_id, parse_date(date or "today", store.today()))
    return {"quote": doc}


def quote_decline(store, ident: str, reason: str | None, date=None) -> dict:
    doc = store.doc(ident, "quote")
    if doc["status"] in ("accepted", "void", "superseded"):
        raise JTPError(f"{doc['id']} is {doc['status']}.")
    doc["status"] = "declined"
    doc["declined"] = {"reason": reason or "", "date": iso(parse_date(date or "today", store.today()))}
    store.save_doc(doc)
    job = store.job(doc["job_id"])
    open_quotes = [
        q_id for q_id in job["quotes"] if q_id != doc["id"] and store.doc(q_id, "quote")["status"] in ("draft", "sent")
    ]
    if not open_quotes and job["status"] in ("lead", "quoted"):
        job["status"] = "lost"
        store.save_job(job)
    store.log("quote.decline", doc["id"], reason=reason)
    return {"quote": doc}


def quote_extend(store, ident: str, days: int | None, until=None) -> dict:
    doc = store.doc(ident, "quote")
    if doc["status"] not in ("draft", "sent", "expired"):
        raise JTPError(f"{doc['id']} is {doc['status']}.")
    base = store.today()
    new_date = parse_date(until, base) if until else base + _dt.timedelta(days=int(days or 14))
    doc["valid_until"] = iso(new_date)
    if doc["status"] == "expired":
        doc["status"] = "sent" if doc.get("sent") else "draft"
    store.save_doc(doc)
    store.log("quote.extend", doc["id"], valid_until=doc["valid_until"])
    return {"quote": doc}


def quote_expire(store, ident: str) -> dict:
    doc = store.doc(ident, "quote")
    if doc["status"] not in ("draft", "sent"):
        raise JTPError(f"{doc['id']} is {doc['status']}.")
    doc["status"] = "expired"
    store.save_doc(doc)
    store.log("quote.expire", doc["id"])
    return {"quote": doc}


# ============================================================== change orders


def co_create(store, job_id: str, spec: dict, dry_run=False) -> dict:
    if not dry_run:
        _require_ready(store, "change order")
    job = store.job(job_id)
    if not spec.get("lines"):
        raise JTPError(
            "A change order needs lines.",
            hint='{"title":"Add shut-off valve","reason":"Old valve seized","lines":[...]}',
        )
    lines, totals, warnings, suggestions = build_lines(store, spec["lines"], prefix="CO")
    doc = {
        "id": PREVIEW_ID if dry_run else store.next_id("change_order"),
        "type": "change_order",
        "status": "draft",
        "job_id": job["id"],
        "customer_id": job["customer_id"],
        "quote_id": job.get("accepted_quote"),
        "title": str(spec.get("title") or "Change to the agreed work"),
        "reason": str(spec.get("reason") or ""),
        "notes": str(spec.get("notes") or ""),
        "date": iso(store.today()),
        "lines": lines,
        "totals": totals,
        "approval": None,
        "invoice": None,
        "sent": [],
        "created_at": now_stamp(),
    }
    if not dry_run:
        store.save_doc(doc)
        job["change_orders"].append(doc["id"])
        store.save_job(job)
        store.log("co.create", doc["id"], job=job["id"])
    return {"change_order": doc, "warnings": warnings, "pricebook_suggestions": suggestions}


def co_update(store, ident: str, spec: dict) -> dict:
    doc = store.doc(ident, "change_order")
    if doc["status"] not in ("draft", "sent"):
        raise JTPError(f"{doc['id']} is {doc['status']} and can't be changed.", hint="Create a new change order.")
    if spec.get("lines"):
        doc["lines"], doc["totals"], warnings, suggestions = build_lines(store, spec["lines"], prefix="CO")
    else:
        warnings, suggestions = [], []
    for key in ("title", "reason", "notes"):
        if spec.get(key) is not None:
            doc[key] = str(spec[key])
    if doc["status"] == "sent":
        doc["status"] = "draft"
        doc["sent"] = []
    store.save_doc(doc)
    store.log("co.update", doc["id"])
    return {"change_order": doc, "warnings": warnings, "pricebook_suggestions": suggestions}


def co_mark_sent(store, ident: str, channel, to, date=None) -> dict:
    doc = store.doc(ident, "change_order")
    if doc["status"] not in ("draft", "sent"):
        raise JTPError(f"{doc['id']} is {doc['status']}.")
    added = _record_send(doc, _channel(channel), to, parse_date(date or "today", store.today()))
    doc["status"] = "sent"
    store.save_doc(doc)
    return {"change_order": doc, "already_recorded": not added}


def co_approve(store, ident: str, by, method, note, date=None) -> dict:
    doc = store.doc(ident, "change_order")
    if doc["status"] == "approved":
        return {"change_order": doc, "already_recorded": True}
    if doc["status"] not in ("draft", "sent"):
        raise JTPError(f"{doc['id']} is {doc['status']}.")
    doc["status"] = "approved"
    doc["approval"] = {
        "by": by or "",
        "method": method or "",
        "note": note or "",
        "date": iso(parse_date(date or "today", store.today())),
    }
    store.save_doc(doc)
    store.log("co.approve", doc["id"], by=by)
    return {"change_order": doc}


def co_decline(store, ident: str, reason) -> dict:
    doc = store.doc(ident, "change_order")
    if doc["status"] not in ("draft", "sent"):
        raise JTPError(f"{doc['id']} is {doc['status']}.")
    doc["status"] = "declined"
    doc["declined"] = {"reason": reason or "", "date": iso(store.today())}
    store.save_doc(doc)
    store.log("co.decline", doc["id"])
    return {"change_order": doc}


def co_void(store, ident: str, reason) -> dict:
    doc = store.doc(ident, "change_order")
    if doc.get("invoice"):
        raise JTPError(f"{doc['id']} is on invoice {doc['invoice']}.", hint="Void that invoice first.")
    warnings = _remember_estimates(store, doc, "voided") if doc["status"] != "void" else []
    doc["status"] = "void"
    doc["void_reason"] = reason or ""
    store.save_doc(doc)
    store.log("co.void", doc["id"])
    return {"change_order": doc, "warnings": warnings}


# =================================================================== invoices


def _fmt_day(store, day_text: str) -> str:
    from .view import format_date  # local import to avoid a cycle

    return format_date(from_iso(day_text), store.profile()["locale"].get("date_format"))


def log_entry_specs(store, job: dict, entries: list, section: str | None) -> tuple:
    """Specs for unbilled log entries: time grouped per labour rate, materials and expenses itemized."""
    profile = store.profile()
    labels = profile_mod.labels(profile)
    specs, notes = [], []
    by_rate = OrderedDict()
    for entry in entries:
        if entry["type"] == "time":
            by_rate.setdefault(entry["rate_key"], []).append(entry)
    for rate_key, group in by_rate.items():
        rate = profile_mod.labour_rate(profile, rate_key)
        hours = sum((to_decimal(e["hours"]) for e in group), ZERO)
        days = OrderedDict()
        for e in group:
            days[e["date"]] = days.get(e["date"], ZERO) + to_decimal(e["hours"])
        disp = store.money()
        short = labels["hour_short"]
        detail = ", ".join(f"{_fmt_day(store, d)} ({format_qty(h, disp)} {short})" for d, h in list(days.items())[:8])
        if len(days) > 8:
            detail += ", ..."
        name = rate.get("name") or labels["labour"]
        notes_text = "; ".join(dict.fromkeys(e["note"].strip() for e in group if (e.get("note") or "").strip()))
        if notes_text:
            detail += f": {notes_text}"
        specs.append(
            {
                "kind": "labour",
                "description": f"{name}: {detail}",
                "qty": trim_decimal(hours),
                "unit": labels["hour_unit"],
                "rate_key": rate_key,
                "section": section,
                "source": {"log": [e["id"] for e in group]},
            }
        )
    for entry in entries:
        if entry["type"] == "material":
            spec = {
                "kind": "material",
                "description": entry.get("description") or "",
                "qty": entry["qty"],
                "unit": entry.get("unit") or "ea",
                "section": section,
                "source": {"log": [entry["id"]]},
            }
            for key in ("unit_cost", "unit_price", "markup_pct", "pricebook"):
                if entry.get(key) not in (None, ""):
                    spec[key] = entry[key]
            specs.append(spec)
        elif entry["type"] == "expense":
            amount = to_decimal(entry["amount"])
            spec = {
                "kind": "other",
                "description": entry["description"],
                "qty": "1",
                "unit": "",
                "unit_cost": entry["amount"],
                "section": section,
                "display_qty": False,
                "source": {"log": [entry["id"]]},
            }
            if entry.get("markup_pct") not in (None, ""):
                spec["markup_pct"] = entry["markup_pct"]
            else:
                spec["unit_price"] = dec_str(amount)
            specs.append(spec)
    return specs, notes


def _line_taxes(store, ln: dict) -> dict:
    """The tax on one stored line, by code (worked out again for lines saved before per-line tax)."""
    if ln.get("tax_amounts") is not None:
        return {code: to_decimal(value) for code, value in ln["tax_amounts"].items()}
    cfg = store.profile()["tax"]
    net = to_decimal(ln.get("net", ln["amount"]))
    return line_tax_breakdown(ln, net, cfg.get("taxes") or [], bool(cfg.get("prices_include_tax")), store.places)[0]


def _tax_left(store, ln: dict, remaining: Decimal, billed_tax: dict) -> dict:
    """Pin the tax on a quote line's last instalment, so all its invoices add up to the quote exactly."""
    already = billed_tax.get(ln["id"]) or {}
    return {
        "net": dec_str(remaining),
        "taxes": {code: dec_str(value - already.get(code, ZERO)) for code, value in _line_taxes(store, ln).items()},
    }


def _quote_billing(store, job: dict, quote_id: str, exclude: str | None = None) -> tuple:
    billed = defaultdict(lambda: ZERO)
    billed_tax = defaultdict(lambda: defaultdict(lambda: ZERO))
    progress = ZERO
    final_exists = False
    for inv_id in job.get("invoices", []):
        if inv_id == exclude:
            continue
        inv = store.doc(inv_id, "invoice")
        source = inv.get("source") or {}
        if inv["status"] == "void" or source.get("quote") != quote_id:
            continue
        if inv.get("kind") in ("progress", "deposit"):
            progress += to_decimal(source.get("progress_percent") or "0")
        else:
            final_exists = True
        for ln in priced(inv["lines"]):
            ref = (ln.get("source") or {}).get("quote_line")
            if ref:
                billed[ref] += to_decimal(ln.get("net", ln["amount"]))
                for code, value in _line_taxes(store, ln).items():
                    billed_tax[ref][code] += value
    return billed, billed_tax, progress, final_exists


def invoice_create(
    store,
    job_id=None,
    customer_id=None,
    from_quote=None,
    progress=None,
    deposit_invoice=False,
    tm=False,
    spec=None,
    include_change_orders=True,
    include_extras=True,
    title=None,
    dry_run=False,
) -> dict:
    if not dry_run:
        _require_ready(store, "invoice")
    profile = store.profile()
    labels = profile_mod.labels(profile)
    spec = spec or {}
    job, new_job = _resolve_job(
        store, job_id, customer_id, title or spec.get("title"), spec.get("site_address"), dry_run, spec.get("tags")
    )
    warnings = []
    specs = []
    source = {}
    kind = "standard"

    use_quote = from_quote or progress is not None or deposit_invoice
    asked_for_quote = bool(use_quote)  # rather than invoice create --job J, which bills whatever is due
    quote_done = None  # the quote itself is fully billed; approved change orders and extras may still be due
    if not use_quote and not tm and not spec.get("lines"):
        if job.get("accepted_quote"):
            use_quote = True
        elif any(
            e.get("billable") and not e.get("invoice")
            for e in job["log"]
            if e["type"] in ("time", "material", "expense")
        ):
            tm = True
        else:
            raise JTPError(
                f"Nothing to invoice on {job['id']} yet.",
                hint="Accept a quote, log time/materials, or pass lines with --spec.",
            )

    if use_quote:
        quote_id = from_quote if isinstance(from_quote, str) else job.get("accepted_quote")
        if not quote_id:
            raise JTPError(
                f"{job['id']} has no accepted quote.",
                hint="Accept one first: jtp quote accept <Q> --option <A>, or invoice with --tm / --spec.",
            )
        quote = store.doc(quote_id, "quote")
        if quote["status"] != "accepted":
            raise JTPError(
                f"{quote['id']} isn't accepted (it's {quote['status']}).",
                hint=f"jtp quote accept {quote['id']} --option <key>",
            )
        option = next(o for o in quote["options"] if o["key"] == quote["accepted"]["option"])
        billed, billed_tax, billed_pct, final_exists = _quote_billing(store, job, quote["id"])
        source.update({"quote": quote["id"], "option": option["key"]})
        if final_exists:
            if asked_for_quote:
                raise JTPError(
                    f"{quote['id']} has already been fully invoiced.",
                    hint=f"Bill extra work with a change order (jtp invoice create --job {job['id']} picks up approved "
                    "ones) or --spec lines.",
                )
            quote_done, kind = quote["id"], "final"
        quote_lines = priced(option["lines"])
        if quote_done:
            pass
        elif progress is not None or deposit_invoice:
            pct = to_decimal(progress if progress is not None else "0", "progress percent")
            if pct <= 0 or pct > HUNDRED - billed_pct:
                raise JTPError(
                    f"Progress percent must be more than 0 and at most {trim_decimal(HUNDRED - billed_pct)} "
                    f"(already billed {trim_decimal(billed_pct)}%)."
                )
            kind = "deposit" if deposit_invoice else "progress"
            source["progress_percent"] = trim_decimal(pct)
            last = billed_pct + pct == HUNDRED
            word = labels["deposit_for"] if deposit_invoice else labels["progress"]
            for ln in quote_lines:
                net = to_decimal(ln["net"])
                amount = net - billed[ln["id"]] if last else q(net * pct / HUNDRED, store.places)
                if amount == 0:
                    continue
                line_spec = {
                    "kind": ln["kind"],
                    "description": f"{ln['description']} ({trim_decimal(pct)}% {word})"
                    if not deposit_invoice
                    else f"{word}: {ln['description']} ({trim_decimal(pct)}%)",
                    "qty": "1",
                    "unit": "",
                    "unit_price": dec_str(amount),
                    "tax_codes": ln.get("tax_codes", []),
                    "display_qty": False,
                    "source": {"quote_line": ln["id"]},
                }
                if ln.get("estimate"):
                    line_spec["estimate"] = True  # part of an estimate is still an estimate until the owner's yes
                if last:
                    line_spec["tax_fixed"] = _tax_left(store, ln, amount, billed_tax)
                specs.append(line_spec)
        else:
            kind = "final"
            if billed_pct == 0 and not billed:
                for spec_line, ln in zip(lines_as_spec(option["lines"]), option["lines"]):
                    if ln.get("kind") in PRICED_KINDS:
                        spec_line["source"] = {"quote_line": ln["id"]}
                    specs.append(spec_line)
            else:
                source["previously_billed_percent"] = trim_decimal(billed_pct)
                for ln in quote_lines:
                    remaining = to_decimal(ln["net"]) - billed[ln["id"]]
                    if remaining == 0:
                        continue
                    if remaining < 0:
                        warnings.append(f"Quote line {ln['id']} was over-billed by {-remaining}; left off.")
                        continue
                    balance_spec = {
                        "kind": ln["kind"],
                        "description": f"{ln['description']} ({labels['balance']})",
                        "qty": "1",
                        "unit": "",
                        "unit_price": dec_str(remaining),
                        "tax_codes": ln.get("tax_codes", []),
                        "display_qty": False,
                        "source": {"quote_line": ln["id"]},
                        "tax_fixed": _tax_left(store, ln, remaining, billed_tax),
                    }
                    if ln.get("estimate"):
                        balance_spec["estimate"] = True
                    specs.append(balance_spec)
        if not specs and kind == "final" and not quote_done:
            if asked_for_quote:
                raise JTPError(f"Nothing left to bill on {quote['id']}.")
            quote_done = quote["id"]
    if tm:
        kind = "tm" if kind == "standard" else kind
        entries = [
            e
            for e in job["log"]
            if e["type"] in ("time", "material", "expense") and e.get("billable") and not e.get("invoice")
        ]
        if not entries and not spec.get("lines"):
            raise JTPError(f"No unbilled time, materials or expenses on {job['id']}.")
        log_specs, _ = log_entry_specs(store, job, entries, None)
        specs += log_specs
    elif kind == "final" and include_extras:
        extras = [
            e
            for e in job["log"]
            if e["type"] in ("time", "material", "expense")
            and e.get("billable")
            and e.get("extra")
            and not e.get("invoice")
        ]
        if extras:
            extra_specs, _ = log_entry_specs(store, job, extras, labels["additional_work"])
            specs += extra_specs
        not_billed = [
            e
            for e in job["log"]
            if e["type"] in ("time", "material", "expense") and not e.get("billable") and not e.get("invoice")
        ]
        if not_billed:
            n = len(not_billed)
            warnings.append(
                f"Not billed: {n} log {'entry' if n == 1 else 'entries'} on this fixed-price job not marked "
                "extra (counted toward job costing only). Mark real extras with --extra."
            )

    if include_change_orders and kind in ("final", "tm", "standard"):
        for co_id in job.get("change_orders", []):
            co = store.doc(co_id, "change_order")
            if co.get("invoice"):
                continue
            if co["status"] in ("draft", "sent"):
                warnings.append(f"{co['id']} ({co['title']}) isn't approved yet, so it's not on this invoice.")
                continue
            if co["status"] != "approved":
                continue
            section = f"{labels['change_order']} {co['id']}: {co['title']}"
            for spec_line, ln in zip(lines_as_spec(co["lines"]), co["lines"]):
                spec_line["section"] = section
                spec_line["source"] = {"change_order": co["id"], "co_line": ln["id"]}
                specs.append(spec_line)

    for extra in spec.get("lines") or []:
        extra = dict(extra)
        if kind != "standard" and "section" not in extra:
            extra["section"] = labels["additional_work"]
        specs.append(extra)

    if not specs:
        if quote_done:
            raise JTPError(
                f"Nothing left to bill on {job['id']}: {quote_done} is fully invoiced, and there are no approved "
                "change orders or extras to bill.",
                hint="Get a change order approved first (jtp co approve CO-...), or bill other work with --spec lines.",
            )
        raise JTPError("Nothing to put on this invoice.")
    lines, totals, warn, suggestions = build_lines(store, specs, prefix="I")
    warnings += warn
    change_orders = sorted({(ln.get("source") or {}).get("change_order") for ln in lines} - {None})
    log_ids = sorted({lid for ln in lines for lid in ((ln.get("source") or {}).get("log") or [])})
    if change_orders:
        source["change_orders"] = change_orders
    if log_ids:
        source["log_entries"] = log_ids
    default_title = job["title"]
    if kind == "progress":
        default_title = f"{job['title']} ({labels['progress']} {source['progress_percent']}%)"
    elif kind == "deposit":
        default_title = f"{labels['deposit_for']}: {job['title']}"
    doc = {
        "id": PREVIEW_ID if dry_run else store.next_id("invoice"),
        "type": "invoice",
        "kind": kind,
        "status": "draft",
        "job_id": job["id"],
        "customer_id": job["customer_id"],
        "title": title or spec.get("title") or default_title,
        "intro": str(spec.get("intro") or ""),
        "notes": str(spec.get("notes") or ""),
        "terms": str(spec["terms"])
        if spec.get("terms") is not None
        else _terms_text(profile["terms"].get("invoice_terms"), profile["terms"].get("warranty")),
        "date": None,
        "due_date": None,
        "lines": lines,
        "totals": totals,
        "source": source,
        "payments": [],
        "credits": [],
        "payment_link": "",
        "external": {},
        "sent": [],
        "followups": [],
        "snooze": None,
        "created_at": now_stamp(),
    }
    if any(ln.get("estimate") for ln in lines):
        doc["had_estimates"] = True  # only the owner's yes when issuing clears this (invoice_issue)
    warnings += _copied_warnings(store, doc)
    if not dry_run:
        store.save_doc(doc)
        job["invoices"].append(doc["id"])
        _link(store, job, doc, attach=True)
        store.save_job(job)
        store.log("invoice.create", doc["id"], job=job["id"], kind=kind, total=totals["total"])
    held = deposits_held(job)
    total = to_decimal(totals["total"])
    return {
        "invoice": doc,
        "estimates": [ln.get("description") or ln["id"] for ln in lines if ln.get("estimate")],
        "job_created": new_job and not dry_run,
        "deposits_held": str(held),
        "balance_after_deposits": str(total - min(held, total)),
        "warnings": warnings,
        "pricebook_suggestions": suggestions,
    }


def deposit_for_percent(store, job_id: str, percent) -> tuple:
    """(amount, option total) for "she paid the 25% deposit": a percent of the accepted option's total."""
    job = store.job(job_id)
    if not job.get("accepted_quote"):
        raise JTPError(
            f"{job['id']} has no accepted quote to take a percentage of.",
            hint="Record the amount that came in instead: --amount <amount>",
        )
    quote = store.doc(job["accepted_quote"], "quote")
    option = next(o for o in quote["options"] if o["key"] == quote["accepted"]["option"])
    pct = to_decimal(percent, "percent")
    if pct <= 0 or pct > HUNDRED:
        raise JTPError("The deposit percent must be more than 0 and at most 100.")
    total = to_decimal(option["totals"]["total"])
    return q(total * pct / HUNDRED, store.places), total


def deposits_held(job: dict) -> Decimal:
    """Deposits paid on the job that haven't been applied to an invoice yet."""
    return sum((to_decimal(p.get("remaining", "0")) for p in job.get("payments", [])), ZERO)


def _link(store, job: dict, inv: dict, attach: bool) -> None:
    """Mark (or release) the log entries and change orders an invoice bills."""
    source = inv.get("source") or {}
    log_ids = set(source.get("log_entries") or [])
    for entry in job["log"]:
        if entry["id"] in log_ids:
            if attach:
                entry["invoice"] = inv["id"]
            elif entry.get("invoice") == inv["id"]:
                entry["invoice"] = None
    for co_id in source.get("change_orders") or []:
        co = store.doc(co_id, "change_order")
        if attach:
            co["invoice"] = inv["id"]
        elif co.get("invoice") == inv["id"]:
            co["invoice"] = None
        store.save_doc(co)


def invoice_update(store, ident: str, spec: dict) -> dict:
    inv = store.doc(ident, "invoice")
    if inv["status"] != "draft":
        raise JTPError(
            f"{inv['id']} is {inv['status']}; issued invoices are locked.",
            hint=f"Correct it with a credit note: jtp credit create --invoice {inv['id']} ...",
        )
    job = store.job(inv["job_id"])
    warnings, suggestions = [], []
    if spec.get("lines"):
        _link(store, job, inv, attach=False)
        before = _estimate_items(store, [ln for ln in inv["lines"] if ln.get("estimate")])
        estimated = {item["key"] for item in before}
        taken_off = list(inv.get("removed_estimates") or [])
        inv["lines"], inv["totals"], warnings, suggestions = build_lines(store, spec["lines"], prefix="I")
        kept = []
        for ln in inv["lines"]:  # an estimate stays one until the owner confirms it when issuing
            back = any(_same_line(t["key"], _line_key(ln)) and t["amount"] == _amount(ln) for t in taken_off)
            if not ln.get("estimate") and (any(_same_line(_line_key(ln), key) for key in estimated) or back):
                ln["estimate"] = True
                kept.append(ln.get("description") or ln["id"])
        if kept:
            warnings.append(f"Kept \"(estimate)\" on {', '.join(kept)}: only the owner's yes when issuing confirms "
                            "an estimated figure.")  # fmt: skip
        # An estimate changed or taken off stays on record here, so the same figure on another invoice still asks
        # and this invoice's own question names it (_estimates_question).
        now = {(_line_key(ln).replace(" ", ""), _amount(ln)) for ln in inv["lines"] if ln.get("estimate")}
        for item in before:
            kept_off = {(t["key"].replace(" ", ""), t["amount"]) for t in taken_off}
            if (item["key"].replace(" ", ""), item["amount"]) not in now | kept_off:
                taken_off.append(dict(item, date=iso(store.record_day())))
        taken_off = [t for t in taken_off if (t["key"].replace(" ", ""), t["amount"]) not in now]
        if taken_off:
            inv["removed_estimates"] = taken_off
        else:
            inv.pop("removed_estimates", None)
        if estimated or any(ln.get("estimate") for ln in inv["lines"]):
            inv["had_estimates"] = True
        warnings += _copied_warnings(store, inv)
        source = {k: v for k, v in (inv.get("source") or {}).items() if k not in ("log_entries", "change_orders")}
        cos = sorted({(ln.get("source") or {}).get("change_order") for ln in inv["lines"]} - {None})
        logs = sorted({lid for ln in inv["lines"] for lid in ((ln.get("source") or {}).get("log") or [])})
        if cos:
            source["change_orders"] = cos
        if logs:
            source["log_entries"] = logs
        inv["source"] = source
        _link(store, job, inv, attach=True)
        store.save_job(job)
    for key in ("title", "intro", "notes", "terms"):
        if spec.get(key) is not None:
            inv[key] = str(spec[key])
    store.save_doc(inv)
    store.log("invoice.update", inv["id"])
    return {"invoice": inv, "warnings": warnings, "pricebook_suggestions": suggestions}


def _line_key(line: dict) -> str:
    return _key_of(line.get("description"))


def _key_of(description) -> str:
    """A description folded for matching: case, spacing, punctuation, accents, lookalike letters (also a 0 or 1 in a
    word, for o and l) and a plural s ignored ("Call-outs", "CALL OUT" and "Cаll-0ut" are all "call out")."""
    return " ".join(_key_word(word) for word in re.sub(r"[\W_]+", " ", fold_letters(description)).split())


def _key_word(word: str) -> str:
    if any(ch.isalpha() for ch in word):
        word = word.translate(_DIGIT_LETTERS)  # "0ut", not "10"
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word  # "parts", "glass"


_DIGIT_LETTERS = str.maketrans({"0": "o", "1": "l"})


def invoice_state(inv: dict, day: _dt.date) -> dict:
    total = to_decimal(inv["totals"]["total"])
    paid = sum((to_decimal(p["amount"]) for p in inv.get("payments", [])), ZERO)
    credits = sum((to_decimal(c["amount"]) for c in inv.get("credits", [])), ZERO)
    balance = total - paid - credits
    status = inv["status"]
    state = {"total": total, "paid": paid, "credits": credits, "balance": balance, "status": status, "days_overdue": 0}
    if status in ("issued", "paid"):
        if balance <= 0:
            state["status"] = "paid" if paid > 0 or credits == 0 else "credited"
        elif paid > 0:
            state["status"] = "partially_paid"
        else:
            state["status"] = "issued"
        if credits > 0:
            state["credited"] = True
        due = from_iso(inv.get("due_date"))
        if balance > 0 and due and day > due:
            state["days_overdue"] = (day - due).days
            state["overdue"] = True
    return state


def _refresh_paid(store, inv: dict) -> None:
    if inv["status"] not in ("issued", "paid"):
        return
    state = invoice_state(inv, store.today())
    if state["balance"] <= 0:
        if inv["status"] != "paid":
            inv["status"] = "paid"
            last = max((p["date"] for p in inv.get("payments", [])), default=iso(store.today()))
            inv["paid_date"] = last
    else:
        inv["status"] = "issued"
        inv.pop("paid_date", None)


def _update_job_after_money(store, job: dict) -> None:
    invoices = [store.doc(i, "invoice") for i in job.get("invoices", [])]
    live = [i for i in invoices if i["status"] != "void"]
    if not live:
        return
    if all(i["status"] == "paid" for i in live) and any(i.get("kind") in ("final", "tm", "standard") for i in live):
        if job["status"] in ("invoiced", "complete", "in_progress", "won", "scheduled"):
            job["status"] = "paid"
    elif job["status"] == "paid":
        job["status"] = "invoiced"


def _estimate_lines(inv: dict) -> list:
    """The lines an invoice's own estimates cover: its marked lines, or every priced line once a mark was renamed
    or removed (it remembers it had estimates). Empty when it has none, or the owner already confirmed them."""
    if inv.get("estimates_confirmed"):
        return []
    marked = [ln for ln in inv["lines"] if ln.get("estimate")]
    return marked or (priced(inv["lines"]) if inv.get("had_estimates") else [])


def _amount(line: dict) -> str:
    """A line's figure for matching: its amount before tax, written plainly ("89.00" and "89" are the same)."""
    return format(to_decimal(line.get("amount") or "0").normalize(), "f")


def _same_line(a: str, b: str) -> bool:
    """Two folded descriptions name the same thing, also with their spaces taken out ("Callout", "Call-out")."""
    return a.replace(" ", "") == b.replace(" ", "")


def _related_line(a: str, b: str) -> bool:
    """For an estimate that left its invoice: the same description, or one inside the other as whole words, spaces
    aside ("Call-out part 1", "Repair and call-out", "Callout fee")."""
    if _same_line(a, b):
        return True
    shorter, longer = sorted((a, b), key=lambda key: len(key.replace(" ", "")))
    target, words = shorter.replace(" ", ""), longer.split()
    return len(target) >= 3 and any("".join(words[i:j]) == target
                                    for i in range(len(words)) for j in range(i + 1, len(words) + 1))  # fmt: skip


REMEMBER_DAYS = 30  # how long an estimate that left its invoice (taken off, deleted, voided) is checked against


def _recent(store, item: dict) -> bool:
    """An estimate that left its invoice is checked against for REMEMBER_DAYS from when it left. That's counted from
    the computer's date, so a clock set back only makes more of them count. It's dated when it leaves by
    store.record_day(), which is never earlier than a date already used, so it can't be dated in the past. After
    REMEMBER_DAYS it's skipped but kept: only the owner's yes deletes it."""
    return str(item.get("date") or "") >= iso(store.today() - _dt.timedelta(days=REMEMBER_DAYS))


def _taken_off(store, inv: dict) -> list:
    """The estimates changed or taken off a draft (invoice_update), each with the day it happened. Records from
    before that was kept get the draft's own day."""
    since = (inv.get("created_at") or "")[:10] or iso(store.record_day())
    return [dict(t, date=t.get("date") or since) for t in inv.get("removed_estimates") or []]


def _estimate_items(store, lines: list) -> list:
    """Estimated lines as {"key", "amount", "figure"}: what's matched and what's named."""
    disp = store.money()
    return [{"key": _line_key(ln), "amount": _amount(ln), "figure": _estimate_figure(ln, disp)} for ln in lines]


def _draft_sources(store, inv: dict) -> list:
    """A draft invoice's estimates the owner hasn't confirmed, as (items, source id, where, exact): its marked lines,
    matched at the same figure; and estimates changed or taken off it since (removed_estimates, kept by
    invoice_update, with the day they left), matched by description at any amount (_estimate_sources)."""
    if inv.get("estimates_confirmed"):
        return []
    marked = _estimate_items(store, [ln for ln in inv["lines"] if ln.get("estimate")])
    present = [_line_key(ln) for ln in priced(inv["lines"])]
    taken = _taken_off(store, inv)
    repriced = [t for t in taken if any(_same_line(t["key"], key) for key in present)]  # still there, re-priced
    removed = [t for t in taken if t not in repriced]
    source = f"invoice:{inv['id']}:taken"
    return [(marked, f"invoice:{inv['id']}", f"on draft {inv['id']}", True),
            (repriced, source, f"changed on draft {inv['id']}", False),
            (removed, source, f"taken off draft {inv['id']}", False)]  # fmt: skip


def _customer_field(store, customer_id, field: str) -> list:
    try:
        return list(store.customer(customer_id).get(field) or [])
    except JTPError:
        return []


def _set_customer_field(store, customer_id, field: str, entries: list) -> None:
    customers = store.customers()
    for cust in customers:
        if cust["id"] == customer_id:
            if entries:
                cust[field] = entries
            else:
                cust.pop(field, None)
    store.save_customers(customers)


def _remembered(store, customer_id) -> list:
    """Estimates the owner never confirmed that left an open document: from this customer's deleted or voided drafts
    and voided change orders, and taken off invoices before they were issued."""
    return _customer_field(store, customer_id, "unconfirmed_estimates")


def _entry_id(entry: dict) -> str:
    """A remembered entry's id. Entries written before ids existed get one from their contents."""
    if entry.get("id"):
        return entry["id"]
    return "remembered:" + hashlib.sha1(json.dumps(entry, sort_keys=True).encode()).hexdigest()[:10]


def _entry_lines(entry: dict) -> list:
    """A remembered entry's estimates, as {"key", "amount", "figure"}. 1.0.1 kept only the figures' text ("Call-out: 1
    ea at $89.00 = $89.00 CAD; ..."): those are read back by description, with any amount (None)."""
    if entry.get("lines"):
        return [dict(item, key=_key_of(item["key"]), amount=item.get("amount"),
                     date=item.get("date") or entry.get("date")) for item in entry["lines"]]  # fmt: skip
    figures = [f for f in str(entry.get("figures") or "").split("; ") if f]
    return [{"key": _key_of(f.split(": ", 1)[0]), "amount": None, "figure": f, "date": entry.get("date")}
            for f in figures]  # fmt: skip


def _remembered_where(entry: dict) -> str:
    if entry.get("change_order"):
        return f"on voided change order {entry['change_order']}"
    if entry.get("removed") == "voided":
        return f"on voided draft {entry.get('invoice')}"
    if entry.get("removed") == "changed":
        return f"changed or taken off invoice {entry.get('invoice')} before it was issued"
    return "on a deleted draft"  # its number was freed for the next invoice, so it isn't named


CONFIRMED_DAYS = 30  # how long the owner's yes about a copy of an estimate covers that estimate
CONFIRMED_KEPT = 200  # the most yeses kept per customer, the newest


def _confirmations(store, customer_id) -> list:
    """The owner's yeses to lines that matched an estimate elsewhere, as {"source", "key", "amount", "date"}: the
    amount is what the matching lines on that invoice added up to. Each covers only the estimate it was about (its
    source and description), for CONFIRMED_DAYS from the computer's date when it was given. It stops counting once
    it's that much older than the latest date already used (store.record_day), so setting the clock back can't bring
    it back. It doesn't count while it's dated after the computer's date, as when given with the clock ahead."""
    since = iso(store.record_day() - _dt.timedelta(days=CONFIRMED_DAYS))
    until = iso(store.today())
    return [c for c in _customer_field(store, customer_id, "confirmed_figures")
            if c.get("source") and since <= str(c.get("date") or "") <= until]  # fmt: skip


def _confirmed_up_to(item: dict, confirmations: list):
    """How far the owner's yeses have confirmed an estimate: the largest amount they said yes to for it (or one
    carried over when it was remembered), or None. Copies up to that amount aren't asked about again."""
    amounts = [to_decimal(c["amount"]) for c in confirmations
               if c["source"] == item["source"] and _same_line(_key_of(c["key"]), item["key"])]  # fmt: skip
    if item.get("confirmed"):
        amounts.append(to_decimal(item["confirmed"]))
    return max(amounts) if amounts else None


def _cleared(item: dict, up_to) -> bool:
    """A yes reached the estimate's own figure, so it's confirmed. A smaller one doesn't clear it: after a yes to a
    $1 "Call-out", one at $89 still asks."""
    if up_to is None:
        return False
    return item.get("amount") is None or up_to >= to_decimal(item["amount"])


def _remembered_line(item: dict, up_to, today: str) -> dict:
    """An estimate kept on the customer, with the day it left and how far the owner's yeses had confirmed it."""
    line = {"key": item["key"], "amount": item["amount"], "figure": item["figure"], "date": item.get("date") or today}
    if up_to is not None:
        line["confirmed"] = format(up_to.normalize(), "f")
    return line


def _drop_confirmations(store, customer_id, sources: set) -> None:
    """The yeses about estimates whose document is gone (issued, deleted or voided): a reused number mustn't inherit
    them."""
    kept = [c for c in _customer_field(store, customer_id, "confirmed_figures") if c.get("source") not in sources]
    if len(kept) != len(_customer_field(store, customer_id, "confirmed_figures")):
        _set_customer_field(store, customer_id, "confirmed_figures", kept)


def _remember_estimates(store, doc: dict, removed: str) -> list:
    """A draft invoice or a change order is being deleted or voided with estimates the owner never confirmed.
    They're kept on the customer: if a line with the same description turns up on another invoice for them, at any
    amount and even without the mark, issuing it asks the owner (_estimates_question). Returns the warnings."""
    if doc["type"] == "invoice":
        found = _draft_sources(store, doc)
    else:
        marked = _estimate_items(store, [ln for ln in doc["lines"] if ln.get("estimate")])
        found = [(marked, f"change_order:{doc['id']}", "", True)]
    confirmations, today = _confirmations(store, doc["customer_id"]), iso(store.record_day())
    left = []
    for items, source, _, _ in found:
        for item in items:
            up_to = _confirmed_up_to(dict(item, source=source), confirmations)
            if not _cleared(item, up_to):
                left.append(_remembered_line(item, up_to, today))
    _drop_confirmations(store, doc["customer_id"], {source for _, source, _, _ in found})
    if not left:
        return []
    entry = {"id": "remembered:" + secrets.token_hex(5), "removed": removed, "date": today, "lines": left}
    if removed != "deleted":  # a deleted draft's number is freed for the next invoice, so it isn't kept
        entry[doc["type"]] = doc["id"]
    _set_customer_field(store, doc["customer_id"], "unconfirmed_estimates",
                        _remembered(store, doc["customer_id"]) + [entry])  # fmt: skip
    checked = [i for i in left if _recent(store, i)]  # the rest left a draft over REMEMBER_DAYS ago: kept, skipped
    if not checked:
        return []
    figures = "; ".join(i["figure"] for i in checked)
    which = "The deleted draft" if removed == "deleted" else doc["id"]
    return [
        f"{which} had estimates the owner never confirmed ({figures}). If these lines go on another invoice for "
        f"{customer_label(store, doc['customer_id'])}, issuing it asks the owner to confirm them."
    ]


def _estimate_sources(store, customer: str, skip: str | None = None) -> list:
    """Estimates the owner hasn't confirmed for this customer (on invoices other than `skip`), each as {"source",
    "key", "amount", "figure", "where", "exact", "up_to"}: marked lines on their draft invoices, unbilled change
    orders and accepted quotes (exact: matched at the same figure), and estimates that left an invoice in the last
    REMEMBER_DAYS (matched by description, at any amount over what the owner's yeses have confirmed of it): changed
    or taken off a draft, or remembered from a deleted, voided or issued one."""
    confirmations = _confirmations(store, customer)
    found = [(_entry_lines(entry), _entry_id(entry), _remembered_where(entry), False)
             for entry in _remembered(store, customer)]  # fmt: skip
    for other in store.docs("invoice"):
        if other["customer_id"] == customer and other["status"] == "draft" and other["id"] != skip:
            found += _draft_sources(store, other)
    for co in store.docs("change_order"):
        unbilled = co["status"] in ("draft", "sent", "approved") and not co.get("invoice")
        if co.get("customer_id") == customer and unbilled:
            marked = _estimate_items(store, [ln for ln in co["lines"] if ln.get("estimate")])
            found.append((marked, f"change_order:{co['id']}", f"on change order {co['id']}", True))
    for quote in store.docs("quote"):
        if quote.get("customer_id") == customer and quote["status"] == "accepted":
            chosen = (quote.get("accepted") or {}).get("option")
            for option in quote["options"]:
                if option["key"] == chosen:
                    marked = _estimate_items(store, [ln for ln in option["lines"] if ln.get("estimate")])
                    found.append((marked, f"quote:{quote['id']}", f"on quote {quote['id']}", True))
    sources = []
    for items, source, where, exact in found:
        for item in items:
            if not exact and not _recent(store, item):
                continue
            up_to = _confirmed_up_to(dict(item, source=source), confirmations)
            if not _cleared(item, up_to):
                sources.append(dict(item, source=source, where=where, exact=exact, up_to=up_to))
    return sources


def _matches(store, inv: dict, lines: list) -> list:
    """[(line, [estimate sources])] for the lines that match an estimate elsewhere (_estimate_sources)."""
    sources = _estimate_sources(store, inv["customer_id"], skip=inv["id"])
    found = []
    for ln in lines:
        key, amount = _line_key(ln), _amount(ln)
        if to_decimal(amount) <= 0:
            continue  # nothing billed ("Call-out (waived)"): it can't be a copy of an estimate's figure
        hits = [s for s in sources if _matches_source(key, amount, s)]
        if hits:
            found.append((ln, hits))
    return found


def _matches_source(key: str, amount: str, source: dict) -> bool:
    """An estimate still on its document needs the same figure. One that left its invoice needs a related
    description, and an amount over what the owner's yeses have confirmed of it (_confirmed_up_to)."""
    if source["exact"]:
        return _same_line(key, source["key"]) and amount == source["amount"]
    up_to = source.get("up_to")
    return _related_line(key, source["key"]) and (up_to is None or to_decimal(amount) > up_to)


def _matches_text(matches: list) -> str:
    """Each matched estimate, with its figure and where it is ("Call-out: 1 ea at $89.00 = $89.00 CAD, on a deleted
    draft")."""
    parts = []
    for _, hits in matches:
        for s in hits:
            text = f"{s['figure']}, {s['where']}"
            if text not in parts:
                parts.append(text)
    return "; ".join(parts)


def _copied_warnings(store, inv: dict) -> list:
    copied = [(ln, hits) for ln, hits in _matches(store, inv, priced(inv["lines"])) if not ln.get("estimate")]
    if not copied:
        return []
    names = ", ".join(ln.get("description") or ln["id"] for ln, _ in copied)
    return [
        f"{names}: matches an estimate for {customer_label(store, inv['customer_id'])} that the owner hasn't "
        f"confirmed ({_matches_text(copied)}), so issuing this invoice asks the owner to confirm it."
    ]


def _estimates_question(store, inv: dict) -> tuple:
    """(the lines to confirm, the question, the estimates taken off it, the matches) before an invoice is issued,
    or ([], None, [], []).

    Its own estimates are named as estimates (part of a quote's estimate on a progress, deposit or balance
    invoice). Estimates changed or taken off it since are named as such, with its figures. A line that matches an
    estimate the owner hasn't confirmed elsewhere for this customer is named too, with where that estimate is."""
    own = _estimate_lines(inv)
    marked = [ln for ln in inv["lines"] if ln.get("estimate")]
    taken_off = list(inv.get("removed_estimates") or [])
    matches = _matches(store, inv, priced(inv["lines"]))
    copied = [(ln, hits) for ln, hits in matches if not ln.get("estimate")]
    ask = {ln["id"] for ln in own} | {ln["id"] for ln, _ in copied}
    lines = [ln for ln in inv["lines"] if ln["id"] in ask] or (priced(inv["lines"]) if taken_off else [])
    if not lines:
        return [], None, [], []
    figures = "; ".join(_estimate_figure(ln, store.money()) for ln in lines)
    name = customer_label(store, inv["customer_id"])
    if copied:
        where = _matches_text(copied)
        main = (f"these figures were estimates, and some lines match estimates for {name} that you haven't confirmed "
                f"({where}). Are they final?") if marked else (
            f"these lines match estimates for {name} that you haven't confirmed ({where}). Are these figures final?")
    elif not marked:
        main = "Are these figures final?" if taken_off else (
            "it had estimated figures, since changed or removed. Are these figures final?")  # fmt: skip
    elif _part_of_quote(inv) and all((ln.get("source") or {}).get("quote_line") for ln in lines):
        main = "these figures are based on estimates on the quote. Are they final?"
    else:
        main = "these figures were estimates. Are they final?"
    if taken_off:
        named = "; ".join(t["figure"] for t in taken_off)
        was = "was an estimate" if len(taken_off) == 1 else "were estimates"
        main = f"{named} {was}, since changed or taken off it. {main[0].upper()}{main[1:]}"
    return lines, f"Before I finalize invoice {inv['id']}: {main} {figures}", taken_off, matches


def _note_confirmed(store, customer_id, matches: list) -> None:
    """The owner said yes to these lines: each estimate they matched is confirmed up to what those lines add up to
    (_confirmed_up_to)."""
    totals = {}
    for ln, hits in matches:
        for s in hits:
            totals[s["source"], s["key"]] = totals.get((s["source"], s["key"]), ZERO) + to_decimal(_amount(ln))
    today = iso(store.today())  # a yes is never dated later than the computer's date: it can't count longer
    added = [{"source": source, "key": key, "amount": format(total.normalize(), "f"), "date": today}
             for (source, key), total in totals.items()]  # fmt: skip
    if added:
        kept = _confirmations(store, customer_id)
        _set_customer_field(store, customer_id, "confirmed_figures", (kept + added)[-CONFIRMED_KEPT:])


def _prune_remembered(store, customer_id) -> None:
    """Remembered estimates the owner's yeses have cleared (_cleared) are forgotten, with the yeses about them. Ones
    over REMEMBER_DAYS old are kept, only skipped (_recent): the date mustn't be able to delete them."""
    confirmations = _confirmations(store, customer_id)
    before, after, gone = _remembered(store, customer_id), [], set()
    for entry in before:
        ident = _entry_id(entry)
        left = [i for i in _entry_lines(entry)
                if not _cleared(i, _confirmed_up_to(dict(i, source=ident), confirmations))]  # fmt: skip
        if left:
            after.append(dict({k: v for k, v in entry.items() if k != "figures"}, id=ident, lines=left))
        else:
            gone.add(ident)
    if after != before:
        _set_customer_field(store, customer_id, "unconfirmed_estimates", after)
    if gone:
        _drop_confirmations(store, customer_id, gone)


# What may follow the owner's plain yes to "Forget these estimates ...?" ("yes, forget them"). They're not a yes on
# their own. No "stop": an answer with "stop" in it reads as a no.
FORGET_WORDS = ("forget them", "forget these", "forget", "forget it", "clear them")


def forget_estimates(store, customer_id: str, owner_confirmed=None, confirm_code=None) -> dict:
    """Stop checking a customer's invoices against estimates that left their invoices (changed or taken off a draft,
    deleted or voided), on the owner's plain yes: once they know those figures won't be billed, the questions stop."""
    cust = store.customer(customer_id)
    name = customer_label(store, cust["id"])
    drafts = [d for d in store.docs("invoice")
              if d["customer_id"] == cust["id"] and d["status"] == "draft" and d.get("removed_estimates")]  # fmt: skip
    listed = [f"{s['figure']} ({s['where']})" for s in _estimate_sources(store, cust["id"]) if not s["exact"]]
    if not listed:
        return {"customer": cust["id"], "forgotten": [],
                "note": f"Nothing to forget: no estimates that left {name}'s invoices are being checked."}  # fmt: skip
    question = (f"Forget these estimates you never confirmed for {name}: {'; '.join(listed)}? Copies of them won't be "
                "asked about any more.")  # fmt: skip
    said = confirm.owner_yes(
        store.home, f"customer.forget_estimates|{cust['id']}|{'; '.join(listed)}", question, owner_confirmed,
        confirm_code,
        why=f"Forgetting {name}'s unconfirmed estimates means copies of them won't be asked about, so only the owner "
        "can approve it: ask, and wait for their answer.",
        echo=FORGET_WORDS,
    )
    _set_customer_field(store, cust["id"], "unconfirmed_estimates", [])
    for draft in drafts:
        draft.pop("removed_estimates", None)
        store.save_doc(draft)
    store.log("customer.forget_estimates", cust["id"], owner_said=said, forgotten=listed)
    confirm.use(store.home, confirm_code)  # the owner's yes counts once
    return {"customer": cust["id"], "forgotten": listed, "owner_said": said}


def _remember_changed(store, inv: dict, taken_off: list) -> None:
    """The owner confirmed an invoice that had estimates changed or taken off it. An estimate re-priced on it is
    confirmed up to what its lines now add up to ($89 re-priced to $99 is cleared; to $1, only up to $1), and a yes
    to a copy elsewhere counts too. What isn't cleared is kept on the customer, so a copy over that still asks."""
    confirmations, today = _confirmations(store, inv["customer_id"]), iso(store.record_day())
    left = []
    for t in _taken_off(store, dict(inv, removed_estimates=taken_off)):
        here = sum((to_decimal(_amount(ln)) for ln in priced(inv["lines"]) if _same_line(t["key"], _line_key(ln))),
                   ZERO)  # fmt: skip
        elsewhere = _confirmed_up_to(dict(t, source=f"invoice:{inv['id']}:taken"), confirmations)
        up_to = max((a for a in (here if here > 0 else None, elsewhere) if a is not None), default=None)
        if not _cleared(t, up_to):
            left.append(_remembered_line(t, up_to, today))
    if left:
        _set_customer_field(store, inv["customer_id"], "unconfirmed_estimates", _remembered(store, inv["customer_id"])
                            + [{"id": "remembered:" + secrets.token_hex(5), "invoice": inv["id"], "removed": "changed",
                                "date": today, "lines": left}])  # fmt: skip


def _part_of_quote(inv: dict) -> bool:
    """A progress, deposit or balance invoice: its quote lines bill part of the quote's figures."""
    source = inv.get("source") or {}
    return inv.get("kind") in ("progress", "deposit") or bool(source.get("previously_billed_percent"))


def _estimate_figure(ln: dict, disp: dict) -> str:
    amount = format_total(to_decimal(ln["amount"]), disp)
    if not ln.get("display_qty", True):
        return f"{ln.get('description') or ln['id']}: {amount}"
    qty = f"{format_qty(to_decimal(ln['qty']), disp)} {ln.get('unit') or ''}".strip()
    price = format_money(to_decimal(ln["unit_price"]), disp, extra_places=2)
    return f"{ln.get('description') or ln['id']}: {qty} at {price} = {amount}"


# What a plain yes to "these figures were estimates: are they final?" may add ("yes, they're final"). Not "right"
# on its own: "yeah right" isn't a yes.
ESTIMATES_FINAL_WORDS = ("final", "they're final", "they are final", "that's right", "they're right", "issue it")


def invoice_issue(
    store, ident: str, date=None, due_days=None, due_date=None, allow_unconfirmed_tax=False, owner_confirmed=None,
    confirm_code=None, dry_run=False,
) -> dict:
    inv = store.doc(ident, "invoice")
    if inv["status"] != "draft":
        if inv["status"] in ("issued", "paid"):
            if owner_confirmed and confirm.reads_as_no(owner_confirmed):  # never answer a no with "issued"
                raise JTPError(
                    f"{inv['id']} was already issued on {_shown_date(store, inv['date'])}, so the owner's no can't "
                    "stop it now. Nothing was changed.",
                    hint=f"To cancel it, void it (jtp invoice void {inv['id']}, which asks the owner first), or "
                    "correct it with a credit note.",
                    code="already_issued",
                )
            return {"invoice": inv, "already_issued": True}
        raise JTPError(f"{inv['id']} is {inv['status']}.")
    profile = store.profile()
    if not profile["tax"].get("confirmed") and not allow_unconfirmed_tax:
        raise JTPError(
            "Tax setup isn't confirmed, so this invoice could show the wrong tax.",
            hint="Confirm the tax setup with the owner first: jtp profile set tax.confirmed true",
            code="setup_needed",
        )
    if not profile["payment"].get("methods"):
        raise JTPError(
            "No payment methods are set, so the invoice wouldn't say how to pay.",
            hint='jtp profile set payment.methods \'[{"name":"Bank transfer","instructions":"..."}]\'',
            code="setup_needed",
        )
    zone = profile["locale"].get("timezone")
    if zone and zone_name(zone) is None:  # saved by hand: profile set refuses it
        raise JTPError(
            f"The saved time zone {zone!r} isn't one, so the invoice could get the computer's date instead of yours.",
            hint="Ask the owner where the business is, then: jtp profile set locale.timezone America/Vancouver (or "
            "their own, e.g. Europe/London)",
            code="setup_needed",
        )
    estimated = [ln for ln in inv["lines"] if ln.get("estimate")]
    # Estimates need the owner's yes, even if the marks were taken off since or the lines came from a draft that
    # was deleted: only that yes clears them (_estimates_question).
    asked_about, question, taken_off, matches = _estimates_question(store, inv)
    said = ""
    if asked_about:
        # Only the owner's plain yes to this exact question counts, sent back with its one-time code. The code
        # covers these figures: if one changes after the owner was asked, they're asked again (confirm.py).
        if not dry_run:  # a preview asks nothing and uses no code; the real run asks
            said = confirm.owner_yes(
                store.home, f"invoice.issue|{inv['id']}|{question}", question, owner_confirmed, confirm_code,
                why=f"{inv['id']} has estimated figures. Issuing makes the invoice final, so only the owner can "
                "confirm them: ask, and wait for their answer.",
                hint="Ask the owner the question in ask_owner, word for word. Only if they say yes: jtp invoice issue "
                f'{inv["id"]} --owner-confirmed "<their exact words>" --confirm <the code in confirm> (their words '
                f"are kept on the invoice). If a figure changed: jtp invoice show {inv['id']} --as-spec > inv.json, "
                f"fix the figure, then jtp invoice update {inv['id']} --spec inv.json. It stays marked as an "
                "estimate until the owner confirms it here.",
                ask_code="estimates_unconfirmed",
                echo=ESTIMATES_FINAL_WORDS,
            )
    day = parse_date(date or "today", store.today())
    if asked_about and not dry_run:
        inv["estimates_confirmed"] = {
            "date": iso(day),
            "owner_said": said,
            "lines": [
                {"id": ln["id"], "description": ln.get("description", ""), "amount": ln["amount"]}
                for ln in asked_about
            ],
        }
        if taken_off:
            inv["estimates_confirmed"]["taken_off"] = taken_off
        inv.pop("removed_estimates", None)
        store.log("invoice.estimates_confirmed", inv["id"], owner_said=said)
    for ln in estimated:
        ln.pop("estimate", None)  # confirmed by the owner: the issued invoice shows firm figures
    if due_date:
        due = parse_date(due_date, store.today())
    else:
        days = profile["terms"].get("payment_due_days", 14) if due_days is None else due_days
        due = day + _dt.timedelta(days=int(days))
    if due < day:
        raise JTPError("The due date can't be before the invoice date.")
    inv["date"] = iso(day)
    inv["due_date"] = iso(due)
    inv["status"] = "issued"
    job = store.job(inv["job_id"])
    applied = []
    balance = invoice_state(inv, day)["balance"]
    for payment in job.get("payments", []):
        remaining = to_decimal(payment.get("remaining", payment["amount"]))
        if remaining <= 0 or balance <= 0:
            continue
        use = min(remaining, balance)
        inv["payments"].append(
            {
                "id": payment["id"],
                "date": payment["date"],
                "amount": str(use),
                "method": payment.get("method", ""),
                "reference": payment.get("reference", ""),
                "from_deposit": True,
                "recorded_at": now_stamp(),
            }
        )
        payment.setdefault("applied", []).append({"invoice": inv["id"], "amount": str(use)})
        payment["remaining"] = str(remaining - use)
        balance -= use
        applied.append({"payment": payment["id"], "amount": str(use)})
    _refresh_paid(store, inv)
    if dry_run:  # nothing saved, no code used: show what issuing would do
        preview = dict(inv, id=PREVIEW_ID)
        return {"invoice": preview, "dry_run": True, "would_issue": inv["id"], "deposits_applied": applied,
                **({"owner_question": question} if question else {})}  # fmt: skip
    store.save_doc(inv)
    if said:  # the owner confirmed these figures: they cover the estimates they matched elsewhere
        _note_confirmed(store, inv["customer_id"], matches)
    _prune_remembered(store, inv["customer_id"])  # the ones the owner's yeses have cleared
    if said:
        _remember_changed(store, inv, taken_off)
        _drop_confirmations(store, inv["customer_id"], {f"invoice:{inv['id']}", f"invoice:{inv['id']}:taken"})
    if inv.get("kind") in ("final", "tm", "standard") and job["status"] not in ("paid", "closed"):
        job["status"] = "invoiced"
    _update_job_after_money(store, job)
    store.save_job(job)
    store.log("invoice.issue", inv["id"], date=inv["date"], due=inv["due_date"])
    if said:
        confirm.use(store.home, confirm_code)  # the owner's yes counts once
    return {"invoice": inv, "deposits_applied": applied}


def invoice_mark_sent(store, ident: str, channel, to, date=None) -> dict:
    inv = store.doc(ident, "invoice")
    if inv["status"] == "draft":
        raise JTPError(f"{inv['id']} is still a draft.", hint=f"Issue it first: jtp invoice issue {inv['id']}")
    if inv["status"] == "void":
        raise JTPError(f"{inv['id']} is void.")
    added = _record_send(inv, _channel(channel), to, parse_date(date or "today", store.today()))
    store.save_doc(inv)
    if added:
        store.log("invoice.sent", inv["id"], channel=channel, to=to)
    return {"invoice": inv, "already_recorded": not added}


def invoice_set(store, ident: str, payment_link=None, external=None, notes=None) -> dict:
    inv = store.doc(ident, "invoice")
    if payment_link is not None:
        if payment_link and not payment_link.startswith(("https://", "http://")):
            raise JTPError("The payment link must start with https://")
        inv["payment_link"] = payment_link
    for key, value in (external or {}).items():
        inv.setdefault("external", {})[key] = value
    if notes is not None and inv["status"] == "draft":
        inv["notes"] = notes
    store.save_doc(inv)
    store.log("invoice.set", inv["id"])
    return {"invoice": inv}


def invoice_delete(store, ident: str) -> dict:
    inv = store.doc(ident, "invoice")
    if inv["status"] != "draft":
        raise JTPError(
            f"{inv['id']} is {inv['status']}; only drafts can be deleted.",
            hint=f"Void it instead: jtp invoice void {inv['id']} --reason '...'",
        )
    if not store.release_last_id("invoice", inv["id"]):
        raise JTPError(
            f"{inv['id']} isn't the newest invoice number, so deleting it would leave a gap.",
            hint=f"Void it instead (keeps the number on record): jtp invoice void {inv['id']}",
        )
    job = store.job(inv["job_id"])
    warnings = _remember_estimates(store, inv, "deleted")
    _link(store, job, inv, attach=False)
    job["invoices"] = [i for i in job["invoices"] if i != inv["id"]]
    store.save_job(job)
    store.delete_doc(inv)
    store.log("invoice.delete", inv["id"])
    removed_job = _drop_empty_auto_job(store, job)
    return {"deleted": inv["id"], "removed_empty_job": job["id"] if removed_job else None, "warnings": warnings}


# What a plain yes to "Void invoice ...?" may add ("yes, void it").
VOID_WORDS = ("void it", "void", "void the invoice", "void invoice")


def invoice_void(store, ident: str, reason: str | None, owner_confirmed=None, confirm_code=None) -> dict:
    inv = store.doc(ident, "invoice")
    if inv["status"] == "void":
        if owner_confirmed and confirm.reads_as_no(owner_confirmed):  # never answer a no with "void"
            raise JTPError(f"{inv['id']} was already void, so the owner's no can't change that. Nothing was changed.",
                           code="already_void")  # fmt: skip
        return {"invoice": inv, "already_void": True}
    credit = None
    if inv["status"] in ("issued", "paid"):
        if inv.get("payments"):
            raise JTPError(
                f"{inv['id']} has payments recorded.",
                hint="Move or undo them first (jtp payment undo <PAY-id>), or issue a partial credit "
                "note instead of voiding.",
            )
        state = invoice_state(inv, store.today())
        if state["balance"] > 0:
            credit, _ = _credit_note(store, inv, amount=str(state["balance"]),
                                     reason=reason or f"Cancels invoice {inv['id']}")  # fmt: skip
    # Voiding can't be undone, so it waits for the owner's plain yes to this question, sent back with its code.
    disp = store.money()
    what = "draft invoice" if inv["status"] == "draft" else "invoice"
    question = (f"Void {what} {inv['id']} for {customer_label(store, inv['customer_id'])} "
                f"({format_total(to_decimal(inv['totals']['total']), disp)})?")  # fmt: skip
    if credit:
        question += f" A credit note for {format_total(to_decimal(credit['totals']['total']), disp)} goes with it."
    if reason:
        question += f" Reason: {reason.strip().rstrip('.')}."
    question += " This can't be undone."
    credited = credit["totals"]["total"] if credit else ""
    said = confirm.owner_yes(
        store.home, f"invoice.void|{inv['id']}|{inv['totals']['total']}|{credited}|{reason or ''}", question,
        owner_confirmed, confirm_code,
        why=f"Voiding {inv['id']} can't be undone, so only the owner can approve it: ask, and wait for their answer.",
        echo=VOID_WORDS + (inv["id"],),
    )
    if credit:
        _save_credit(store, inv, credit, said, on_job=False)
    warnings = _remember_estimates(store, inv, "voided") if inv["status"] == "draft" else []
    job = store.job(inv["job_id"])
    inv["status"] = "void"
    inv["void_reason"] = reason or ""
    inv["void_approved"] = {"date": iso(store.today()), "owner_said": said}
    _link(store, job, inv, attach=False)
    store.save_doc(inv)
    if job["status"] in ("invoiced", "paid"):
        job["status"] = "complete"
    store.save_job(job)
    store.log("invoice.void", inv["id"], reason=reason, owner_said=said)
    confirm.use(store.home, confirm_code)  # the owner's yes counts once
    return {
        "invoice": inv,
        "credit_note": credit,
        "note": "Any time, materials or change orders on it are free to bill again.",
        "warnings": warnings,
    }


# =============================================================== credit notes


def _credit_lines_for_amount(store, inv: dict, amount: Decimal, reason: str) -> list:
    profile = store.profile()
    places = store.places
    groups = OrderedDict()
    for ln in priced(inv["lines"]):
        key = tuple(ln.get("tax_codes") or [])
        groups[key] = groups.get(key, ZERO) + to_decimal(ln.get("net", ln["amount"]))
    groups = OrderedDict((k, v) for k, v in groups.items() if v != 0)
    if not groups:
        raise JTPError("That invoice has nothing to credit.")
    inclusive = profile["tax"].get("prices_include_tax")
    gross = OrderedDict()
    for key, net in groups.items():
        synthetic = [{"kind": "other", "qty": "1", "unit_price": str(net), "amount": str(net), "tax_codes": list(key)}]
        gross[key] = to_decimal(compute_totals(synthetic, profile["tax"], places)["total"])
    shares = allocate(amount, list(gross.values()), places)
    names = {t["code"]: t.get("name") or t["code"] for t in profile["tax"].get("taxes") or []}
    specs = []
    for (key, net), share, g in zip(groups.items(), shares, gross.values()):
        if share == 0:
            continue
        unit = share if inclusive or g == 0 else q(share * net / g, places)
        description = reason
        if len(groups) > 1:
            taxed = " + ".join(names.get(c, c) for c in key) or "no tax"
            description = f"{reason} ({taxed})"
        specs.append(
            {
                "kind": "other",
                "description": description,
                "qty": "1",
                "unit": "",
                "unit_price": dec_str(unit),
                "tax_codes": list(key),
                "display_qty": False,
            }
        )
    # Nudge for rounding so the credit lands on exactly the requested amount.
    for _ in range(4):
        lines, totals, _, _ = build_lines(store, specs, prefix="N")
        diff = amount - to_decimal(totals["total"])
        if diff == 0:
            break
        biggest = max(range(len(specs)), key=lambda i: to_decimal(specs[i]["unit_price"]))
        key = tuple(specs[biggest]["tax_codes"])
        ratio = (groups[key] / gross[key]) if (not inclusive and gross[key]) else Decimal(1)
        step = q(diff * ratio, places)
        if step == 0:
            step = diff
        specs[biggest]["unit_price"] = dec_str(to_decimal(specs[biggest]["unit_price"]) + step)
    return specs


# What a plain yes to "Issue a credit note ...?" may add ("yes, issue it").
CREDIT_WORDS = ("issue it", "issue the credit note", "credit it", "credit note")


def credit_create(
    store, invoice_id: str, spec=None, amount=None, reason=None, dry_run=False, owner_confirmed=None, confirm_code=None
) -> dict:
    inv = store.doc(invoice_id, "invoice")
    doc, warnings = _credit_note(store, inv, spec, amount, reason)
    disp = store.money()
    question = (f"Issue a credit note for {format_total(to_decimal(doc['totals']['total']), disp)} on invoice "
                f"{inv['id']} for {customer_label(store, inv['customer_id'])}? Reason: {doc['reason'].rstrip('.')}. "
                "This can't be undone.")  # fmt: skip
    refund = to_decimal(doc["refund_due"])
    if refund > 0:
        question += f" They've already paid, so you'd owe them a refund of {format_total(refund, disp)}."
    if dry_run:  # a preview asks nothing and uses no code
        return _credit_out(doc, warnings, disp, owner_question=question)
    said = confirm.owner_yes(
        store.home, f"credit.create|{inv['id']}|{doc['totals']['total']}|{doc['reason']}", question, owner_confirmed,
        confirm_code,
        why=f"A credit note on {inv['id']} can't be undone, so only the owner can approve it: ask, and wait for their "
        "answer.",
        echo=CREDIT_WORDS,
    )
    _save_credit(store, inv, doc, said)
    confirm.use(store.home, confirm_code)  # the owner's yes counts once
    return _credit_out(doc, warnings, disp)


def _credit_note(store, inv: dict, spec=None, amount=None, reason=None) -> tuple:
    """The credit note a credit would make, and its warnings: checked, but not numbered or saved yet."""
    if inv["status"] not in ("issued", "paid"):
        raise JTPError(
            f"{inv['id']} is {inv['status']}.",
            hint="Drafts can just be edited or voided; credit notes are for issued invoices.",
        )
    places = store.places
    labels = profile_mod.labels(store.profile())
    reason = (reason or (spec or {}).get("reason") or f"{labels['credit_for']} {inv['id']}").strip()
    if spec and spec.get("lines"):
        specs = spec["lines"]
    elif amount is not None:
        value = q(to_decimal(amount, "amount"), places)
        if value <= 0:
            raise JTPError("A credit amount must be more than zero.")
        specs = _credit_lines_for_amount(store, inv, value, reason)
    else:
        raise JTPError(
            "Say how much to credit (--amount) or which lines (--spec).",
            hint=f"jtp credit create --invoice {inv['id']} --amount 50 --reason 'Goodwill'",
        )
    lines, totals, warnings, _ = build_lines(store, specs, prefix="N")
    state = invoice_state(inv, store.today())
    credit_total = to_decimal(totals["total"])
    already = state["credits"]
    if credit_total > state["total"] - already:
        raise JTPError(
            f"Credit {credit_total} is more than the invoice total left to credit ({state['total'] - already})."
        )
    refund_due = max(ZERO, credit_total - max(state["balance"], ZERO))
    doc = {
        "id": PREVIEW_ID,  # numbered when saved (_save_credit)
        "type": "credit_note",
        "status": "issued",
        "invoice_id": inv["id"],
        "job_id": inv["job_id"],
        "customer_id": inv["customer_id"],
        "title": reason,
        "reason": reason,
        "date": iso(store.today()),
        "lines": lines,
        "totals": totals,
        "refund_due": str(q(refund_due, places)),
        "created_at": now_stamp(),
    }
    return doc, warnings


def _save_credit(store, inv: dict, doc: dict, said: str, on_job: bool = True) -> None:
    """Number and save a credit note the owner approved, and take it off the invoice. A void's credit note isn't
    listed on the job: the void already takes the invoice out of the job's sales (reports.job_summary)."""
    doc["id"] = store.next_id("credit_note")
    doc["owner_approved"] = {"date": doc["date"], "owner_said": said}
    store.save_doc(doc)
    inv["credits"].append({"credit_note": doc["id"], "amount": doc["totals"]["total"], "date": doc["date"]})
    _refresh_paid(store, inv)
    store.save_doc(inv)
    job = store.job(inv["job_id"])
    if on_job:
        job.setdefault("credit_notes", []).append(doc["id"])
    _update_job_after_money(store, job)
    store.save_job(job)
    store.log("credit.create", doc["id"], invoice=inv["id"], total=doc["totals"]["total"], owner_said=said)


def _credit_out(doc: dict, warnings: list, disp: dict, **extra) -> dict:
    out = {"credit_note": doc, "warnings": warnings, **extra}
    refund = to_decimal(doc["refund_due"])
    if refund > 0:
        out["note"] = (
            f"The customer had already paid, so you owe them a refund of {format_total(refund, disp)}. "
            "Refund it the way they paid and keep this credit note with your records."
        )
    return out


def credit_mark_sent(store, ident: str, channel, to, date=None) -> dict:
    doc = store.doc(ident, "credit_note")
    added = _record_send(doc, _channel(channel), to, parse_date(date or "today", store.today()))
    store.save_doc(doc)
    if added:
        store.log("credit.sent", doc["id"], channel=channel, to=to)
    return {"credit_note": doc, "already_recorded": not added}


# =================================================================== payments


def _find_duplicate(payments: list, amount: Decimal, day: str, method: str, reference: str) -> dict | None:
    for p in payments:
        if to_decimal(p["amount"]) != amount:
            continue
        if reference and p.get("reference") == reference:
            return p
        if not reference and p.get("date") == day and (p.get("method") or "") == method:
            try:
                recorded = _dt.datetime.fromisoformat(p.get("recorded_at", ""))
                if (_dt.datetime.now(_dt.timezone.utc) - recorded).total_seconds() < 600:
                    return p
            except ValueError:
                continue
    return None


def payment_record(
    store,
    invoice_id=None,
    job_id=None,
    amount=None,
    method=None,
    date=None,
    reference=None,
    note=None,
    deposit=False,
    allow_duplicate=False,
) -> dict:
    places = store.places
    value = q(to_decimal(amount, "amount"), places)
    if value <= 0:
        raise JTPError("A payment must be more than zero.")
    day = iso(parse_date(date or "today", store.today()))
    method, method_note = _payment_method(store, method)
    reference = (reference or "").strip()
    if invoice_id:
        inv = store.doc(invoice_id, "invoice")
        if inv["status"] == "draft":
            raise JTPError(
                f"{inv['id']} is still a draft.",
                hint=f"Issue it first (jtp invoice issue {inv['id']}), or record a deposit on the job "
                f"with: jtp payment record --job {inv['job_id']} --deposit --amount ...",
            )
        if inv["status"] == "void":
            raise JTPError(f"{inv['id']} is void.")
        state = invoice_state(inv, store.today())
        dup = None if allow_duplicate else _find_duplicate(inv["payments"], value, day, method, reference)
        if dup:
            return {
                "payment": dup,
                "invoice": inv,
                "duplicate": True,
                "note": "This looks like a payment that was just recorded, so it wasn't added twice. "
                "Pass --allow-duplicate if it really is a second payment.",
            }
        if value > state["balance"]:
            raise JTPError(
                f"{value} is more than the balance due on {inv['id']} ({state['balance']}).",
                hint="Record the balance due, and note any overpayment separately.",
            )
        payment = {
            "id": store.next_id("payment"),
            "date": day,
            "amount": str(value),
            "method": method,
            "reference": reference,
            "note": note or "",
            "recorded_at": now_stamp(),
        }
        inv["payments"].append(payment)
        _refresh_paid(store, inv)
        store.save_doc(inv)
        job = store.job(inv["job_id"])
        _update_job_after_money(store, job)
        store.save_job(job)
        store.log("payment.record", payment["id"], invoice=inv["id"], amount=payment["amount"], method=method)
        out = {"payment": payment, "invoice": inv, "balance": str(invoice_state(inv, store.today())["balance"])}
        if method_note:
            out["warnings"] = [method_note]
        return out
    if job_id:
        job = store.job(job_id)
        dup = None if allow_duplicate else _find_duplicate(job.get("payments", []), value, day, method, reference)
        if dup:
            return {"payment": dup, "job": job["id"], "duplicate": True}
        payment = {
            "id": store.next_id("payment"),
            "date": day,
            "amount": str(value),
            "method": method,
            "reference": reference,
            "note": note or "",
            "kind": "deposit" if deposit else "prepayment",
            "remaining": str(value),
            "applied": [],
            "recorded_at": now_stamp(),
        }
        job.setdefault("payments", []).append(payment)
        store.save_job(job)
        store.log("payment.deposit", payment["id"], job=job["id"], amount=payment["amount"], method=method)
        return {
            "payment": payment,
            "job": job["id"],
            "note": "Held on the job; it comes off the next invoice automatically when you issue it.",
            "warnings": [method_note] if method_note else [],
        }
    raise JTPError("Say which invoice (INV-...) or which job (--job) the payment is for.")


def _payment_method(store, method: str | None) -> tuple:
    """(method, note): the owner's own name for a method when it matches one of theirs, else a heads-up."""
    method = (method or "").strip()
    known = [m.get("name", "").strip() for m in store.profile()["payment"].get("methods") or [] if m.get("name")]
    if not method or not known:
        return method, None
    lower = method.lower()
    for name in known:
        if lower == name.lower() or lower in name.lower() or name.lower() in lower:
            return name, None
    return method, (
        f"Recorded as '{method}', which isn't one of the payment methods on your documents ({', '.join(known)}). "
        "Check with the owner that it's right."
    )


def payment_undo(store, payment_id: str) -> dict:
    target = payment_id.upper()
    touched = []
    for inv in store.docs("invoice"):
        before = len(inv.get("payments", []))
        inv["payments"] = [p for p in inv.get("payments", []) if p["id"].upper() != target]
        if len(inv["payments"]) != before:
            if inv["status"] == "paid":
                inv["status"] = "issued"
            _refresh_paid(store, inv)
            store.save_doc(inv)
            touched.append(inv["id"])
    for job in store.jobs():
        payments = job.get("payments", [])
        keep = [p for p in payments if p["id"].upper() != target]
        changed = len(keep) != len(payments)
        if changed:
            job["payments"] = keep
        if changed or any(i in job.get("invoices", []) for i in touched):
            _update_job_after_money(store, job)
            store.save_job(job)
            touched.append(job["id"])
    if not touched:
        raise JTPError(f"No payment {payment_id}.")
    store.log("payment.undo", payment_id, touched=touched)
    return {"removed": payment_id, "updated": touched}


def payment_list(store, start=None, end=None) -> list:
    out = []
    for inv in store.docs("invoice"):
        for p in inv.get("payments", []):
            if p.get("from_deposit"):
                continue
            out.append({**p, "invoice": inv["id"], "customer_id": inv["customer_id"], "job_id": inv["job_id"]})
    for job in store.jobs():
        for p in job.get("payments", []):
            out.append({**p, "invoice": None, "customer_id": job["customer_id"], "job_id": job["id"]})
    if start:
        out = [p for p in out if p["date"] >= start]
    if end:
        out = [p for p in out if p["date"] <= end]
    return sorted(out, key=lambda p: (p["date"], p["id"]))


def find_payment(store, payment_id: str) -> tuple:
    """(payment, invoice_or_None, job) for rendering receipts."""
    target = payment_id.upper()
    for inv in store.docs("invoice"):
        for p in inv.get("payments", []):
            if p["id"].upper() == target and not p.get("from_deposit"):
                return p, inv, store.job(inv["job_id"])
    for job in store.jobs():
        for p in job.get("payments", []):
            if p["id"].upper() == target:
                return p, None, job
    raise JTPError(f"No payment {payment_id}.")


def amount_str(value) -> str:
    return dec_str(to_decimal(value))
