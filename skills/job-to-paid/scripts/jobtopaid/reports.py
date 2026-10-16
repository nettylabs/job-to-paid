"""Money brief, receivables aging, job profit, pricing insight, tax collected,
sales, CSV exports and search."""

from __future__ import annotations

import csv
import datetime as _dt
from collections import OrderedDict, defaultdict
from decimal import Decimal
from pathlib import Path

from . import profile as profile_mod
from .documents import invoice_state, log_entry_specs, priced
from .followups import due_actions
from .money import HUNDRED, ZERO, compute_totals, format_money, q
from .pricing import cost_before_tax, normalize_lines
from .util import JTPError, from_iso, iso, parse_date, to_decimal, trim_decimal


def _day(store, text) -> str:
    """A date in the owner's format (e.g. 15/10/2026)."""
    from .view import format_date  # local import avoids a cycle

    value = from_iso(text) if isinstance(text, str) else text
    return format_date(value, store.profile()["locale"].get("date_format")) if value else ""


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


ACTION_WORDS = {
    "payment_reminder": "payment reminder",
    "escalate": "escalation",
    "send_invoice": "unsent invoice",
    "change_order_followup": "change order follow-up",
    "quote_followup": "quote follow-up",
    "quote_expired": "expired quote",
    "send_quote": "unsent quote",
    "service_reminder": "service reminder",
    "review_request": "review request",
}


def _fmt(store, value) -> str:
    return format_money(to_decimal(value), store.money())


def _period(store, start, end, default_days=None) -> tuple:
    today = store.today()
    end_d = parse_date(end, today) if end else today
    if start:
        start_d = parse_date(start, today)
    elif default_days:
        start_d = end_d - _dt.timedelta(days=default_days)
    else:
        start_d = _dt.date(end_d.year, 1, 1)
    if start_d > end_d:
        raise JTPError("The start date is after the end date.")
    return start_d, end_d


# ------------------------------------------------------------- job money


def unbilled_value(store, job: dict) -> Decimal:
    entries = [
        e
        for e in job["log"]
        if e["type"] in ("time", "material", "expense") and e.get("billable") and not e.get("invoice")
    ]
    if not entries:
        return ZERO
    specs, _ = log_entry_specs(store, job, entries, None)
    try:
        lines, _, _ = normalize_lines(store.profile(), store.pricebook(), specs, store.places, "U")
    except JTPError:
        return ZERO
    return sum((to_decimal(ln["amount"]) for ln in lines if "amount" in ln), ZERO)


def _planned_costs(store, job: dict) -> tuple:
    """(labour, materials) cost already priced into the accepted quote, approved change orders and
    any hand-written invoice lines, from the cost figures stored on those lines."""
    lines = []
    if job.get("accepted_quote"):
        quote = store.doc(job["accepted_quote"], "quote")
        lines += next(o for o in quote["options"] if o["key"] == quote["accepted"]["option"])["lines"]
    for co_id in job.get("change_orders", []):
        co = store.doc(co_id, "change_order")
        if co["status"] == "approved":
            lines += co["lines"]
    for inv_id in job.get("invoices", []):
        inv = store.doc(inv_id, "invoice")
        if inv["status"] != "void":
            lines += [ln for ln in inv["lines"] if not ln.get("source")]
    labour = materials = ZERO
    for ln in priced(lines):
        if ln.get("unit_cost") in (None, ""):
            continue
        cost = to_decimal(ln["qty"]) * to_decimal(ln["unit_cost"])
        if ln["kind"] == "labour":
            labour += cost
        elif ln["kind"] in ("material", "equipment", "subcontract"):
            materials += cost
    return labour, materials


def _cost_ex(profile: dict, kind: str, value) -> Decimal:
    return cost_before_tax(profile, kind, to_decimal(value))


def job_costs(store, job: dict) -> dict:
    """Actual costs from the work log. For quoted jobs, materials fall back to the quote's own cost
    figures when fewer receipts were logged than were priced in (so margins aren't overstated)."""
    profile = store.profile()
    places = store.places
    hours = ZERO
    labour_cost = ZERO
    labour_cost_known = True
    materials = ZERO
    expenses = ZERO
    for entry in job["log"]:
        if entry["type"] == "time":
            h = to_decimal(entry["hours"])
            hours += h
            try:
                rate = profile_mod.labour_rate(profile, entry.get("rate_key"))
                cost = rate.get("cost") or profile["rates"].get("internal_labour_cost")
            except JTPError:
                cost = profile["rates"].get("internal_labour_cost")
            if cost not in (None, ""):
                labour_cost += h * to_decimal(cost)
            else:
                labour_cost_known = False
        elif entry["type"] == "material" and entry.get("unit_cost") not in (None, ""):
            materials += to_decimal(entry["qty"]) * _cost_ex(profile, "material", entry["unit_cost"])
        elif entry["type"] == "expense":
            expenses += _cost_ex(profile, "other", entry["amount"])
    basis = "logged"
    planned_labour, planned_materials = _planned_costs(store, job)
    if hours == 0 and planned_labour > 0:
        labour_cost = planned_labour
        labour_cost_known = True
        basis = "estimated from the priced lines (no time logged)"
    logged_planned_scope = sum(
        (
            to_decimal(e["qty"]) * _cost_ex(profile, "material", e["unit_cost"])
            for e in job["log"]
            if e["type"] == "material" and not e.get("extra") and e.get("unit_cost")
        ),
        ZERO,
    )
    if planned_materials > logged_planned_scope:
        materials += planned_materials - logged_planned_scope
        basis = "logged, plus quoted cost for materials without receipts" if basis == "logged" else basis
    return {
        "hours": hours,
        "labour_cost": q(labour_cost, places),
        "labour_cost_known": labour_cost_known,
        "materials": q(materials, places),
        "expenses": q(expenses, places),
        "materials_basis": basis,
        "total": q(labour_cost + materials + expenses, places),
    }


