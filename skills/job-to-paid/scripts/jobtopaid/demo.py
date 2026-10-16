"""Demo data (so reviewers and new users can try everything in a minute) and
a self-test that checks the money math and renders every document type."""

from __future__ import annotations

import datetime as _dt
import os
import tempfile
from decimal import Decimal
from pathlib import Path

from . import documents as docs
from . import followups, records, reports
from .money import compute_totals
from .profile import new_profile
from .render import render_doc, render_receipt
from .store import Store
from .util import JTPError, iso, write_json


def demo_profile(currency: str = "USD") -> dict:
    profile = new_profile(currency)
    profile["business"].update(
        {
            "name": "Demo Plumbing & Heating",
            "trade": "Plumbing, heating and gas",
            "address": ["100 Example Street", "Springfield"],
            "phone": "+1 555 0100",
            "email": "office@demo-plumbing.example",
            "registration_numbers": [{"label": "Tax reg. no.", "value": "DEMO-123456"}],
        }
    )
    profile["tax"] = {
        "confirmed": True,
        "prices_include_tax": False,
        "taxes": [
            {
                "code": "TAX",
                "name": "Sales tax",
                "rate": "5",
                "applies_to": ["labour", "material", "equipment", "subcontract", "fee", "other"],
            }
        ],
        "note": "",
    }
    profile["rates"].update(
        {
            "labour": [
                {"key": "standard", "name": "Labour", "rate": "95", "cost": "45"},
                {"key": "after_hours", "name": "After-hours labour", "rate": "140", "cost": "55"},
                {"key": "apprentice", "name": "Apprentice labour", "rate": "60", "cost": "25"},
            ],
            "default_labour_key": "standard",
            "material_markup_pct": "25",
            "equipment_markup_pct": "15",
        }
    )
    profile["terms"].update(
        {
            "quote_valid_days": 30,
            "payment_due_days": 14,
            "deposit_percent": "0",
            "quote_terms": "Prices include supply and installation as described. Anything outside this scope "
            "is quoted separately before work starts.",
            "invoice_terms": "Payment due by the due date shown. Thank you!",
            "warranty": "12-month workmanship warranty. Manufacturer warranties apply to equipment.",
        }
    )
    profile["payment"]["methods"] = [
        {
            "name": "Bank transfer",
            "instructions": "Demo Bank, account 12345678. Use your invoice number as the reference.",
        },
        {"name": "Card", "instructions": "Use the Pay online button when there is one, or call us."},
    ]
    profile["followups"]["review_link"] = "https://example.com/review-demo-plumbing"
    return profile


PRICEBOOK = [
    {"name": "40 gal gas water heater", "kind": "material", "unit": "ea", "cost": "980", "tags": "water heater"},
    {
        "name": "50 gal high-efficiency gas water heater",
        "kind": "material",
        "unit": "ea",
        "cost": "1350",
        "tags": "water heater",
    },
    {"name": "Tankless gas water heater", "kind": "material", "unit": "ea", "cost": "2100", "tags": "water heater"},
    {"name": "Thermal expansion tank", "kind": "material", "unit": "ea", "cost": "65"},
    {"name": "1/2 in ball valve", "kind": "material", "unit": "ea", "cost": "18.50"},
    {"name": "Permit and inspection", "kind": "fee", "unit": "ea", "price": "175"},
    {"name": "Haul-away and disposal", "kind": "fee", "unit": "ea", "price": "60"},
]


