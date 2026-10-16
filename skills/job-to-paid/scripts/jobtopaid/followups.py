"""What to chase today: quote follow-ups, payment reminders, review requests,
service reminders, and anything created but never sent.

This only *suggests*. The agent drafts each message, the owner approves it,
and only then is it sent and marked done here.
"""

from __future__ import annotations

import datetime as _dt

from .documents import invoice_state
from .money import format_money
from .util import JTPError, add_months, from_iso, iso, parse_date, to_decimal


def _customer_card(store, customer_id: str) -> dict:
    try:
        cust = store.customer(customer_id)
    except JTPError:
        return {"id": customer_id, "name": customer_id}
    return {
        k: cust.get(k)
        for k in ("id", "name", "company", "email", "phone", "whatsapp", "preferred_contact", "language")
        if cust.get(k)
    }


def _done_stages(records: list) -> set:
    return {r.get("stage") for r in records or []}


def _pdf(store, ident: str) -> str | None:
    path = store.output_dir / f"{ident}.pdf"
    return str(path) if path.exists() else None


def _invoice_tone(stage_day: int, is_last: bool) -> str:
    if stage_day < 0:
        return "friendly_heads_up"
    if stage_day <= 1:
        return "due_now"
    if is_last:
        return "final_notice"
    if stage_day <= 14:
        return "overdue_polite"
    return "overdue_firm"