def job_summary(store, job_id: str) -> dict:
    job = store.job(job_id)
    day = store.today()
    quoted = None
    quoted_hours = None
    if job.get("accepted_quote"):
        quote = store.doc(job["accepted_quote"], "quote")
        option = next(o for o in quote["options"] if o["key"] == quote["accepted"]["option"])
        quoted = to_decimal(option["totals"]["total"])
        quoted_hours = to_decimal(option["totals"].get("labour_hours") or "0")
    cos = [store.doc(c, "change_order") for c in job.get("change_orders", [])]
    approved = sum((to_decimal(c["totals"]["total"]) for c in cos if c["status"] == "approved"), ZERO)
    if quoted_hours is not None:
        # Approved change orders add agreed hours, so they count as "quoted" too.
        quoted_hours += sum(
            (to_decimal(c["totals"].get("labour_hours") or "0") for c in cos if c["status"] == "approved"), ZERO
        )
    pending = [c["id"] for c in cos if c["status"] in ("draft", "sent")]
    invoiced = paid = credits = owing = net_sales = ZERO
    invoices = []
    for inv_id in job.get("invoices", []):
        inv = store.doc(inv_id, "invoice")
        state = invoice_state(inv, day)
        invoices.append(
            {
                "id": inv["id"],
                "kind": inv.get("kind"),
                "status": state["status"],
                "total": _fmt(store, state["total"]),
                "balance": _fmt(store, state["balance"]),
            }
        )
        if inv["status"] in ("issued", "paid"):
            invoiced += state["total"]
            paid += state["paid"]
            credits += state["credits"]
            owing += max(state["balance"], ZERO)
            net_sales += to_decimal(inv["totals"]["net_excl_tax"])
    for cn_id in job.get("credit_notes", []):
        cn = store.doc(cn_id, "credit_note")
        net_sales -= to_decimal(cn["totals"]["net_excl_tax"])
    deposits = sum((to_decimal(p.get("remaining", "0")) for p in job.get("payments", [])), ZERO)
    received = paid + deposits
    costs = job_costs(store, job)
    unbilled = unbilled_value(store, job)
    out = {
        "job": job["id"],
        "title": job["title"],
        "status": job["status"],
        "pricing": job.get("pricing"),
        "customer_id": job["customer_id"],
        "quoted_total": _fmt(store, quoted) if quoted is not None else None,
        "approved_change_orders": _fmt(store, approved),
        "change_orders_waiting": pending,
        "invoiced": _fmt(store, invoiced),
        "received": _fmt(store, received),
        "paid_on_invoices": _fmt(store, paid),
        "deposits_held": _fmt(store, deposits),
        "credits": _fmt(store, credits),
        "owing": _fmt(store, owing),
        "unbilled_logged_work": _fmt(store, unbilled),
        "invoices": invoices,
        "hours_logged": trim_decimal(costs["hours"]),
        "hours_quoted": trim_decimal(quoted_hours) if quoted_hours else None,
        "costs": _fmt(store, costs["total"]),
        "costs_breakdown": {
            "labour": _fmt(store, costs["labour_cost"]),
            "materials": _fmt(store, costs["materials"]),
            "expenses": _fmt(store, costs["expenses"]),
            "materials_basis": costs["materials_basis"],
        },
        "net_sales_excl_tax": _fmt(store, net_sales),
    }
    parts = []
    if quoted is not None:
        parts.append(
            f"quoted {_fmt(store, quoted)}" + (f" + approved changes {_fmt(store, approved)}" if approved else "")
        )
    parts.append(f"invoiced {_fmt(store, invoiced)}")
    held_text = f" (incl. {_fmt(store, deposits)} deposit not yet invoiced)" if deposits else ""
    parts.append(f"received {_fmt(store, received)}{held_text}")
    parts.append(f"owing {_fmt(store, owing)}")
    out["summary"] = f"{job['id']} {job['title']}: " + ", ".join(parts)
    if net_sales and costs["total"]:
        margin = net_sales - costs["total"]
        out["margin"] = _fmt(store, margin)
        out["margin_percent"] = trim_decimal(q(margin / net_sales * HUNDRED, 1))
        if not costs["labour_cost_known"]:
            out["margin_note"] = "Labour cost unknown for some hours (set a cost on your labour rates)."
    if quoted_hours and costs["hours"]:
        out["hours_vs_quote_percent"] = trim_decimal(q((costs["hours"] - quoted_hours) / quoted_hours * HUNDRED, 1))
    return out