def seed(store: Store, currency: str = "USD", history: bool = True) -> dict:
    if store.exists():
        raise JTPError(
            f"{store.home} already has data.", hint="Use a fresh folder for the demo, e.g. --home /tmp/jtp-demo"
        )
    store.home.mkdir(parents=True, exist_ok=True)
    store.save_profile(demo_profile(currency))
    for item in PRICEBOOK:
        records.pricebook_add(store, item)
    jane = records.customer_add(
        store,
        {
            "name": "Jane Doe",
            "email": "jane@example.com",
            "phone": "+1 555 0142",
            "address": ["12 Maple Avenue", "Springfield"],
            "preferred_contact": "email",
        },
    )["customer"]
    bakery = records.customer_add(
        store,
        {
            "name": "Bob Martin",
            "company": "Bob's Bakery Ltd",
            "email": "bob@bakery.example",
            "phone": "+1 555 0177",
            "address": ["5 Market Square", "Springfield"],
        },
    )["customer"]
    priya = records.customer_add(
        store,
        {
            "name": "Priya Patel",
            "email": "priya@example.com",
            "phone": "+1 555 0190",
            "address": ["88 Hillside Road", "Shelbyville"],
        },
    )["customer"]
    made = {"customers": [jane["id"], bakery["id"], priya["id"]]}
    if not history:
        return made
    today = store.today()

    # An overdue time-and-materials invoice.
    job = records.job_create(store, bakery["id"], "Repair leaking grease trap line", tags="drain", pricing="tm")
    records.job_log(store, job["id"], "time", {"hours": "3", "who": "Sam", "date": iso(today - _dt.timedelta(days=41))})
    records.job_log(
        store,
        job["id"],
        "material",
        {
            "description": "2 in ABS fittings and cement",
            "qty": "1",
            "unit_cost": "42.80",
            "date": iso(today - _dt.timedelta(days=41)),
        },
    )
    records.job_update(store, job["id"], {"status": "complete", "completed": iso(today - _dt.timedelta(days=41))})
    inv = docs.invoice_create(store, job_id=job["id"], tm=True)["invoice"]
    docs.invoice_issue(store, inv["id"], date=iso(today - _dt.timedelta(days=40)))
    docs.invoice_mark_sent(store, inv["id"], "email", bakery["email"], date=iso(today - _dt.timedelta(days=40)))
    made["overdue_invoice"] = inv["id"]

    # A paid fixed-price job with an annual service reminder.
    job2 = records.job_create(store, priya["id"], "Annual furnace service", tags="furnace,service")
    quote = docs.quote_create(
        store,
        {
            "title": "Annual furnace service",
            "lines": [
                {"kind": "labour", "description": "Furnace inspection, cleaning and safety test", "qty": "1.5"},
                {"kind": "material", "description": "Furnace filter", "qty": "1", "unit_cost": "24"},
            ],
        },
        job_id=job2["id"],
    )["quote"]
    docs.quote_mark_sent(store, quote["id"], "email", priya["email"], date=iso(today - _dt.timedelta(days=70)))
    docs.quote_accept(store, quote["id"], None, "Priya Patel", "email", None, date=iso(today - _dt.timedelta(days=69)))
    records.job_log(
        store, job2["id"], "time", {"hours": "1.75", "who": "Sam", "date": iso(today - _dt.timedelta(days=62))}
    )
    records.job_update(
        store,
        job2["id"],
        {"status": "complete", "completed": iso(today - _dt.timedelta(days=62)), "service_interval_months": 12},
    )
    inv2 = docs.invoice_create(store, job_id=job2["id"])["invoice"]
    docs.invoice_issue(store, inv2["id"], date=iso(today - _dt.timedelta(days=62)))
    docs.invoice_mark_sent(store, inv2["id"], "email", priya["email"], date=iso(today - _dt.timedelta(days=62)))
    state = docs.invoice_state(store.doc(inv2["id"], "invoice"), today)
    docs.payment_record(
        store,
        invoice_id=inv2["id"],
        amount=str(state["balance"]),
        method="Card",
        date=iso(today - _dt.timedelta(days=55)),
    )
    made["paid_invoice"] = inv2["id"]

    # A good / better / best quote waiting on the customer.
    spec = {
        "title": "Water heater replacement",
        "intro": "Thanks for having us out. Here are three ways to replace your leaking water heater.",
        "options": [
            {
                "name": "Good: standard 40 gal tank",
                "lines": [
                    {"kind": "material", "pricebook": "40 gal gas water heater", "qty": 1},
                    {"kind": "labour", "description": "Remove old tank, install and test new heater", "qty": 4},
                    {"kind": "fee", "pricebook": "Haul-away and disposal", "qty": 1},
                ],
            },
            {
                "name": "Better: 50 gal high-efficiency",
                "recommended": True,
                "description": "Lower running costs and faster recovery.",
                "lines": [
                    {"kind": "material", "pricebook": "50 gal high-efficiency gas water heater", "qty": 1},
                    {"kind": "material", "pricebook": "Thermal expansion tank", "qty": 1},
                    {"kind": "labour", "description": "Remove old tank, install and test new heater", "qty": 4.5},
                    {"kind": "fee", "pricebook": "Haul-away and disposal", "qty": 1},
                ],
            },
            {
                "name": "Best: tankless",
                "description": "Endless hot water and the longest warranty.",
                "lines": [
                    {"kind": "material", "pricebook": "Tankless gas water heater", "qty": 1},
                    {
                        "kind": "labour",
                        "description": "Remove old tank, install tankless unit, venting and gas line",
                        "qty": 7,
                    },
                    {"kind": "fee", "pricebook": "Permit and inspection", "qty": 1},
                    {"kind": "fee", "pricebook": "Haul-away and disposal", "qty": 1},
                ],
            },
        ],
    }
    quote3 = docs.quote_create(store, spec, customer_id=jane["id"])["quote"]
    sent_day = today - _dt.timedelta(days=3)
    doc = store.doc(quote3["id"], "quote")
    doc["date"] = iso(sent_day)
    doc["valid_until"] = iso(sent_day + _dt.timedelta(days=30))
    store.save_doc(doc)
    docs.quote_mark_sent(store, quote3["id"], "email", jane["email"], date=iso(sent_day))
    made["waiting_quote"] = quote3["id"]
    for ident in (inv["id"], inv2["id"], quote3["id"], quote["id"]):
        try:
            render_doc(store, store.doc(ident))
        except Exception:  # rendering is a nice-to-have in the demo
            pass
    return made