def due_actions(store, day: _dt.date | None = None) -> list:
    day = day or store.today()
    profile = store.profile()
    cfg = profile["followups"]
    disp = store.money()
    quote_days = sorted(int(d) for d in cfg.get("quote_days") or [])
    invoice_days = sorted(int(d) for d in cfg.get("invoice_days") or [])
    actions = []
    quotes = store.docs("quote")
    accepted_job_quote = {j["id"]: j.get("accepted_quote") for j in store.jobs()}
    accepted_by_customer = {}
    for doc in quotes:
        if doc["status"] == "accepted" and doc.get("accepted"):
            accepted_by_customer.setdefault(doc["customer_id"], []).append(doc)

    for doc in quotes:
        status = doc["status"]
        if accepted_job_quote.get(doc["job_id"]) not in (None, doc["id"]):
            continue  # its job went ahead with another quote
        if status == "draft":
            created = from_iso(doc.get("date"))
            if created and (day - created).days >= 1:
                actions.append(
                    {
                        "action": "send_quote",
                        "doc": doc["id"],
                        "customer": _customer_card(store, doc["customer_id"]),
                        "title": doc["title"],
                        "created": doc["date"],
                        "why": "Quote was created but never marked as sent.",
                        "pdf": _pdf(store, doc["id"]),
                    }
                )
            continue
        if status != "sent":
            continue
        valid_until = from_iso(doc.get("valid_until"))
        if valid_until and day > valid_until:
            actions.append(
                {
                    "action": "quote_expired",
                    "doc": doc["id"],
                    "customer": _customer_card(store, doc["customer_id"]),
                    "title": doc["title"],
                    "valid_until": doc["valid_until"],
                    "why": "Quote passed its valid-until date. Extend it, follow up, or mark it expired.",
                }
            )
            continue
        sent_dates = [
            from_iso(s["date"]) for s in doc.get("sent", []) if s.get("revision", 1) == doc.get("revision", 1)
        ]
        if not sent_dates or not quote_days:
            continue
        since = (day - min(sent_dates)).days
        eligible = [i + 1 for i, d in enumerate(quote_days) if since >= d]
        if not eligible:
            continue
        stage = max(eligible)
        if stage in _done_stages(doc.get("followups")) or any(
            s >= stage for s in _done_stages(doc.get("followups")) if isinstance(s, int)
        ):
            continue
        best = max(doc["options"], key=lambda o: (o.get("recommended", False), 0))
        actions.append(
            {
                "action": "quote_followup",
                "doc": doc["id"],
                "stage": stage,
                "of": len(quote_days),
                "days_since_sent": since,
                "customer": _customer_card(store, doc["customer_id"]),
                "title": doc["title"],
                "valid_until": doc.get("valid_until"),
                "total": format_money(to_decimal(best["totals"]["total"]), disp),
                "options": len(doc["options"]),
                "pdf": _pdf(store, doc["id"]),
                "tone": "check_in" if stage == 1 else ("helpful_nudge" if stage < len(quote_days) else "last_check"),
            }
        )

    for doc in store.docs("change_order"):
        if doc["status"] != "sent":
            continue
        sent = [from_iso(s["date"]) for s in doc.get("sent", [])]
        if sent and (day - min(sent)).days >= 2 and "approval" not in _done_stages(doc.get("followups")):
            actions.append(
                {
                    "action": "change_order_followup",
                    "doc": doc["id"],
                    "stage": "approval",
                    "customer": _customer_card(store, doc["customer_id"]),
                    "title": doc["title"],
                    "total": format_money(to_decimal(doc["totals"]["total"]), disp),
                    "why": "Change order is waiting for the customer's approval.",
                    "pdf": _pdf(store, doc["id"]),
                }
            )

    stop_after = int(cfg.get("stop_after_days_overdue") or 90)
    for doc in store.docs("invoice"):
        if doc["status"] != "issued":
            continue
        state = invoice_state(doc, day)
        if state["balance"] <= 0:
            continue
        card = _customer_card(store, doc["customer_id"])
        if not doc.get("sent"):
            actions.append(
                {
                    "action": "send_invoice",
                    "doc": doc["id"],
                    "customer": card,
                    "title": doc["title"],
                    "balance": format_money(state["balance"], disp),
                    "due_date": doc["due_date"],
                    "why": "Invoice is issued but was never marked as sent.",
                    "pdf": _pdf(store, doc["id"]),
                    "payment_link": doc.get("payment_link") or None,
                }
            )
            continue
        snooze = doc.get("snooze") or {}
        if snooze.get("until") and day <= from_iso(snooze["until"]):
            continue
        due = from_iso(doc["due_date"])
        rel = (day - due).days
        done = _done_stages(doc.get("followups"))
        if rel > stop_after:
            if "escalate" not in done:
                actions.append(
                    {
                        "action": "escalate",
                        "doc": doc["id"],
                        "customer": card,
                        "title": doc["title"],
                        "days_overdue": rel,
                        "balance": format_money(state["balance"], disp),
                        "why": f"Over {stop_after} days overdue. Reminders stop here; decide on a phone call, "
                        "a payment plan, or your local small-claims / collections process.",
                    }
                )
            continue
        eligible = [i + 1 for i, d in enumerate(invoice_days) if rel >= d]
        if not eligible:
            continue
        stage = max(eligible)
        if any(s >= stage for s in done if isinstance(s, int)):
            continue
        stage_day = invoice_days[stage - 1]
        timing = {"days_overdue": rel} if rel > 0 else ({"days_until_due": -rel} if rel < 0 else {})
        actions.append(
            {
                "action": "payment_reminder",
                "doc": doc["id"],
                "stage": stage,
                "of": len(invoice_days),
                "tone": _invoice_tone(stage_day, stage == len(invoice_days)),
                "due_date": doc["due_date"],
                "customer": card,
                "title": doc["title"],
                "total": format_money(state["total"], disp),
                "balance": format_money(state["balance"], disp),
                "payment_link": doc.get("payment_link") or None,
                "pdf": _pdf(store, doc["id"]),
                **timing,
            }
        )

    review_days = cfg.get("review_request_days")
    review_link = cfg.get("review_link")
    for job in store.jobs():
        done = _done_stages(job.get("followups"))
        if review_link and review_days is not None and job["status"] in ("paid", "closed") and "review" not in done:
            paid_dates = []
            for inv_id in job.get("invoices", []):
                inv = store.doc(inv_id, "invoice")
                if inv["status"] == "paid" and inv.get("paid_date"):
                    paid_dates.append(from_iso(inv["paid_date"]))
            if (
                paid_dates
                and (day - max(paid_dates)).days >= int(review_days)
                and (day - max(paid_dates)).days <= int(review_days) + 30
            ):
                actions.append(
                    {
                        "action": "review_request",
                        "job": job["id"],
                        "stage": "review",
                        "customer": _customer_card(store, job["customer_id"]),
                        "title": job["title"],
                        "review_link": review_link,
                    }
                )
        months = job.get("service_interval_months")
        completed = from_iso(job.get("completed"))
        if months and completed:
            due = add_months(completed, int(months))
            key = f"service:{iso(due)}"
            if day >= due - _dt.timedelta(days=14) and key not in done:
                actions.append(
                    {
                        "action": "service_reminder",
                        "job": job["id"],
                        "stage": key,
                        "customer": _customer_card(store, job["customer_id"]),
                        "title": job["title"],
                        "service_due": iso(due),
                        "completed": job["completed"],
                        "why": f"Last done {job['completed']}; service every {months} months.",
                    }
                )

    order = {
        "payment_reminder": 0,
        "escalate": 1,
        "send_invoice": 2,
        "change_order_followup": 3,
        "quote_followup": 4,
        "quote_expired": 5,
        "send_quote": 6,
        "service_reminder": 7,
        "review_request": 8,
    }
    actions.sort(key=lambda a: (order.get(a["action"], 9), -a.get("days_overdue", 0)))
    _flag_replaced_quotes(store, actions, quotes, accepted_by_customer)
    _decorate(store, actions)
    return actions