def customer_summary(store, customer_id: str) -> dict:
    cust = store.customer(customer_id)
    day = store.today()
    jobs = [j for j in store.jobs() if j["customer_id"] == cust["id"]]
    lifetime = owing = held = ZERO
    history = []
    for job in jobs:
        held += sum((to_decimal(p.get("remaining", "0")) for p in job.get("payments", [])), ZERO)
        for inv_id in job.get("invoices", []):
            inv = store.doc(inv_id, "invoice")
            if inv["status"] not in ("issued", "paid"):
                continue
            state = invoice_state(inv, day)
            lifetime += state["total"] - state["credits"]
            owing += max(state["balance"], ZERO)
        history.append({"job": job["id"], "title": job["title"], "status": job["status"], "created": job["created"]})
    return {
        "customer": cust,
        "jobs": history,
        "lifetime_billed": _fmt(store, lifetime),
        "owing": _fmt(store, owing),
        "deposits_held": _fmt(store, held),
    }


# ------------------------------------------------------------------ brief


def brief(store, day=None) -> dict:
    day = day or store.today()
    disp_fmt = lambda v: _fmt(store, v)  # noqa: E731
    open_invoices = []
    overdue_total = owing_total = ZERO
    for inv in store.docs("invoice"):
        if inv["status"] != "issued":
            continue
        state = invoice_state(inv, day)
        if state["balance"] <= 0:
            continue
        owing_total += state["balance"]
        cust = _customer_name(store, inv["customer_id"])
        row = {
            "id": inv["id"],
            "customer": cust,
            "balance": disp_fmt(state["balance"]),
            "due_date": inv["due_date"],
            "due_date_display": _day(store, inv["due_date"]),
            "days_overdue": state["days_overdue"],
        }
        if state.get("overdue"):
            overdue_total += state["balance"]
        open_invoices.append(row)
    open_invoices.sort(key=lambda r: -r["days_overdue"])
    overdue = [r for r in open_invoices if r["days_overdue"] > 0]

    quotes_waiting = []
    waiting_total = ZERO
    accepted_job_quote = {j["id"]: j.get("accepted_quote") for j in store.jobs()}
    for doc in store.docs("quote"):
        if doc["status"] != "sent" or accepted_job_quote.get(doc["job_id"]) not in (None, doc["id"]):
            continue
        best = max(doc["options"], key=lambda o: (o.get("recommended", False), 0))
        total = to_decimal(best["totals"]["total"])
        waiting_total += total
        expires = from_iso(doc.get("valid_until"))
        quotes_waiting.append(
            {
                "id": doc["id"],
                "customer": _customer_name(store, doc["customer_id"]),
                "title": doc["title"],
                "total": disp_fmt(total),
                "valid_until": doc.get("valid_until"),
                "valid_until_display": _day(store, doc.get("valid_until")),
                "expires_in_days": (expires - day).days if expires else None,
            }
        )

    unbilled_jobs = []
    unbilled_total = ZERO
    for job in store.jobs():
        if job["status"] in ("lost", "cancelled", "closed"):
            continue
        value = unbilled_value(store, job)
        has_final = any(
            store.doc(i, "invoice")["status"] != "void"
            and store.doc(i, "invoice").get("kind") in ("final", "tm", "standard")
            for i in job.get("invoices", [])
        )
        approved_cos = [
            c
            for c in job.get("change_orders", [])
            if store.doc(c, "change_order")["status"] == "approved" and not store.doc(c, "change_order").get("invoice")
        ]
        reason = []
        if job["status"] == "complete" and not has_final:
            reason.append("finished but not invoiced")
        if value > 0:
            reason.append(f"{disp_fmt(value)} of logged work not billed")
        if approved_cos and has_final:
            reason.append(f"{_plural(len(approved_cos), 'approved change order')} not billed")
        if job.get("accepted_quote") and not has_final and job["status"] == "complete":
            quote = store.doc(job["accepted_quote"], "quote")
            option = next(o for o in quote["options"] if o["key"] == quote["accepted"]["option"])
            value += to_decimal(option["totals"]["total"])
        if reason:
            unbilled_total += value
            unbilled_jobs.append(
                {
                    "job": job["id"],
                    "title": job["title"],
                    "customer": _customer_name(store, job["customer_id"]),
                    "why": "; ".join(reason),
                }
            )

    week_start = day - _dt.timedelta(days=day.weekday())
    month_start = day.replace(day=1)
    paid_week = paid_month = ZERO
    for inv in store.docs("invoice"):
        for p in inv.get("payments", []):
            if p.get("from_deposit"):
                continue
            d = from_iso(p["date"])
            if d and month_start <= d <= day:
                paid_month += to_decimal(p["amount"])
            if d and week_start <= d <= day:
                paid_week += to_decimal(p["amount"])
    for job in store.jobs():
        for p in job.get("payments", []):
            d = from_iso(p["date"])
            if d and month_start <= d <= day:
                paid_month += to_decimal(p["amount"])
            if d and week_start <= d <= day:
                paid_week += to_decimal(p["amount"])

    actions = due_actions(store, day)
    counts = defaultdict(int)
    for action in actions:
        counts[action["action"]] += 1
    over_budget = []
    for job in store.jobs():
        if job["status"] not in ("in_progress", "complete") or not job.get("accepted_quote"):
            continue
        summary = job_summary(store, job["id"])
        pct = summary.get("hours_vs_quote_percent")
        if pct and to_decimal(pct) >= 15:
            over_budget.append(
                {
                    "job": job["id"],
                    "title": job["title"],
                    "hours_logged": summary["hours_logged"],
                    "hours_quoted": summary["hours_quoted"],
                    "over_percent": pct,
                }
            )

    check = profile_mod.check(store.profile())
    lines = [f"Money brief: {_day(store, day)}"]
    if open_invoices:
        lines.append(
            f"Owed to you: {disp_fmt(owing_total)} on {_plural(len(open_invoices), 'invoice')}"
            + (f", {disp_fmt(overdue_total)} overdue on {len(overdue)}" if overdue else "")
        )
    else:
        lines.append("Owed to you: nothing outstanding")
    for row in overdue[:3]:
        lines.append(
            f"  - {row['id']} {row['customer']}: {row['balance']}, {_plural(row['days_overdue'], 'day')} overdue"
        )
    if quotes_waiting:
        lines.append(f"Quotes waiting on customers: {len(quotes_waiting)}, worth {disp_fmt(waiting_total)}")
        for row in quotes_waiting:
            if row["expires_in_days"] is not None and 0 <= row["expires_in_days"] <= 3:
                lines.append(f"  - {row['id']} {row['customer']} expires in {_plural(row['expires_in_days'], 'day')}")
    if unbilled_jobs:
        lines.append(f"Not billed yet: {_plural(len(unbilled_jobs), 'job')}")
        for row in unbilled_jobs[:5]:
            lines.append(f"  - {row['job']} {row['customer']}: {row['why']}")
    for row in over_budget[:3]:
        lines.append(
            f"Over quoted hours: {row['job']} {row['title']} ({row['hours_logged']} h vs "
            f"{row['hours_quoted']} h quoted)"
        )
    lines.append(f"Paid this week: {disp_fmt(paid_week)}; this month: {disp_fmt(paid_month)}")
    ready = [a for a in actions if not a.get("heads_up")]
    held = [a for a in actions if a.get("heads_up")]
    if ready:
        ready_counts = defaultdict(int)
        for action in ready:
            ready_counts[action["action"]] += 1
        parts = ", ".join(
            _plural(n, ACTION_WORDS.get(name, name.replace("_", " "))) for name, n in ready_counts.items()
        )
        lines.append(f"Ready for your OK today: {parts}")
    if held:
        lines.append("Check with you first (don't chase yet):")
        for action in held[:3]:
            other = action.get("accepted_instead") or {}
            who = (action.get("customer") or {}).get("display_name") or "The customer"
            lines.append(
                f"  - {action['doc']}: {who} accepted {other.get('quote')} on {other.get('date_display')}. "
                f"Is {action['doc']} still open, or was it replaced?"
            )
    if not actions:
        lines.append("Nothing to chase today.")
    if check["missing"]:
        lines.append("Setup still needed: " + "; ".join(check["missing"]))
    return {
        "date": iso(day),
        "owed": {
            "total": disp_fmt(owing_total),
            "count": len(open_invoices),
            "overdue_total": disp_fmt(overdue_total),
            "overdue": overdue,
        },
        "quotes_waiting": quotes_waiting,
        "quotes_waiting_total": disp_fmt(waiting_total),
        "not_billed": unbilled_jobs,
        "over_quoted_hours": over_budget,
        "paid_this_week": disp_fmt(paid_week),
        "paid_this_month": disp_fmt(paid_month),
        "actions": actions,
        "action_counts": dict(counts),
        "setup_missing": check["missing"],
        "text": "\n".join(lines),
    }


