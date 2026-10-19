"""Demo data (so reviewers and new users can try everything in a minute) and
a self-test that checks the money math and renders every document type."""

from __future__ import annotations

import datetime as _dt
import os
import tempfile
from decimal import Decimal
from pathlib import Path

from . import confirm
from . import documents as docs
from . import followups, records, reports
from .money import compute_totals, currency_display, format_total
from .profile import follow_contact, new_profile
from .render import render_doc, render_receipt
from .store import Store
from .util import JTPError, iso, write_json


EVERY_KIND = ["labour", "material", "equipment", "subcontract", "fee", "other"]

# Local-looking demo details. Phone numbers come from ranges kept for fiction (555-01xx in North
# America, 020 7946 0xxx in London, 02 5550 xxxx in Australia).
DEMO_REGIONS = {
    "USD": {
        "business": (["100 Example Street", "Springfield, IL 62701"], "+1 217 555 0100"),
        "customers": [
            (["12 Maple Avenue", "Springfield, IL 62704"], "+1 217 555 0142"),
            (["5 Market Square", "Springfield, IL 62701"], "+1 217 555 0177"),
            (["88 Hillside Road", "Chatham, IL 62629"], "+1 217 555 0190"),
        ],
        "registration": [{"label": "Tax reg. no.", "value": "DEMO-123456"}],
        "taxes": [{"code": "TAX", "name": "Sales tax", "rate": "5", "applies_to": EVERY_KIND}],
        "bank": (
            "Bank transfer",
            "Demo Bank, routing 000000000, account 12345678. Please use {number} as the reference.",
        ),
        "timezone": "America/Chicago",
    },
    "CAD": {  # British Columbia: GST on everything, PST on materials and equipment
        "business": (["100 Example Street", "Vancouver BC V6B 1A1"], "+1 604 555 0100"),
        "customers": [
            (["12 Maple Avenue", "Burnaby BC V5H 2E2"], "+1 604 555 0142"),
            (["5 Market Square", "Surrey BC V3T 2W1"], "+1 604 555 0177"),
            (["88 Hillside Road", "North Vancouver BC V7L 1A1"], "+1 604 555 0190"),
        ],
        "registration": [
            {"label": "GST No.", "value": "123456789 RT0001"},
            {"label": "PST No.", "value": "PST-1234-5678"},
        ],
        "taxes": [
            {"code": "GST", "name": "GST", "rate": "5", "applies_to": EVERY_KIND},
            {"code": "PST", "name": "PST", "rate": "7", "applies_to": ["material", "equipment"]},
        ],
        "bank": ("Interac e-Transfer", "Send to pay@demo-plumbing.example and put {number} in the message."),
        "timezone": "America/Vancouver",
    },
    "GBP": {
        "business": (["100 Example Street", "London E1 6AN"], "+44 20 7946 0100"),
        "customers": [
            (["12 Maple Avenue", "London N16 0AA"], "+44 20 7946 0142"),
            (["5 Market Square", "London SE1 9AA"], "+44 20 7946 0177"),
            (["88 Hillside Road", "Croydon CR0 1AA"], "+44 20 7946 0190"),
        ],
        "registration": [{"label": "VAT reg. no.", "value": "GB 123 4567 89"}],
        "taxes": [{"code": "VAT", "name": "VAT", "rate": "20", "applies_to": EVERY_KIND}],
        "bank": ("Bank transfer", "Sort code 00-00-00, account 12345678. Please use {number} as the reference."),
        "timezone": "Europe/London",
    },
    "AUD": {
        "business": (["100 Example Street", "Sydney NSW 2000"], "+61 2 5550 0100"),
        "customers": [
            (["12 Maple Avenue", "Parramatta NSW 2150"], "+61 2 5550 0142"),
            (["5 Market Square", "Chatswood NSW 2067"], "+61 2 5550 0177"),
            (["88 Hillside Road", "Manly NSW 2095"], "+61 2 5550 0190"),
        ],
        "registration": [{"label": "ABN", "value": "12 345 678 901"}],
        "taxes": [{"code": "GST", "name": "GST", "rate": "10", "applies_to": EVERY_KIND}],
        "bank": ("Bank transfer", "BSB 000-000, account 12345678. Please use {number} as the reference."),
        "labels": {"invoice": "Tax Invoice"},
        "timezone": "Australia/Sydney",
    },
}
GENERIC_REGION = {
    "business": (["100 Example Street", "Springfield"], "555 0100"),
    "customers": [
        (["12 Maple Avenue", "Springfield"], "555 0142"),
        (["5 Market Square", "Springfield"], "555 0177"),
        (["88 Hillside Road", "Shelbyville"], "555 0190"),
    ],
    "registration": [{"label": "Tax reg. no.", "value": "DEMO-123456"}],
    "taxes": [{"code": "TAX", "name": "Sales tax", "rate": "5", "applies_to": EVERY_KIND}],
    "bank": ("Bank transfer", "Demo Bank, account 12345678. Please use {number} as the reference."),
    "timezone": "UTC",
}


