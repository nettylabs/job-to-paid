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

from jobtopaid import documents as docs  # noqa: E402
from jobtopaid import followups, records, reports  # noqa: E402
from jobtopaid.demo import demo_profile, seed  # noqa: E402
from jobtopaid.money import compute_totals, currency_display, format_money  # noqa: E402
from jobtopaid.pdf import _png_decode  # noqa: E402
from jobtopaid.pricing import normalize_lines  # noqa: E402
from jobtopaid.render import render_doc, render_receipt  # noqa: E402
from jobtopaid.store import Store  # noqa: E402
from jobtopaid.util import JTPError, to_decimal  # noqa: E402

EVERY = ["labour", "material", "equipment", "subcontract", "fee", "other"]


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
        out = docs.invoice_void(self.store, inv["id"], "wrong customer")
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
        credit = docs.credit_create(self.store, inv["id"], amount="105", reason="Cancelled")["credit_note"]
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
        self.assertEqual(october["taxes"]["GST"]["tax_charged"], "$20.00")
        self.assertEqual(november["taxes"]["GST"]["tax_charged"], "$30.00")
        self.assertEqual(accrual["taxes"]["GST"]["tax_charged"], "$50.00")

    def test_exports_include_credit_notes(self):
        inv = self._issued()
        docs.credit_create(self.store, inv["id"], amount="21", reason="Goodwill")
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
        with self.assertRaises(JTPError) as caught:
            docs.invoice_issue(self.store, inv_id)
        self.assertEqual(caught.exception.code, "estimates_unconfirmed")
        self.assertIn("Install: 3 hr at $95.00 = $285.00", caught.exception.ask_owner)  # the question for the owner
        for not_an_answer in ("", " ", "<their exact words>"):
            with self.assertRaises(JTPError):
                docs.invoice_issue(self.store, inv_id, owner_confirmed=not_an_answer)
        issued = docs.invoice_issue(self.store, inv_id, owner_confirmed="Yes, 3 hours is right")["invoice"]
        self.assertFalse([ln for ln in issued["lines"] if ln.get("estimate")])
        self.assertEqual(issued["estimates_confirmed"]["owner_said"], "Yes, 3 hours is right")

    def test_deposits_count_as_received(self):
        quote = self._fixed_quote([{"kind": "fee", "description": "Work", "unit_price": "1000"}])
        job_id = docs.quote_accept(self.store, quote["id"], None, "Jane", "email", None)["job"]
        docs.payment_record(self.store, job_id=job_id, amount="250", method="Bank", deposit=True)
        summary = reports.job_summary(self.store, job_id)
        self.assertEqual((summary["received"], summary["deposits_held"]), ("$250.00", "$250.00"))
        self.assertIn("received $250.00", summary["summary"])

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


class RenderTests(StoreCase):
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


class CliTests(unittest.TestCase):
    def run_cli(self, home, *args, stdin=None):
        env = dict(os.environ, JTP_TODAY="2026-10-15")
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "jtp.py"), "--home", str(home), *args],
            capture_output=True,
            text=True,
            input=stdin,
            env=env,
        )
        return proc.returncode, json.loads(proc.stdout)

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
            code, out = self.run_cli(
                home, "credit", "create", "--invoice", inv_id, "--amount", "12", "--reason", "Oops"
            )
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


if __name__ == "__main__":
    unittest.main()