def _flag_replaced_quotes(store, actions: list, quotes: list, accepted_by_customer: dict) -> None:
    """A customer who accepted a newer quote (on another job) may not want the old one chased."""
    from .view import format_date  # local import avoids a cycle

    fmt = store.profile()["locale"].get("date_format")
    by_id = {d["id"]: d for d in quotes}
    for action in actions:
        doc = (
            by_id.get(action.get("doc"))
            if action["action"] in ("send_quote", "quote_followup", "quote_expired")
            else None
        )
        if not doc:
            continue
        since = min([s["date"] for s in doc.get("sent") or []] or [doc.get("date") or ""])
        later = [
            d
            for d in accepted_by_customer.get(doc["customer_id"], [])
            if d["id"] != doc["id"] and d["accepted"].get("date", "") >= since
        ]
        if later:
            other = later[-1]
            when = format_date(from_iso(other["accepted"]["date"]), fmt)
            action["heads_up"] = (
                f"The customer accepted {other['id']} ({other['title']}) on {when}. If that replaced this quote, "
                f"don't chase it: jtp quote supersede {doc['id']} --with {other['id']}. Otherwise ask the owner first."
            )


def _decorate(store, actions: list) -> None:
    """Add owner-format dates and payment instructions, so messages need no reformatting."""
    from .view import format_date  # local import avoids a cycle

    from .view import payment_lines

    fmt = store.profile()["locale"].get("date_format")
    for action in actions:
        for key in ("due_date", "valid_until", "created", "service_due", "completed"):
            if action.get(key):
                action[f"{key}_display"] = format_date(from_iso(action[key]), fmt)
        if action["action"] in ("payment_reminder", "send_invoice", "escalate"):
            action["how_to_pay"] = payment_lines(store.profile(), action.get("doc", ""))
        card = action.get("customer") or {}
        company, name = card.get("company"), card.get("name")
        card["display_name"] = (
            f"{company} ({name})" if company and name and company.lower() != name.lower() else (name or company or "")
        )


def _record(store, ident: str, record: dict) -> dict:
    target = None
    for loader, saver in ((lambda: store.doc(ident), store.save_doc), (lambda: store.job(ident), store.save_job)):
        try:
            target = loader()
        except JTPError:
            continue
        target.setdefault("followups", []).append(record)
        saver(target)
        store.log("followup", target["id"], **record)
        return target
    raise JTPError(f"No quote, invoice, change order or job {ident}.")


def _stage_value(stage):
    if stage is None:
        raise JTPError("Say which stage (from `jtp followups due`).")
    try:
        return int(stage)
    except (TypeError, ValueError):
        return str(stage)


def mark_done(store, ident: str, stage, channel: str | None, note: str | None, skipped=False, date=None) -> dict:
    record = {
        "stage": _stage_value(stage),
        "date": iso(parse_date(date or "today", store.today())),
        "channel": (channel or "").lower(),
        "status": "skipped" if skipped else "done",
        "note": note or "",
    }
    target = _record(store, ident, record)
    return {"id": target["id"], "recorded": record}


def snooze(store, ident: str, until, note: str | None) -> dict:
    doc = store.doc(ident, "invoice")
    day = parse_date(until, store.today())
    doc["snooze"] = {"until": iso(day), "note": note or "", "set": iso(store.today())}
    store.save_doc(doc)
    store.log("followup.snooze", doc["id"], until=iso(day), note=note)
    return {"invoice": doc["id"], "snoozed_until": iso(day)}
