"""Tests for the Job to Paid toolkit. Run from the plugin root:

python3 -m unittest discover -s tests -v
"""

import csv
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "job-to-paid" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from jobtopaid import confirm, pdf  # noqa: E402
from jobtopaid import documents as docs  # noqa: E402
from jobtopaid import followups, records, reports  # noqa: E402
from jobtopaid.demo import demo_profile, seed  # noqa: E402
from jobtopaid.money import compute_totals, currency_display, format_money, format_total  # noqa: E402
from jobtopaid.pdf import _png_decode  # noqa: E402
from jobtopaid.pricing import lines_as_spec, normalize_lines  # noqa: E402
from jobtopaid.profile import check as profile_check  # noqa: E402
from jobtopaid.profile import follow_contact  # noqa: E402
from jobtopaid.render import render_doc, render_receipt  # noqa: E402
from jobtopaid.view import build_view  # noqa: E402
from jobtopaid.store import Store  # noqa: E402
from jobtopaid import util  # noqa: E402
from jobtopaid.util import JTPError, to_decimal  # noqa: E402
from jobtopaid.documents import priced  # noqa: E402

util.TEST_DATES = True  # JTP_TODAY sets the date in these tests, and only here (commands run by CliTests ignore it)

EVERY = ["labour", "material", "equipment", "subcontract", "fee", "other"]
# Replies an independent review of 1.0.0 saw count as the owner's yes (any two characters did), and more.
NOT_A_YES = ["ok", "k", "sure", "fine", "maybe", "?", "👍", "👎", "yes jk", "yeah right", "yes, next week",
             "yes lol jk", "yes once she pays", "ok yes", "yes...", "yes 👍", "yes, 3 hours is right", "that's right",
             "absolutely not"]


def owner_approves(action, *args, **kwargs):
    """Run an action that needs the owner's yes the way an agent must: it refuses with the question and a code, and
    the owner's "yes" goes back with that code."""
    try:
        action(*args, **kwargs)
    except JTPError as exc:
        if exc.code != "needs_owner" or not (exc.ask_owner and exc.confirm):
            raise
        return action(*args, owner_confirmed="yes", confirm_code=exc.confirm, **kwargs)
    raise AssertionError(f"{action.__name__} went ahead without asking the owner")


def totals(lines, taxes, places=2, inclusive=False):
    return compute_totals(lines, {"taxes": taxes, "prices_include_tax": inclusive}, places)


class MoneyTests(unittest.TestCase):
    def test_tax_only_on_materials(self):
        gst = {"code": "GST", "rate": "5", "applies_to": EVERY}
        pst = {"code": "PST", "rate": "7", "applies_to": ["material"]}
        t = totals(
            [
                {"kind": "labour", "qty": "1", "amount": "200.00", "tax_codes": ["GST"]},
                {"kind": "material", "qty": "1", "amount": "100.00", "tax_codes": ["GST", "PST"]},
            ],
            [gst, pst],
        )
        self.assertEqual(t["total"], "322.00")
        self.assertEqual([r["amount"] for r in t["taxes"]], ["15.00", "7.00"])

    def test_compound_tax(self):
        taxes = [
            {"code": "A", "rate": "5", "applies_to": EVERY},
            {"code": "B", "rate": "9.975", "applies_to": EVERY, "compound": True},
        ]
        t = totals([{"kind": "other", "qty": "1", "amount": "100.00", "tax_codes": ["A", "B"]}], taxes)
        self.assertEqual(t["total"], "115.47")

    def test_tax_inclusive(self):
        vat = {"code": "VAT", "rate": "20", "applies_to": EVERY}
        t = totals([{"kind": "other", "qty": "1", "amount": "99.99", "tax_codes": ["VAT"]}], [vat], inclusive=True)
        self.assertEqual(t["total"], "99.99")
        self.assertEqual(Decimal(t["net_excl_tax"]) + Decimal(t["tax_total"]), Decimal("99.99"))

    def test_inclusive_with_compound_is_refused(self):
        taxes = [{"code": "A", "rate": "5", "applies_to": EVERY, "compound": True}]
        with self.assertRaises(JTPError):
            totals([{"kind": "other", "qty": "1", "amount": "1", "tax_codes": ["A"]}], taxes, inclusive=True)

    def test_currency_places(self):
        ct = {"code": "CT", "rate": "10", "applies_to": EVERY}
        self.assertEqual(
            totals([{"kind": "other", "qty": "1", "amount": "333", "tax_codes": ["CT"]}], [ct], 0)["total"], "366"
        )
        self.assertEqual(
            totals([{"kind": "other", "qty": "1", "amount": "1.234", "tax_codes": []}], [], 3)["total"], "1.234"
        )

    def test_discount_is_spread_exactly(self):
        gst = {"code": "GST", "rate": "5", "applies_to": EVERY}
        lines = [
            {"kind": "labour", "qty": "1", "amount": "0.01", "tax_codes": ["GST"]},
            {"kind": "material", "qty": "1", "amount": "333.33", "tax_codes": ["GST"]},
            {"kind": "material", "qty": "1", "amount": "666.66", "tax_codes": ["GST"]},
            {"kind": "discount", "percent": "33"},
        ]
        t = totals(lines, [gst])
        self.assertEqual(sum(Decimal(ln["discount"]) for ln in lines[:3]), -Decimal(t["discount"]))
        self.assertEqual(Decimal(t["net"]), sum(Decimal(ln["net"]) for ln in lines[:3]))

    def test_discount_stays_in_its_section(self):
        lines = [
            {"kind": "labour", "qty": "1", "amount": "100.00", "tax_codes": []},
            {"kind": "discount", "percent": "10"},
            {"kind": "labour", "qty": "1", "amount": "50.00", "tax_codes": [], "section": "Change order"},
        ]
        t = totals(lines, [])
        self.assertEqual(t["discount"], "-10.00")
        self.assertEqual(lines[2]["discount"], "0")

    def test_discount_too_big(self):
        with self.assertRaises(JTPError):
            totals(
                [
                    {"kind": "fee", "qty": "1", "amount": "10.00", "tax_codes": []},
                    {"kind": "discount", "amount_input": "11"},
                ],
                [],
            )

    def test_currency_code_only_where_the_symbol_is_shared(self):
        def shown(code, setting="auto"):
            return currency_display({"currency": code, "show_currency_code": setting})["show_code"]

        self.assertEqual([shown(c) for c in ("USD", "CAD", "JPY", "SEK")], [True] * 4)  # $, ¥ and kr are shared
        self.assertEqual([shown(c) for c in ("EUR", "GBP", "CHF", "XYZ")], [False] * 4)  # unique, or already a code
        self.assertEqual((shown("EUR", "always"), shown("USD", "never")), (True, False))

    def test_formats(self):
        self.assertEqual(format_money(Decimal("1234.5"), currency_display({"currency": "USD"})), "$1,234.50")
        self.assertEqual(format_money(Decimal("1234.5"), currency_display({"currency": "EUR"})), "1.234,50 €")
        self.assertEqual(format_money(Decimal("1234.5"), currency_display({"currency": "CHF"})), "CHF 1'234.50")
        self.assertEqual(format_money(Decimal("1234"), currency_display({"currency": "JPY"})), "¥1,234")
        self.assertEqual(format_money(Decimal("-5"), currency_display({"currency": "GBP"})), "-£5.00")

    def test_strict_number_parsing(self):
        self.assertEqual(to_decimal("$1,234.50"), Decimal("1234.50"))
        self.assertEqual(to_decimal("7%"), Decimal("7"))
        for bad in ("1.234,50", "12,5", "abc", None, True):
            with self.assertRaises(JTPError):
                to_decimal(bad)


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["JTP_TODAY"] = "2026-10-15"
        self.store = Store(Path(self.tmp.name) / "data")
        self.store.home.mkdir(parents=True)
        profile = demo_profile("CAD")
        profile["tax"]["taxes"] = [
            {"code": "GST", "name": "GST", "rate": "5", "applies_to": EVERY},
            {"code": "PST", "name": "PST", "rate": "7", "applies_to": ["material", "equipment"]},
        ]
        self.store.save_profile(profile)
        self.cust = records.customer_add(self.store, {"name": "Jane Doe", "email": "jane@example.com"})["customer"]

    def tearDown(self):
        os.environ.pop("JTP_TODAY", None)
        self.tmp.cleanup()


class PricingTests(StoreCase):
    def test_labour_rate_markup_and_pricebook(self):
        records.pricebook_add(self.store, {"name": "Valve", "kind": "material", "cost": "20", "markup_pct": "50"})
        lines, warnings, suggestions = normalize_lines(
            self.store.profile(),
            self.store.pricebook(),
            [
                {"kind": "labour", "description": "Install", "qty": 2},
                {"kind": "material", "description": "Pipe", "qty": 3, "unit_cost": "10", "estimate": True},
                {"kind": "material", "pricebook": "valve", "qty": 1},
            ],
            2,
        )
        self.assertEqual(lines[0]["unit_price"], "95")
        self.assertEqual(lines[1]["unit_price"], "12.50")
        self.assertEqual(lines[2]["unit_price"], "30.00")
        self.assertEqual(lines[1]["tax_codes"], ["GST", "PST"])
        self.assertEqual(lines[0]["tax_codes"], ["GST"])
        self.assertTrue(warnings)
        self.assertEqual([s["name"] for s in suggestions], ["Pipe"])

    def test_a_price_book_fee_stays_a_fee_on_a_material_line(self):
        """An independent review: a permit (a fee in the price book) on a line marked material was charged PST
        too, with no warning."""
        records.pricebook_add(self.store, {"name": "Permit and inspection", "kind": "fee", "price": "175"})
        permit = "permit and inspection"
        specs = [{"kind": "material", "pricebook": permit}, {"pricebook": permit}, {"kind": "fee", "pricebook": permit},
                 {"kind": "material", "pricebook": permit, "tax_codes": ["GST", "PST"]}]  # fmt: skip
        lines, warnings, _ = normalize_lines(self.store.profile(), self.store.pricebook(), specs, 2)
        self.assertEqual([(ln["kind"], ln["tax_codes"]) for ln in lines],
                         [("fee", ["GST"])] * 3 + [("fee", ["GST", "PST"])])  # fmt: skip
        self.assertEqual([w for w in warnings if "price book lists" in w], [
            "Line 1: the price book lists Permit and inspection as fee, so it's billed and taxed as fee, not material.",
            "Line 4: the price book lists Permit and inspection as fee, so it's billed as fee, not material "
            "(the line's own tax codes still apply).",
        ])  # fmt: skip

    def test_contact_changes_reach_the_payment_instructions_and_nothing_else(self):
        profile = demo_profile("CAD")
        profile["payment"]["methods"].append({"name": "Cheque", "instructions": (
            "Questions? (604) 555-0100 or office@demo-plumbing.example. Not backoffice@demo-plumbing.example or "
            "office@demo-plumbing.example.shop.example. Account 6045550199, ref 2026-10-18.")})  # fmt: skip
        changed, warnings = follow_contact(profile, "business.phone", "+1 604 555 0100", "+1 604 555 0123")
        self.assertEqual((changed, warnings), (["Card", "Cheque"], []))
        changed, warnings = follow_contact(profile, "business.email", "office@demo-plumbing.example", "hi@new.example")
        self.assertEqual(changed, ["Cheque"])
        self.assertEqual(profile["payment"]["methods"][-1]["instructions"], (
            "Questions? +1 604 555 0123 or hi@new.example. Not backoffice@demo-plumbing.example or "
            "office@demo-plumbing.example.shop.example. Account 6045550199, ref 2026-10-18."))  # fmt: skip
        self.assertIn("pay@demo-plumbing.example", warnings[0])  # the e-Transfer address: only the owner knows
        self.assertEqual(follow_contact(profile, "business.name", "A", "B"), ([], []))

    def test_phone_formats_with_a_bracketed_0_and_local_numbers(self):
        """An independent review of 1.0.1: "+44 (0)20 ..." wasn't taken as the old number, and a 7-digit local
        number was left behind with no warning."""
        uk = {"payment": {"methods": [{"name": "Phone", "instructions": "Ring +44 (0)20 7946 0100."}]}}
        self.assertEqual(follow_contact(uk, "business.phone", "+44 20 7946 0100", "+44 20 7946 0999"), (["Phone"], []))
        self.assertEqual(uk["payment"]["methods"][0]["instructions"], "Ring +44 20 7946 0999.")
        local = {"payment": {"methods": [{"name": "Card", "instructions": "Call 555-0100 or (604) 555-0100."}]}}
        changed, warnings = follow_contact(local, "business.phone", "+1 604 555 0100", "+1 778 555 0123")
        self.assertEqual(local["payment"]["methods"][0]["instructions"], "Call 555-0100 or +1 778 555 0123.")
        self.assertEqual(warnings, ["The payment instructions for Card still give 555-0100, which ends like the old "
                                    "number +1 604 555 0100. If that's the old number, change it there too: jtp "
                                    "profile set payment.methods '<json>'"])  # fmt: skip
        area = {"payment": {"methods": [{"name": "Card", "instructions": "Call (604) 555-0100."}]}}
        self.assertEqual(follow_contact(area, "business.phone", "+1 604 555 0100", "+1 778 555 0100"), (["Card"], []))

    def test_a_phone_change_leaves_account_numbers_alone(self):
        """An independent review: an account number that ended with the phone's digits, and a 7-digit reference
        equal to its last 7 digits, were rewritten as the new phone number."""
        profile = demo_profile("CAD")
        profile["payment"]["methods"] = [
            {"name": "Bank", "instructions": "Account 99916045550100, ref 5550100."},
            {"name": "Card", "instructions": "Call (604) 555-0100, 604.555.0100 or 1-604-555-0100."},
        ]  # fmt: skip
        changed, _ = follow_contact(profile, "business.phone", "+1 604 555 0100", "+1 778 555 0123")
        self.assertEqual(changed, ["Card"])
        self.assertEqual([m["instructions"] for m in profile["payment"]["methods"]], [
            "Account 99916045550100, ref 5550100.",
            "Call +1 778 555 0123, +1 778 555 0123 or +1 778 555 0123.",
        ])  # fmt: skip
        uk = {"payment": {"methods": [{"name": "Phone", "instructions": "Ring 020 7946 0100. Sort code 20-79-46."}]}}
        self.assertEqual(follow_contact(uk, "business.phone", "+44 20 7946 0100", "+44 20 7946 0999")[0], ["Phone"])
        self.assertEqual(uk["payment"]["methods"][0]["instructions"], "Ring +44 20 7946 0999. Sort code 20-79-46.")

    def _gst_only(self, inclusive=False, costs_include_tax=False):
        profile = self.store.profile()
        profile["tax"]["taxes"] = [{"code": "GST", "name": "GST", "rate": "10", "applies_to": EVERY}]
        profile["tax"]["prices_include_tax"] = inclusive
        profile["tax"]["costs_include_tax"] = costs_include_tax
        self.store.save_profile(profile)
        return self.store.profile()

    def test_markup_with_tax_inclusive_prices(self):
        profile = self._gst_only(inclusive=True)
        spec = [{"kind": "material", "description": "Flex", "qty": 1, "unit_cost": "28", "markup_pct": "20"}]
        lines, _, _ = normalize_lines(profile, [], spec, 2)
        # 28 + 20% = 33.60 before GST; the customer sees 36.96 with GST included.
        self.assertEqual(lines[0]["unit_price"], "36.96")
        self.assertEqual(Decimal(lines[0]["unit_cost"]), Decimal("28"))

    def test_supplier_costs_that_include_tax(self):
        profile = self._gst_only(costs_include_tax=True)
        spec = [{"kind": "material", "description": "Flex", "qty": 1, "unit_cost": "110", "markup_pct": "20"}]
        lines, _, _ = normalize_lines(profile, [], spec, 2)
        # 110 paid including 10% GST is 100 before tax; +20% markup = 120 before tax.
        self.assertEqual(lines[0]["unit_price"], "120.00")
        self.assertEqual(Decimal(lines[0]["unit_cost"]), Decimal("100"))

    def test_bad_lines(self):
        profile, book = self.store.profile(), self.store.pricebook()
        for spec in (
            [{"kind": "material", "description": "x", "qty": 1}],
            [{"kind": "fee", "description": "x", "unit_price": 5, "tax_codes": ["VAT"]}],
            [{"kind": "fee", "description": "x", "unit_price": 5, "qty": 0}],
            [{"kind": "spaceship", "description": "x", "unit_price": 5}],
            [{"kind": "text", "description": "only text"}],
        ):
            with self.assertRaises(JTPError):
                normalize_lines(profile, book, spec, 2)