def _customer_name(store, customer_id: str) -> str:
    """Company first for business customers: \"Bob's Café (Bob)\"."""
    try:
        cust = store.customer(customer_id)
    except JTPError:
        return customer_id
    company, name = cust.get("company"), cust.get("name", "")
    if company and company.strip().lower() != name.strip().lower():
        return f"{company} ({name})" if name else company
    return name or company or customer_id


# ------------------------------------------------------------------ aging


def aging(store, day=None) -> dict:
    day = day or store.today()
    buckets = OrderedDict((k, ZERO) for k in ("current", "1-30", "31-60", "61-90", "90+"))
    rows = []
    for inv in store.docs("invoice"):
        if inv["status"] != "issued":
            continue
        state = invoice_state(inv, day)
        if state["balance"] <= 0:
            continue
        late = state["days_overdue"]
        key = (
            "current"
            if late <= 0
            else "1-30"
            if late <= 30
            else "31-60"
            if late <= 60
            else "61-90"
            if late <= 90
            else "90+"
        )
        buckets[key] += state["balance"]
        rows.append(
            {
                "invoice": inv["id"],
                "customer": _customer_name(store, inv["customer_id"]),
                "due_date": inv["due_date"],
                "days_overdue": late,
                "bucket": key,
                "balance": _fmt(store, state["balance"]),
            }
        )
    rows.sort(key=lambda r: -r["days_overdue"])
    total = sum(buckets.values(), ZERO)
    return {
        "date": iso(day),
        "buckets": {k: _fmt(store, v) for k, v in buckets.items()},
        "total": _fmt(store, total),
        "invoices": rows,
    }


# ----------------------------------------------------------------- profit


