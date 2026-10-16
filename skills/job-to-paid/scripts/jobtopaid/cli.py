"""Command line for Job to Paid. Every command prints JSON: {"ok": true, ...}
or {"ok": false, "error": "...", "hint": "..."} with exit code 1.

Run `jtp <command> --help` for examples. Nothing here talks to the network.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import shutil
import sys
import zipfile
from pathlib import Path

from . import __version__
from . import documents as docs
from . import followups, records, reports
from . import profile as profile_mod
from .demo import seed, selftest
from .pricing import lines_as_spec
from .render import find_browser, render_doc, render_receipt
from .store import Store, default_home
from .util import JTPError, from_iso, iso, read_json, to_decimal, write_json

EPILOG_HELP = (
    "Data folder: --home, else $JTP_HOME, else /workspace/job-to-paid (if /workspace exists), else ~/job-to-paid."
)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        _emit({"ok": False, "error": message, "hint": f"Run: {self.prog} --help", "code": "usage"}, 2)


def _emit(payload: dict, code: int = 0):
    sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n")
    sys.exit(code)


def _spec(value: str | None):
    if value is None:
        return None
    text = value.strip()
    if text == "-":
        text = sys.stdin.read()
    elif not text.startswith(("{", "[")):
        path = Path(text).expanduser()
        if not path.is_file():
            raise JTPError(f"Spec file {value} not found.", hint="Pass a JSON file path, - for stdin, or inline JSON.")
        text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise JTPError(f"The spec isn't valid JSON ({exc.msg} at line {exc.lineno}, column {exc.colno}).") from exc
    if isinstance(data, list):
        data = {"lines": data}
    if not isinstance(data, dict):
        raise JTPError("The spec must be a JSON object (or a list of lines).")
    return data


def _render(store, args, doc, out: dict):
    if getattr(args, "no_render", False):
        return
    try:
        result = render_doc(store, doc)
        out.setdefault("files", {})[doc["id"]] = result["files"]
        if result["warnings"]:
            out.setdefault("warnings", []).extend(result["warnings"])
    except Exception as exc:  # a render problem must never undo a saved document
        out.setdefault("warnings", []).append(f"Saved, but the PDF couldn't be made: {exc}")


def _keep_sent_copy(store, args, doc, out: dict) -> None:
    """Keep the PDF exactly as it was sent: output/sent/<id>-<date>.pdf (never overwritten)."""
    if getattr(args, "no_render", False):
        return
    try:
        source = store.output_dir / f"{doc['id']}.pdf"
        if not source.exists():
            render_doc(store, doc)
        sent = (doc.get("sent") or [{}])[-1]
        rev = f"-r{doc['revision']}" if doc.get("revision", 1) > 1 else ""
        target = store.output_dir / "sent" / f"{doc['id']}{rev}-{sent.get('date') or iso(store.today())}.pdf"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copyfile(source, target)
        out["sent_copy"] = str(target)
    except Exception as exc:  # recording the send matters more than the copy
        out.setdefault("warnings", []).append(f"Recorded as sent, but no copy of the PDF was kept: {exc}")


def _money(store, value) -> str:
    from .money import format_money

    return format_money(to_decimal(value), store.money())


def _day(store, text) -> str:
    from .view import format_date

    return format_date(from_iso(text), store.profile()["locale"].get("date_format")) if text else ""


def _cust_name(store, customer_id) -> str:
    if customer_id == docs.SAMPLE_CUSTOMER:
        return "Sample Customer"
    try:
        cust = store.customer(customer_id)
    except JTPError:
        return customer_id
    company, name = cust.get("company"), cust.get("name")
    if company and name and company.lower() != name.lower():
        return f"{company} ({name})"
    return name or company or customer_id


def _quote_summary(store, doc) -> str:
    parts = []
    many = len(doc["options"]) > 1
    for option in doc["options"]:
        star = " (recommended)" if option.get("recommended") and many else ""
        label = f"{option['key']} {option['name']}" if many else option["name"]
        parts.append(f"{label}{star}: {_money(store, option['totals']['total'])}")
    rev = f" rev {doc['revision']}" if doc.get("revision", 1) > 1 else ""
    status = doc["status"]
    if status == "superseded" and (doc.get("superseded") or {}).get("by"):
        status = f"replaced by {doc['superseded']['by']}"
    accepted = doc.get("accepted") or {}
    if status == "accepted" and accepted:
        status = f"accepted, option {accepted['option']}" if many else "accepted"
        if accepted.get("date"):
            status += f", on {_day(store, accepted['date'])}"
    text = f"Quote {doc['id']}{rev} for {_cust_name(store, doc['customer_id'])} ({status}): " + "; ".join(parts)
    if doc["status"] in ("draft", "sent") and doc.get("valid_until"):
        text += f". Valid until {_day(store, doc['valid_until'])}"
    return text


def _invoice_summary(store, doc) -> str:
    state = docs.invoice_state(doc, store.today())
    status = {"partially_paid": "partly paid", "credited": "cancelled by credit note"}.get(
        state["status"], state["status"]
    )
    text = f"Invoice {doc['id']} for {_cust_name(store, doc['customer_id'])} ({status}): "
    text += f"total {_money(store, state['total'])}"
    if state["credits"] > 0:
        text += f", credited {_money(store, state['credits'])}"
    if doc["status"] == "void":
        return text + (f" ({doc['void_reason']})" if doc.get("void_reason") else "")
    if doc["status"] == "draft":
        try:
            held = docs.deposits_held(store.job(doc["job_id"]))
        except JTPError:
            held = 0
        if held > 0:
            left = state["total"] - min(held, state["total"])
            text += f". Deposit held {_money(store, held)} comes off when it's issued, leaving {_money(store, left)}"
        return text
    if state["paid"] > 0 and state["balance"] > 0:
        text += f", paid {_money(store, state['paid'])}"
    if state["balance"] <= 0:
        if state["paid"] > 0 and doc.get("paid_date"):
            text += f", paid on {_day(store, doc['paid_date'])}"
        return text
    text += f", balance {_money(store, state['balance'])}"
    if doc.get("due_date"):
        text += f", due {_day(store, doc['due_date'])}"
    if state.get("overdue"):
        days = state["days_overdue"]
        text += f" ({days} day{'' if days == 1 else 's'} overdue)"
    return text


def _co_summary(store, doc) -> str:
    return (
        f"Change order {doc['id']} on {doc['job_id']} ({doc['status']}): {doc['title']}, "
        f"{_money(store, doc['totals']['total'])}"
    )


def _credit_summary(store, doc) -> str:
    text = (
        f"Credit note {doc['id']} on invoice {doc['invoice_id']} for {_cust_name(store, doc['customer_id'])}: "
        f"{_money(store, doc['totals']['total'])} ({doc['reason']})"
    )
    if to_decimal(doc.get("refund_due") or "0") > 0:
        text += f". Refund due to the customer: {_money(store, doc['refund_due'])}"
    if doc.get("sent"):
        text += f". Sent {_day(store, doc['sent'][-1]['date'])}"
    return text


def _slim(store, doc: dict) -> dict:
    """The few fields that matter after a status change (use `show` for everything)."""
    keep = ("id", "type", "kind", "status", "revision", "title", "customer_id", "job_id", "invoice_id", "date")
    out = {k: doc[k] for k in keep if doc.get(k) not in (None, "")}
    if doc["type"] == "quote":
        out["valid_until"] = doc.get("valid_until")
        out["options"] = []
        for option in doc["options"]:
            item = {"key": option["key"], "name": option["name"], "total": option["totals"]["total"]}
            if option.get("deposit_due"):
                item["deposit_due"] = option["deposit_due"]
            out["options"].append(item)
        for key in ("accepted", "declined", "superseded"):
            if doc.get(key):
                out[key] = doc[key]
    elif doc["type"] == "invoice":
        state = docs.invoice_state(doc, store.today())
        out.update({k: str(state[k]) for k in ("total", "paid", "credits", "balance")})
        out["state"] = state["status"]
        for key in ("due_date", "paid_date", "payment_link", "void_reason"):
            if doc.get(key):
                out[key] = doc[key]
        if doc.get("external"):
            out["external"] = doc["external"]
    else:
        out["total"] = doc["totals"]["total"]
        for key in ("approval", "invoice", "refund_due", "void_reason"):
            if doc.get(key):
                out[key] = doc[key]
    if doc.get("sent"):
        out["sent"] = doc["sent"]
    return out


def _store(args) -> Store:
    home = Path(args.home).expanduser() if args.home else default_home()
    return Store(home)


def _set_dotted(data: dict, key: str, value):
    parts = key.split(".")
    node = data
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            raise JTPError(f"Unknown setting {key!r}.", hint="See the current settings with: jtp profile show")
        node = node[part]
    if (
        parts[-1] not in node
        and parts[0] not in ("labels",)
        and not (
            parts[0] == "locale"
            and parts[-1] in ("currency_symbol", "symbol_position", "decimal_separator", "thousands_separator")
        )
    ):
        raise JTPError(f"Unknown setting {key!r}.", hint="See the current settings with: jtp profile show")
    node[parts[-1]] = value


def _parse_value(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _get_dotted(data: dict, key: str):
    node = data
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


# ------------------------------------------------------------------- commands


def cmd_init(store, args):
    if store.exists():
        return {
            "home": str(store.home),
            "already_initialized": True,
            "next": "jtp profile check   (see what's left to set up)",
        }
    store.home.mkdir(parents=True, exist_ok=True)
    store.save_profile(profile_mod.new_profile(args.currency))
    write_json(store.home / "counters.json", store.counters())
    store.log("init", None, currency=args.currency)
    return {
        "home": str(store.home),
        "created": True,
        "next": "Fill in the profile (jtp profile set ...), then jtp profile check",
    }


def cmd_doctor(store, args):
    out = {
        "version": __version__,
        "python": sys.version.split()[0],
        "python_ok": sys.version_info >= (3, 9),
        "home": str(store.home),
        "initialized": store.exists(),
        "browser_for_unicode_pdfs": find_browser(),
    }
    try:
        store.home.mkdir(parents=True, exist_ok=True)
        probe = store.home / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        out["writable"] = True
    except OSError as exc:
        out["writable"] = False
        out["write_error"] = str(exc)
    if store.exists():
        out["setup"] = profile_mod.check(store.profile())
        out["counts"] = {
            "customers": len(store.customers()),
            "jobs": len(store.jobs()),
            "quotes": len(store.docs("quote")),
            "invoices": len(store.docs("invoice")),
        }
    return out


def cmd_demo(store, args):
    made = seed(store, args.currency, history=not args.no_history)
    return {"home": str(store.home), "demo": made, "next": f"Try: jtp --home {store.home} brief --text"}


def cmd_selftest(store, args):
    result = selftest()
    return result if result["passed"] else _fail_payload(result)


def _fail_payload(result):
    raise JTPError(
        "Self-test failed: " + "; ".join(f"{c['name']}: {c['detail']}" for c in result["checks"] if not c["ok"])
    )


def cmd_profile(store, args):
    if args.action == "show":
        prof = store.profile()
        return {"profile": prof[args.section] if args.section else prof}
    if args.action == "check":
        return profile_mod.check(store.profile())
    if args.action == "labels":
        current = store.profile().get("labels") or {}
        return {
            "labels": profile_mod.labels(store.profile()),
            "overridden": sorted(current),
            "how": "Translate every value, then: jtp profile set labels '<json object>'",
        }
    if args.action == "set":
        if not args.key or args.value is None:
            raise JTPError(
                "Usage: jtp profile set <dotted.key> <value>", hint='jtp profile set business.name "Acme Plumbing"'
            )
        raw = read_json(store.profile_path, {})
        data = profile_mod.merged(raw)
        value = _parse_value(args.value)
        existing = _get_dotted(data, args.key)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and (isinstance(existing, str) or args.key.startswith("labels."))
        ):
            value = args.value  # keep phone numbers, rates and codes as typed
        if args.key == "locale.currency":
            value = str(value).upper()
            for k in ("currency_symbol", "symbol_position", "decimal_separator", "thousands_separator"):
                data["locale"].pop(k, None)
        _set_dotted(data, args.key, value)
        errors = profile_mod.validate(data)
        if errors:
            raise JTPError("Not saved: " + " ".join(errors))
        store.save_profile(data)
        store.log("profile.set", None, key=args.key)
        warnings = []
        if args.key == "business.logo_path" and value and not Path(str(value)).expanduser().is_file():
            warnings.append(f"No file at {value}; the logo will be skipped until it exists.")
        return {"saved": args.key, "value": value, "warnings": warnings}
    raise JTPError("Unknown profile action.")


def cmd_numbering(store, args):
    store.set_counter(args.kind, args.next)
    return {"next": store.next_id(args.kind, peek=True)}


def _customer_fields(args) -> dict:
    data = {}
    for key in ("name", "company", "email", "phone", "address", "notes", "language", "whatsapp"):
        if getattr(args, key, None) is not None:
            data[key] = getattr(args, key)
    if getattr(args, "preferred", None):
        data["preferred_contact"] = args.preferred
    if getattr(args, "tax_id", None):
        data["tax_id"] = args.tax_id
    if getattr(args, "site", None):
        data["site_addresses"] = [args.site]
    return data


def cmd_customer(store, args):
    if args.action == "add":
        return records.customer_add(store, _customer_fields(args))
    if args.action == "list":
        found = records.customer_list(store, args.search)
        return {"customers": found, "count": len(found)}
    if args.action == "show":
        return reports.customer_summary(store, args.id)
    if args.action == "update":
        return {"customer": records.customer_update(store, args.id, _customer_fields(args))}
    raise JTPError("Unknown customer action.")


def cmd_pricebook(store, args):
    if args.action == "add":
        return records.pricebook_add(
            store,
            {
                k: getattr(args, k)
                for k in ("name", "kind", "unit", "cost", "price", "sku", "supplier", "tags")
                if getattr(args, k, None) is not None
            }
            | ({"markup_pct": args.markup} if args.markup is not None else {}),
        )
    if args.action == "list":
        items = records.pricebook_list(store, args.search)
        return {"items": items, "count": len(items)}
    if args.action == "remove":
        return records.pricebook_remove(store, args.id)
    if args.action == "import":
        path = Path(args.file).expanduser()
        if not path.is_file():
            raise JTPError(f"No file {args.file}.")
        added, updated, errors = 0, 0, []
        with open(path, newline="", encoding="utf-8-sig") as handle:
            for n, row in enumerate(csv.DictReader(handle), 2):
                row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                if "markup" in row and "markup_pct" not in row:
                    row["markup_pct"] = row["markup"]
                try:
                    result = records.pricebook_add(store, {k: v for k, v in row.items() if v})
                    updated += result["updated"]
                    added += not result["updated"]
                except JTPError as exc:
                    errors.append(f"row {n}: {exc.message}")
        return {"added": added, "updated": updated, "errors": errors}
    raise JTPError("Unknown pricebook action.")


def cmd_job(store, args):
    if args.action == "create":
        if not args.customer or not args.title:
            raise JTPError(
                "A job needs --customer and --title.",
                hint='jtp job create --customer C-0001 --title "Replace water heater"',
            )
        job = records.job_create(
            store, args.customer, args.title, site=args.site, tags=args.tags, pricing=args.pricing, notes=args.notes
        )
        return {"job": job, "summary": f"Job {job['id']}: {job['title']} for {_cust_name(store, job['customer_id'])}"}
    if args.action == "list":
        jobs = records.job_list(store, args.status or "open", args.search, args.customer)
        return {"jobs": jobs, "count": len(jobs)}
    if args.action == "show":
        job = store.job(args.id)
        return {"job": job, "money": reports.job_summary(store, job["id"])}
    if args.action == "update":
        changes = {
            "status": args.status,
            "title": args.title,
            "site_address": args.site,
            "tags": args.tags,
            "service_interval_months": args.service_months,
            "completed": args.completed,
            "notes": args.notes,
            "pricing": args.pricing,
        }
        return {"job": records.job_update(store, args.id, changes)}
    if args.action == "log":
        kinds = [k for k in ("time", "material", "expense", "note", "photo") if getattr(args, k) is not None]
        if len(kinds) != 1:
            raise JTPError(
                "Log exactly one thing: --time HOURS, --material DESC, --expense DESC, --note TEXT or --photo PATH.",
                hint='jtp job log J-0001 --time 3.5 --who Sam   |   jtp job log J-0001 --material "Ball valve" --qty 2 --cost 18.50',
            )
        kind = kinds[0]
        data = {"date": args.date, "who": args.who, "extra": args.extra, "billable": args.billable}
        if kind == "time":
            data.update({"hours": args.time, "rate_key": args.rate, "note": args.note_text})
        elif kind == "material":
            data.update(
                {
                    "description": args.material,
                    "qty": args.qty or "1",
                    "unit": args.unit,
                    "unit_cost": args.cost,
                    "unit_price": args.price,
                    "markup_pct": args.markup,
                    "pricebook": args.pricebook,
                    "receipt": args.receipt,
                    "supplier": args.supplier,
                    "note": args.note_text,
                }
            )
        elif kind == "expense":
            data.update(
                {
                    "description": args.expense,
                    "amount": args.amount,
                    "markup_pct": args.markup,
                    "receipt": args.receipt,
                    "supplier": args.supplier,
                    "note": args.note_text,
                }
            )
        elif kind == "note":
            data.update({"text": args.note})
        else:
            data.update({"path": args.photo, "caption": args.caption})
        return records.job_log(store, args.id, kind, data)
    if args.action == "unlog":
        return records.job_unlog(store, args.id, args.entry)
    raise JTPError("Unknown job action.")


def _need_id(args, noun: str, example: str) -> None:
    if not getattr(args, "id", None):
        raise JTPError(f"Say which {noun}.", hint=f"For example: jtp {args.command} {args.action} {example}")


def cmd_quote(store, args):
    a = args.action
    if a not in ("create", "list"):
        _need_id(args, "quote", "Q-0001")
    if a == "create":
        spec = _spec(args.spec)
        if spec is None:
            raise JTPError(
                "A quote needs --spec (a JSON file, - for stdin, or inline JSON).",
                hint="See the format with: jtp quote create --help",
            )
        if args.title:
            spec["title"] = args.title
        if args.tags:
            spec["tags"] = args.tags
        out = docs.quote_create(store, spec, job_id=args.job, customer_id=args.customer, dry_run=args.dry_run)
        if args.tags and args.job and not args.dry_run:
            records.job_update(store, args.job, {"tags": args.tags})
        out["summary"] = (
            _quote_summary(store, out["quote"])
            if not args.dry_run
            else "Preview only, nothing saved. " + _quote_summary(store, out["quote"])
        )
        _render(store, args, out["quote"], out)
        return out
    if a == "revise":
        out = docs.quote_revise(store, args.id, _spec(args.spec) or {})
        out["summary"] = _quote_summary(store, out["quote"])
        _render(store, args, out["quote"], out)
        return out
    if a == "show":
        doc = store.doc(args.id, "quote")
        if args.as_spec:
            return {
                "spec": {
                    "title": doc["title"],
                    "intro": doc["intro"],
                    "notes": doc["notes"],
                    "options": [
                        {
                            "key": o["key"],
                            "name": o["name"],
                            "description": o["description"],
                            "recommended": o["recommended"],
                            "lines": lines_as_spec(o["lines"]),
                        }
                        for o in doc["options"]
                    ],
                }
            }
        return {"quote": doc, "summary": _quote_summary(store, doc)}
    if a == "list":
        found = [d for d in store.docs("quote") if not args.status or d["status"] == args.status]
        return {
            "quotes": [
                {
                    "id": d["id"],
                    "status": d["status"],
                    "title": d["title"],
                    "date": d["date"],
                    "customer": _cust_name(store, d["customer_id"]),
                    "job": d["job_id"],
                    "totals": {o["key"]: _money(store, o["totals"]["total"]) for o in d["options"]},
                }
                for d in found
            ],
            "count": len(found),
        }
    if a == "mark-sent":
        out = docs.quote_mark_sent(store, args.id, args.channel, args.to, args.date)
        _keep_sent_copy(store, args, out["quote"], out)
        return _status_out(store, out, "quote", _quote_summary)
    if a == "accept":
        out = docs.quote_accept(store, args.id, args.option, args.by, args.method, args.note, args.date)
        if out.get("accepted_total"):
            out["accepted_total_display"] = _money(store, out["accepted_total"])
        if out.get("deposit_due"):
            out["deposit_due_display"] = _money(store, out["deposit_due"])
            out["next"] = (
                f"Deposit due: {out['deposit_due_display']}. When it arrives: "
                f"jtp payment record --job {out.get('job')} --deposit --amount {out['deposit_due']}"
            )
        if out.get("replaced_quotes"):
            out["note"] = (
                f"Also marked {', '.join(out['replaced_quotes'])} as replaced (other quotes on this job), so they "
                "get no more follow-ups."
            )
        _render(store, args, out["quote"], out)
        return _status_out(store, out, "quote", _quote_summary)
    if a == "supersede":
        out = docs.quote_supersede(store, args.id, args.with_quote, args.date)
        return _status_out(store, out, "quote", _quote_summary)
    if a == "decline":
        return _status_out(store, docs.quote_decline(store, args.id, args.reason, args.date), "quote", _quote_summary)
    if a == "extend":
        return _status_out(store, docs.quote_extend(store, args.id, args.days, args.until), "quote", _quote_summary)
    if a == "expire":
        return _status_out(store, docs.quote_expire(store, args.id), "quote", _quote_summary)
    raise JTPError("Unknown quote action.")


def _status_out(store, out: dict, key: str, summarize) -> dict:
    """After a status change: a one-line summary and the short form of the document."""
    if out.get(key):
        out["summary"] = summarize(store, out[key])
        out[key] = _slim(store, out[key])
    return out


def cmd_co(store, args):
    a = args.action
    if a not in ("create", "list"):
        _need_id(args, "change order", "CO-0001")
    if a == "create":
        spec = _spec(args.spec)
        if spec is None:
            raise JTPError("A change order needs --spec.")
        out = docs.co_create(store, args.job, spec, dry_run=args.dry_run)
        out["summary"] = ("Preview only, nothing saved. " if args.dry_run else "") + _co_summary(
            store, out["change_order"]
        )
        _render(store, args, out["change_order"], out)
        return out
    if a == "update":
        out = docs.co_update(store, args.id, _spec(args.spec) or {})
        out["summary"] = _co_summary(store, out["change_order"])
        _render(store, args, out["change_order"], out)
        return out
    if a == "show":
        doc = store.doc(args.id, "change_order")
        return {"change_order": doc, "summary": _co_summary(store, doc)}
    if a == "list":
        found = [d for d in store.docs("change_order") if not args.job or d["job_id"].lower() == args.job.lower()]
        return {
            "change_orders": [
                {
                    "id": d["id"],
                    "job": d["job_id"],
                    "status": d["status"],
                    "title": d["title"],
                    "total": _money(store, d["totals"]["total"]),
                    "invoice": d.get("invoice"),
                }
                for d in found
            ]
        }
    if a == "mark-sent":
        out = docs.co_mark_sent(store, args.id, args.channel, args.to, args.date)
        _keep_sent_copy(store, args, out["change_order"], out)
        return _status_out(store, out, "change_order", _co_summary)
    if a == "approve":
        out = docs.co_approve(store, args.id, args.by, args.method, args.note, args.date)
        _render(store, args, out["change_order"], out)
        return _status_out(store, out, "change_order", _co_summary)
    if a == "decline":
        return _status_out(store, docs.co_decline(store, args.id, args.reason), "change_order", _co_summary)
    if a == "void":
        out = docs.co_void(store, args.id, args.reason)
        _render(store, args, out["change_order"], out)
        return _status_out(store, out, "change_order", _co_summary)
    raise JTPError("Unknown change order action.")


def cmd_invoice(store, args):
    a = args.action
    if a not in ("create", "list"):
        _need_id(args, "invoice", "INV-0001")
    if a == "create":
        from_quote = args.from_quote if args.from_quote not in (None, "") else (True if args.from_quote == "" else None)
        spec = _spec(args.spec)
        if args.tags:
            spec = dict(spec or {}, tags=args.tags)
        out = docs.invoice_create(
            store,
            job_id=args.job,
            customer_id=args.customer,
            from_quote=from_quote,
            progress=args.progress if args.progress is not None else args.deposit_invoice,
            deposit_invoice=args.deposit_invoice is not None,
            tm=args.tm,
            spec=spec,
            include_change_orders=not args.no_change_orders,
            include_extras=not args.no_extras,
            title=args.title,
            dry_run=args.dry_run,
        )
        out["summary"] = ("Preview only, nothing saved. " if args.dry_run else "") + _invoice_summary(
            store, out["invoice"]
        )
        if not args.dry_run:
            inv_id = out["invoice"]["id"]
            out["next"] = f"Show the owner the draft; once they OK it: jtp invoice issue {inv_id}"
            if out.get("estimates"):
                out["next"] = (
                    f"Some figures were estimates on the quote ({'; '.join(out['estimates'])}). Ask the owner for the "
                    f"real figures and fix the draft if they changed (jtp invoice update {inv_id} --spec ...). When "
                    f"the owner confirms, issue it: jtp invoice issue {inv_id} --estimates-confirmed"
                )
        _render(store, args, out["invoice"], out)
        return out
    if a == "update":
        out = docs.invoice_update(store, args.id, _spec(args.spec) or {})
        out["summary"] = _invoice_summary(store, out["invoice"])
        _render(store, args, out["invoice"], out)
        return out
    if a == "issue":
        out = docs.invoice_issue(
            store,
            args.id,
            args.date,
            args.due_days,
            args.due_date,
            args.allow_unconfirmed_tax,
            estimates_confirmed=args.estimates_confirmed,
        )
        _render(store, args, out["invoice"], out)
        return _status_out(store, out, "invoice", _invoice_summary)
    if a == "show":
        doc = store.doc(args.id, "invoice")
        if args.as_spec:
            return {
                "spec": {
                    "title": doc["title"],
                    "intro": doc["intro"],
                    "notes": doc["notes"],
                    "lines": lines_as_spec(doc["lines"]),
                }
            }
        state = docs.invoice_state(doc, store.today())
        return {
            "invoice": doc,
            "state": {k: str(v) if not isinstance(v, (bool, int)) else v for k, v in state.items()},
            "summary": _invoice_summary(store, doc),
        }
    if a == "list":
        day = store.today()
        rows = []
        for d in store.docs("invoice"):
            state = docs.invoice_state(d, day)
            status = state["status"]
            wanted = args.status or "unpaid"
            if wanted == "unpaid" and not (d["status"] == "issued" and state["balance"] > 0):
                continue
            if wanted == "overdue" and not state.get("overdue"):
                continue
            if wanted in ("paid", "draft", "void") and d["status"] != wanted:
                continue
            if args.customer and d["customer_id"].lower() != args.customer.lower():
                continue
            rows.append(
                {
                    "id": d["id"],
                    "status": status,
                    "date": d.get("date"),
                    "due_date": d.get("due_date"),
                    "customer": _cust_name(store, d["customer_id"]),
                    "title": d["title"],
                    "total": _money(store, state["total"]),
                    "balance": _money(store, state["balance"]),
                    "days_overdue": state["days_overdue"],
                }
            )
        return {"invoices": rows, "count": len(rows)}
    if a == "mark-sent":
        out = docs.invoice_mark_sent(store, args.id, args.channel, args.to, args.date)
        _keep_sent_copy(store, args, out["invoice"], out)
        return _status_out(store, out, "invoice", _invoice_summary)
    if a == "set":
        external = {}
        for item in args.external or []:
            if "=" not in item:
                raise JTPError("--external takes key=value, e.g. --external xero=INV-123")
            key, value = item.split("=", 1)
            external[key.strip()] = value.strip()
        out = docs.invoice_set(store, args.id, args.payment_link, external, args.notes)
        _render(store, args, out["invoice"], out)
        return _status_out(store, out, "invoice", _invoice_summary)
    if a == "void":
        out = docs.invoice_void(store, args.id, args.reason)
        _render(store, args, out["invoice"], out)
        if out.get("credit_note"):
            _render(store, args, out["credit_note"], out)
            out["credit_note"] = _slim(store, out["credit_note"])
        return _status_out(store, out, "invoice", _invoice_summary)
    if a == "delete":
        return docs.invoice_delete(store, args.id)
    raise JTPError("Unknown invoice action.")


def cmd_credit(store, args):
    if args.action == "create":
        if not args.invoice:
            raise JTPError("Say which invoice to credit.", hint="jtp credit create --invoice INV-0003 --amount 50")
        out = docs.credit_create(
            store, args.invoice, spec=_spec(args.spec), amount=args.amount, reason=args.reason, dry_run=args.dry_run
        )
        out["summary"] = ("Preview only, nothing saved. " if args.dry_run else "") + _credit_summary(
            store, out["credit_note"]
        )
        _render(store, args, out["credit_note"], out)
        if not args.dry_run:
            invoice = store.doc(args.invoice, "invoice")
            _render(store, args, invoice, out)
            out["invoice_summary"] = _invoice_summary(store, invoice)
            out["next"] = (
                f"Send the credit note to the customer, then: jtp credit mark-sent {out['credit_note']['id']} "
                "--channel email --to <address>"
            )
        return out
    if args.action in ("show", "mark-sent"):
        _need_id(args, "credit note", "CN-0001")
    if args.action == "show":
        doc = store.doc(args.id, "credit_note")
        return {"credit_note": doc, "summary": _credit_summary(store, doc)}
    if args.action == "mark-sent":
        out = docs.credit_mark_sent(store, args.id, args.channel, args.to, args.date)
        _keep_sent_copy(store, args, out["credit_note"], out)
        return _status_out(store, out, "credit_note", _credit_summary)
    raise JTPError("Unknown credit action.")


def cmd_payment(store, args):
    if args.action == "record":
        if not args.invoice and not args.job:
            raise JTPError("Say which invoice (jtp payment record INV-0001 ...) or job (--job J-0001 --deposit).")
        amount, basis = args.amount, ""
        if args.percent is not None:
            if args.amount is not None or not args.job:
                raise JTPError(
                    "--percent is for a deposit on a job, instead of --amount.",
                    hint="jtp payment record --job J-0004 --deposit --percent 25 --method 'Bank transfer'",
                )
            value, total = docs.deposit_for_percent(store, args.job, args.percent)
            amount, basis = str(value), f" ({args.percent.strip().rstrip('%')}% of {_money(store, total)})"
        out = docs.payment_record(
            store,
            invoice_id=args.invoice,
            job_id=args.job,
            amount=amount,
            method=args.method,
            date=args.date,
            reference=args.reference,
            note=args.note,
            deposit=args.deposit,
            allow_duplicate=args.allow_duplicate,
        )
        if out.get("invoice") and not out.get("duplicate"):
            _render(store, args, out["invoice"], out)
        if out.get("invoice"):
            out = _status_out(store, out, "invoice", _invoice_summary)
        elif out.get("payment") and out.get("job"):
            out["summary"] = f"{_money(store, out['payment']['amount'])}{basis} held on {out['job']}" + (
                " (already recorded)" if out.get("duplicate") else ""
            )
        return out
    if args.action == "list":
        found = docs.payment_list(store, args.start, args.end)
        return {
            "payments": found,
            "count": len(found),
            "total": _money(store, sum((to_decimal(p["amount"]) for p in found), to_decimal(0))),
        }
    if args.action == "undo":
        return docs.payment_undo(store, args.id)
    if args.action == "receipt":
        return render_receipt(store, args.id, args.format, args.engine)
    raise JTPError("Unknown payment action.")


def cmd_render(store, args):
    return render_doc(store, store.doc(args.id), args.format, args.engine, args.out)


def cmd_price(store, args):
    spec = _spec(args.spec)
    if spec is None:
        raise JTPError("Pass lines with --spec.")
    out = reports.sample_totals(store, spec.get("lines") or [])
    out["totals_formatted"] = {k: _money(store, out["totals"][k]) for k in ("subtotal", "tax_total", "total")}
    return out


def cmd_followups(store, args):
    if args.action == "due":
        day = store.today() if not args.date else _date(store, args.date)
        actions = followups.due_actions(store, day)
        return {"date": iso(day), "actions": actions, "count": len(actions)}
    if args.action in ("done", "skip"):
        return followups.mark_done(
            store, args.id, args.stage, args.channel, args.note, skipped=args.action == "skip", date=args.date
        )
    if args.action == "snooze":
        return followups.snooze(store, args.id, args.until, args.note)
    raise JTPError("Unknown followups action.")


def _date(store, text):
    from .util import parse_date

    return parse_date(text, store.today())


def cmd_brief(store, args):
    day = _date(store, args.date) if args.date else None
    out = reports.brief(store, day)
    if args.text:
        sys.stdout.write(out["text"] + "\n")
        sys.exit(0)
    return out


def cmd_report(store, args):
    if args.kind == "aging":
        return reports.aging(store)
    if args.kind == "profit":
        return reports.profit(store, args.start, args.end)
    if args.kind == "pricing":
        return reports.pricing_insight(store, args.tag)
    if args.kind == "tax":
        return reports.tax_report(store, args.start, args.end, args.basis)
    if args.kind == "sales":
        return reports.sales_report(store, args.start, args.end)
    raise JTPError("Unknown report.")


def cmd_export(store, args):
    return reports.export(store, args.what, args.out, args.start, args.end)


def cmd_search(store, args):
    return reports.search(store, " ".join(args.words))


def cmd_backup(store, args):
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = Path(args.out).expanduser() if args.out else store.home / "backups" / f"job-to-paid-{stamp}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(store.home.rglob("*")):
            if not path.is_file() or path.name == ".lock" or "backups" in path.relative_to(store.home).parts:
                continue
            if path.resolve() == target.resolve():
                continue
            archive.write(path, path.relative_to(store.home))
            count += 1
    return {"backup": str(target), "files": count}


# ----------------------------------------------------------------- parser


def _add(sub, name, help_text, examples, func, **kwargs):
    parser = sub.add_parser(
        name,
        help=help_text,
        description=help_text,
        epilog="Examples:\n  " + "\n  ".join(examples),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        **kwargs,
    )
    parser.set_defaults(func=func)
    return parser


QUOTE_SPEC_HELP = """Spec format (JSON):
  {"title": "Water heater replacement",
   "intro": "Thanks for having us out...",            (optional)
   "lines": [                                          (one option)
     {"kind": "labour", "description": "Install and test", "qty": 4},          uses your labour rate
     {"kind": "labour", "rate_key": "after_hours", "description": "Call-out", "qty": 1},
     {"kind": "material", "description": "40 gal heater", "qty": 1, "unit_cost": 980},  cost + your markup
     {"kind": "material", "pricebook": "PB-0003", "qty": 2},                   from your price book
     {"kind": "fee", "description": "Permit", "unit_price": 175},
     {"kind": "discount", "description": "Repeat customer", "percent": 10},
     {"kind": "text", "description": "A wording-only line (no price)"}],
   OR "options": [{"name": "Good", "lines": [...]}, {"name": "Better", "recommended": true, "lines": [...]}],
   "deposit_percent": 30,  or  "deposit_amount": 500,   (optional, default from your profile)
   "valid_days": 30, "notes": "...", "terms": "..."}   (optional)