class WorkflowTests(StoreCase):
    def _quote(self, deposit=None):
        spec = {
            "title": "Water heater",
            "options": [
                {
                    "name": "Good",
                    "lines": [
                        {"kind": "material", "description": "Tank", "qty": 1, "unit_cost": "800"},
                        {"kind": "labour", "description": "Install", "qty": 4},
                    ],
                },
                {
                    "name": "Better",
                    "recommended": True,
                    "lines": [
                        {"kind": "material", "description": "HE tank", "qty": 1, "unit_cost": "1200"},
                        {"kind": "labour", "description": "Install", "qty": 4.5},
                        {"kind": "discount", "percent": 5},
                    ],
                },
            ],
        }
        if deposit:
            spec["deposit_percent"] = deposit
        return docs.quote_create(self.store, spec, customer_id=self.cust["id"])["quote"]

    def test_quote_to_paid(self):
        quote = self._quote(deposit=25)
        docs.quote_mark_sent(self.store, quote["id"], "email", "jane@example.com")
        with self.assertRaises(JTPError):
            docs.quote_accept(self.store, quote["id"], None, None, None, None)
        accepted = docs.quote_accept(self.store, quote["id"], "B", "Jane", "email", None)
        job_id = accepted["job"]
        docs.payment_record(self.store, job_id=job_id, amount=accepted["deposit_due"], method="Bank", deposit=True)
        co = docs.co_create(
            self.store,
            job_id,
            {
                "title": "Extra valve",
                "lines": [{"kind": "material", "description": "Valve", "qty": 1, "unit_cost": "20"}],
            },
        )["change_order"]
        draft_co = docs.co_create(
            self.store,
            job_id,
            {"title": "Not approved", "lines": [{"kind": "fee", "description": "Thing", "unit_price": "10"}]},
        )["change_order"]
        docs.co_approve(self.store, co["id"], "Jane", "sms", None)
        records.job_log(
            self.store, job_id, "material", {"description": "Connector", "qty": 1, "unit_cost": "10", "extra": True}
        )
        records.job_log(self.store, job_id, "time", {"hours": "6"})
        result = docs.invoice_create(self.store, job_id=job_id)
        inv = result["invoice"]
        self.assertTrue(any(draft_co["id"] in w for w in result["warnings"]))
        sources = [ln.get("source") or {} for ln in inv["lines"]]
        self.assertTrue(any(s.get("change_order") == co["id"] for s in sources))
        self.assertTrue(any(s.get("log") for s in sources))
        issued = docs.invoice_issue(self.store, inv["id"])
        self.assertEqual(issued["deposits_applied"][0]["amount"], accepted["deposit_due"])
        state = docs.invoice_state(self.store.doc(inv["id"]), self.store.today())
        with self.assertRaises(JTPError):
            docs.payment_record(self.store, invoice_id=inv["id"], amount=str(state["balance"] + 1), method="Card")
        docs.payment_record(self.store, invoice_id=inv["id"], amount=str(state["balance"]), method="Card")
        self.assertEqual(self.store.doc(inv["id"])["status"], "paid")
        self.assertEqual(self.store.job(job_id)["status"], "paid")

    def test_progress_billing_bills_exact_net(self):
        quote = self._quote()
        job_id = docs.quote_accept(self.store, quote["id"], "B", None, None, None)["job"]
        first = docs.invoice_create(self.store, job_id=job_id, progress="33")["invoice"]
        docs.invoice_issue(self.store, first["id"])
        with self.assertRaises(JTPError):
            docs.invoice_create(self.store, job_id=job_id, progress="70")
        second = docs.invoice_create(self.store, job_id=job_id, progress="33")["invoice"]
        docs.invoice_issue(self.store, second["id"])
        final = docs.invoice_create(self.store, job_id=job_id)["invoice"]
        option = next(o for o in self.store.doc(quote["id"])["options"] if o["key"] == "B")
        billed_net = sum(Decimal(i["totals"]["net_excl_tax"]) for i in (first, second, final))
        self.assertEqual(billed_net, Decimal(option["totals"]["net_excl_tax"]))
        with self.assertRaises(JTPError):
            docs.invoice_create(self.store, job_id=job_id, from_quote=True)

    def test_numbering_has_no_gaps(self):
        first = docs.invoice_create(
            self.store,
            customer_id=self.cust["id"],
            spec={"title": "A", "lines": [{"kind": "fee", "description": "x", "unit_price": 10}]},
        )["invoice"]
        second = docs.invoice_create(
            self.store,
            customer_id=self.cust["id"],
            spec={"title": "B", "lines": [{"kind": "fee", "description": "y", "unit_price": 10}]},
        )["invoice"]
        with self.assertRaises(JTPError):
            docs.invoice_delete(self.store, first["id"])
        docs.invoice_delete(self.store, second["id"])
        again = docs.invoice_create(
            self.store,
            customer_id=self.cust["id"],
            spec={"title": "C", "lines": [{"kind": "fee", "description": "z", "unit_price": 10}]},
        )["invoice"]
        self.assertEqual(again["id"], second["id"])

    def test_void_issued_invoice_releases_work(self):
        job = records.job_create(self.store, self.cust["id"], "Leak", pricing="tm")
        records.job_log(self.store, job["id"], "time", {"hours": "2"})
        inv = docs.invoice_create(self.store, job_id=job["id"], tm=True)["invoice"]
        docs.invoice_issue(self.store, inv["id"])
        out = owner_approves(docs.invoice_void, self.store, inv["id"], "wrong customer")
        self.assertEqual(out["credit_note"]["totals"]["total"], inv["totals"]["total"])
        self.assertIsNone(self.store.job(job["id"])["log"][0]["invoice"])
        again = docs.invoice_create(self.store, job_id=job["id"], tm=True)["invoice"]
        self.assertEqual(again["totals"]["total"], inv["totals"]["total"])

    def test_credit_note_exact_amount_across_tax_groups(self):
        job = records.job_create(self.store, self.cust["id"], "Mixed", pricing="tm")
        records.job_log(self.store, job["id"], "time", {"hours": "3.3"})
        records.job_log(self.store, job["id"], "material", {"description": "Part", "qty": 1, "unit_cost": "77.77"})
        inv = docs.invoice_create(self.store, job_id=job["id"], tm=True)["invoice"]
        docs.invoice_issue(self.store, inv["id"])
        for amount in ("0.01", "13.37", "99.99"):
            credit = docs.credit_create(self.store, inv["id"], amount=amount, reason="Goodwill", dry_run=True)
            self.assertEqual(credit["credit_note"]["totals"]["total"], amount)

    def test_duplicate_payment_is_ignored(self):
        inv = docs.invoice_create(
            self.store,
            customer_id=self.cust["id"],
            spec={"title": "A", "lines": [{"kind": "fee", "description": "x", "unit_price": 100}]},
        )["invoice"]
        docs.invoice_issue(self.store, inv["id"])
        docs.payment_record(self.store, invoice_id=inv["id"], amount="20", method="Cash")
        dup = docs.payment_record(self.store, invoice_id=inv["id"], amount="20", method="Cash")
        self.assertTrue(dup["duplicate"])
        self.assertEqual(len(self.store.doc(inv["id"])["payments"]), 1)

    def test_tax_must_be_confirmed(self):
        profile = self.store.profile()
        profile["tax"]["confirmed"] = False
        self.store.save_profile(profile)
        with self.assertRaises(JTPError):
            self._quote()
        preview = docs.quote_create(
            self.store,
            {"title": "x", "lines": [{"kind": "fee", "description": "x", "unit_price": 1}]},
            customer_id=self.cust["id"],
            dry_run=True,
        )
        self.assertEqual(preview["quote"]["id"], "PREVIEW")

    def _issued(self, amount="100"):
        spec = {"title": "A", "lines": [{"kind": "fee", "description": "Work", "unit_price": amount}]}
        inv = docs.invoice_create(self.store, customer_id=self.cust["id"], spec=spec)["invoice"]
        return docs.invoice_issue(self.store, inv["id"])["invoice"]

    def test_deposit_only_above_threshold(self):
        profile = self.store.profile()
        profile["terms"]["deposit_percent"] = "25"
        profile["terms"]["deposit_min_total"] = "1000"
        self.store.save_profile(profile)
        spec = {
            "title": "Two sizes",
            "options": [
                {"name": "Small", "lines": [{"kind": "fee", "description": "Small job", "unit_price": "200"}]},
                {"name": "Big", "lines": [{"kind": "fee", "description": "Big job", "unit_price": "2000"}]},
            ],
        }
        quote = docs.quote_create(self.store, spec, customer_id=self.cust["id"], dry_run=True)["quote"]
        self.assertIsNone(quote["options"][0]["deposit_due"])
        self.assertEqual(quote["options"][1]["deposit_due"], "525.00")  # 25% of 2,100 incl. GST
        explicit = docs.quote_create(
            self.store, dict(spec, deposit_percent=25), customer_id=self.cust["id"], dry_run=True
        )["quote"]
        self.assertEqual(explicit["options"][0]["deposit_due"], "52.50")  # asked for, so always taken

    def test_change_order_hours_count_as_quoted(self):
        quote = self._quote()
        job_id = docs.quote_accept(self.store, quote["id"], "A", "Jane", "email", None)["job"]
        spec = {"title": "Extra", "lines": [{"kind": "labour", "description": "Reroute", "qty": 2}]}
        co = docs.co_create(self.store, job_id, spec)["change_order"]
        self.assertEqual(reports.job_summary(self.store, job_id)["hours_quoted"], "4")
        docs.co_approve(self.store, co["id"], "Jane", "text", None)
        self.assertEqual(reports.job_summary(self.store, job_id)["hours_quoted"], "6")

    def test_full_credit_marks_invoice_credited(self):
        inv = self._issued()
        credit = owner_approves(docs.credit_create, self.store, inv["id"], amount="105", reason="Cancelled")
        credit = credit["credit_note"]
        state = docs.invoice_state(self.store.doc(inv["id"]), self.store.today())
        self.assertEqual((state["status"], state["balance"]), ("credited", Decimal("0")))
        sent = docs.credit_mark_sent(self.store, credit["id"], "email", "jane@example.com")
        self.assertEqual(sent["credit_note"]["sent"][0]["channel"], "email")
        again = docs.credit_mark_sent(self.store, credit["id"], "email", "jane@example.com")
        self.assertTrue(again["already_recorded"])

    def test_cash_basis_tax_follows_payments(self):
        inv = self._issued("1000")  # 1,050.00 including 5% GST
        docs.payment_record(self.store, invoice_id=inv["id"], amount="420", method="Cash")
        os.environ["JTP_TODAY"] = "2026-11-02"
        docs.payment_record(self.store, invoice_id=inv["id"], amount="630", method="Cash")
        october = reports.tax_report(self.store, "2026-10-01", "2026-10-31", basis="cash")
        november = reports.tax_report(self.store, "2026-11-01", "2026-11-30", basis="cash")
        accrual = reports.tax_report(self.store, "2026-10-01", "2026-10-31")
        self.assertEqual(october["taxes"]["GST"]["tax_charged"], "$20.00 CAD")
        self.assertEqual(november["taxes"]["GST"]["tax_charged"], "$30.00 CAD")
        self.assertEqual(accrual["taxes"]["GST"]["tax_charged"], "$50.00 CAD")

    def test_exports_include_credit_notes(self):
        inv = self._issued()
        owner_approves(docs.credit_create, self.store, inv["id"], amount="21", reason="Goodwill")
        with open(reports.export(self.store, "invoices", None)["file"], encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        credit = [r for r in rows if r["type"] == "credit_note"]
        self.assertEqual((credit[0]["total"], credit[0]["credits_invoice"]), ("-21.00", inv["id"]))
        with open(reports.export(self.store, "lines", None)["file"], encoding="utf-8") as handle:
            lines = [r for r in csv.DictReader(handle) if r["type"] == "credit_note"]
        self.assertEqual(lines[0]["net_excl_tax"], "-20.00")

    def test_deposit_as_a_percent_of_the_accepted_quote(self):
        quote = self._quote()
        job_id = docs.quote_accept(self.store, quote["id"], "B", "Jane", "email", None)["job"]
        amount, total = docs.deposit_for_percent(self.store, job_id, "25%")
        option_b = next(o for o in self.store.doc(quote["id"])["options"] if o["key"] == "B")
        self.assertEqual(total, Decimal(option_b["totals"]["total"]))
        self.assertEqual(amount, (total * Decimal("0.25")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        no_quote = records.job_create(self.store, self.cust["id"], "Not quoted yet")
        with self.assertRaises(JTPError):
            docs.deposit_for_percent(self.store, no_quote["id"], "25")

    def test_preview_without_a_customer(self):
        spec = {"title": "Sample", "lines": [{"kind": "fee", "description": "Call-out", "unit_price": 10}]}
        quote = docs.quote_create(self.store, spec, dry_run=True)["quote"]
        self.assertEqual((quote["id"], quote["customer_id"]), ("PREVIEW", "SAMPLE"))
        self.assertFalse(self.store.docs("quote") or self.store.jobs())
        result = render_doc(self.store, quote, fmt="html", engine="builtin")
        html = Path(result["files"]["html"]).read_text(encoding="utf-8")
        self.assertIn("Sample Customer", html)
        self.assertIn("DRAFT", html)

    def _fixed_quote(self, lines, job_id=None, **extra):
        spec = dict({"title": "Fixed", "lines": lines}, **extra)
        if job_id:
            return docs.quote_create(self.store, spec, job_id=job_id)["quote"]
        return docs.quote_create(self.store, spec, customer_id=self.cust["id"])["quote"]

    def test_invoice_equals_quote_plus_change_orders_to_the_cent(self):
        # Rounding tax on the combined total would make this invoice a cent more than the two documents.
        lines = [
            {"kind": "other", "description": d, "unit_price": p}
            for d, p in (("A", "1687.50"), ("B", "81.25"), ("C", "427.50"))
        ]
        quote = self._fixed_quote(lines)
        job_id = docs.quote_accept(self.store, quote["id"], None, "Jane", "email", None)["job"]
        co = docs.co_create(
            self.store,
            job_id,
            {
                "title": "Valve",
                "lines": [
                    {"kind": "other", "description": "Valve", "unit_price": "30"},
                    {"kind": "other", "description": "Fit valve", "unit_price": "71.25"},
                ],
            },
        )["change_order"]
        docs.co_approve(self.store, co["id"], "Jane", "text", None)
        invoice = docs.invoice_create(self.store, job_id=job_id)["invoice"]
        quoted = Decimal(self.store.doc(quote["id"])["options"][0]["totals"]["total"])
        changed = Decimal(self.store.doc(co["id"])["totals"]["total"])
        self.assertEqual(Decimal(invoice["totals"]["total"]), quoted + changed)

    def test_progress_invoices_add_up_to_the_quote_to_the_cent(self):
        quote = self._fixed_quote([{"kind": "other", "description": "Work", "unit_price": "101"}])
        job_id = docs.quote_accept(self.store, quote["id"], None, "Jane", "email", None)["job"]
        first = docs.invoice_create(self.store, job_id=job_id, progress="30")["invoice"]
        docs.invoice_issue(self.store, first["id"])
        final = docs.invoice_create(self.store, job_id=job_id)["invoice"]
        quoted = Decimal(self.store.doc(quote["id"])["options"][0]["totals"]["total"])  # 106.05
        self.assertEqual(Decimal(first["totals"]["total"]) + Decimal(final["totals"]["total"]), quoted)

    def test_accepting_a_quote_retires_the_other_quotes_on_its_job(self):
        first = self._fixed_quote([{"kind": "fee", "description": "Option one", "unit_price": "100"}])
        second = self._fixed_quote(
            [{"kind": "fee", "description": "Option two", "unit_price": "150"}], job_id=first["job_id"]
        )
        for doc in (first, second):
            docs.quote_mark_sent(self.store, doc["id"], "email", "jane@example.com")
        out = docs.quote_accept(self.store, second["id"], None, "Jane", "email", None)
        self.assertEqual(out["replaced_quotes"], [first["id"]])
        self.assertEqual(self.store.doc(first["id"])["status"], "superseded")
        os.environ["JTP_TODAY"] = "2026-11-01"
        quoted = [a["doc"] for a in followups.due_actions(self.store) if a["action"].startswith("quote")]
        self.assertNotIn(first["id"], quoted)
        with self.assertRaises(JTPError):
            docs.quote_accept(self.store, first["id"], None, "Jane", "email", None)

    def test_old_quote_on_another_job_gets_a_heads_up(self):
        old = self._fixed_quote([{"kind": "fee", "description": "Old", "unit_price": "100"}])
        docs.quote_mark_sent(self.store, old["id"], "email", "jane@example.com")
        created = docs.quote_create(
            self.store,
            {"title": "New", "lines": [{"kind": "fee", "description": "New", "unit_price": "120"}]},
            customer_id=self.cust["id"],
        )
        self.assertTrue(any(old["id"] in w for w in created["warnings"]))
        accepted = docs.quote_accept(self.store, created["quote"]["id"], None, "Jane", "email", None)
        self.assertEqual([o["id"] for o in accepted["other_open_quotes"]], [old["id"]])
        os.environ["JTP_TODAY"] = "2026-10-20"
        nudge = next(a for a in followups.due_actions(self.store) if a.get("doc") == old["id"])
        self.assertIn(f"jtp quote supersede {old['id']} --with {created['quote']['id']}", nudge["heads_up"])
        brief = reports.brief(self.store)["text"]  # the short text says to check first, not to chase
        self.assertIn(f"Check with you first (don't chase yet):\n  - {old['id']}: Jane Doe accepted", brief)
        self.assertNotIn("quote follow-up", brief)
        docs.quote_supersede(self.store, old["id"], created["quote"]["id"])
        self.assertFalse([a for a in followups.due_actions(self.store) if a.get("doc") == old["id"]])

    def test_estimates_stay_estimates_until_the_owner_confirms(self):
        quote = self._fixed_quote(
            [
                {"kind": "labour", "description": "Install", "qty": 3, "estimate": True},
                {"kind": "fee", "description": "Call-out", "unit_price": "80"},
            ]
        )
        html = Path(render_doc(self.store, self.store.doc(quote["id"]), fmt="html", engine="builtin")["files"]["html"])
        self.assertIn("Install (estimate)", html.read_text(encoding="utf-8"))
        job_id = docs.quote_accept(self.store, quote["id"], None, "Jane", "email", None)["job"]
        created = docs.invoice_create(self.store, job_id=job_id)
        self.assertEqual(created["estimates"], ["Install"])
        inv_id = created["invoice"]["id"]
        asked = self._asked(inv_id)
        self.assertIn("Install: 3 hr at $95.00 = $285.00", asked.ask_owner)  # the question for the owner
        for not_an_answer in ("", " ", "<their exact words>"):
            with self.assertRaises(JTPError):
                docs.invoice_issue(self.store, inv_id, owner_confirmed=not_an_answer, confirm_code=asked.confirm)
        with self.assertRaises(JTPError) as caught:  # a yes with no code isn't tied to the question
            docs.invoice_issue(self.store, inv_id, owner_confirmed="yes")
        self.assertEqual(caught.exception.code, "needs_owner")
        issued = docs.invoice_issue(self.store, inv_id, owner_confirmed="Yes, they're final",
                                    confirm_code=asked.confirm)["invoice"]  # fmt: skip
        self.assertFalse([ln for ln in issued["lines"] if ln.get("estimate")])
        self.assertEqual(issued["estimates_confirmed"]["owner_said"], "Yes, they're final")

    def _estimate_invoice(self, qty=3):
        quote = self._fixed_quote([{"kind": "labour", "description": "Install", "qty": qty, "estimate": True},
                                   {"kind": "fee", "description": "Call-out", "unit_price": "80"}])  # fmt: skip
        job_id = docs.quote_accept(self.store, quote["id"], None, "Jane", "email", None)["job"]
        return docs.invoice_create(self.store, job_id=job_id)["invoice"]["id"]

    def _asked(self, inv_id):
        with self.assertRaises(JTPError) as caught:
            docs.invoice_issue(self.store, inv_id)
        self.assertEqual(caught.exception.code, "estimates_unconfirmed")
        self.assertTrue(caught.exception.confirm)
        return caught.exception

    def test_only_a_plain_yes_confirms_the_estimates(self):
        """An independent review of 1.0.0: a vague "ok" counted as the owner's yes."""
        inv_id = self._estimate_invoice()
        code = self._asked(inv_id).confirm
        for reply in NOT_A_YES:
            with self.subTest(reply=reply), self.assertRaises(JTPError) as caught:
                docs.invoice_issue(self.store, inv_id, owner_confirmed=reply, confirm_code=code)
            self.assertEqual(caught.exception.code, "owner_declined" if reply == "absolutely not" else "unclear_answer")
            if reply != "absolutely not":
                self.assertTrue(caught.exception.ask_owner and caught.exception.confirm)  # asked again, with a code
        self.assertEqual(self.store.doc(inv_id)["status"], "draft")
        for reply in ("yes", "Yep!", "Yes, they're final.", "yes, issue it", "yes please", "go ahead",
                      "yes, that's right"):
            self.assertTrue(confirm.plain_yes(reply, docs.ESTIMATES_FINAL_WORDS), reply)
        self.assertEqual(docs.invoice_issue(self.store, inv_id, owner_confirmed="yes",
                                            confirm_code=code)["invoice"]["status"], "issued")  # fmt: skip

    def test_the_yes_belongs_to_one_invoice_and_its_figures_and_counts_once(self):
        first, second = self._estimate_invoice(), self._estimate_invoice()
        code = self._asked(first).confirm
        with self.assertRaises(JTPError) as caught:  # another invoice's code
            docs.invoice_issue(self.store, second, owner_confirmed="yes", confirm_code=code)
        self.assertEqual(caught.exception.code, "needs_owner")
        inv = self.store.doc(first)
        spec = {"title": inv["title"], "lines": lines_as_spec(inv["lines"])}
        next(ln for ln in spec["lines"] if ln.get("estimate"))["qty"] = 4  # the figure changed after asking
        docs.invoice_update(self.store, first, spec)
        with self.assertRaises(JTPError) as caught:
            docs.invoice_issue(self.store, first, owner_confirmed="yes", confirm_code=code)
        self.assertEqual(caught.exception.code, "needs_owner")
        self.assertIn("4 hr", caught.exception.ask_owner)  # asked afresh, about the new figure
        fresh = caught.exception.confirm
        docs.invoice_issue(self.store, first, owner_confirmed="yes", confirm_code=fresh)
        used = json.loads((self.store.home / confirm.USED_FILE).read_text(encoding="utf-8"))
        self.assertIn(fresh, used)  # kept until it would have expired, so it never works twice
        code = confirm.code_for(self.store.home, "invoice.issue|INV-9999|x")
        self.assertEqual(confirm.owner_yes(self.store.home, "invoice.issue|INV-9999|x", "Final?", "yes", code, why=""),
                         "yes")  # fmt: skip
        confirm.use(self.store.home, code)
        with self.assertRaises(JTPError) as caught:
            confirm.owner_yes(self.store.home, "invoice.issue|INV-9999|x", "Final?", "yes", code, why="")
        self.assertIn("already used", caught.exception.message)

    def test_an_estimate_cant_be_dropped_by_editing_the_invoice(self):
        """An independent review of 1.0.1: taking "estimate" off the lines with invoice update let the invoice issue
        with no question, no code and nothing recorded, and the hint said to do it."""
        inv_id = self._estimate_invoice()
        inv = self.store.doc(inv_id)
        spec = {"title": inv["title"], "lines": lines_as_spec(inv["lines"])}
        for line in spec["lines"]:
            line.pop("estimate", None)
        out = docs.invoice_update(self.store, inv_id, spec)
        self.assertIn('Kept "(estimate)" on Install', " ".join(out["warnings"]))
        asked = self._asked(inv_id)
        self.assertIn("Install: 3 hr", asked.ask_owner)
        self.assertNotIn('remove "estimate"', asked.hint)
        spec["lines"][0]["description"] = "Install and test"  # a renamed line loses its mark; the invoice remembers
        docs.invoice_update(self.store, inv_id, spec)
        self.assertFalse([ln for ln in self.store.doc(inv_id)["lines"] if ln.get("estimate")])
        asked = self._asked(inv_id)
        self.assertIn("Install: 3 hr at $95.00 = $285.00 CAD was an estimate, since changed or taken off it. Are these "
                      "figures final?", asked.ask_owner)  # the renamed estimate is named  # fmt: skip
        self.assertIn("Install and test: 3 hr", asked.ask_owner)  # with no line marked, every figure is named
        self.assertIn("Call-out", asked.ask_owner)
        issued = docs.invoice_issue(self.store, inv_id, owner_confirmed="yes", confirm_code=asked.confirm)["invoice"]
        self.assertEqual((issued["status"], issued["estimates_confirmed"]["owner_said"]), ("issued", "yes"))

    def _estimated_quote_job(self):
        quote = self._fixed_quote([{"kind": "material", "description": "Valve", "qty": 1, "unit_cost": "100",
                                    "estimate": True}, {"kind": "labour", "description": "Install", "qty": 3}])  # fmt: skip
        return docs.quote_accept(self.store, quote["id"], None, "Jane", "email", None)["job"]

    def _issue_with_the_owners_yes(self, inv_id):
        asked = self._asked(inv_id)
        docs.invoice_issue(self.store, inv_id, owner_confirmed="yes", confirm_code=asked.confirm)
        return asked.ask_owner

    def test_progress_deposit_and_balance_invoices_keep_the_quotes_estimates(self):
        """An independent review of 1.0.1: a progress, deposit or balance invoice from a quote dropped the estimate
        mark and issued with no owner's yes (--progress 100 billed the whole job unasked)."""
        routes = {"progress 100": [{"progress": "100"}], "progress 50, then the balance": [{"progress": "50"}, {}],
                  "deposit 25, then the balance": [{"progress": "25", "deposit_invoice": True}, {}]}  # fmt: skip
        for route, steps in routes.items():
            with self.subTest(route=route):
                job_id = self._estimated_quote_job()
                for step in steps:
                    inv = docs.invoice_create(self.store, job_id=job_id, **step)["invoice"]
                    question = self._issue_with_the_owners_yes(inv["id"])
                    self.assertIn("these figures are based on estimates on the quote. Are they final?", question)
                    self.assertIn("Valve", question)
                    self.assertNotIn("Install", question)  # only the estimated line's part is named
                    self.assertEqual(self.store.doc(inv["id"])["status"], "issued")
                self.assertEqual(len(self.store.job(job_id)["invoices"]), len(steps))

    def _estimated_draft(self, *extra):
        spec = {"title": "Call-out", "lines": [{"kind": "fee", "description": "Call-out", "unit_price": "89",
                                                "estimate": True}, *extra]}  # fmt: skip
        return docs.invoice_create(self.store, customer_id=self.cust["id"], spec=spec)["invoice"]

    def _stripped_copy(self, draft):
        lines = lines_as_spec(draft["lines"])
        self.assertTrue(lines[0].get("estimate"))  # the export keeps the mark
        for line in lines:
            line.pop("estimate", None)
        return docs.invoice_create(self.store, customer_id=self.cust["id"], spec={"title": "Copy", "lines": lines})

    def test_a_draft_deleted_or_voided_with_estimates_is_remembered(self):
        """An independent review of 1.0.1: export an estimated draft with --as-spec, strip the mark, delete the
        draft and make it again, and it issued unasked."""
        draft = self._estimated_draft({"kind": "labour", "description": "Work", "qty": 2})
        copy_lines = lines_as_spec(draft["lines"])
        deleted = docs.invoice_delete(self.store, draft["id"])
        self.assertTrue(deleted["warnings"][0].startswith("The deleted draft had estimates the owner never confirmed "
                                                          "(Call-out: 1 ea at $89.00 = $89.00 CAD)"))  # fmt: skip
        record = self.store.customer(self.cust["id"])["unconfirmed_estimates"][0]
        self.assertNotIn("invoice", record)  # its number is freed for the next invoice, so it isn't kept
        for line in copy_lines:
            line.pop("estimate", None)
        again = docs.invoice_create(self.store, customer_id=self.cust["id"], spec={"title": "C", "lines": copy_lines})
        self.assertEqual(again["invoice"]["id"], draft["id"])  # the same number, released by the delete
        self.assertIn("Call-out: matches an estimate for Jane Doe that the owner hasn't confirmed (Call-out: 1 ea at "
                      "$89.00 = $89.00 CAD, on a deleted draft)", " ".join(again["warnings"]))  # fmt: skip
        question = self._asked(again["invoice"]["id"]).ask_owner
        self.assertEqual(question, f"Before I finalize invoice {draft['id']}: these lines match estimates for Jane Doe "
                         "that you haven't confirmed (Call-out: 1 ea at $89.00 = $89.00 CAD, on a deleted draft). Are "
                         "these figures final? Call-out: 1 ea at $89.00 = $89.00 CAD")  # not Work  # fmt: skip
        self._issue_with_the_owners_yes(again["invoice"]["id"])
        self.assertNotIn("unconfirmed_estimates", self.store.customer(self.cust["id"]))  # confirmed: forgotten
        draft = docs.invoice_create(self.store, customer_id=self.cust["id"], spec={"title": "Call-out", "lines": [
            {"kind": "fee", "description": "Call-out", "unit_price": "95", "estimate": True}]})["invoice"]  # fmt: skip
        voided = owner_approves(docs.invoice_void, self.store, draft["id"], "Wrong job")
        self.assertIn("never confirmed", voided["warnings"][0])
        copy = self._stripped_copy(draft)["invoice"]["id"]
        self.assertIn(f"(Call-out: 1 ea at $95.00 = $95.00 CAD, on voided draft {draft['id']})",
                      self._asked(copy).ask_owner)  # fmt: skip

    def _customer(self, name):
        email = name.lower().replace(" ", ".") + "@x.example"
        return records.customer_add(self.store, {"name": name, "email": email})["customer"]["id"]

    def _draft_for(self, customer, *lines):
        return docs.invoice_create(self.store, customer_id=customer, spec={"title": "T", "lines": list(lines)})[
            "invoice"]["id"]  # fmt: skip

    def test_an_estimate_moved_to_another_invoice_asks(self):
        """An independent review of 1.0.2: an estimated line removed from draft A (or renamed there), then retyped
        without the mark on a new invoice B for the same customer, issued unasked, and A's own question named the
        wrong line."""
        call_out = {"kind": "fee", "description": "Call-out", "unit_price": "89"}
        repair = {"kind": "labour", "description": "Repair", "qty": 2}
        renamed = {"kind": "fee", "description": "Call-out fee", "unit_price": "89"}
        for route, kept in (("removed", [repair]), ("renamed", [repair, renamed])):
            with self.subTest(route=route):
                customer = self._customer(f"Moved {route}")
                a = self._draft_for(customer, dict(call_out, estimate=True), repair)
                self.assertEqual(docs.invoice_update(self.store, a, {"lines": kept})["warnings"], [])
                b = docs.invoice_create(self.store, customer_id=customer, spec={"lines": [call_out]})
                self.assertIn(f"Call-out: matches an estimate for Moved {route} that the owner hasn't confirmed "
                              f"(Call-out: 1 ea at $89.00 = $89.00 CAD, taken off draft {a})", " ".join(b["warnings"]))
                self.assertIn(f"$89.00 CAD, taken off draft {a})", self._asked(b["invoice"]["id"]).ask_owner)
                self.assertTrue(self._asked(a).ask_owner.startswith(
                    f"Before I finalize invoice {a}: Call-out: 1 ea at $89.00 = $89.00 CAD was an estimate, since "
                    "changed or taken off it. Are these figures final? Repair: 2 hr"))  # the right line, named
                other = self._draft_for(customer, repair)  # Repair was never an estimate: not asked
                self.assertEqual(docs.invoice_issue(self.store, other)["invoice"]["status"], "issued")
                owner_approves(docs.invoice_void, self.store, a, "Wrong job")
                remembered = self.store.customer(customer)["unconfirmed_estimates"][0]["lines"]
                self.assertEqual([(i["key"], i["amount"]) for i in remembered], [("call out", "89")])
                self.assertIn(f"$89.00 CAD, on voided draft {a})", self._asked(b["invoice"]["id"]).ask_owner)

    def test_an_estimate_taken_off_an_issued_invoice_still_counts(self):
        """Taking an estimate off a draft and issuing it leaves the estimate unconfirmed, so the same figure on
        another invoice still asks. Lines that never were estimates don't."""
        customer = self._customer("Taken Off")
        call_out = {"kind": "fee", "description": "Call-out", "unit_price": "89"}
        labour = {"kind": "labour", "description": "Labour", "qty": 1}
        a = self._draft_for(customer, dict(call_out, estimate=True), labour)
        docs.invoice_update(self.store, a, {"lines": [labour]})
        self.assertIn("Call-out: 1 ea at $89.00 = $89.00 CAD was an estimate", self._issue_with_the_owners_yes(a))
        self.assertEqual(self.store.doc(a)["estimates_confirmed"]["taken_off"][0]["key"], "call out")
        self.assertEqual(docs.invoice_issue(self.store, self._draft_for(customer, labour))["invoice"]["status"],
                         "issued")  # Labour was never an estimate: not asked  # fmt: skip
        copy = self._draft_for(customer, call_out)
        self.assertIn(f"(Call-out: 1 ea at $89.00 = $89.00 CAD, changed or taken off invoice {a} before it was issued)",
                      self._asked(copy).ask_owner)  # fmt: skip

    def test_an_open_estimate_only_matches_its_own_figure_and_a_yes_sticks(self):
        """An independent review of 1.0.2: while an estimated "Labour 4 hr" draft was open, every "Labour 1 hr"
        invoice for that customer asked, again after every yes, because only the description was matched."""
        customer = self._customer("Busy Customer")
        labour = {"kind": "labour", "description": "Labour", "qty": 1}
        estimate = self._draft_for(customer, {"kind": "labour", "description": "Labour", "qty": 4, "estimate": True})
        for _ in range(3):  # a different figure: not a copy of the estimate
            self.assertEqual(docs.invoice_issue(self.store, self._draft_for(customer, labour))["invoice"]["status"],
                             "issued")  # fmt: skip
        same = {"kind": "labour", "description": "LABOUR", "qty": 4}
        self.assertIn(f"on draft {estimate}", self._issue_with_the_owners_yes(self._draft_for(customer, same)))
        self.assertEqual(docs.invoice_issue(self.store, self._draft_for(customer, same))["invoice"]["status"],
                         "issued")  # the owner's yes to that figure sticks while the draft stays open  # fmt: skip
        self._asked(estimate)  # the estimate's own invoice still asks

    def test_an_accepted_quotes_estimates_are_tracked(self):
        """An independent review of 1.0.2: an accepted quote's estimated lines retyped with invoice create --spec
        (on its job or for its customer) issued unasked."""
        quote = self._fixed_quote([{"kind": "material", "description": "Heater", "qty": 1, "unit_cost": "900",
                                    "estimate": True}, {"kind": "labour", "description": "Install", "qty": 3}])  # fmt: skip
        job_id = docs.quote_accept(self.store, quote["id"], None, "Jane", "email", None)["job"]
        retyped = [{"kind": "material", "description": "Heater", "qty": 1, "unit_price": "1125"},
                   {"kind": "labour", "description": "Install", "qty": 3}]  # fmt: skip
        on_job = docs.invoice_create(self.store, job_id=job_id, spec={"lines": retyped})
        self.assertIn(f"$1,125.00 CAD, on quote {quote['id']})", " ".join(on_job["warnings"]))
        question = self._asked(on_job["invoice"]["id"]).ask_owner
        self.assertIn("these lines match estimates for Jane Doe that you haven't confirmed (Heater: 1 ea at "
                      f"$1,125.00 = $1,125.00 CAD, on quote {quote['id']})", question)  # fmt: skip
        self.assertIn("Heater: 1 ea at $1,125.00", question)
        self.assertNotIn("Install", question)  # not an estimate on the quote
        for_customer = self._draft_for(self.cust["id"], *retyped)
        self._issue_with_the_owners_yes(for_customer)
        again = self._draft_for(self.cust["id"], *retyped)  # the owner confirmed that figure: it sticks
        self.assertEqual(docs.invoice_issue(self.store, again)["invoice"]["status"], "issued")

    def test_an_estimate_that_left_its_invoice_matches_at_any_amount(self):
        """An independent review of 1.0.2: an estimate taken off a draft (or deleted with it), retyped at $89.01,
        split into $50 + $39 or merged into a bigger line, issued unasked. An open estimate still needs the same
        figure."""
        customer = self._customer("Any Amount")
        call_out = {"kind": "fee", "description": "Call-out", "unit_price": "89"}
        repair = {"kind": "labour", "description": "Repair", "qty": 2}
        a = self._draft_for(customer, dict(call_out, estimate=True), repair)
        docs.invoice_update(self.store, a, {"lines": [repair]})  # the estimate taken off A
        question = self._asked(self._draft_for(customer, dict(call_out, unit_price="89.01"))).ask_owner
        self.assertIn(f"(Call-out: 1 ea at $89.00 = $89.00 CAD, taken off draft {a})", question)  # the estimate shown
        merged = {"kind": "fee", "description": "Repair and call-out", "unit_price": "279"}
        self._asked(self._draft_for(customer, merged))
        self._issue_with_the_owners_yes(self._draft_for(customer, dict(call_out, unit_price="50")))
        part = {"kind": "fee", "description": "Call-out part 2", "unit_price": "39"}  # within the $50 said yes to
        self.assertEqual(docs.invoice_issue(self.store, self._draft_for(customer, part))["invoice"]["status"], "issued")
        other = self._customer("Deleted Any Amount")
        draft = self._draft_for(other, dict(call_out, estimate=True))
        docs.invoice_delete(self.store, draft)
        for retyped in ("Callout", "Cаll-out", "call out!"):  # no space, a Cyrillic а, punctuation
            with self.subTest(retyped=retyped):
                copy = self._draft_for(other, dict(call_out, description=retyped, unit_price="95"))
                question = self._asked(copy).ask_owner
                self.assertIn("(Call-out: 1 ea at $89.00 = $89.00 CAD, on a deleted draft)", question)

    def _issued_plain(self, customer, *lines):
        return docs.invoice_issue(self.store, self._draft_for(customer, *lines))["invoice"]["status"]

    def test_removed_estimate_memory_asks_once_per_amount_and_lapses_after_30_days(self):
        """An independent review of 1.0.3: after a deleted "Labour 8 h" estimate, the next several "Labour 2 h"
        invoices each asked, and "Extra labour" or "Spare parts" still asked months later."""
        customer = self._customer("Labour Customer")
        labour = {"kind": "labour", "description": "Labour", "qty": 2}
        docs.invoice_delete(self.store, self._draft_for(customer, dict(labour, qty=8, estimate=True)))
        self.assertIn("Labour: 8 hr", self._issue_with_the_owners_yes(self._draft_for(customer, labour)))
        self.assertEqual(self._issued_plain(customer, labour), "issued")  # the owner said yes to that amount
        self.assertEqual(self._issued_plain(customer, {"kind": "labour", "description": "Extra labour", "qty": 1}),
                         "issued")  # fmt: skip
        self._asked(self._draft_for(customer, dict(labour, qty=3)))  # over it: asks
        later = self._customer("Later Customer")
        call_out = {"kind": "fee", "description": "Call-out", "unit_price": "89"}
        docs.invoice_delete(self.store, self._draft_for(later, dict(call_out, estimate=True)))
        a = self._draft_for(later, dict(call_out, description="Travel", estimate=True), labour)
        docs.invoice_update(self.store, a, {"lines": [labour]})  # an estimate taken off an open draft
        self._asked(self._draft_for(later, dict(call_out, unit_price="95")))
        os.environ["JTP_TODAY"] = "2026-11-16"  # 32 days on: the deleted and taken-off estimates have lapsed
        self.assertEqual(self._issued_plain(later, dict(call_out, unit_price="95")), "issued")
        self.assertEqual(self._issued_plain(later, {"kind": "fee", "description": "Travel", "unit_price": "40"}),
                         "issued")  # fmt: skip
        self.assertIn("unconfirmed_estimates", self.store.customer(later))  # skipped, not deleted

    def test_an_honest_reprice_confirmed_on_its_invoice_isnt_remembered_as_taken_off(self):
        """An independent review of 1.0.3: Call-out $89 re-priced to $99 with the mark kept, and the owner's yes
        given on that invoice, still made the next Call-out invoice ask about "$89 taken off"."""
        customer = self._customer("Reprice Customer")
        call_out = {"kind": "fee", "description": "Call-out", "unit_price": "89"}
        a = self._draft_for(customer, dict(call_out, estimate=True))
        docs.invoice_update(self.store, a, {"lines": [dict(call_out, unit_price="99", estimate=True)]})
        for price in ("89", "88.99", "95"):  # while A is a draft, its old figure counts at any amount (1.0.4 check)
            with self.subTest(price=price):
                copy = self._draft_for(customer, dict(call_out, unit_price=price))
                self.assertIn(f"(Call-out: 1 ea at $89.00 = $89.00 CAD, changed on draft {a})",
                              self._asked(copy).ask_owner)  # fmt: skip
        question = self._issue_with_the_owners_yes(a)
        self.assertIn("Call-out: 1 ea at $89.00 = $89.00 CAD was an estimate, since changed", question)
        self.assertNotIn("unconfirmed_estimates", self.store.customer(customer))  # the owner said yes to $99
        self.assertEqual(self._issued_plain(customer, dict(call_out, unit_price="99")), "issued")
        self.assertEqual(self._issued_plain(customer, call_out), "issued")
        parts = {"kind": "material", "description": "Parts", "qty": 1, "unit_cost": "240"}
        b = self._draft_for(customer, dict(parts, estimate=True), {"kind": "labour", "description": "Fit", "qty": 1})
        docs.invoice_update(self.store, b, {"lines": [{"kind": "labour", "description": "Fit", "qty": 1}]})
        self._issue_with_the_owners_yes(b)  # a real removal, then the owner's yes: still remembered
        spare = {"kind": "material", "description": "Spare parts", "qty": 1, "unit_cost": "20"}
        self.assertIn(f"(Parts: 1 ea at $300.00 = $300.00 CAD, changed or taken off invoice {b} before it was issued)",
                      self._issue_with_the_owners_yes(self._draft_for(customer, spare)))  # fmt: skip
        self.assertEqual(self._issued_plain(customer, spare), "issued")  # the owner said yes to $25

    def test_an_open_estimate_matches_its_name_written_without_the_space(self):
        """A copy of an estimate still on its draft, with its name written "Callout" for "Call-out", asks."""
        customer = self._customer("Spacing Customer")
        call_out = {"kind": "fee", "description": "Call-out", "unit_price": "89"}
        estimate = self._draft_for(customer, dict(call_out, estimate=True))
        copy = self._draft_for(customer, dict(call_out, description="Callout"))
        self.assertIn(f"(Call-out: 1 ea at $89.00 = $89.00 CAD, on draft {estimate})", self._asked(copy).ask_owner)

    def test_a_zero_line_never_matches_an_estimate(self):
        """An independent review of 1.0.3: a "Call-out (waived)" $0 line asked on every invoice."""
        customer = self._customer("Waived Customer")
        call_out = {"kind": "fee", "description": "Call-out", "unit_price": "89"}
        docs.invoice_delete(self.store, self._draft_for(customer, dict(call_out, estimate=True)))
        for _ in range(2):
            waived = dict(call_out, description="Call-out (waived)", unit_price="0")
            self.assertEqual(self._issued_plain(customer, waived, {"kind": "labour", "description": "Fit", "qty": 1}),
                             "issued")  # fmt: skip
        self._asked(self._draft_for(customer, call_out))  # the estimate is still remembered

    def test_more_retypes_match_and_the_owner_can_forget_estimates(self):
        """An independent review of 1.0.3: "Callout fee", "Call-outs", an Armenian օ, a Latin ɑ and a digit 0 got
        past a removed "Call-out"; and nothing could retire a customer's remembered estimates. The 1.0.4 check: a
        small capital ᴀ too."""
        customer = self._customer("Retype Customer")
        call_out = {"kind": "fee", "description": "Call-out", "unit_price": "89"}
        docs.invoice_delete(self.store, self._draft_for(customer, dict(call_out, estimate=True)))
        for retyped in ("Callout fee", "Call-outs", "Call-\u0585ut", "C\u0251ll-out", "Call-0ut", "C\u1d00ll-out",
                        "\u1d04\u1d00\u029f\u029f-\u1d0f\u1d1c\u1d1b"):  # fmt: skip
            with self.subTest(retyped=retyped):
                self._asked(self._draft_for(customer, dict(call_out, description=retyped, unit_price="95")))
        with self.assertRaises(JTPError) as caught:
            docs.forget_estimates(self.store, customer)
        asked = caught.exception
        self.assertEqual(asked.ask_owner, "Forget these estimates you never confirmed for Retype Customer: Call-out: 1 "
                         "ea at $89.00 = $89.00 CAD (on a deleted draft)? Copies of them won't be asked about any "
                         "more.")  # fmt: skip
        with self.assertRaises(JTPError) as caught:
            docs.forget_estimates(self.store, customer, owner_confirmed="ok", confirm_code=asked.confirm)
        self.assertEqual(caught.exception.code, "unclear_answer")
        out = docs.forget_estimates(self.store, customer, owner_confirmed="yes, forget them",
                                    confirm_code=asked.confirm)  # fmt: skip
        self.assertEqual(out["forgotten"], ["Call-out: 1 ea at $89.00 = $89.00 CAD (on a deleted draft)"])
        self.assertEqual(self._issued_plain(customer, dict(call_out, unit_price="95")), "issued")
        self.assertIn("Nothing to forget", docs.forget_estimates(self.store, customer)["note"])

    def test_the_date_never_goes_backwards(self):
        """An independent check of 1.0.4: with the date set back, deleting a draft or taking an estimate off one dated
        the record in the past, so it had already lapsed and a copy issued unasked."""
        customer = self._customer("Clock Customer")
        call_out = {"kind": "fee", "description": "Call-out", "unit_price": "89"}
        repair = {"kind": "labour", "description": "Repair", "qty": 2}
        a = self._draft_for(customer, dict(call_out, estimate=True), repair)
        b = self._draft_for(customer, dict(call_out, description="Travel", estimate=True))
        os.environ["JTP_TODAY"] = "2026-09-01"  # the computer's clock set back
        self.assertEqual((str(self.store.today()), str(self.store.record_day())), ("2026-09-01", "2026-10-15"))
        docs.invoice_delete(self.store, b)
        docs.invoice_update(self.store, a, {"lines": [repair]})
        self.assertEqual(self.store.customer(customer)["unconfirmed_estimates"][0]["date"], "2026-10-15")
        self.assertEqual(self.store.doc(a)["removed_estimates"][0]["date"], "2026-10-15")
        os.environ["JTP_TODAY"] = "2026-10-16"
        for line in (dict(call_out, unit_price="95"), dict(call_out, description="Travel", unit_price="40")):
            self._asked(self._draft_for(customer, line))

    def test_documents_use_the_computers_date_after_a_clock_that_was_ahead(self):
        """The 1.0.4 rechecks: once the saved date was ahead (a clock briefly wrong, or a time zone switched and
        back), every later quote, invoice and due date was dated ahead. Only the estimate checks keep it now."""
        customer = self._customer("Clock Ahead")
        os.environ["JTP_TODAY"] = "2027-03-01"  # the computer's clock briefly ahead
        self._customer("Clock Ahead Too")  # anything saved then keeps that date
        os.environ["JTP_TODAY"] = "2026-10-15"  # put right
        drain = {"kind": "fee", "description": "Drain clean", "unit_price": "10"}
        issued = docs.invoice_issue(self.store, self._draft_for(customer, drain))["invoice"]
        self.assertEqual((issued["date"], issued["due_date"]), ("2026-10-15", "2026-10-29"))
        quote = docs.quote_create(self.store, {"title": "Drains", "lines": [drain]}, customer_id=customer)["quote"]
        self.assertEqual(quote["date"], "2026-10-15")
        call_out = {"kind": "fee", "description": "Call-out", "unit_price": "89", "estimate": True}
        docs.invoice_delete(self.store, self._draft_for(customer, call_out))
        self.assertEqual(self.store.customer(customer)["unconfirmed_estimates"][0]["date"], "2027-03-01")

    def test_a_clock_that_was_ahead_needs_no_reset(self):
        """The 1.0.4 rechecks asked for a way back after a clock that was ahead, and the 1.0.5 recheck found the
        reset itself let a clock set back date estimates in the past. Now nothing needs resetting: once the clock is
        right, documents and the 30 days use its date again, and what was removed while it was ahead still asks."""
        customer = self._customer("Clock Was Ahead")
        call_out = {"kind": "fee", "description": "Call-out", "unit_price": "89"}
        travel = dict(call_out, description="Travel", unit_price="40")
        docs.invoice_delete(self.store, self._draft_for(customer, dict(call_out, estimate=True)))  # before the jump
        os.environ["JTP_TODAY"] = "2028-10-15"  # one run with the computer's clock two years ahead
        docs.invoice_delete(self.store, self._draft_for(customer, dict(travel, estimate=True)))  # removed while ahead
        os.environ["JTP_TODAY"] = "2026-10-16"  # put right
        drain = {"kind": "fee", "description": "Drain clean", "unit_price": "10"}
        issued = docs.invoice_issue(self.store, self._draft_for(customer, drain))["invoice"]
        self.assertEqual(issued["date"], "2026-10-16")
        self._asked(self._draft_for(customer, dict(call_out, unit_price="95")))  # counted from the computer's date
        self._asked(self._draft_for(customer, travel))

    def test_a_clock_set_back_cant_date_a_removal_in_the_past(self):
        """An independent recheck of 1.0.5: after a gap in use, a reset with the clock set back dated removals in
        the past, and new removals made while it was back too, so their copies issued unasked."""
        call_out = {"kind": "fee", "description": "Call-out", "unit_price": "89"}
        repair = {"kind": "labour", "description": "Repair", "qty": 2}
        travel = dict(call_out, description="Travel", unit_price="40")
        customer = self._customer("Gap Customer")  # last used on 10-15
        os.environ["JTP_TODAY"] = "2026-11-19"  # used again after a gap
        a = self._draft_for(customer, dict(call_out, estimate=True), repair)
        docs.invoice_update(self.store, a, {"lines": [repair]})  # taken off on 11-19
        os.environ["JTP_TODAY"] = "2026-10-16"  # the computer's clock set back, into the gap
        docs.invoice_delete(self.store, self._draft_for(customer, dict(travel, estimate=True)))  # removed while back
        self.assertEqual(self.store.customer(customer)["unconfirmed_estimates"][-1]["date"], "2026-11-19")
        for day in ("2026-11-19", "2026-12-18"):  # put right, and 29 days on
            os.environ["JTP_TODAY"] = day
            for line in (dict(call_out, unit_price="95"), travel):
                with self.subTest(day=day, line=line["description"]):
                    self._asked(self._draft_for(customer, line))

    def test_no_clock_setting_makes_the_owners_yes_last_longer(self):
        """An independent recheck of 1.0.5: a yes dated while the clock was ahead covered copies for too long, and
        a reset with the clock set back brought a lapsed yes back."""
        call_out = {"kind": "fee", "description": "Call-out", "unit_price": "89"}
        half = dict(call_out, unit_price="50")  # a yes to $50 covers copies up to $50, and keeps the $89 estimate
        customer = self._customer("Yes Customer")
        docs.invoice_delete(self.store, self._draft_for(customer, dict(call_out, estimate=True)))  # removed 10-15
        os.environ["JTP_TODAY"] = "2026-10-20"
        self._issue_with_the_owners_yes(self._draft_for(customer, half))  # the owner's yes, dated 10-20
        self.assertEqual(self._issued_plain(customer, half), "issued")  # it covers another $50 copy
        os.environ["JTP_TODAY"] = "2026-11-25"
        self._customer("Used Later")  # the folder used on 11-25: the yes has lapsed
        os.environ["JTP_TODAY"] = "2026-11-01"  # the clock set back: the removal counts again, but not the yes
        self._asked(self._draft_for(customer, half))
        other = self._customer("Ahead Yes")
        os.environ["JTP_TODAY"] = "2026-11-26"
        docs.invoice_delete(self.store, self._draft_for(other, dict(call_out, estimate=True)))  # removed 11-26
        os.environ["JTP_TODAY"] = "2026-12-10"  # the clock two weeks ahead
        self._issue_with_the_owners_yes(self._draft_for(other, half))  # a yes dated 12-10
        self.assertEqual(self._issued_plain(other, half), "issued")  # it counts while the clock says 12-10
        os.environ["JTP_TODAY"] = "2026-11-27"  # put right: a yes dated later than today doesn't count
        self._asked(self._draft_for(other, half))
        third = self._customer("Set Back Yes")
        docs.invoice_delete(self.store, self._draft_for(third, dict(call_out, estimate=True)))  # removed 12-10
        os.environ["JTP_TODAY"] = "2026-11-12"  # the clock set back: a yes now is dated 11-12, not 12-10
        self._issue_with_the_owners_yes(self._draft_for(third, half))
        os.environ["JTP_TODAY"] = "2026-12-13"  # put right, 31 days after that yes: lapsed
        self._asked(self._draft_for(third, half))

    def test_the_date_never_deletes_a_removed_estimate(self):
        """An independent check of 1.0.4: issuing an unrelated invoice with the date set ahead deleted the customer's
        removed estimates for good. The date only decides which are checked; only the owner's yes deletes one, so
        deleting or issuing their draft with the date ahead keeps them too."""
        call_out = {"kind": "fee", "description": "Call-out", "unit_price": "89"}
        repair = {"kind": "labour", "description": "Repair", "qty": 2}
        for route in ("an unrelated invoice issued", "the draft deleted", "the draft issued"):
            with self.subTest(route=route):
                customer = self._customer(f"Ahead {route}")
                a = self._draft_for(customer, dict(call_out, estimate=True), repair)
                if route == "an unrelated invoice issued":
                    docs.invoice_delete(self.store, a)
                else:
                    docs.invoice_update(self.store, a, {"lines": [repair]})  # the estimate taken off A
                os.environ["JTP_TODAY"] = "2026-12-01"  # the computer's clock set ahead
                if route == "an unrelated invoice issued":
                    drain = {"kind": "fee", "description": "Drain clean", "unit_price": "10"}
                    self.assertEqual(self._issued_plain(customer, drain), "issued")
                elif route == "the draft deleted":
                    docs.invoice_delete(self.store, a)
                else:
                    self._issue_with_the_owners_yes(a)
                self.assertIn("unconfirmed_estimates", self.store.customer(customer))
                (self.store.home / "clock.json").unlink()  # the owner puts the clock right
                os.environ["JTP_TODAY"] = "2026-10-16"
                self.store = Store(self.store.home)
                self._asked(self._draft_for(customer, dict(call_out, unit_price="95")))

    def test_a_yes_covers_a_removed_estimate_only_up_to_the_amount_confirmed(self):
        """An independent check of 1.0.4: after the owner's yes to a "Call-out $1" copy of a removed $89 estimate, a
        $50 half or a "Repair and call-out" $5, or to the estimate re-priced to $1 on its own invoice, Call-outs at
        $89 and $500 issued unasked."""
        call_out = {"kind": "fee", "description": "Call-out", "unit_price": "89"}
        repair = {"kind": "labour", "description": "Repair", "qty": 2}
        smaller = {"$1 copy": dict(call_out, unit_price="1"), "$50 half": dict(call_out, unit_price="50"),
                   "merged $5": dict(call_out, description="Repair and call-out", unit_price="5")}  # fmt: skip
        for route, small in smaller.items():
            with self.subTest(route=route):
                customer = self._customer(f"Small Yes {route}")
                a = self._draft_for(customer, dict(call_out, estimate=True), repair)
                docs.invoice_update(self.store, a, {"lines": [repair]})  # the estimate taken off A
                self.assertIn(f"(Call-out: 1 ea at $89.00 = $89.00 CAD, taken off draft {a})",
                              self._issue_with_the_owners_yes(self._draft_for(customer, small)))  # fmt: skip
                for price in ("89", "500"):
                    self._asked(self._draft_for(customer, dict(call_out, unit_price=price)))
                self.assertEqual(self._issued_plain(customer, dict(small, description="Call-out")), "issued")
        customer = self._customer("Full Yes")
        docs.invoice_delete(self.store, self._draft_for(customer, dict(call_out, estimate=True)))
        self._issue_with_the_owners_yes(self._draft_for(customer, call_out))  # a yes to the estimate's own figure
        self.assertNotIn("unconfirmed_estimates", self.store.customer(customer))  # clears it
        self.assertEqual(self._issued_plain(customer, dict(call_out, unit_price="500")), "issued")
        customer = self._customer("Split Yes")
        docs.invoice_delete(self.store, self._draft_for(customer, dict(call_out, estimate=True)))
        halves = [dict(call_out, description="Call-out part 1", unit_price="50"),
                  dict(call_out, description="Call-out part 2", unit_price="39")]  # fmt: skip
        self._issue_with_the_owners_yes(self._draft_for(customer, *halves))  # $89 in all, on one invoice
        self.assertEqual(self._issued_plain(customer, dict(call_out, unit_price="500")), "issued")
        for route in ("voided", "issued"):  # a yes given while A was a draft still counts once A is gone
            with self.subTest(route=route):
                customer = self._customer(f"Carried Yes {route}")
                a = self._draft_for(customer, dict(call_out, estimate=True), repair)
                docs.invoice_update(self.store, a, {"lines": [repair]})
                self._issue_with_the_owners_yes(self._draft_for(customer, dict(call_out, unit_price="50")))
                if route == "voided":
                    owner_approves(docs.invoice_void, self.store, a, "Wrong job")
                else:
                    self._issue_with_the_owners_yes(a)
                self.assertEqual(self._issued_plain(customer, dict(call_out, unit_price="50")), "issued")
                self._asked(self._draft_for(customer, call_out))
        customer = self._customer("Repriced Down")
        a = self._draft_for(customer, dict(call_out, estimate=True))
        docs.invoice_update(self.store, a, {"lines": [dict(call_out, unit_price="1", estimate=True)]})
        self.assertIn("Call-out: 1 ea at $89.00 = $89.00 CAD was an estimate", self._issue_with_the_owners_yes(a))
        self.assertIn(f"(Call-out: 1 ea at $89.00 = $89.00 CAD, changed or taken off invoice {a} before it was issued)",
                      self._asked(self._draft_for(customer, call_out)).ask_owner)  # fmt: skip
        self.assertEqual(self._issued_plain(customer, dict(call_out, unit_price="1")), "issued")

    def test_the_question_names_each_matched_estimate_with_its_figure_once(self):
        """An independent check of 1.0.4: an exact copy's question left out the estimate's figure, and two deleted
        drafts read "on a deleted draft; on a deleted draft"."""
        customer = self._customer("Twice Deleted")
        call_out = {"kind": "fee", "description": "Call-out", "unit_price": "89"}
        for _ in range(2):
            docs.invoice_delete(self.store, self._draft_for(customer, dict(call_out, estimate=True)))
        question = self._asked(self._draft_for(customer, call_out)).ask_owner
        self.assertIn("you haven't confirmed (Call-out: 1 ea at $89.00 = $89.00 CAD, on a deleted draft). Are these "
                      "figures final?", question)  # fmt: skip

    def test_every_word_a_question_lists_counts_after_a_yes(self):
        """An independent check of 1.0.4: "stop checking" was listed for forget-estimates, but "yes, stop checking"
        read as a no."""
        for words in (docs.FORGET_WORDS, docs.ESTIMATES_FINAL_WORDS, docs.VOID_WORDS, docs.CREDIT_WORDS):
            for phrase in words:
                with self.subTest(phrase=phrase):
                    self.assertFalse(confirm.reads_as_no(f"yes, {phrase}"))
                    self.assertTrue(confirm.plain_yes(f"yes, {phrase}", words))

    def test_a_yes_only_covers_the_estimates_it_was_about(self):
        """An independent review of 1.0.2: after a yes to a copy of "Call-out $89", a later, new estimate of the same
        figure could be moved, deleted or retyped unasked, and that yes never expired."""
        customer = self._customer("Scoped Yes")
        call_out = {"kind": "fee", "description": "Call-out", "unit_price": "89"}
        first = self._draft_for(customer, dict(call_out, estimate=True))
        self._issue_with_the_owners_yes(self._draft_for(customer, call_out))  # a copy of the first estimate
        repeat = self._draft_for(customer, call_out)
        self.assertEqual(docs.invoice_issue(self.store, repeat)["invoice"]["status"], "issued")  # the yes covers it
        second = self._draft_for(customer, dict(call_out, estimate=True))  # a new estimate, the same figure
        self.assertIn(f"$89.00 CAD, on draft {second})", self._asked(self._draft_for(customer, call_out)).ask_owner)
        docs.invoice_update(self.store, second, {"lines": [{"kind": "labour", "description": "Repair", "qty": 1}]})
        self.assertIn("Call-out: 1 ea at $89.00 = $89.00 CAD was an estimate", self._asked(second).ask_owner)
        os.environ["JTP_TODAY"] = "2026-11-20"  # more than 30 days on: the yes about the first has expired, and the
        # one taken off the second has lapsed (only open estimates are still checked)
        self.assertIn(f"$89.00 CAD, on draft {first})", self._asked(self._draft_for(customer, call_out)).ask_owner)

    def test_a_yes_to_other_figures_doesnt_clear_remembered_estimates(self):
        """An independent review of 1.0.1: the question named only the new invoice's figures, so a yes to a $10
        invoice cleared an $89 estimate the owner never saw, and a copy of it then issued unasked."""
        draft = self._estimated_draft()
        copy_lines = lines_as_spec(draft["lines"])
        docs.invoice_delete(self.store, draft["id"])
        filter_only = self._open_draft()  # an unrelated invoice: nothing to ask, and nothing cleared
        self.assertEqual(docs.invoice_issue(self.store, filter_only)["invoice"]["status"], "issued")
        self.assertIn("unconfirmed_estimates", self.store.customer(self.cust["id"]))
        other = docs.invoice_create(self.store, customer_id=self.cust["id"], spec={"title": "F", "lines": [
            {"kind": "fee", "description": "Filter", "unit_price": "10", "estimate": True}]})["invoice"]["id"]  # fmt: skip
        self.assertNotIn("Call-out", self._issue_with_the_owners_yes(other))  # a yes to another estimate...
        self.assertIn("unconfirmed_estimates", self.store.customer(self.cust["id"]))  # ...keeps this one
        copy_lines[0].pop("estimate")
        copy = docs.invoice_create(self.store, customer_id=self.cust["id"], spec={"title": "C", "lines": copy_lines})
        self.assertIn("Call-out: 1 ea at $89.00 = $89.00 CAD", self._asked(copy["invoice"]["id"]).ask_owner)

    def test_a_copy_of_an_open_estimated_draft_asks(self):
        """An independent review of 1.0.1: a stripped copy issued while the estimated original was still a draft
        asked nothing."""
        older = self._open_draft()
        original = self._estimated_draft()
        copy = self._stripped_copy(original)
        self.assertIn(f"$89.00 CAD, on draft {original['id']})", " ".join(copy["warnings"]))
        spec = {"title": "Older", "lines": [{"kind": "fee", "description": "Call out", "unit_price": "89"}]}
        updated = docs.invoice_update(self.store, older, spec)  # copied into an older draft ("Call out": the same)
        self.assertIn("matches an estimate", " ".join(updated["warnings"]))
        question = self._issue_with_the_owners_yes(copy["invoice"]["id"])
        self.assertIn("these lines match estimates for Jane Doe that you haven't confirmed (Call-out: 1 ea at $89.00 = "
                      f"$89.00 CAD, on draft {original['id']})", question)  # fmt: skip
        self.assertEqual(docs.invoice_issue(self.store, older)["invoice"]["status"], "issued")  # the yes sticks
        self._asked(original["id"])  # the original still asks for itself

    def test_a_copy_of_an_estimate_on_an_open_change_order_asks(self):
        quote = self._fixed_quote([{"kind": "fee", "description": "Work", "unit_price": "1000"}])
        job_id = docs.quote_accept(self.store, quote["id"], None, "Jane", "email", None)["job"]
        valve = {"kind": "fee", "description": "Extra valve", "unit_price": "50"}
        sent = docs.co_create(self.store, job_id, {"title": "Valve", "lines": [dict(valve, estimate=True)]})
        docs.co_mark_sent(self.store, sent["change_order"]["id"], "email", "jane@example.com")  # not approved yet
        copy = docs.co_create(self.store, job_id, {"title": "Valve", "lines": [valve]})["change_order"]["id"]
        docs.co_approve(self.store, copy, "Jane", "text", None)
        inv = docs.invoice_create(self.store, job_id=job_id)["invoice"]["id"]  # bills the copy, not the sent one
        self.assertIn("these lines match estimates for Jane Doe that you haven't confirmed (Extra valve: 1 ea at "
                      f"$50.00 = $50.00 CAD, on change order {sent['change_order']['id']})", self._asked(inv).ask_owner)

    def test_a_voided_change_order_keeps_its_estimates(self):
        """An independent review of 1.0.1: co void dropped a change order's estimate, so the same work re-created
        without the mark was billed unasked."""
        quote = self._fixed_quote([{"kind": "fee", "description": "Work", "unit_price": "1000"}])
        job_id = docs.quote_accept(self.store, quote["id"], None, "Jane", "email", None)["job"]
        valve = {"kind": "material", "description": "Extra valve", "qty": 1, "unit_cost": "40"}
        co = docs.co_create(self.store, job_id, {"title": "Valve", "lines": [dict(valve, estimate=True)]})
        voided = docs.co_void(self.store, co["change_order"]["id"], "Re-doing it")
        self.assertIn("never confirmed (Extra valve: 1 ea at $50.00 = $50.00 CAD)", voided["warnings"][0])
        again = docs.co_create(self.store, job_id, {"title": "Valve", "lines": [valve]})["change_order"]
        docs.co_approve(self.store, again["id"], "Jane", "text", None)
        inv = docs.invoice_create(self.store, job_id=job_id)["invoice"]
        question = self._asked(inv["id"]).ask_owner
        self.assertIn(f"$50.00 CAD, on voided change order {co['change_order']['id']})", question)
        self.assertIn("Extra valve", question)
        self.assertNotIn("Work", question)  # the quote's own line wasn't an estimate

    def _open_draft(self):
        spec = {"title": "A", "lines": [{"kind": "fee", "description": "Work", "unit_price": "10"}]}
        return docs.invoice_create(self.store, customer_id=self.cust["id"], spec=spec)["invoice"]["id"]

    def test_change_orders_are_billed_after_the_quote_is_fully_billed(self):
        """An independent review: once progress invoices had billed 100% of the quote, an approved change order
        couldn't be billed ("Nothing left to bill"), and the same after a final invoice."""
        quote = self._fixed_quote([{"kind": "fee", "description": "Work", "unit_price": "1000"}])
        job_id = docs.quote_accept(self.store, quote["id"], None, "Jane", "email", None)["job"]
        docs.invoice_issue(self.store, docs.invoice_create(self.store, job_id=job_id, progress="100")["invoice"]["id"])
        with self.assertRaises(JTPError) as caught:  # nothing due at all yet
            docs.invoice_create(self.store, job_id=job_id)
        self.assertIn("no approved change orders or extras", caught.exception.message)
        for title in ("Add valve", "Add tap"):
            co = docs.co_create(self.store, job_id, {"title": title, "lines": [
                {"kind": "fee", "description": title, "unit_price": "50"}]})["change_order"]  # fmt: skip
            docs.co_approve(self.store, co["id"], "Jane", "text", None)
            inv = docs.invoice_create(self.store, job_id=job_id)["invoice"]  # after 100% progress, then after a final
            self.assertEqual([ln["description"] for ln in priced(inv["lines"])], [title])
            docs.invoice_issue(self.store, inv["id"])
        with self.assertRaises(JTPError):  # asked for the quote itself: it's done
            docs.invoice_create(self.store, job_id=job_id, from_quote=quote["id"])

    def test_the_setup_hint_only_names_options_that_exist(self):
        """An independent review: the tax hint suggested profile set --dry-run, which doesn't exist."""
        profile = self.store.profile()
        profile["tax"]["confirmed"] = False
        self.store.save_profile(profile)
        with self.assertRaises(JTPError) as caught:
            docs.invoice_create(self.store, customer_id=self.cust["id"], spec={"lines": [
                {"kind": "fee", "description": "Work", "unit_price": "1"}]})  # fmt: skip
        self.assertEqual(caught.exception.code, "setup_needed")
        self.assertNotIn("--dry-run", caught.exception.hint)

    def test_renaming_a_customer_to_another_customers_name_gets_a_warning(self):
        """An independent review of 1.0.2: the duplicate-name warning covered customer add only."""
        sam = records.customer_add(self.store, {"name": "Sam Lee"})["customer"]
        out = records.customer_update(self.store, sam["id"], {"name": "Jane  Doe"})
        self.assertEqual(out["warnings"], [f"There's already a customer called Jane Doe: {self.cust['id']} "
                                           f"(jane@example.com). If it's the same person, use {self.cust['id']} "
                                           f"instead of {sam['id']}."])  # fmt: skip
        self.assertNotIn("warnings", records.customer_update(self.store, sam["id"], {"phone": "555 0100"}))

    def test_a_second_customer_with_the_same_name_gets_a_warning(self):
        """An independent review of 1.0.1: adding "Jane Doe" again with a new email gave no warning."""
        again = records.customer_add(self.store, {"name": "  jane   DOE ", "email": "jd@other.example"})
        self.assertFalse(again["existing"])  # two people can share a name: it's still added
        self.assertEqual(again["warnings"], [f"There's already a customer called Jane Doe: {self.cust['id']} "
                                             f"(jane@example.com). If it's the same person, use {self.cust['id']} "
                                             f"instead of {again['customer']['id']}."])  # fmt: skip
        self.assertNotIn("warnings", records.customer_add(self.store, {"name": "Sam Lee"}))
        accented = records.customer_add(self.store, {"name": "Jane Doé", "email": "jd2@other.example"})
        self.assertIn(f"There's already a customer called Jane Doe: {self.cust['id']}", accented["warnings"][0])

    def test_a_draft_from_an_older_version_that_had_estimates_still_asks(self):
        """A draft saved by 1.0.1 with its estimate taken off has had_estimates but no record of the estimate itself:
        issuing it still asks, naming every figure."""
        inv_id = self._estimated_draft({"kind": "labour", "description": "Work", "qty": 2})["id"]
        doc = self.store.doc(inv_id)
        for line in doc["lines"]:
            line.pop("estimate", None)
        doc.pop("removed_estimates", None)
        self.store.save_doc(doc)
        question = self._asked(inv_id).ask_owner
        self.assertIn("it had estimated figures, since changed or removed. Are these figures final? Call-out: 1 ea at "
                      "$89.00 = $89.00 CAD; Work: 2 hr", question)  # fmt: skip

    def test_a_dry_run_issues_nothing(self):
        """An independent review of 1.0.1: invoice issue --dry-run issued the invoice and used up the owner's code."""
        spec = {"title": "A", "lines": [{"kind": "fee", "description": "Work", "unit_price": "100"}]}
        inv_id = docs.invoice_create(self.store, customer_id=self.cust["id"], spec=spec)["invoice"]["id"]
        preview = docs.invoice_issue(self.store, inv_id, dry_run=True)
        self.assertEqual((preview["invoice"]["id"], preview["invoice"]["status"], preview["would_issue"]),
                         ("PREVIEW", "issued", inv_id))  # fmt: skip
        self.assertEqual(self.store.doc(inv_id)["status"], "draft")
        estimated = self._estimate_invoice()
        code = self._asked(estimated).confirm
        preview = docs.invoice_issue(self.store, estimated, owner_confirmed="yes", confirm_code=code, dry_run=True)
        self.assertIn("Are they final?", preview["owner_question"])
        self.assertEqual(self.store.doc(estimated)["status"], "draft")
        self.assertFalse((self.store.home / confirm.USED_FILE).exists())  # the code wasn't used up
        issued = docs.invoice_issue(self.store, estimated, owner_confirmed="yes", confirm_code=code)["invoice"]
        self.assertEqual(issued["status"], "issued")

    def test_void_and_credit_notes_wait_for_the_owners_yes(self):
        """An independent review of 1.0.1: invoice void and credit create went ahead with no question, and neither
        can be undone."""
        inv = self._issued()
        with self.assertRaises(JTPError) as caught:
            docs.invoice_void(self.store, inv["id"], "Wrong customer")
        asked = caught.exception
        self.assertEqual(asked.code, "needs_owner")
        self.assertEqual(asked.ask_owner, f"Void invoice {inv['id']} for Jane Doe ($105.00 CAD)? A credit note for "
                         "$105.00 CAD goes with it. Reason: Wrong customer. This can't be undone.")  # fmt: skip
        for reply, code, refused in (("ok", asked.confirm, "unclear_answer"), ("no", asked.confirm, "owner_declined"),
                                     ("yes", None, "needs_owner")):  # fmt: skip
            with self.subTest(reply=reply), self.assertRaises(JTPError) as caught:
                docs.invoice_void(self.store, inv["id"], "Wrong customer", owner_confirmed=reply, confirm_code=code)
            self.assertEqual(caught.exception.code, refused)
        with self.assertRaises(JTPError):  # the code covers that reason: another reason is another question
            docs.invoice_void(self.store, inv["id"], "Duplicate", owner_confirmed="yes", confirm_code=asked.confirm)
        self.assertEqual((self.store.doc(inv["id"])["status"], self.store.docs("credit_note")), ("issued", []))
        out = docs.invoice_void(self.store, inv["id"], "Wrong customer", owner_confirmed="yes, void it",
                                confirm_code=asked.confirm)  # fmt: skip
        self.assertEqual(out["invoice"]["void_approved"]["owner_said"], "yes, void it")
        with self.assertRaises(JTPError) as caught:
            docs.invoice_void(self.store, inv["id"], "Wrong customer", owner_confirmed="no")
        self.assertEqual(caught.exception.code, "already_void")
        self.assertEqual((out["credit_note"]["id"], out["credit_note"]["owner_approved"]["owner_said"]),
                         ("CN-0001", "yes, void it"))  # asking used up no number  # fmt: skip
        other = self._issued("50")
        with self.assertRaises(JTPError) as caught:
            docs.credit_create(self.store, other["id"], amount="10", reason="Goodwill")
        asked = caught.exception
        self.assertEqual(asked.ask_owner, f"Issue a credit note for $10.00 CAD on invoice {other['id']} for Jane Doe? "
                         "Reason: Goodwill. This can't be undone.")  # fmt: skip
        preview = docs.credit_create(self.store, other["id"], amount="10", reason="Goodwill", dry_run=True)
        self.assertEqual((preview["owner_question"], len(self.store.docs("credit_note"))), (asked.ask_owner, 1))
        with self.assertRaises(JTPError):  # a yes to $10 doesn't cover $20
            docs.credit_create(self.store, other["id"], amount="20", reason="Goodwill", owner_confirmed="yes",
                               confirm_code=asked.confirm)  # fmt: skip
        credit = docs.credit_create(self.store, other["id"], amount="10", reason="Goodwill",
                                    owner_confirmed="yes, issue it", confirm_code=asked.confirm)["credit_note"]
        self.assertEqual((credit["id"], credit["owner_approved"]["owner_said"]), ("CN-0002", "yes, issue it"))
        draft = docs.invoice_create(self.store, customer_id=self.cust["id"], spec={"title": "D", "lines": [
            {"kind": "fee", "description": "Work", "unit_price": "1"}]})["invoice"]  # fmt: skip
        with self.assertRaises(JTPError) as caught:
            docs.invoice_void(self.store, draft["id"], None)
        self.assertTrue(caught.exception.ask_owner.startswith(f"Void draft invoice {draft['id']}"))

    def test_a_no_on_an_issued_invoice_isnt_reported_as_issued(self):
        """An independent review of 1.0.1: issuing an issued invoice again with "absolutely not" answered ok, and
        the CLI kept the owner's earlier words as if they were this answer."""
        inv = self._issued()
        with self.assertRaises(JTPError) as caught:
            docs.invoice_issue(self.store, inv["id"], owner_confirmed="absolutely not")
        self.assertEqual(caught.exception.code, "already_issued")
        self.assertIn("already issued on 15 Oct 2026", caught.exception.message)  # the owner's date format
        self.assertIn("Nothing was changed", caught.exception.message)
        self.assertTrue(docs.invoice_issue(self.store, inv["id"])["already_issued"])

    def _open_invoice(self, amount, **issue):
        spec = {"title": "A", "lines": [{"kind": "fee", "description": "Work", "unit_price": amount}]}
        inv = docs.invoice_create(self.store, customer_id=self.cust["id"], spec=spec)["invoice"]
        return docs.invoice_issue(self.store, inv["id"], **issue)["invoice"]["id"]

    def test_the_brief_names_every_open_invoice_overdue_first(self):
        """An independent review: "Owed to you: ... on 2 invoices" named only the overdue one."""
        later = self._open_invoice("30", due_days=30)
        late = self._open_invoice("10", date="2026-09-01")
        soon = self._open_invoice("20", due_days=7)
        today = self._open_invoice("40", due_days=0)
        text = reports.brief(self.store)["text"].splitlines()
        day = lambda iso_day: reports._day(self.store, iso_day)  # noqa: E731
        self.assertEqual(text[1], "Owed to you: $105.00 CAD on 4 invoices, $10.50 CAD overdue on 1")
        self.assertEqual(text[2:6], [f"  - {late} Jane Doe: $10.50 CAD, 30 days overdue (due {day('2026-09-15')})",
                                     f"  - {today} Jane Doe: $42.00 CAD, due today",
                                     f"  - {soon} Jane Doe: $21.00 CAD, due {day('2026-10-22')}",
                                     f"  - {later} Jane Doe: $31.50 CAD, due {day('2026-11-14')}"])  # fmt: skip
        for _ in range(8):
            self._open_invoice("1")
        brief = reports.brief(self.store)
        self.assertIn("  - and 2 more: jtp invoice list", brief["text"].splitlines())
        self.assertEqual(len(brief["owed"]["open"]), 12)

    def test_chat_totals_show_the_currency_code_where_the_symbol_is_shared(self):
        """An independent review: the PDFs said "$359.18 CAD", but the chat and the brief said "$359.18"."""
        self.assertEqual(format_total(Decimal("1234.5"), currency_display({"currency": "CAD"})), "$1,234.50 CAD")
        never = currency_display({"currency": "CAD", "show_currency_code": "never"})
        self.assertEqual(format_total(Decimal("1234.5"), never), "$1,234.50")
        self.assertIn("= $285.00 CAD", self._asked(self._estimate_invoice()).ask_owner)

    def test_the_demo_sets_its_towns_time_zone(self):
        """An independent review: the demo left the time zone blank, so doctor warned and "today" was the
        computer's day."""
        zones = {c: demo_profile(c)["locale"]["timezone"] for c in ("USD", "CAD", "GBP", "AUD", "EUR")}
        self.assertEqual(zones, {"USD": "America/Chicago", "CAD": "America/Vancouver", "GBP": "Europe/London",
                                 "AUD": "Australia/Sydney", "EUR": "UTC"})  # fmt: skip
        self.assertFalse([w for w in profile_check(demo_profile("CAD"))["warnings"] if "time zone" in w])

    def test_deposits_count_as_received(self):
        quote = self._fixed_quote([{"kind": "fee", "description": "Work", "unit_price": "1000"}])
        job_id = docs.quote_accept(self.store, quote["id"], None, "Jane", "email", None)["job"]
        docs.payment_record(self.store, job_id=job_id, amount="250", method="Bank", deposit=True)
        summary = reports.job_summary(self.store, job_id)
        self.assertEqual((summary["received"], summary["deposits_held"]), ("$250.00 CAD", "$250.00 CAD"))
        self.assertIn("received $250.00 CAD", summary["summary"])

    def test_payment_instructions_name_the_document(self):
        profile = self.store.profile()
        profile["payment"]["methods"] = [{"name": "Bank", "instructions": "Use {number} as the reference."}]
        profile["terms"]["deposit_percent"] = "25"
        self.store.save_profile(profile)
        quote = self._fixed_quote([{"kind": "fee", "description": "Work", "unit_price": "1000"}])
        html = Path(render_doc(self.store, self.store.doc(quote["id"]), fmt="html", engine="builtin")["files"]["html"])
        self.assertIn(f"Use {quote['id']} as the reference.", html.read_text(encoding="utf-8"))

    def test_demo_looks_local(self):
        ca = Store(Path(self.tmp.name) / "demo-ca")
        seed(ca, "CAD")
        taxes = {t["code"]: t["applies_to"] for t in ca.profile()["tax"]["taxes"]}
        self.assertEqual(list(taxes), ["GST", "PST"])  # British Columbia: two taxes
        self.assertEqual(taxes["PST"], ["material", "equipment"])
        self.assertIn("Vancouver", " ".join(ca.profile()["business"]["address"]))
        quote = ca.doc(next(d["id"] for d in ca.docs("quote") if d["status"] == "sent"))
        rows = {r["code"]: r for r in quote["options"][0]["totals"]["taxes"]}
        self.assertEqual(set(rows), {"GST", "PST"})
        self.assertLess(Decimal(rows["PST"]["base"]), Decimal(rows["GST"]["base"]))  # no PST on labour
        au = Store(Path(self.tmp.name) / "demo-au")
        seed(au, "AUD", history=False)
        self.assertEqual(au.profile()["labels"]["invoice"], "Tax Invoice")

    def test_paid_invoice_and_receipt_read_cleanly(self):
        inv = self._issued()
        pay = docs.payment_record(self.store, invoice_id=inv["id"], amount="105", method="Cash")["payment"]
        pdf = Path(render_doc(self.store, self.store.doc(inv["id"]), engine="builtin")["files"]["pdf"]).read_bytes()
        ops = b"".join(zlib.decompress(m) for m in re.findall(rb"\nstream\n(.*?)\nendstream", pdf, re.S))
        self.assertIn(b"(PAID) Tj", ops)  # a badge, read back as one word
        self.assertNotIn(b"Tm (PAID) Tj", ops)  # not a diagonal watermark
        receipt = Path(render_receipt(self.store, pay["id"], "html", "builtin")["files"]["html"]).read_text(
            encoding="utf-8"
        )
        self.assertIn("Received from", receipt)
        self.assertNotIn(">Qty<", receipt)

    def test_documents_say_who_approved_and_what_was_a_deposit(self):
        quote = self._fixed_quote([{"kind": "fee", "description": "Work", "unit_price": "1000"}])
        job_id = docs.quote_accept(self.store, quote["id"], None, "Jane", "email", None)["job"]
        co = docs.co_create(
            self.store,
            job_id,
            {"title": "Extra", "lines": [{"kind": "fee", "description": "Extra", "unit_price": "50"}]},
        )["change_order"]
        self.assertTrue(co["lines"][0]["id"].startswith("CO"))
        docs.co_approve(self.store, co["id"], "Jane", "text message", None, date="2026-10-15")
        html = Path(render_doc(self.store, self.store.doc(co["id"]), fmt="html", engine="builtin")["files"]["html"])
        self.assertIn("Approved by Jane on 15 Oct 2026 (text message)", html.read_text(encoding="utf-8"))
        docs.payment_record(self.store, job_id=job_id, amount="200", method="Bank", deposit=True)
        inv = docs.invoice_create(self.store, job_id=job_id)["invoice"]
        docs.invoice_issue(self.store, inv["id"])
        html = Path(render_doc(self.store, self.store.doc(inv["id"]), fmt="html", engine="builtin")["files"]["html"])
        self.assertIn("Deposit received (15 Oct 2026)", html.read_text(encoding="utf-8"))

    def test_reminders_skip_meaningless_day_counts(self):
        inv = self._issued()
        docs.invoice_mark_sent(self.store, inv["id"], "email", "jane@example.com")
        os.environ["JTP_TODAY"] = "2026-10-27"  # due 29 Oct: the heads-up stage
        early = next(a for a in followups.due_actions(self.store) if a["action"] == "payment_reminder")
        self.assertEqual(early["days_until_due"], 2)
        self.assertNotIn("days_overdue", early)

    def test_typical_invoice_fits_on_one_page(self):
        lines = [{"kind": "material", "description": f"Part {n}", "unit_price": "100"} for n in range(4)]
        quote = self._fixed_quote(lines)
        job_id = docs.quote_accept(self.store, quote["id"], None, "Jane", "email", None)["job"]
        co = docs.co_create(
            self.store,
            job_id,
            {
                "title": "Valve",
                "lines": [
                    {"kind": "material", "description": "Valve", "unit_price": "30"},
                    {"kind": "labour", "description": "Fit valve", "qty": 0.75},
                ],
            },
        )["change_order"]
        docs.co_approve(self.store, co["id"], "Jane", "text message", None)
        docs.payment_record(self.store, job_id=job_id, amount="100", method="Bank", deposit=True)
        inv = docs.invoice_create(self.store, job_id=job_id)["invoice"]
        docs.invoice_issue(self.store, inv["id"])
        pdf = Path(render_doc(self.store, self.store.doc(inv["id"]), engine="builtin")["files"]["pdf"]).read_bytes()
        self.assertEqual(pdf.count(b"/Type /Page "), 1)

    def test_payment_methods_use_the_owners_wording(self):
        profile = self.store.profile()
        profile["payment"]["methods"] = [
            {"name": "Interac e-Transfer", "instructions": "pay@x.example"},
            {"name": "Card", "instructions": "Call us"},
        ]
        self.store.save_profile(profile)
        quote = self._fixed_quote([{"kind": "fee", "description": "Work", "unit_price": "1000"}])
        job_id = docs.quote_accept(self.store, quote["id"], None, "Jane", "email", None)["job"]
        same = docs.payment_record(self.store, job_id=job_id, amount="100", method="e-transfer", deposit=True)
        self.assertEqual((same["payment"]["method"], same["warnings"]), ("Interac e-Transfer", []))
        other = docs.payment_record(self.store, job_id=job_id, amount="50", method="Bank transfer", deposit=True)
        self.assertIn("isn't one of the payment methods", other["warnings"][0])

    def test_reminder_ladder(self):
        inv = docs.invoice_create(
            self.store,
            customer_id=self.cust["id"],
            spec={"title": "A", "lines": [{"kind": "fee", "description": "x", "unit_price": 100}]},
        )["invoice"]
        docs.invoice_issue(self.store, inv["id"])
        docs.invoice_mark_sent(self.store, inv["id"], "email", "jane@example.com")
        seen = []
        for day, expected in (("2026-10-27", 1), ("2026-10-30", 2), ("2026-11-05", 3), ("2026-11-29", 5)):
            os.environ["JTP_TODAY"] = day
            due = [a for a in followups.due_actions(self.store) if a["action"] == "payment_reminder"]
            seen.append(due[0]["stage"] if due else None)
            self.assertEqual(due[0]["stage"], expected)
        followups.mark_done(self.store, inv["id"], 5, "email", None)
        self.assertFalse([a for a in followups.due_actions(self.store) if a["action"] == "payment_reminder"])

    def test_brief_and_reports(self):
        tmp = Store(Path(self.tmp.name) / "demo")
        seed(tmp)
        brief = reports.brief(tmp)
        self.assertTrue(brief["owed"]["overdue"])
        self.assertIn("Money brief", brief["text"])
        self.assertIn("TAX", reports.tax_report(tmp)["taxes"])
        self.assertTrue(reports.search(tmp, "jane")["customers"])


def page_starts(view) -> list:
    """The first text on each page of a rendered view, under the running title (built-in PDF engine)."""
    seen = {}
    real = pdf.Canvas.text

    def record(canvas, x, top, text, *args, **kwargs):
        seen.setdefault(id(canvas.page), []).append(text)
        return real(canvas, x, top, text, *args, **kwargs)

    with tempfile.TemporaryDirectory() as tmp:
        pdf.Canvas.text = record
        try:
            layout = pdf.Layout(view, [])
            layout.build(Path(tmp) / "x.pdf")
        finally:
            pdf.Canvas.text = real
    title = f"{view['title']} {view['number']}"
    return [next((t for t in seen.get(id(page), []) if t != title), "") for page in layout.pdf.pages]


class RenderTests(StoreCase):
    def test_the_terms_never_sit_on_a_page_of_their_own(self):
        """An independent review of 1.0.0: the demo's good/better/best quote had a third page holding only the
        terms. They now go with the acceptance (or payment) box before them, at any length and paper size."""
        demo = Store(Path(self.tmp.name) / "demo")
        quote = demo.doc(seed(demo)["waiting_quote"])
        for paper in ("letter", "a4"):
            view = build_view(demo, quote)
            view["paper"] = paper
            starts = page_starts(view)
            self.assertNotIn("TERMS", starts, paper)
            if paper == "letter":  # three pages: the last starts with the acceptance box, the terms under it
                self.assertEqual((len(starts), starts[-1]), (3, "ACCEPTANCE"), starts)
        terms = "Prices include supply and installation as described. " * 4
        for n in range(1, 45):
            lines = [{"kind": "fee", "description": f"Item {i}", "unit_price": "10"} for i in range(n)]
            quote = docs.quote_create(self.store, {"title": "Work", "lines": lines, "terms": terms, "notes": "Thanks"},
                                      customer_id=self.cust["id"])["quote"]  # fmt: skip
            invoice = docs.invoice_create(self.store, customer_id=self.cust["id"],
                                          spec={"title": "Work", "lines": lines})["invoice"]  # fmt: skip
            for doc in (quote, invoice):
                for paper in ("letter", "a4"):
                    view = build_view(self.store, doc)
                    view["paper"], view["terms"] = paper, view.get("terms") or terms
                    starts = page_starts(view)
                    self.assertFalse({"TERMS", "NOTES"} & set(starts[1:]), (doc["id"], n, paper, starts))

    def test_pdf_and_html(self):
        quote = docs.quote_create(
            self.store,
            {
                "title": "Ünïcödé-ish café work — 2 options",
                "options": [
                    {"name": "A", "lines": [{"kind": "fee", "description": "Thing " * 40, "unit_price": "10"}] * 30},
                    {"name": "B", "lines": [{"kind": "fee", "description": "Other", "unit_price": "20"}]},
                ],
            },
            customer_id=self.cust["id"],
        )["quote"]
        result = render_doc(self.store, self.store.doc(quote["id"]), fmt="both", engine="builtin")
        pdf = Path(result["files"]["pdf"]).read_bytes()
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        self.assertIn(b"%%EOF", pdf[-20:])
        self.assertGreater(pdf.count(b"/Type /Page "), 1)
        html = Path(result["files"]["html"]).read_text(encoding="utf-8")
        self.assertIn("Ünïcödé-ish", html)
        self.assertNotIn("unit_cost", html)

    def test_currency_code_on_headline_totals(self):
        spec = {"title": "Call-out", "lines": [{"kind": "fee", "description": "Call-out", "unit_price": "100"}]}
        quote = docs.quote_create(self.store, spec, customer_id=self.cust["id"])["quote"]

        def html():
            result = render_doc(self.store, self.store.doc(quote["id"]), fmt="html", engine="builtin")
            return Path(result["files"]["html"]).read_text(encoding="utf-8")

        self.assertIn("$105.00 CAD", html())  # Canada's $ is shared with the US, Australia...
        profile = self.store.profile()
        profile["locale"]["show_currency_code"] = "never"
        self.store.save_profile(profile)
        self.assertNotIn("105.00 CAD", html())

    def test_costs_never_reach_customer_documents(self):
        quote = docs.quote_create(
            self.store,
            {
                "title": "Secret margins",
                "lines": [{"kind": "material", "description": "Widget", "qty": 1, "unit_cost": "123.45"}],
            },
            customer_id=self.cust["id"],
        )["quote"]
        result = render_doc(self.store, self.store.doc(quote["id"]), fmt="html", engine="builtin")
        html = Path(result["files"]["html"]).read_text(encoding="utf-8")
        self.assertNotIn("123.45", html)
        self.assertIn("154.31", html)

    def test_png_logo_decoding(self):
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

        raw = b"".join(b"\x00" + bytes([255, 0, 0, 128]) * 2 for _ in range(2))
        png = (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b"")
        )
        width, height, rgb = _png_decode(png)
        self.assertEqual((width, height), (2, 2))
        self.assertEqual(rgb[:3], bytes([255, 127, 127]))


class DocsTests(unittest.TestCase):
    """An independent review of 1.0.0: the README called the listings live before any was approved."""

    def test_the_readme_says_what_works_today_and_how_updates_arrive(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        install = readme.split("## Install", 1)[1].split("\n## ", 1)[0]
        self.assertIn("**Works today:**", install)
        self.assertIn("**Coming soon:**", install)
        self.assertNotIn("open **Plugins** in the sidebar", readme)
        self.assertIn("\n## Updates\n", readme)

    def test_the_skill_says_hands_off_the_data_files_and_ask_before_co_void_or_payment_undo(self):
        """An independent review: the SKILL only said not to edit totals, and didn't mention co void or payment
        undo, which take no code."""
        skill = (ROOT / "skills" / "job-to-paid" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("never edit the data files by hand", skill)
        self.assertIn("`jtp co void`", skill)
        self.assertIn("`jtp payment undo`", skill)
        self.assertIn("and so does `jtp customer forget-estimates`", " ".join(skill.split()))

    def test_security_states_the_known_limit_and_fits_the_wrap_width(self):
        """An independent review of 1.0.1: the estimate check is per customer, which SECURITY.md didn't say, and one
        of its lines ran past the wrap width."""
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        limit = " ".join(security.split("- **Known limit.**", 1)[1].split("\n- ", 1)[0].split())
        self.assertIn("Figures copied to a different customer, a copy of an open estimate retyped at a different "
                      "amount, or a copy under a different description aren't recognised.", limit)  # fmt: skip
        self.assertEqual([n for n, line in enumerate(security.splitlines(), 1) if len(line) > 120], [])

    def test_the_guide_says_what_asks_but_not_how_matching_works(self):
        """An independent review of 1.0.2: invoicing.md spelled out the matching rule ("the same description and
        amount", "isn't asked about again"), which reads as how to avoid the question. SECURITY.md keeps it."""
        guide = " ".join((ROOT / "skills" / "job-to-paid" / "references" / "invoicing.md").read_text(
            encoding="utf-8").split())  # fmt: skip
        self.assertIn("A line that repeats an estimate the owner hasn't confirmed", guide)
        for detail in ("description and amount", "same description", "isn't asked about again", "any amount"):
            self.assertNotIn(detail, guide)
        security = " ".join((ROOT / "SECURITY.md").read_text(encoding="utf-8").split())
        self.assertIn("by the same description and amount", security)
        self.assertIn("by its description at any amount", security)
        self.assertIn("for 30 days", security)

    def test_security_says_what_the_30_days_are_counted_on(self):
        """An independent check of 1.0.4: SECURITY.md didn't say what the 30 days were measured against."""
        security = " ".join((ROOT / "SECURITY.md").read_text(encoding="utf-8").split())
        self.assertIn("counted on the computer's clock, in the business's time zone", security)
        self.assertIn("the dates only ever move the way that asks more", security)  # the 1.0.5 recheck
        self.assertIn("so setting the clock back can't date it in the past, and nothing ever moves a recorded date",
                      security)  # fmt: skip
        self.assertIn("never while it's dated later than the computer's date", security)
        self.assertIn("`JTP_TODAY` sets the date only in the toolkit's own tests", security)
        self.assertIn("Invoices and quotes use the computer's date as it is.", security)  # the 1.0.4 rechecks
        self.assertIn("and yeses on separate invoices aren't added together", security)  # the 1.0.5 check
        self.assertNotIn("clock reset", security)

    def test_the_skill_says_to_keep_the_estimate_mark_when_changing_a_quantity(self):
        """An independent review of 1.0.2: the likeliest honest slip is dropping the mark while changing a quantity."""
        skill = " ".join((ROOT / "skills" / "job-to-paid" / "SKILL.md").read_text(encoding="utf-8").split())
        self.assertIn("keep the estimate mark when you change a quantity or price", skill)

    def test_the_skill_never_searches_the_home_folder(self):
        skill = (ROOT / "skills" / "job-to-paid" / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("find ~", skill)


class CliTests(unittest.TestCase):
    def run_cli(self, home, *args, stdin=None, env=None):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "jtp.py"), "--home", str(home), *args],
            capture_output=True,
            text=True,
            input=stdin,
            env=dict(os.environ, **(env or {})),
        )
        return proc.returncode, json.loads(proc.stdout)

    def test_jtp_today_is_ignored_outside_the_tests(self):
        """An independent check of 1.0.4: JTP_TODAY moved "today" for any command, so a removed estimate could lapse
        with no owner involved: deleted with a past date, a copy issued with a future one, or the memory wiped by
        issuing an unrelated invoice with a future date."""
        with tempfile.TemporaryDirectory() as tmp:
            home, past, ahead = Path(tmp) / "data", {"JTP_TODAY": "2026-09-01"}, {"JTP_TODAY": "2027-06-01"}
            self.run_cli(home, "demo", "--currency", "CAD")
            spec = '[{"kind":"fee","description":"Call-out","unit_price":89,"estimate":true}]'
            draft = self.run_cli(home, "invoice", "create", "--customer", "C-0001", "--spec", spec)[1]["invoice"]["id"]
            self.run_cli(home, "invoice", "delete", draft, env=past)
            remembered = Store(home).customer("C-0001")["unconfirmed_estimates"][0]
            self.assertNotEqual(remembered["date"], "2026-09-01")  # dated by the computer's clock
            copy = self.run_cli(home, "invoice", "create", "--customer", "C-0001", "--spec",
                                '[{"kind":"fee","description":"Call-out","unit_price":89}]')[1]["invoice"]["id"]  # fmt: skip
            for env in (None, ahead):
                code, out = self.run_cli(home, "invoice", "issue", copy, env=env)
                self.assertEqual((code, out["code"]), (1, "estimates_unconfirmed"), env)
            drain = self.run_cli(home, "invoice", "create", "--customer", "C-0001", "--spec",
                                 '[{"kind":"fee","description":"Drain clean","unit_price":10}]')[1]["invoice"]["id"]
            code, out = self.run_cli(home, "invoice", "issue", drain, env=ahead)
            self.assertEqual(code, 0)
            self.assertNotEqual(Store(home).doc(drain)["date"], "2027-06-01")
            code, out = self.run_cli(home, "invoice", "issue", copy)
            self.assertEqual((code, out["code"]), (1, "estimates_unconfirmed"))  # the memory is still there

    def test_the_clock_shows_its_dates_and_nothing_needs_resetting(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "data"
            self.run_cli(home, "demo")
            code, out = self.run_cli(home, "clock", "show")
            self.assertEqual((code, out["clock_behind"]), (0, False))
            (home / "clock.json").write_text('{"latest": "2099-01-01"}', encoding="utf-8")
            code, out = self.run_cli(home, "clock", "show")
            self.assertEqual((out["clock_behind"], out["latest_date"]), (True, "2099-01-01"))
            code, out = self.run_cli(home, "clock", "reset")
            self.assertEqual((code, out["code"]), (2, "usage"))

    def run_cli_on(self, day, home, *args):
        """The CLI with the computer's date standing at `day` (JTP_TODAY works only where the tests turn it on)."""
        start = ("import sys; sys.path.insert(0, sys.argv[1]); from jobtopaid import util; util.TEST_DATES = True; "
                 "from jobtopaid.cli import main; main(sys.argv[2:])")  # fmt: skip
        proc = subprocess.run([sys.executable, "-c", start, str(SCRIPTS), "--home", str(home), *args],
                              capture_output=True, text=True, env=dict(os.environ, JTP_TODAY=day))  # fmt: skip
        return proc.returncode, json.loads(proc.stdout)

    def test_after_a_gap_in_use_a_clock_set_back_cant_make_an_estimate_lapse(self):
        """An independent recheck of 1.0.5: with the folder last used on 09-01 and an estimate removed on 10-19, a
        reset approved with the clock set back dated it in the past, so a copy issued unasked on 10-19 and 11-17.
        There's no reset now, and nothing moves a recorded date."""
        estimate = ('[{"kind":"fee","description":"Call-out","unit_price":89,"estimate":true},'
                    '{"kind":"labour","description":"Repair","qty":2}]')  # fmt: skip
        repair = '[{"kind":"labour","description":"Repair","qty":2}]'
        copy = '[{"kind":"fee","description":"Call-out","unit_price":89}]'
        travel_estimate = '[{"kind":"fee","description":"Travel","unit_price":40,"estimate":true}]'
        travel_copy = '[{"kind":"fee","description":"Travel","unit_price":40}]'
        for route in ("deleted", "taken off", "voided"):
            with self.subTest(route=route), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp) / "data"
                self.run_cli_on("2026-09-01", home, "demo", "--currency", "CAD")
                draft = self.run_cli_on("2026-10-19", home, "invoice", "create", "--customer", "C-0001", "--spec",
                                        estimate)[1]["invoice"]["id"]  # fmt: skip
                if route == "deleted":
                    self.run_cli_on("2026-10-19", home, "invoice", "delete", draft)
                elif route == "taken off":
                    self.run_cli_on("2026-10-19", home, "invoice", "update", draft, "--spec", repair)
                else:
                    void = ("invoice", "void", draft, "--reason", "Wrong job")
                    asked = self.run_cli_on("2026-10-19", home, *void)[1]
                    self.run_cli_on("2026-10-19", home, *void, "--owner-confirmed", "yes", "--confirm",
                                    asked["confirm"])  # fmt: skip
                for back in ("2026-09-01", "2026-09-02", "2026-09-15"):  # the computer's clock set back
                    code, out = self.run_cli_on(back, home, "clock", "reset")
                    self.assertEqual((code, out["code"]), (2, "usage"))  # nothing to approve
                    self.assertTrue(self.run_cli_on(back, home, "clock", "show")[1]["clock_behind"])
                    self.run_cli_on(back, home, "customer", "update", "C-0002", "--notes", "Seen with the clock back")
                travel = self.run_cli_on("2026-09-15", home, "invoice", "create", "--customer", "C-0001", "--spec",
                                         travel_estimate)[1]["invoice"]["id"]  # fmt: skip
                self.run_cli_on("2026-09-15", home, "invoice", "delete", travel)  # removed with the clock still back
                store = Store(home)  # no record is dated before the day it was made
                dates = [line["date"] for entry in store.customer("C-0001").get("unconfirmed_estimates") or []
                         for line in entry["lines"]]  # fmt: skip
                dates += [t["date"] for doc in store.docs("invoice") for t in doc.get("removed_estimates") or []]
                self.assertEqual(sorted(set(dates)), ["2026-10-19"])
                for day, line in (("2026-10-19", copy), ("2026-11-17", copy), ("2026-10-19", travel_copy)):
                    inv = self.run_cli_on(day, home, "invoice", "create", "--customer", "C-0001", "--spec", line)
                    code, out = self.run_cli_on(day, home, "invoice", "issue", inv[1]["invoice"]["id"])
                    self.assertEqual((code, out["code"]), (1, "estimates_unconfirmed"), (day, line))

    def test_time_zone_names_are_checked(self):
        """The 1.0.4 rechecks: an unknown time zone ("Mars/Olympus") was saved with no warning, and "today" quietly
        used the computer's local time."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "data"
            self.run_cli(home, "demo", "--currency", "CAD")
            code, out = self.run_cli(home, "profile", "set", "locale.timezone", "Mars/Olympus")
            self.assertEqual(code, 1)
            self.assertIn("locale.timezone 'Mars/Olympus' isn't a time zone name", out["error"])
            self.assertEqual(Store(home).profile()["locale"]["timezone"], "America/Vancouver")  # not saved
            code, out = self.run_cli(home, "profile", "set", "locale.timezone", "europe/london")
            self.assertEqual((code, out["value"]), (0, "Europe/London"))  # spelled the tz database's way
            self.assertEqual(Store(home).profile()["locale"]["timezone"], "Europe/London")
            profile = json.loads((home / "profile.json").read_text(encoding="utf-8"))
            profile["locale"]["timezone"] = "Mars/Olympus"  # saved before this check existed
            (home / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
            code, out = self.run_cli(home, "doctor")
            self.assertIn("isn't a time zone name", " ".join(out["setup"]["errors"]))
            code, out = self.run_cli(home, "invoice", "create", "--customer", "C-0001", "--spec",
                                     '[{"kind":"fee","description":"Drain clean","unit_price":10}]')  # fmt: skip
            code, out = self.run_cli(home, "invoice", "issue", out["invoice"]["id"])  # the 1.0.5 check: not issued
            self.assertEqual((code, out["code"]), (1, "setup_needed"))
            self.assertIn("The saved time zone 'Mars/Olympus' isn't one", out["error"])
            for name in ("localtime", "Factory", "posixrules"):  # listed by some tz databases, but not places
                code, out = self.run_cli(home, "profile", "set", "locale.timezone", name)
                self.assertEqual(code, 1, name)

    def test_doctor_says_when_the_computers_clock_is_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "data"
            self.run_cli(home, "demo")
            (home / "clock.json").write_text('{"latest": "2099-01-01"}', encoding="utf-8")
            code, out = self.run_cli(home, "doctor")
            self.assertIn("is earlier than the last date this folder was used (2099-01-01)", out["date_note"])
            self.assertIn("the estimate questions may come more often until the clock catches up", out["date_note"])

    def test_cli_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "data"
            code, out = self.run_cli(home, "quote", "list")
            self.assertEqual((code, out["ok"], out["code"]), (1, False, "not_initialized"))
            self.assertEqual(self.run_cli(home, "init", "--currency", "GBP")[1]["created"], True)
            for key, value in (
                ("business.name", "Test Ltd"),
                ("business.email", "a@b.example"),
                ("tax.taxes", '[{"code":"VAT","name":"VAT","rate":"20"}]'),
                ("tax.confirmed", "true"),
                ("rates.labour", '[{"key":"standard","name":"Labour","rate":"60"}]'),
                ("payment.methods", '[{"name":"Bank","instructions":"12-34-56 12345678"}]'),
            ):
                self.assertTrue(self.run_cli(home, "profile", "set", key, value)[1]["ok"])
            self.assertTrue(self.run_cli(home, "profile", "check")[1]["ready_for_invoices"])
            cust = self.run_cli(home, "customer", "add", "--name", "Sam", "--phone", "07700 900123")[1]["customer"]
            code, out = self.run_cli(
                home,
                "quote",
                "create",
                "--customer",
                cust["id"],
                "--title",
                "Tap",
                "--spec",
                "-",
                stdin='[{"kind":"labour","description":"Fix tap","qty":1.5}]',
            )
            self.assertEqual(code, 0)
            self.assertIn("£108.00", out["summary"])
            code, out = self.run_cli(home, "quote", "accept", out["quote"]["id"])
            self.assertEqual(code, 0)
            self.assertTrue(Path(out["as_quoted_copy"]).is_file())  # never marked sent: keep what was quoted
            job = out["job"]
            code, out = self.run_cli(
                home, "payment", "record", "--job", job, "--deposit", "--percent", "25", "--method", "Bank"
            )
            self.assertEqual((code, out["payment"]["amount"]), (0, "27.00"))  # 25% of £108.00
            code, out = self.run_cli(home, "invoice", "create", "--job", job)
            self.assertEqual(code, 0)
            self.assertIn("leaving £81.00", out["summary"])
            code, out = self.run_cli(home, "invoice", "issue", out["invoice"]["id"])
            self.assertIn("£108.00", out["summary"])
            self.assertNotIn("lines", out["invoice"])  # status changes print the short form
            inv_id = out["invoice"]["id"]
            code, out = self.run_cli(home, "invoice", "mark-sent", inv_id, "--channel", "email", "--to", "s@x.example")
            self.assertTrue(Path(out["sent_copy"]).is_file())
            credit = ("credit", "create", "--invoice", inv_id, "--amount", "12", "--reason", "Oops")
            code, out = self.run_cli(home, *credit)
            self.assertEqual((code, out["code"]), (1, "needs_owner"))  # a credit note asks the owner first
            self.assertIn("Issue a credit note for £12.00", out["ask_owner"])
            code, out = self.run_cli(home, *credit, "--owner-confirmed", "yes", "--confirm", out["confirm"])
            self.assertEqual(code, 0)
            code, out = self.run_cli(home, "credit", "mark-sent", out["credit_note"]["id"], "--channel", "email")
            self.assertEqual((code, out["credit_note"]["sent"][0]["channel"]), (0, "email"))
            code, out = self.run_cli(home, "report", "tax", "--basis", "cash")
            self.assertEqual((code, out["basis"]), (0, "cash"))
            code, out = self.run_cli(
                home, "quote", "create", "--dry-run", "--spec", '[{"kind":"fee","description":"x","unit_price":5}]'
            )
            self.assertTrue(Path(out["files"]["PREVIEW"]["pdf"]).is_file())
            code, out = self.run_cli(home, "brief")
            self.assertEqual(code, 0)
            code, out = self.run_cli(home, "invoice", "frobnicate")
            self.assertEqual((code, out["code"]), (2, "usage"))

    def test_pricebook_import_takes_the_file_as_shown_in_its_example(self):
        """`jtp pricebook import items.csv` crashed: the file name landed in the id slot (found by running every
        example in the command reference)."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "data"
            self.run_cli(home, "demo")
            (Path(tmp) / "items.csv").write_text("name,kind,price\nBrass tap washer,material,2.50\n",
                                                 encoding="utf-8")  # fmt: skip
            code, out = self.run_cli(home, "pricebook", "import", str(Path(tmp) / "items.csv"))
            self.assertEqual((code, out["added"], out["errors"]), (0, 1, []), out)
            code, out = self.run_cli(home, "pricebook", "import", str(Path(tmp) / "missing.csv"))
            self.assertEqual(code, 1)
            self.assertIn("No file", out["error"])

    def test_doctor_on_an_empty_folder_says_how_to_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self.run_cli(Path(tmp) / "data", "doctor")
            self.assertEqual((code, out["initialized"]), (0, False))
            self.assertIn("jtp init --currency", out["next"])
            self.assertIn("demo", out["next"])

    def test_dry_run_previews_or_refuses(self):
        """An independent review of 1.0.1: invoice issue --dry-run issued the invoice for real. An action that
        can't preview now refuses --dry-run rather than going ahead."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "data"
            self.run_cli(home, "demo")
            code, out = self.run_cli(home, "invoice", "create", "--customer", "C-0001", "--spec",
                                     '[{"kind":"fee","description":"Call-out","unit_price":89}]')  # fmt: skip
            inv = out["invoice"]["id"]
            code, out = self.run_cli(home, "invoice", "issue", inv, "--dry-run")
            self.assertEqual(code, 0)
            self.assertTrue(out["summary"].startswith(f"Preview only, nothing saved or issued. Invoice {inv} "))
            self.assertTrue(Path(out["files"]["PREVIEW"]["pdf"]).is_file())
            for args in (("invoice", "void", inv), ("invoice", "mark-sent", inv, "--channel", "email"),
                         ("quote", "accept", "Q-0001"), ("co", "approve", "CO-0001"), ("credit", "mark-sent", "CN-1")):
                code, out = self.run_cli(home, *args, "--dry-run")
                self.assertEqual((code, out["code"]), (1, "usage"), args)
            self.assertEqual(self.run_cli(home, "invoice", "show", inv)[1]["invoice"]["status"], "draft")
            for args in (("customer", "forget-estimates", "C-0001"), ("payment", "record", inv, "--amount", "5"),
                         ("brief",)):  # commands with no --dry-run say so too (an independent check of 1.0.4)
                code, out = self.run_cli(home, *args, "--dry-run")
                self.assertEqual((code, out["code"]), (1, "usage"), args)
                self.assertTrue(out["error"].startswith(f"--dry-run doesn't work with {' '.join(args[:2])}"), out)
            code, out = self.run_cli(home, "invoice", "issue", inv, "--no-such-flag")
            self.assertEqual((code, out["code"], out["error"]), (2, "usage", "unrecognized arguments: --no-such-flag"))

    def test_forget_estimates_takes_the_owners_two_step_yes(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "data"
            self.run_cli(home, "demo")
            code, out = self.run_cli(home, "customer", "forget-estimates", "C-0001")
            self.assertEqual((code, out["forgotten"]), (0, []))  # nothing remembered: nothing to ask
            self.run_cli(home, "invoice", "create", "--customer", "C-0001", "--spec",
                         '[{"kind":"fee","description":"Call-out","unit_price":89,"estimate":true}]')  # fmt: skip
            self.run_cli(home, "invoice", "delete", "INV-0003")
            code, out = self.run_cli(home, "customer", "forget-estimates", "C-0001")
            self.assertEqual((code, out["code"]), (1, "needs_owner"))
            code, out = self.run_cli(home, "customer", "forget-estimates", "C-0001", "--owner-confirmed", "yes",
                                     "--confirm", out["confirm"])  # fmt: skip
            self.assertEqual((code, len(out["forgotten"])), (0, 1))

    def test_customer_update_warns_about_a_name_another_customer_has(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "data"
            self.run_cli(home, "demo")
            code, out = self.run_cli(home, "customer", "update", "C-0002", "--name", "Jane Doe")
            self.assertEqual((code, out["customer"]["name"]), (0, "Jane Doe"))
            self.assertIn("There's already a customer called Jane Doe: C-0001", out["warnings"][0])

    def test_issuing_again_says_so_and_a_no_isnt_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "data"
            self.run_cli(home, "demo", "--currency", "CAD")
            code, out = self.run_cli(home, "invoice", "create", "--customer", "C-0001", "--spec",
                                     '[{"kind":"fee","description":"Call-out","unit_price":89}]')  # fmt: skip
            inv = out["invoice"]["id"]
            code, out = self.run_cli(home, "invoice", "issue", inv)
            self.assertIn("total $93.45 CAD", out["summary"])  # chat totals carry the code, like the PDFs
            code, out = self.run_cli(home, "invoice", "issue", inv, "--owner-confirmed", "absolutely not")
            self.assertEqual((code, out["code"]), (1, "already_issued"))
            code, out = self.run_cli(home, "invoice", "issue", inv, "--owner-confirmed", "yes")
            self.assertTrue(out["summary"].startswith(f"{inv} was already issued on "), out["summary"])
            self.assertNotIn("note", out)
            code, out = self.run_cli(home, "invoice", "void", inv)
            self.assertEqual((code, out["code"]), (1, "needs_owner"))
            code, out = self.run_cli(home, "invoice", "void", inv, "--owner-confirmed", "Yes, void it.",
                                     "--confirm", out["confirm"])  # fmt: skip
            self.assertEqual((code, out["invoice"]["status"]), (0, "void"))

    def test_payment_instructions_follow_the_business_phone_and_email(self):
        """An independent review: after a new phone number and email, the payment instructions still gave the old
        ones, with no warning."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "data"
            self.run_cli(home, "demo", "--currency", "CAD")
            code, out = self.run_cli(home, "profile", "set", "business.phone", "+1 604 555 0199")
            self.assertEqual((code, out["payment_methods_updated"]), (0, ["Card"]))
            code, out = self.run_cli(home, "profile", "set", "business.email", "hello@new-plumbing.example")
            self.assertIn("pay@demo-plumbing.example", " ".join(out["warnings"]))
            methods = Store(home).profile()["payment"]["methods"]
            self.assertEqual(methods[1]["instructions"], "Call us on +1 604 555 0199 to pay by card.")

    def test_the_owners_yes_takes_two_steps(self):
        """The agent asks the owner the exact question, then sends their words back with the question's code."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "data"
            self.run_cli(home, "demo")
            code, out = self.run_cli(home, "invoice", "create", "--customer", "C-0001", "--spec",
                                     '[{"kind":"fee","description":"Call-out","unit_price":89,"estimate":true}]')
            inv = out["invoice"]["id"]
            code, out = self.run_cli(home, "invoice", "issue", inv)
            self.assertEqual((code, out["code"]), (1, "estimates_unconfirmed"))
            self.assertIn("Are they final?", out["ask_owner"])
            question = out["confirm"]
            code, out = self.run_cli(home, "invoice", "issue", inv, "--owner-confirmed", "ok", "--confirm", question)
            self.assertEqual((code, out["code"]), (1, "unclear_answer"))
            code, out = self.run_cli(home, "invoice", "issue", inv, "--owner-confirmed", "yes, they're final",
                                     "--confirm", question)  # fmt: skip
            self.assertEqual((code, out["invoice"]["status"]), (0, "issued"))


if __name__ == "__main__":
    unittest.main()