# ----------------------------------------------------------------- selftest


def selftest() -> dict:
    checks = []

    def check(name, condition, detail=""):
        checks.append({"name": name, "ok": bool(condition), "detail": "" if condition else str(detail)})

    def totals(lines, taxes, places=2, inclusive=False):
        return compute_totals(lines, {"taxes": taxes, "prices_include_tax": inclusive}, places)

    every = ["labour", "material", "equipment", "subcontract", "fee", "other"]
    gst = {"code": "GST", "name": "GST", "rate": "5", "applies_to": every}
    pst = {"code": "PST", "name": "PST", "rate": "7", "applies_to": ["material"]}

    t = totals(
        [
            {"kind": "labour", "qty": "2", "amount": "200.00", "tax_codes": ["GST"]},
            {"kind": "material", "qty": "1", "amount": "100.00", "tax_codes": ["GST", "PST"]},
        ],
        [gst, pst],
    )
    check("two taxes, one only on materials", t["total"] == "322.00" and t["tax_total"] == "22.00", t)

    qst = {"code": "QST", "name": "QST", "rate": "9.975", "applies_to": every, "compound": True}
    t = totals([{"kind": "other", "qty": "1", "amount": "100.00", "tax_codes": ["GST", "QST"]}], [gst, qst])
    check("compound tax", t["total"] == "115.47", t)

    vat = {"code": "VAT", "name": "VAT", "rate": "20", "applies_to": every}
    t = totals([{"kind": "other", "qty": "1", "amount": "120.00", "tax_codes": ["VAT"]}], [vat], inclusive=True)
    check(
        "tax-inclusive prices (VAT)",
        t["total"] == "120.00" and t["tax_total"] == "20.00" and t["net_excl_tax"] == "100.00",
        t,
    )

    ct = {"code": "CT", "name": "Consumption tax", "rate": "10", "applies_to": every}
    t = totals([{"kind": "other", "qty": "3", "amount": "3000", "tax_codes": ["CT"]}], [ct], places=0)
    check("zero-decimal currency (JPY)", t["total"] == "3300", t)

    t = totals([{"kind": "other", "qty": "1", "amount": "10.125", "tax_codes": []}], [], places=3)
    check("three-decimal currency (KWD)", t["total"] == "10.125", t)

    lines = [
        {"kind": "labour", "qty": "1", "amount": "333.33", "tax_codes": ["GST"]},
        {"kind": "material", "qty": "1", "amount": "666.67", "tax_codes": ["GST", "PST"]},
        {"kind": "discount", "percent": "10"},
    ]
    t = totals(lines, [gst, pst])
    allocated = sum(Decimal(ln["discount"]) for ln in lines[:2])
    check(
        "percent discount spread exactly across lines", t["discount"] == "-100.00" and allocated == Decimal("100.00"), t
    )

    with tempfile.TemporaryDirectory(prefix="jtp-selftest-") as tmp:
        old_today = os.environ.pop("JTP_TODAY", None)
        try:
            store = Store(Path(tmp) / "data")
            made = seed(store)
            check("demo data loads", len(made["customers"]) == 3)
            brief = reports.brief(store)
            check(
                "money brief finds the overdue invoice",
                any(r["id"] == made["overdue_invoice"] for r in brief["owed"]["overdue"]),
                brief["owed"],
            )
            actions = followups.due_actions(store)
            kinds = {a["action"] for a in actions}
            check("follow-ups due (reminder + quote follow-up)", {"payment_reminder", "quote_followup"} <= kinds, kinds)

            quote_id = made["waiting_quote"]
            accepted = docs.quote_accept(store, quote_id, "B", "Jane Doe", "email", "Go with B")
            job_id = accepted["job"]
            check("accept option B", store.doc(quote_id)["status"] == "accepted")
            co = docs.co_create(
                store,
                job_id,
                {
                    "title": "Replace corroded gas shut-off",
                    "reason": "Found on site",
                    "lines": [
                        {"kind": "material", "pricebook": "1/2 in ball valve", "qty": 1},
                        {"kind": "labour", "description": "Swap valve", "qty": 0.5},
                    ],
                },
            )
            docs.co_approve(store, co["change_order"]["id"], "Jane Doe", "text message", None)
            records.job_log(store, job_id, "time", {"hours": "5.5", "who": "Sam"})
            records.job_log(
                store,
                job_id,
                "material",
                {"description": "Gas flex connector", "qty": "1", "unit_cost": "28", "extra": True},
            )
            progress = docs.invoice_create(store, job_id=job_id, progress="30")["invoice"]
            docs.invoice_issue(store, progress["id"])
            pstate = docs.invoice_state(store.doc(progress["id"]), store.today())
            docs.payment_record(store, invoice_id=progress["id"], amount=str(pstate["balance"]), method="Bank transfer")
            final = docs.invoice_create(store, job_id=job_id)["invoice"]
            quote = store.doc(quote_id)
            option_total = Decimal(next(o for o in quote["options"] if o["key"] == "B")["totals"]["total"])
            co_total = Decimal(store.doc(co["change_order"]["id"])["totals"]["total"])
            billed = Decimal(store.doc(progress["id"])["totals"]["total"]) + Decimal(final["totals"]["total"])
            extra = sum(
                Decimal(ln["amount"]) for ln in final["lines"] if (ln.get("source") or {}).get("log")
            ) * Decimal("1.05")
            check(
                "progress + final bill the quote and change order exactly",
                abs(billed - option_total - co_total - extra) <= Decimal("0.02"),
                f"{billed} vs {option_total} + {co_total} + {extra}",
            )
            docs.invoice_issue(store, final["id"])
            fstate = docs.invoice_state(store.doc(final["id"]), store.today())
            docs.payment_record(store, invoice_id=final["id"], amount="500", method="Card")
            credit = docs.credit_create(store, final["id"], amount="25.00", reason="Goodwill")
            check(
                "credit note lands on the exact amount",
                credit["credit_note"]["totals"]["total"] == "25.00",
                credit["credit_note"]["totals"],
            )
            rest = docs.invoice_state(store.doc(final["id"]), store.today())["balance"]
            check("balance after payment and credit", rest == fstate["balance"] - Decimal("525.00"), rest)
            pay = docs.payment_record(store, invoice_id=final["id"], amount=str(rest), method="Card")
            check("invoice marked paid", store.doc(final["id"])["status"] == "paid")
            dup = (
                docs.payment_record(store, invoice_id=final["id"], amount=str(rest), method="Card")
                if rest > 0
                else {"duplicate": True}
            )
            check("double-recorded payment is caught", dup.get("duplicate"), dup)

            rendered = 0
            for ident in (quote_id, co["change_order"]["id"], progress["id"], final["id"], credit["credit_note"]["id"]):
                result = render_doc(store, store.doc(ident), fmt="both", engine="builtin")
                data = Path(result["files"]["pdf"]).read_bytes()
                if data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF") and b"/Count" in data:
                    rendered += 1
            receipt = render_receipt(store, pay["payment"]["id"], engine="builtin")
            data = Path(receipt["files"]["pdf"]).read_bytes()
            rendered += data.startswith(b"%PDF") and 1 or 0
            check("PDF + HTML render for every document type", rendered == 6, rendered)
            tax = reports.tax_report(store)
            check("tax report adds up", "TAX" in tax["taxes"], tax)
            export = reports.export(store, "invoices", str(Path(tmp) / "inv.csv"))
            check("CSV export", export["rows"] >= 3, export)
            write_json(Path(tmp) / "ok.json", {"ok": True})
        finally:
            if old_today:
                os.environ["JTP_TODAY"] = old_today
    passed = all(c["ok"] for c in checks)
    return {
        "passed": passed,
        "checks": checks,
        "summary": f"{sum(c['ok'] for c in checks)}/{len(checks)} checks passed",
    }