def profit(store, start=None, end=None) -> dict:
    start_d, end_d = _period(store, start, end, default_days=90)
    rows = []
    tag_stats = defaultdict(lambda: {"jobs": 0, "sales": ZERO, "costs": ZERO, "over": []})
    total_sales = total_costs = ZERO
    for job in store.jobs():
        dates = []
        for inv_id in job.get("invoices", []):
            inv = store.doc(inv_id, "invoice")
            if inv["status"] in ("issued", "paid") and inv.get("date"):
                dates.append(from_iso(inv["date"]))
        if not dates or not (start_d <= max(dates) <= end_d):
            continue
        s = job_summary(store, job["id"])
        costs = job_costs(store, job)
        net_sales = ZERO
        for inv_id in job.get("invoices", []):
            inv = store.doc(inv_id, "invoice")
            if inv["status"] in ("issued", "paid"):
                net_sales += to_decimal(inv["totals"]["net_excl_tax"])
        for cn_id in job.get("credit_notes", []):
            net_sales -= to_decimal(store.doc(cn_id, "credit_note")["totals"]["net_excl_tax"])
        margin = net_sales - costs["total"]
        row = {
            "job": job["id"],
            "title": job["title"],
            "customer": _customer_name(store, job["customer_id"]),
            "sales_excl_tax": _fmt(store, net_sales),
            "costs": _fmt(store, costs["total"]),
            "margin": _fmt(store, margin),
            "margin_percent": trim_decimal(q(margin / net_sales * HUNDRED, 1)) if net_sales else None,
            "hours": trim_decimal(costs["hours"]),
            "hours_vs_quote_percent": s.get("hours_vs_quote_percent"),
        }
        if not costs["labour_cost_known"]:
            row["note"] = "Some labour has no cost rate; margin is overstated."
        rows.append(row)
        total_sales += net_sales
        total_costs += costs["total"]
        for tag in job.get("tags") or ["untagged"]:
            stats = tag_stats[tag]
            stats["jobs"] += 1
            stats["sales"] += net_sales
            stats["costs"] += costs["total"]
            if s.get("hours_vs_quote_percent") is not None:
                stats["over"].append(to_decimal(s["hours_vs_quote_percent"]))
    by_tag = {}
    for tag, stats in tag_stats.items():
        margin = stats["sales"] - stats["costs"]
        by_tag[tag] = {
            "jobs": stats["jobs"],
            "sales_excl_tax": _fmt(store, stats["sales"]),
            "margin": _fmt(store, margin),
            "margin_percent": trim_decimal(q(margin / stats["sales"] * HUNDRED, 1)) if stats["sales"] else None,
            "avg_hours_vs_quote_percent": trim_decimal(q(sum(stats["over"], ZERO) / len(stats["over"]), 1))
            if stats["over"]
            else None,
        }
    margin = total_sales - total_costs
    return {
        "from": iso(start_d),
        "to": iso(end_d),
        "jobs": rows,
        "by_tag": by_tag,
        "sales_excl_tax": _fmt(store, total_sales),
        "costs": _fmt(store, total_costs),
        "margin": _fmt(store, margin),
        "margin_percent": trim_decimal(q(margin / total_sales * HUNDRED, 1)) if total_sales else None,
    }


def pricing_insight(store, tag: str | None = None) -> dict:
    """How long jobs of a kind really take vs. what was quoted, to price the next one."""
    samples = []
    for job in store.jobs():
        if tag and tag.lower() not in (job.get("tags") or []):
            continue
        if not job.get("accepted_quote") or job["status"] not in ("complete", "invoiced", "paid", "closed"):
            continue
        s = job_summary(store, job["id"])
        if not s.get("hours_quoted") or s["hours_logged"] in (None, "0"):
            continue
        samples.append(
            {
                "job": job["id"],
                "title": job["title"],
                "hours_quoted": s["hours_quoted"],
                "hours_logged": s["hours_logged"],
                "over_percent": s.get("hours_vs_quote_percent"),
            }
        )
    if not samples:
        return {"tag": tag, "samples": [], "advice": "Not enough finished, quoted jobs with logged hours yet."}
    actual = [to_decimal(s["hours_logged"]) for s in samples]
    quoted = [to_decimal(s["hours_quoted"]) for s in samples]
    avg_actual = sum(actual, ZERO) / len(actual)
    avg_quoted = sum(quoted, ZERO) / len(quoted)
    over = (avg_actual - avg_quoted) / avg_quoted * HUNDRED if avg_quoted else ZERO
    advice = (
        f"Across {len(samples)} job(s), work took {trim_decimal(q(avg_actual, 1))} h on average vs "
        f"{trim_decimal(q(avg_quoted, 1))} h quoted ({trim_decimal(q(over, 1))}%)."
    )
    if over >= 10:
        advice += f" Consider quoting about {trim_decimal(q(avg_actual, 1))} h for this kind of job."
    elif over <= -10:
        advice += " You're finishing faster than quoted; your quotes have room to be more competitive."
    return {
        "tag": tag,
        "samples": samples,
        "avg_hours_logged": trim_decimal(q(avg_actual, 2)),
        "avg_hours_quoted": trim_decimal(q(avg_quoted, 2)),
        "over_percent": trim_decimal(q(over, 1)),
        "advice": advice,
    }


# -------------------------------------------------------------------- tax