unit_cost is what the owner pays (markup is added); a bare figure from the owner is a cost.
Add "estimate": true to a line whose price still needs confirming.
Add "tax_codes": [] (no tax) or ["GST"] only when the owner says that line is taxed differently.
--dry-run prices it without saving and writes output/PREVIEW.pdf to show the owner."""


FLAG_HELP = {
    "--job": "Job id, e.g. J-0001",
    "--customer": "Customer id, e.g. C-0001",
    "--invoice": "Invoice id, e.g. INV-0001",
    "--spec": "Lines as JSON: a file path, - for stdin, or inline JSON",
    "--title": "Short title the customer sees",
    "--channel": "email, sms, whatsapp, phone, in_person, portal or other",
    "--to": "Where it went (email address or phone number)",
    "--option": "The option the customer chose (A, B, C...)",
    "--by": "Who accepted or approved (customer name)",
    "--method": "How: email, text message, signed PDF, in person... (payments: e-Transfer, card, cash...)",
    "--note": "Free text kept on record",
    "--reason": "Why (kept on record)",
    "--date": "YYYY-MM-DD, today, yesterday, or +N / -N days (default: today)",
    "--until": "A date: YYYY-MM-DD or +N days",
    "--status": "Filter by status",
    "--tags": "Job-type tags, comma separated (e.g. 'water heater,gas')",
    "--search": "Words to match",
    "--name": "Name",
    "--company": "Company name",
    "--email": "Email address",
    "--phone": "Phone number",
    "--address": "Address; separate lines with ; (e.g. '12 Maple Ave; Surrey BC V3S 1A1')",
    "--site": "Job site address if different",
    "--notes": "Notes",
    "--language": "Language for this customer's documents and messages (e.g. en, fr, es)",
    "--whatsapp": "WhatsApp number if different from phone",
    "--tax-id": "Customer's tax/VAT number (printed on their invoices)",
    "--kind": "labour, material, equipment, subcontract, fee or other",
    "--unit": "Unit, e.g. ea, hr, m, ft, day",
    "--cost": "What it costs you (your markup is added)",
    "--price": "Fixed selling price",
    "--markup": "Markup percent (overrides your default)",
    "--sku": "Supplier part number",
    "--supplier": "Supplier name",
    "--completed": "Date the work was completed",
    "--time": "Hours worked",
    "--material": "Material description",
    "--expense": "Expense description (dump fee, parking...)",
    "--photo": "Path to a photo",
    "--caption": "Photo caption",
    "--qty": "Quantity",
    "--amount": "Amount (including tax where it's a payment or credit)",
    "--rate": "Labour rate key, e.g. standard or after_hours",
    "--who": "Who did the work",
    "--receipt": "Path to the receipt photo or PDF",
    "--pricebook": "Price book item id or exact name",
    "--reference": "Payment reference (e-transfer id, cheque no.)",
    "--due-date": "Explicit due date",
    "--payment-link": "Online payment URL (https://...)",
    "--stage": "Stage from `jtp followups due` (a number, review, service:<date> or approval)",
}


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="jtp",
        description="Job to Paid: quotes, change orders, invoices and getting paid. Every command prints JSON.",
        epilog=EPILOG_HELP,
    )
    parser.add_argument("--home", help="Data folder (default: $JTP_HOME or /workspace/job-to-paid or ~/job-to-paid)")
    parser.add_argument("--no-render", action="store_true", help="Don't make PDFs automatically")
    parser.add_argument("--version", action="version", version=f"jtp {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>", parser_class=_Parser)

    p = _add(
        sub,
        "init",
        "Create the data folder and a blank business profile.",
        ["jtp init --currency CAD", "jtp --home ~/my-business init --currency EUR"],
        cmd_init,
    )
    p.add_argument("--currency", default="USD", help="ISO currency code, e.g. USD, CAD, GBP, EUR, AUD")
    _add(sub, "doctor", "Check Python, the data folder, setup gaps and PDF options.", ["jtp doctor"], cmd_doctor)
    p = _add(
        sub,
        "demo",
        "Fill a NEW folder with a demo business, customers, jobs, invoices and a quote.",
        ["jtp --home /tmp/jtp-demo demo", "jtp --home /tmp/jtp-demo demo --currency GBP --no-history"],
        cmd_demo,
    )
    p.add_argument("--currency", default="USD")
    p.add_argument("--no-history", action="store_true", help="Only the business, customers and price book")
    _add(
        sub,
        "selftest",
        "Run built-in checks (money math, a full job, PDFs) in a temporary folder.",
        ["jtp selftest"],
        cmd_selftest,
    )

    p = _add(
        sub,
        "profile",
        "Show, check or change business settings.",
        [
            "jtp profile check",
            "jtp profile show --section tax",
            "jtp profile labels   (document wording to translate)",
            'jtp profile set business.name "Acme Plumbing"',
            'jtp profile set tax.taxes \'[{"code":"GST","name":"GST","rate":"5"}]\'',
            "jtp profile set tax.confirmed true",
            'jtp profile set rates.labour \'[{"key":"standard","name":"Labour","rate":"95","cost":"45"}]\'',
            'jtp profile set payment.methods \'[{"name":"Bank transfer","instructions":"Acct 123. Use {number} as the reference"}]\'',
        ],
        cmd_profile,
    )
    p.add_argument("action", choices=["show", "check", "set", "labels"])
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p.add_argument("--section")

    p = _add(
        sub,
        "numbering",
        "Set the next number (e.g. to continue from your old invoice numbers).",
        ["jtp numbering invoice 1047", "jtp numbering quote 300"],
        cmd_numbering,
    )
    p.add_argument("kind", choices=["quote", "invoice", "change_order", "credit_note", "job", "customer", "payment"])
    p.add_argument("next", type=int)

    p = _add(
        sub,
        "customer",
        "Add, find, show or update customers.",
        [
            'jtp customer add --name "Jane Doe" --phone "+1 604 555 0142" --email jane@example.com '
            '--address "12 Maple Ave; Surrey BC V3S 1A1"',
            "jtp customer list --search jane",
            "jtp customer show C-0001",
            "jtp customer update C-0001 --preferred whatsapp",
        ],
        cmd_customer,
    )
    p.add_argument("action", choices=["add", "list", "show", "update"])
    p.add_argument("id", nargs="?")
    for flag in (
        "--name",
        "--company",
        "--email",
        "--phone",
        "--address",
        "--notes",
        "--language",
        "--whatsapp",
        "--tax-id",
        "--site",
        "--search",
    ):
        p.add_argument(flag, help=FLAG_HELP.get(flag))
    p.add_argument("--preferred", choices=list(records.CONTACT_CHANNELS))

    p = _add(
        sub,
        "pricebook",
        "Your saved items and prices (learned from your quotes).",
        [
            'jtp pricebook add --name "1/2 in ball valve" --kind material --cost 18.50',
            'jtp pricebook add --name "Drain clearing (flat rate)" --kind labour --price 189',
            "jtp pricebook list --search valve",
            "jtp pricebook import items.csv",
            "jtp pricebook remove PB-0004",
        ],
        cmd_pricebook,
    )
    p.add_argument("action", choices=["add", "list", "remove", "import"])
    p.add_argument("id", nargs="?", help="Item id (remove)")
    p.add_argument("file", nargs="?", help="CSV with name,kind,unit,cost,price,markup_pct,sku,supplier (import)")
    for flag in (
        "--name",
        "--kind",
        "--unit",
        "--cost",
        "--price",
        "--markup",
        "--sku",
        "--supplier",
        "--tags",
        "--search",
    ):
        p.add_argument(flag, help=FLAG_HELP.get(flag))

    p = _add(
        sub,
        "job",
        "Jobs and the work log (time, materials, expenses, notes, photos).",
        [
            'jtp job create --customer C-0001 --title "Replace water heater" --tags "water heater"',
            "jtp job list",
            "jtp job list --status complete",
            "jtp job show J-0001",
            "jtp job update J-0001 --status complete --service-months 12",
            "jtp job log J-0001 --time 3.5 --who Sam",
            'jtp job log J-0001 --material "Gas flex connector" --qty 1 --cost 28 --extra --receipt /path/r.jpg',
            'jtp job log J-0001 --expense "Dump fee" --amount 25',
            'jtp job log J-0001 --note "Customer wants a softener quote"',
            "jtp job unlog J-0001 L3",
        ],
        cmd_job,
    )
    p.add_argument("action", choices=["create", "list", "show", "update", "log", "unlog"])
    p.add_argument("id", nargs="?")
    p.add_argument("entry", nargs="?", help="Log entry id (unlog)")
    for flag in (
        "--customer",
        "--title",
        "--site",
        "--tags",
        "--notes",
        "--search",
        "--status",
        "--completed",
        "--time",
        "--material",
        "--expense",
        "--note",
        "--photo",
        "--caption",
        "--qty",
        "--unit",
        "--cost",
        "--price",
        "--markup",
        "--amount",
        "--rate",
        "--who",
        "--date",
        "--receipt",
        "--supplier",
        "--pricebook",
    ):
        p.add_argument(flag, help=FLAG_HELP.get(flag))
    p.add_argument("--note-text", help="A note to attach to a time/material/expense entry")
    p.add_argument("--pricing", choices=["fixed", "tm"])
    p.add_argument("--service-months", type=int)
    p.add_argument("--extra", action="store_true", help="Extra work outside a fixed-price quote: bill it")
    p.add_argument("--billable", dest="billable", action="store_true", default=None)
    p.add_argument("--not-billable", dest="billable", action="store_false")

    p = _add(
        sub,
        "quote",
        "Create, revise, send-track, accept or decline quotes.\n\n" + QUOTE_SPEC_HELP,
        [
            "jtp quote create --customer C-0001 --spec quote.json --dry-run   (totals + output/PREVIEW.pdf)",
            "jtp quote create --spec quote.json --dry-run   (a sample PDF before any customer exists)",
            "jtp quote create --job J-0002 --spec quote.json",
            "jtp quote mark-sent Q-0001 --channel email --to jane@example.com",
            'jtp quote accept Q-0001 --option B --by "Jane Doe" --method email',
            "jtp quote revise Q-0001 --spec quote-v2.json",
            "jtp quote show Q-0001 --as-spec",
            "jtp quote extend Q-0001 --days 14",
            "jtp quote decline Q-0001 --reason 'Went with cheaper'",
            "jtp quote supersede Q-0001 --with Q-0004   (replaced by a newer quote: no more follow-ups)",
        ],
        cmd_quote,
    )
    p.add_argument(
        "action",
        choices=["create", "revise", "show", "list", "mark-sent", "accept", "decline", "extend", "expire", "supersede"],
    )
    p.add_argument("id", nargs="?")
    for flag in (
        "--job",
        "--customer",
        "--spec",
        "--title",
        "--channel",
        "--to",
        "--option",
        "--by",
        "--method",
        "--note",
        "--reason",
        "--date",
        "--until",
        "--status",
        "--tags",
    ):
        p.add_argument(flag, help=FLAG_HELP.get(flag))
    p.add_argument("--days", type=int)
    p.add_argument("--dry-run", action="store_true", help="Price it without saving (writes output/PREVIEW.pdf)")
    p.add_argument("--with", dest="with_quote", help="The quote that replaced this one (supersede)")
    p.add_argument("--as-spec", action="store_true", help="Show as an editable spec")

    p = _add(
        sub,
        "co",
        "Change orders: get extra or changed work approved before doing it.",
        [
            'jtp co create --job J-0001 --spec \'{"title":"Add shut-off valve","reason":"Old valve seized",'
            '"lines":[{"kind":"material","pricebook":"PB-0005","qty":1},{"kind":"labour","description":"Swap valve","qty":0.5}]}\'',
            'jtp co approve CO-0001 --by "Jane Doe" --method "text message"',
            "jtp co list --job J-0001",
        ],
        cmd_co,
    )
    p.add_argument("action", choices=["create", "update", "show", "list", "mark-sent", "approve", "decline", "void"])
    p.add_argument("id", nargs="?")
    for flag in ("--job", "--spec", "--channel", "--to", "--by", "--method", "--note", "--reason", "--date"):
        p.add_argument(flag, help=FLAG_HELP.get(flag))
    p.add_argument("--dry-run", action="store_true", help="Preview it (output/PREVIEW.pdf) without saving")

    p = _add(
        sub,
        "invoice",
        "Create, issue, track and correct invoices.",
        [
            "jtp invoice create --job J-0001            (accepted quote + approved change orders + extras)",
            "jtp invoice create --job J-0001 --progress 30   (30% progress invoice)",
            "jtp invoice create --job J-0001 --deposit-invoice 25",
            "jtp invoice create --job J-0002 --tm            (time and materials from the log)",
            'jtp invoice create --customer C-0003 --spec \'[{"kind":"fee","description":"Service call","unit_price":189}]\'',
            "jtp invoice issue INV-0001",
            "jtp invoice mark-sent INV-0001 --channel email --to jane@example.com",
            "jtp invoice set INV-0001 --payment-link https://buy.stripe.com/...",
            "jtp invoice list --status overdue",
            "jtp invoice void INV-0001 --reason 'Billed wrong customer'",
        ],
        cmd_invoice,
    )
    p.add_argument(
        "action", choices=["create", "update", "issue", "show", "list", "mark-sent", "set", "void", "delete"]
    )
    p.add_argument("id", nargs="?")
    for flag in (
        "--job",
        "--customer",
        "--spec",
        "--title",
        "--channel",
        "--to",
        "--date",
        "--due-date",
        "--reason",
        "--payment-link",
        "--notes",
        "--status",
        "--tags",
    ):
        p.add_argument(flag, help=FLAG_HELP.get(flag))
    p.add_argument("--from-quote", nargs="?", const="", default=None, help="Bill the accepted quote (optional id)")
    p.add_argument("--progress", help="Bill this percent of the accepted quote")
    p.add_argument("--deposit-invoice", help="A deposit invoice for this percent of the accepted quote")
    p.add_argument("--tm", action="store_true", help="Bill unbilled time, materials and expenses from the log")
    p.add_argument("--no-change-orders", action="store_true")
    p.add_argument("--no-extras", action="store_true")
    p.add_argument("--due-days", type=int)
    p.add_argument("--external", action="append", help="key=value id in another system, e.g. xero=INV-88")
    p.add_argument("--allow-unconfirmed-tax", action="store_true")
    p.add_argument(
        "--estimates-confirmed",
        action="store_true",
        help="The owner confirmed the lines that were estimates (issue)",
    )
    p.add_argument("--dry-run", action="store_true", help="Preview it (output/PREVIEW.pdf) without saving")
    p.add_argument("--as-spec", action="store_true")

    p = _add(
        sub,
        "credit",
        "Credit notes: the proper way to correct an issued invoice.",
        [
            'jtp credit create --invoice INV-0003 --amount 50 --reason "Goodwill discount"',
            "jtp credit create --invoice INV-0003 --spec credit.json",
            "jtp credit mark-sent CN-0001 --channel email --to jane@example.com",
            "jtp credit show CN-0001",
        ],
        cmd_credit,
    )
    p.add_argument("action", choices=["create", "show", "mark-sent"])
    p.add_argument("id", nargs="?")
    for flag in ("--invoice", "--amount", "--reason", "--spec", "--channel", "--to", "--date"):
        p.add_argument(flag, help=FLAG_HELP.get(flag))
    p.add_argument("--dry-run", action="store_true", help="Preview it (output/PREVIEW.pdf) without saving")

    p = _add(
        sub,
        "payment",
        "Record payments and deposits, list them, undo mistakes, make receipts.",
        [
            'jtp payment record INV-0003 --amount 1543.50 --method "e-Transfer" --reference CA1234',
            'jtp payment record --job J-0004 --deposit --amount 500 --method "Bank transfer"',
            'jtp payment record --job J-0004 --deposit --percent 25 --method "Bank transfer"   (25% of the accepted quote)',
            "jtp payment list --from 2026-10-01",
            "jtp payment undo PAY-0007",
            "jtp payment receipt PAY-0007",
        ],
        cmd_payment,
    )
    p.add_argument("action", choices=["record", "list", "undo", "receipt"])
    p.add_argument("id_or_invoice", nargs="?", metavar="ID")
    for flag in ("--job", "--amount", "--method", "--date", "--reference", "--note"):
        p.add_argument(flag, help=FLAG_HELP.get(flag))
    p.add_argument("--from", dest="start")
    p.add_argument("--to", dest="end")
    p.add_argument("--deposit", action="store_true", help="Money in before invoicing, held on the job (with --job)")
    p.add_argument("--percent", help="Deposit as a percent of the accepted quote, instead of --amount (e.g. 25)")
    p.add_argument("--allow-duplicate", action="store_true")
    p.add_argument("--format", default="pdf", choices=["pdf", "html", "both"])
    p.add_argument("--engine", default="auto", choices=["auto", "builtin", "chrome"])

    p = _add(
        sub,
        "render",
        "Make (or remake) the PDF/HTML for a document.",
        ["jtp render Q-0001", "jtp render INV-0004 --format both", "jtp render INV-0004 --engine chrome"],
        cmd_render,
    )
    p.add_argument("id")
    p.add_argument("--format", default="pdf", choices=["pdf", "html", "both"])
    p.add_argument("--engine", default="auto", choices=["auto", "builtin", "chrome"])
    p.add_argument("--out", help="Folder (default: <data folder>/output)")

    p = _add(
        sub,
        "price",
        "Price some lines without saving anything.",
        [
            'jtp price --spec \'[{"kind":"labour","description":"Install","qty":3},{"kind":"material","description":"Valve","qty":2,"unit_cost":18.5}]\''
        ],
        cmd_price,
    )
    p.add_argument("--spec", required=True)

    p = _add(
        sub,
        "followups",
        "What to chase today, and recording what was sent.",
        [
            "jtp followups due",
            "jtp followups done INV-0003 --stage 2 --channel email",
            "jtp followups skip Q-0007 --stage 1 --note 'Talked on the phone'",
            "jtp followups done J-0004 --stage review --channel sms",
            "jtp followups snooze INV-0003 --until 2026-10-24 --note 'Promised to pay Friday'",
        ],
        cmd_followups,
    )
    p.add_argument("action", choices=["due", "done", "skip", "snooze"])
    p.add_argument("id", nargs="?")
    for flag in ("--stage", "--channel", "--note", "--date", "--until"):
        p.add_argument(flag, help=FLAG_HELP.get(flag))

    p = _add(
        sub,
        "brief",
        "The money brief: owed, overdue, quotes waiting, unbilled work, today's follow-ups.",
        ["jtp brief", "jtp brief --text"],
        cmd_brief,
    )
    p.add_argument("--date")
    p.add_argument("--text", action="store_true", help="Plain text only")

    p = _add(
        sub,
        "report",
        "Reports: aging, profit, pricing, tax, sales.",
        [
            "jtp report aging",
            "jtp report profit --from 2026-07-01 --to 2026-09-30",
            'jtp report pricing --tag "water heater"',
            "jtp report tax --from 2026-07-01 --to 2026-09-30",
            "jtp report tax --from 2026-07-01 --to 2026-09-30 --basis cash",
            "jtp report sales --from 2026-01-01",
        ],
        cmd_report,
    )
    p.add_argument("kind", choices=["aging", "profit", "pricing", "tax", "sales"])
    p.add_argument("--from", dest="start")
    p.add_argument("--to", dest="end")
    p.add_argument("--tag")
    p.add_argument(
        "--basis",
        choices=["accrual", "cash"],
        default="accrual",
        help="tax: accrual = invoices issued in the period (default); cash = payments received in the period",
    )

    p = _add(
        sub,
        "export",
        "CSV exports for your accountant or accounting app.",
        [
            "jtp export invoices --from 2026-07-01 --to 2026-09-30",
            "jtp export payments",
            "jtp export expenses --out ~/expenses.csv",
        ],
        cmd_export,
    )
    p.add_argument("what", choices=list(reports.EXPORTS))
    p.add_argument("--from", dest="start")
    p.add_argument("--to", dest="end")
    p.add_argument("--out")

    p = _add(
        sub,
        "search",
        "Find customers, jobs and documents by any words.",
        ["jtp search jane water heater", "jtp search 555 0142"],
        cmd_search,
    )
    p.add_argument("words", nargs="+")

    p = _add(
        sub, "backup", "Zip the whole data folder.", ["jtp backup", "jtp backup --out ~/jtp-backup.zip"], cmd_backup
    )
    p.add_argument("--out")
    return parser


NO_PROFILE_NEEDED = {"init", "doctor", "demo", "selftest"}
READ_ONLY = {"doctor", "selftest", "brief", "report", "search", "price", "render"}


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        sys.exit(0)
    if args.command == "payment":
        if args.action in ("undo", "receipt"):
            args.id = args.id_or_invoice
            args.invoice = None
        else:
            args.invoice = args.id_or_invoice
    store = _store(args)
    try:
        if args.command not in NO_PROFILE_NEEDED:
            store.require()
        if args.command in ("doctor", "selftest", "demo") or args.command in READ_ONLY:
            result = args.func(store, args)
        else:
            store.home.mkdir(parents=True, exist_ok=True)
            with store.lock():
                result = args.func(store, args)
        payload = {"ok": True}
        payload.update(result or {})
        _emit(payload)
    except JTPError as exc:
        payload = {"ok": False, "error": exc.message, "code": exc.code}
        if exc.hint:
            payload["hint"] = exc.hint
        _emit(payload, 1)
    except KeyboardInterrupt:
        _emit({"ok": False, "error": "Interrupted."}, 130)


if __name__ == "__main__":  # pragma: no cover
    main()