def demo_region(currency: str) -> dict:
    return DEMO_REGIONS.get((currency or "USD").upper(), GENERIC_REGION)


def demo_profile(currency: str = "USD") -> dict:
    profile = new_profile(currency)
    region = demo_region(currency)
    address, phone = region["business"]
    profile["business"].update(
        {
            "name": "Demo Plumbing & Heating",
            "trade": "Plumbing, heating and gas",
            "address": list(address),
            "phone": phone,
            "email": "office@demo-plumbing.example",
            "owner_name": "Sam",
            "registration_numbers": [dict(r) for r in region["registration"]],
        }
    )
    profile["tax"] = {
        "confirmed": True,
        "prices_include_tax": False,
        "taxes": [dict(t, applies_to=list(t["applies_to"])) for t in region["taxes"]],
        "note": "",
    }
    if region.get("labels"):
        profile["labels"] = dict(region["labels"])
    profile["locale"]["timezone"] = region["timezone"]  # "today" is the demo town's day, not the computer's
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
    bank_name, bank_instructions = region["bank"]
    profile["payment"]["methods"] = [
        {"name": bank_name, "instructions": bank_instructions},
        {"name": "Card", "instructions": f"Call us on {phone} to pay by card."},
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
    profile = demo_profile(currency)
    # The demo business takes a 25% deposit to book jobs of 1,000 or more.
    profile["terms"]["deposit_percent"] = "25"
    profile["terms"]["deposit_min_total"] = "1000"
    store.save_profile(profile)
    for item in PRICEBOOK:
        records.pricebook_add(store, item)
    places = demo_region(currency)["customers"]
    jane = records.customer_add(
        store,
        {
            "name": "Jane Doe",
            "email": "jane@example.com",
            "phone": places[0][1],
            "address": list(places[0][0]),
            "preferred_contact": "email",
        },
    )["customer"]
    bakery = records.customer_add(
        store,
        {
            "name": "Bob Martin",
            "company": "Bob's Bakery Ltd",
            "email": "bob@bakery.example",
            "phone": places[1][1],
            "address": list(places[1][0]),
        },
    )["customer"]
    priya = records.customer_add(
        store,
        {
            "name": "Priya Patel",
            "email": "priya@example.com",
            "phone": places[2][1],
            "address": list(places[2][0]),
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
    job2 = records.job_create(store, jane["id"], "Annual furnace service", tags="furnace,service")
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
    docs.quote_mark_sent(store, quote["id"], "email", jane["email"], date=iso(today - _dt.timedelta(days=70)))
    docs.quote_accept(store, quote["id"], None, "Jane Doe", "email", None, date=iso(today - _dt.timedelta(days=69)))
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
    docs.invoice_mark_sent(store, inv2["id"], "email", jane["email"], date=iso(today - _dt.timedelta(days=62)))
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
    quote3 = docs.quote_create(store, spec, customer_id=priya["id"])["quote"]
    sent_day = today - _dt.timedelta(days=3)
    doc = store.doc(quote3["id"], "quote")
    doc["date"] = iso(sent_day)
    doc["valid_until"] = iso(sent_day + _dt.timedelta(days=30))
    store.save_doc(doc)
    docs.quote_mark_sent(store, quote3["id"], "email", priya["email"], date=iso(sent_day))
    made["waiting_quote"] = quote3["id"]
    for ident in (inv["id"], inv2["id"], quote3["id"], quote["id"]):
        try:
            render_doc(store, store.doc(ident))
        except Exception:  # rendering is a nice-to-have in the demo
            pass
    return made


# ----------------------------------------------------------------- selftest


def _with_owners_yes(action, *args, **kwargs):
    """Run an action that waits for the owner's yes, answering "yes" to its question as the demo's owner."""
    try:
        return action(*args, **kwargs)
    except JTPError as exc:
        if exc.code != "needs_owner" or not getattr(exc, "confirm", None):
            raise
        return action(*args, owner_confirmed="yes", confirm_code=exc.confirm, **kwargs)


def _refusal(action, *args, **kwargs):
    """The code an action refuses with, or None if it went ahead."""
    try:
        action(*args, **kwargs)
    except JTPError as exc:
        return exc.code
    return None


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
            accepted = docs.quote_accept(store, quote_id, "B", "Priya Patel", "email", "Go with B")
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
            docs.co_approve(store, co["change_order"]["id"], "Priya Patel", "text message", None)
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
                (
                    Decimal(ln["net"]) + sum((Decimal(v) for v in ln["tax_amounts"].values()), Decimal(0))
                    for ln in final["lines"]
                    if (ln.get("source") or {}).get("log")
                ),
                Decimal(0),
            )
            check(
                "progress + final bill the quote and change order to the cent",
                billed == option_total + co_total + extra,
                f"{billed} vs {option_total} + {co_total} + {extra}",
            )
            docs.invoice_issue(store, final["id"])
            fstate = docs.invoice_state(store.doc(final["id"]), store.today())
            docs.payment_record(store, invoice_id=final["id"], amount="500", method="Card")
            credit = _with_owners_yes(docs.credit_create, store, final["id"], amount="25.00", reason="Goodwill")
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

            # The findings of an independent review of 1.0.1, one check each.
            customer = made["customers"][0]
            fee = [{"kind": "fee", "description": "Call-out", "unit_price": "89"}]
            draft = docs.invoice_create(store, customer_id=customer, spec={"lines": [dict(fee[0], estimate=True)]})
            draft_id = draft["invoice"]["id"]
            docs.invoice_update(store, draft_id, {"lines": fee})  # the "estimate" mark left out
            asked = _refusal(docs.invoice_issue, store, draft_id)
            check("an estimate can't be dropped by editing the invoice: issuing still asks the owner",
                  asked == "estimates_unconfirmed" and store.doc(draft_id)["status"] == "draft", asked)  # fmt: skip
            preview = docs.invoice_issue(store, draft_id, dry_run=True)
            check("issue --dry-run issues nothing and shows the owner's question",
                  preview.get("dry_run") and preview.get("owner_question") and store.doc(draft_id)["status"] == "draft",
                  preview.get("invoice", {}).get("status"))  # fmt: skip
            overdue_id, notes = made["overdue_invoice"], len(store.docs("credit_note"))
            gates = (_refusal(docs.invoice_void, store, overdue_id, "Test"),
                     _refusal(docs.credit_create, store, overdue_id, amount="10"))  # fmt: skip
            check("voiding and credit notes wait for the owner's yes",
                  gates == ("needs_owner", "needs_owner") and store.doc(overdue_id)["status"] == "issued"
                  and len(store.docs("credit_note")) == notes, gates)  # fmt: skip
            declined = _refusal(docs.invoice_issue, store, overdue_id, owner_confirmed="absolutely not")
            check("a no on an invoice that's already issued isn't reported as issued", declined == "already_issued",
                  declined)  # fmt: skip
            permit, _, warned, _ = docs.build_lines(store, [{"kind": "material", "pricebook": "Permit and inspection"}],
                                                    prefix="T")  # fmt: skip
            check("a price book fee on a material line stays a fee, with a warning",
                  permit[0]["kind"] == "fee" and warned, (permit[0]["kind"], warned))  # fmt: skip
            canada = demo_profile("CAD")
            moved, _ = follow_contact(canada, "business.phone", canada["business"]["phone"], "+1 604 555 0199")
            card = canada["payment"]["methods"][-1]["instructions"]
            check("payment instructions follow a new phone number", moved == ["Card"] and "555 0199" in card, card)
            soon = docs.invoice_create(store, customer_id=customer, spec={"lines": fee})["invoice"]["id"]
            docs.invoice_issue(store, soon)
            brief = reports.brief(store)
            named = [line.split()[1] for line in brief["text"].splitlines() if line.startswith("  - INV-")]
            check("the brief names every open invoice with its due date, overdue first",
                  named == [overdue_id, soon] and all("due " in line for line in brief["text"].splitlines()[2:4]),
                  brief["text"])  # fmt: skip
            cad = format_total(Decimal("1234.5"), currency_display({"currency": "CAD"}))
            check("chat and brief totals show the currency code where the symbol is shared",
                  cad == "$1,234.50 CAD" and brief["owed"]["total"].endswith(" USD"), (cad, brief["owed"]["total"]))
            zones = {c: demo_profile(c)["locale"]["timezone"] for c in ("USD", "CAD", "GBP", "AUD", "EUR")}
            check("the demo sets its town's time zone", all(zones.values()) and zones["CAD"] == "America/Vancouver",
                  zones)  # fmt: skip

            # The findings of the review's third round.
            estimated = docs.quote_create(store, {"title": "Valve", "lines": [
                {"kind": "material", "description": "Valve", "qty": 1, "unit_cost": "100", "estimate": True}]},
                customer_id=customer)["quote"]  # fmt: skip
            whole_job = docs.quote_accept(store, estimated["id"], None, "Jane Doe", "email", None)["job"]
            progress_100 = docs.invoice_create(store, job_id=whole_job, progress="100")["invoice"]["id"]
            asked = _refusal(docs.invoice_issue, store, progress_100)
            check("a progress invoice from an estimated quote asks the owner, even at 100%",
                  asked == "estimates_unconfirmed", asked)  # fmt: skip
            docs.invoice_delete(store, progress_100)  # the newest draft, so its number goes back
            copied = docs.invoice_create(store, customer_id=customer, spec={"lines": fee})["invoice"]["id"]
            asked = _refusal(docs.invoice_issue, store, copied)
            check("a draft deleted with estimates is remembered: the customer's next invoice asks",
                  asked == "estimates_unconfirmed", asked)  # fmt: skip
            bank = {"payment": {"methods": [{"name": "Bank", "instructions": "Account 99916045550100, ref 5550100."}]}}
            moved, _ = follow_contact(bank, "business.phone", "+1 604 555 0100", "+1 604 555 0199")
            check("a phone change leaves account numbers alone", not moved, bank["payment"]["methods"])
            added = docs.co_create(store, job_id, {"title": "Add a tap", "lines": [
                {"kind": "fee", "description": "Add a tap", "unit_price": "50"}]})["change_order"]  # fmt: skip
            docs.co_approve(store, added["id"], "Priya Patel", "text message", None)
            later = docs.invoice_create(store, job_id=job_id)["invoice"]
            check("a change order approved after the final invoice can still be billed",
                  [ln["description"] for ln in later["lines"]] == ["Add a tap"], later["lines"])  # fmt: skip
            write_json(Path(tmp) / "ok.json", {"ok": True})
        finally:
            if old_today:
                os.environ["JTP_TODAY"] = old_today

    with tempfile.TemporaryDirectory(prefix="jtp-selftest-yes-") as tmp:
        action, question = "invoice.issue|INV-0001|Install: 3 hr", "Are the figures final?"
        try:
            confirm.owner_yes(tmp, action, question, None, None, why="Issuing makes them final.")
            code = None
        except JTPError as exc:
            code = exc.confirm
        refused = 0
        for vague in ("ok", "sure", "yes, next week", "yes jk", "👍"):
            try:
                confirm.owner_yes(tmp, action, question, vague, code, why="")
            except JTPError as exc:
                refused += exc.code == "unclear_answer"
        plain = bool(code) and confirm.owner_yes(tmp, action, question, "yes", code, why="") == "yes"
        check("only the owner's plain yes counts (not ok, sure or a yes with a condition)", plain and refused == 5)
        confirm.use(tmp, code)
        try:
            confirm.owner_yes(tmp, action, question, "yes", code, why="")
            reused = True
        except JTPError:
            reused = False
        check("each confirmation code works once", not reused)
        check("\"that's right\" alone isn't a yes; \"yes, that's right\" is",
              not confirm.plain_yes("that's right", docs.ESTIMATES_FINAL_WORDS)
              and confirm.plain_yes("yes, that's right", docs.ESTIMATES_FINAL_WORDS))  # fmt: skip
    passed = all(c["ok"] for c in checks)
    return {
        "passed": passed,
        "checks": checks,
        "summary": f"{sum(c['ok'] for c in checks)}/{len(checks)} checks passed",
    }