def tax_report(store, start=None, end=None, basis: str = "accrual") -> dict:
    """Tax charged in a period, per tax.

    accrual: invoices issued in the period, less credit notes issued in it.
    cash:    tax inside the payments received in the period (each payment split in
             the same proportions as its invoice, after credit notes).
    """
    if basis not in ("accrual", "cash"):
        raise JTPError("basis must be accrual or cash.")
    start_d, end_d = _period(store, start, end, default_days=90)
    taxes = OrderedDict()
    sales_excl = sales_incl = ZERO
    count = 0
    unallocated = ZERO

    def add_tax(code, name, taxable, tax):
        entry = taxes.setdefault(code, {"name": name, "taxable": ZERO, "tax": ZERO})
        entry["taxable"] += taxable
        entry["tax"] += tax

    if basis == "accrual":
        for inv in store.docs("invoice"):
            if inv["status"] not in ("issued", "paid", "void") or not inv.get("date"):
                continue
            if start_d <= from_iso(inv["date"]) <= end_d:
                count += 1
                sales_excl += to_decimal(inv["totals"]["net_excl_tax"])
                sales_incl += to_decimal(inv["totals"]["total"])
                for row in inv["totals"].get("taxes", []):
                    add_tax(row["code"], row["name"], to_decimal(row["base"]), to_decimal(row["amount"]))
        for cn in store.docs("credit_note"):
            if start_d <= from_iso(cn["date"]) <= end_d:
                sales_excl -= to_decimal(cn["totals"]["net_excl_tax"])
                sales_incl -= to_decimal(cn["totals"]["total"])
                for row in cn["totals"].get("taxes", []):
                    add_tax(row["code"], row["name"], -to_decimal(row["base"]), -to_decimal(row["amount"]))
    else:
        credit_notes = {}
        for cn in store.docs("credit_note"):
            credit_notes.setdefault(cn["invoice_id"], []).append(cn)
        for inv in store.docs("invoice"):
            payments = [p for p in inv.get("payments", []) if start_d <= from_iso(p["date"]) <= end_d]
            if not payments:
                continue
            total = to_decimal(inv["totals"]["total"])
            net = to_decimal(inv["totals"]["net_excl_tax"])
            rows = {
                r["code"]: [r["name"], to_decimal(r["base"]), to_decimal(r["amount"])]
                for r in inv["totals"].get("taxes", [])
            }
            for cn in credit_notes.get(inv["id"], []):
                total -= to_decimal(cn["totals"]["total"])
                net -= to_decimal(cn["totals"]["net_excl_tax"])
                for r in cn["totals"].get("taxes", []):
                    entry = rows.setdefault(r["code"], [r["name"], ZERO, ZERO])
                    entry[1] -= to_decimal(r["base"])
                    entry[2] -= to_decimal(r["amount"])
            if total <= 0:
                continue
            count += 1
            for p in payments:
                share = to_decimal(p["amount"]) / total
                sales_incl += to_decimal(p["amount"])
                sales_excl += net * share
                for code, (name, base, amount) in rows.items():
                    add_tax(code, name, base * share, amount * share)
        for job in store.jobs():
            for p in job.get("payments", []):
                if start_d <= from_iso(p["date"]) <= end_d:
                    unallocated += to_decimal(p.get("remaining", "0"))
    places = store.places
    out = {
        "from": iso(start_d),
        "to": iso(end_d),
        "basis": basis,
        "basis_note": "invoices issued in the period, less credit notes"
        if basis == "accrual"
        else "payments received in the period, split like their invoices",
        "invoices": count,
        "sales_incl_tax": _fmt(store, q(sales_incl, places)),
        "sales_excl_tax": _fmt(store, q(sales_excl, places)),
        "taxes": {
            code: {
                "name": t["name"],
                "taxable_sales": _fmt(store, q(t["taxable"], places)),
                "tax_charged": _fmt(store, q(t["tax"], places)),
            }
            for code, t in taxes.items()
        },
        "note": "Check this against your accountant's numbers before you file. Use --basis cash if you report "
        "tax on payments received rather than on invoices issued.",
    }
    if basis == "cash" and unallocated:
        out["deposits_not_yet_invoiced"] = _fmt(store, unallocated)
        out["note"] += (
            " Deposits not yet on an invoice aren't split into tax here; ask your accountant how to report them."
        )
    return out


def sales_report(store, start=None, end=None) -> dict:
    start_d, end_d = _period(store, start, end)
    months = OrderedDict()
    for inv in store.docs("invoice"):
        if inv["status"] in ("issued", "paid") and inv.get("date"):
            d = from_iso(inv["date"])
            if start_d <= d <= end_d:
                m = months.setdefault(d.strftime("%Y-%m"), {"invoiced": ZERO, "collected": ZERO})
                m["invoiced"] += to_decimal(inv["totals"]["total"])
        for p in inv.get("payments", []):
            if p.get("from_deposit"):
                continue
            d = from_iso(p["date"])
            if start_d <= d <= end_d:
                months.setdefault(d.strftime("%Y-%m"), {"invoiced": ZERO, "collected": ZERO})["collected"] += (
                    to_decimal(p["amount"])
                )
    for job in store.jobs():
        for p in job.get("payments", []):
            d = from_iso(p["date"])
            if start_d <= d <= end_d:
                months.setdefault(d.strftime("%Y-%m"), {"invoiced": ZERO, "collected": ZERO})["collected"] += (
                    to_decimal(p["amount"])
                )
    quotes = [d for d in store.docs("quote") if d.get("date") and start_d <= from_iso(d["date"]) <= end_d]
    won = [d for d in quotes if d["status"] == "accepted"]
    decided = [d for d in quotes if d["status"] in ("accepted", "declined", "expired")]
    days_to_pay = []
    for inv in store.docs("invoice"):
        if inv["status"] == "paid" and inv.get("date") and inv.get("paid_date"):
            days_to_pay.append((from_iso(inv["paid_date"]) - from_iso(inv["date"])).days)
    return {
        "from": iso(start_d),
        "to": iso(end_d),
        "by_month": {
            k: {"invoiced": _fmt(store, v["invoiced"]), "collected": _fmt(store, v["collected"])}
            for k, v in sorted(months.items())
        },
        "quotes_sent": len([d for d in quotes if d["status"] != "draft"]),
        "quotes_won": len(won),
        "win_rate_percent": trim_decimal(q(Decimal(len(won)) / len(decided) * HUNDRED, 1)) if decided else None,
        "avg_days_to_pay": trim_decimal(q(Decimal(sum(days_to_pay)) / len(days_to_pay), 1)) if days_to_pay else None,
    }


# ----------------------------------------------------------------- export

EXPORTS = ("invoices", "lines", "payments", "customers", "expenses", "quotes")


def _line_row(kind: str, doc: dict, cust: dict, ln: dict, sign: int) -> dict:
    def signed(value):
        return str(sign * to_decimal(value)) if value not in (None, "") else ""

    return {
        "type": kind,
        "document": doc["id"],
        "date": doc["date"],
        "customer": cust.get("name", ""),
        "customer_company": cust.get("company", ""),
        "kind": ln["kind"],
        "description": ln.get("description", ""),
        "qty": ln.get("qty", ""),
        "unit": ln.get("unit", ""),
        "unit_price": ln.get("unit_price", ""),
        "amount": signed(ln.get("amount")),
        "amount_after_discount": signed(ln.get("net", ln.get("amount"))),
        "net_excl_tax": signed(ln.get("net_excl_tax", ln.get("net", ln.get("amount")))),
        "tax_codes": " ".join(ln.get("tax_codes") or []),
        "section": ln.get("section") or "",
    }


def export(store, what: str, out: str | None, start=None, end=None) -> dict:
    if what not in EXPORTS:
        raise JTPError(f"Can't export {what!r}.", hint=f"Choose one of: {', '.join(EXPORTS)}")
    start_d, end_d = _period(store, start, end, default_days=3650)
    in_range = lambda text: bool(text) and start_d <= from_iso(text) <= end_d  # noqa: E731
    customers = {c["id"]: c for c in store.customers()}
    rows = []
    if what in ("invoices", "lines"):
        for inv in store.docs("invoice"):
            if inv["status"] not in ("issued", "paid", "void") or not in_range(inv.get("date")):
                continue
            state = invoice_state(inv, store.today())
            cust = customers.get(inv["customer_id"], {})
            if what == "invoices":
                row = {
                    "type": "invoice",
                    "invoice": inv["id"],
                    "credits_invoice": "",
                    "status": inv["status"],
                    "date": inv["date"],
                    "due_date": inv["due_date"],
                    "customer": cust.get("name", ""),
                    "customer_company": cust.get("company", ""),
                    "customer_email": cust.get("email", ""),
                    "job": inv["job_id"],
                    "title": inv["title"],
                    "net_excl_tax": inv["totals"]["net_excl_tax"],
                    "tax_total": inv["totals"]["tax_total"],
                    "total": inv["totals"]["total"],
                    "paid": str(state["paid"]),
                    "credits": str(state["credits"]),
                    "balance": str(state["balance"]),
                    "currency": store.money()["code"],
                }
                for tax in inv["totals"].get("taxes", []):
                    row[f"tax_{tax['code']}"] = tax["amount"]
                rows.append(row)
            else:
                for ln in inv["lines"]:
                    if ln.get("kind") == "text":
                        continue
                    rows.append(_line_row("invoice", inv, cust, ln, 1))
        for cn in store.docs("credit_note"):
            if not in_range(cn.get("date")):
                continue
            cust = customers.get(cn["customer_id"], {})
            if what == "invoices":
                row = {
                    "type": "credit_note",
                    "invoice": cn["id"],
                    "credits_invoice": cn["invoice_id"],
                    "status": "issued",
                    "date": cn["date"],
                    "due_date": "",
                    "customer": cust.get("name", ""),
                    "customer_company": cust.get("company", ""),
                    "customer_email": cust.get("email", ""),
                    "job": cn["job_id"],
                    "title": cn.get("reason") or cn.get("title", ""),
                    "net_excl_tax": str(-to_decimal(cn["totals"]["net_excl_tax"])),
                    "tax_total": str(-to_decimal(cn["totals"]["tax_total"])),
                    "total": str(-to_decimal(cn["totals"]["total"])),
                    "paid": "",
                    "credits": "",
                    "balance": "",
                    "currency": store.money()["code"],
                }
                for tax in cn["totals"].get("taxes", []):
                    row[f"tax_{tax['code']}"] = str(-to_decimal(tax["amount"]))
                rows.append(row)
            else:
                for ln in cn["lines"]:
                    if ln.get("kind") != "text":
                        rows.append(_line_row("credit_note", cn, cust, ln, -1))
    elif what == "payments":
        from .documents import payment_list

        for p in payment_list(store, iso(start_d), iso(end_d)):
            rows.append(
                {
                    "payment": p["id"],
                    "date": p["date"],
                    "amount": p["amount"],
                    "method": p.get("method", ""),
                    "reference": p.get("reference", ""),
                    "invoice": p.get("invoice") or "",
                    "job": p.get("job_id", ""),
                    "customer": customers.get(p["customer_id"], {}).get("name", ""),
                    "kind": p.get("kind", "invoice payment"),
                }
            )
    elif what == "customers":
        for c in store.customers():
            rows.append(
                {
                    "id": c["id"],
                    "name": c.get("name", ""),
                    "company": c.get("company", ""),
                    "email": c.get("email", ""),
                    "phone": c.get("phone", ""),
                    "address": " / ".join(c.get("address") or []),
                }
            )
    elif what == "expenses":
        for job in store.jobs():
            for e in job["log"]:
                if e["type"] not in ("material", "expense") or not in_range(e.get("date")):
                    continue
                cost = (
                    to_decimal(e["amount"])
                    if e["type"] == "expense"
                    else (to_decimal(e["qty"]) * to_decimal(e["unit_cost"]) if e.get("unit_cost") else None)
                )
                rows.append(
                    {
                        "date": e["date"],
                        "job": job["id"],
                        "type": e["type"],
                        "description": e.get("description", ""),
                        "supplier": e.get("supplier", ""),
                        "cost": str(q(cost, store.places)) if cost is not None else "",
                        "receipt": e.get("receipt", ""),
                        "billed_on": e.get("invoice") or "",
                    }
                )
    elif what == "quotes":
        for doc in store.docs("quote"):
            if not in_range(doc.get("date")):
                continue
            for option in doc["options"]:
                rows.append(
                    {
                        "quote": doc["id"],
                        "date": doc["date"],
                        "status": doc["status"],
                        "customer": customers.get(doc["customer_id"], {}).get("name", ""),
                        "title": doc["title"],
                        "option": option["key"],
                        "option_name": option["name"],
                        "total": option["totals"]["total"],
                        "accepted": "yes" if (doc.get("accepted") or {}).get("option") == option["key"] else "",
                    }
                )
    label = f"{iso(start_d)}-to-{iso(end_d)}" if start else f"all-to-{iso(end_d)}"
    path = Path(out) if out else store.home / "exports" / f"{what}-{label}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields or ["empty"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return {"file": str(path), "rows": len(rows)}


# ----------------------------------------------------------------- search


def search(store, text: str) -> dict:
    words = [w for w in (text or "").lower().split() if w]
    if not words:
        raise JTPError("Give some words to search for.")

    def hit(*fields):
        hay = " ".join(str(f or "") for f in fields).lower()
        return all(w in hay for w in words)

    out = {"customers": [], "jobs": [], "documents": []}
    names = {}
    for c in store.customers():
        names[c["id"]] = c.get("name", "")
        if hit(c.get("name"), c.get("company"), c.get("email"), c.get("phone"), " ".join(c.get("address") or [])):
            out["customers"].append(
                {"id": c["id"], "name": c.get("name"), "phone": c.get("phone"), "email": c.get("email")}
            )
    for job in store.jobs():
        if hit(
            job["id"],
            job["title"],
            job.get("site_address"),
            " ".join(job.get("tags") or []),
            names.get(job["customer_id"]),
            job.get("notes"),
            " ".join(e.get("description", "") + " " + e.get("text", "") for e in job["log"]),
        ):
            out["jobs"].append(
                {
                    "id": job["id"],
                    "title": job["title"],
                    "status": job["status"],
                    "customer": names.get(job["customer_id"]),
                }
            )
    for kind in ("quote", "invoice", "change_order", "credit_note"):
        for doc in store.docs(kind):
            lines = doc.get("lines") or [ln for o in doc.get("options", []) for ln in o["lines"]]
            if hit(
                doc["id"],
                doc.get("title"),
                names.get(doc.get("customer_id")),
                " ".join(ln.get("description", "") for ln in lines),
            ):
                total = doc.get("totals", {}).get("total") or (doc.get("options") or [{}])[0].get("totals", {}).get(
                    "total"
                )
                out["documents"].append(
                    {
                        "id": doc["id"],
                        "type": kind,
                        "status": doc["status"],
                        "title": doc.get("title"),
                        "customer": names.get(doc.get("customer_id")),
                        "date": doc.get("date"),
                        "total": _fmt(store, total) if total else None,
                    }
                )
    for key in out:
        out[key] = out[key][:25]
    return out


def sample_totals(store, specs) -> dict:
    """Quick price check without saving anything (used by `jtp price`)."""
    lines, warnings, _ = normalize_lines(store.profile(), store.pricebook(), specs, store.places, "P")
    totals = compute_totals(lines, store.profile()["tax"], store.places)
    return {
        "lines": [
            {k: ln.get(k) for k in ("description", "qty", "unit", "unit_price", "amount")} for ln in priced(lines)
        ],
        "totals": totals,
        "warnings": warnings,
    }
