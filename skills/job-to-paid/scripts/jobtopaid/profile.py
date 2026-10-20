"""The business profile: who you are, how you price, how you get paid.

Every jurisdiction-specific choice (currency, tax names and rates, number and
date formats, paper size, document wording) lives here, set once during setup.
"""

from __future__ import annotations

import copy
import re

from .money import CURRENCY_DEFAULTS, PRICED_KINDS
from .util import JTPError, to_decimal, zone_name

DEFAULT_LABELS = {
    "quote": "Quote",
    "invoice": "Invoice",
    "change_order": "Change Order",
    "credit_note": "Credit Note",
    "receipt": "Receipt",
    "number": "No.",
    "date": "Date",
    "valid_until": "Valid until",
    "due_date": "Due date",
    "job": "Job",
    "reference": "Reference",
    "prepared_for": "Prepared for",
    "bill_to": "Bill to",
    "job_site": "Job site",
    "description": "Description",
    "qty": "Qty",
    "unit_price": "Unit price",
    "amount": "Amount",
    "subtotal": "Subtotal",
    "discount": "Discount",
    "total": "Total",
    "total_incl_tax": "Total (incl. tax)",
    "includes_tax": "Includes",
    "amount_paid": "Paid",
    "credits": "Credits",
    "balance_due": "Balance due",
    "deposit_required": "Deposit required to book the work",
    "how_to_pay": "How to pay",
    "pay_online": "Pay online",
    "notes": "Notes",
    "terms": "Terms",
    "option": "Option",
    "recommended": "Recommended",
    "your_options": "Your options",
    "accept_heading": "Acceptance",
    "accept_text": "To go ahead, reply with the option you'd like or sign below.",
    "accept_text_single": "To go ahead, reply to accept this quote or sign below.",
    "how_to_pay_deposit": "How to pay the deposit",
    "no_deposit_for": "no deposit needed for",
    "reason": "Reason",
    "signature": "Signature",
    "name": "Name",
    "page": "Page",
    "of": "of",
    "paid_stamp": "PAID",
    "void_stamp": "VOID",
    "draft_stamp": "DRAFT",
    "superseded_stamp": "REPLACED",
    "received_from": "Received from",
    "accepted_by": "Accepted by {name} on {date}",
    "approved_by": "Approved by {name} on {date}",
    "deposit_received": "Deposit received",
    "estimate": "estimate",
    "payments_received": "Payments received",
    "previously_invoiced": "Previously invoiced",
    "payment_received": "Payment received",
    "payment_method": "Method",
    "for_invoice": "Invoice",
    "deposit_for": "Deposit for",
    "thank_you": "Thank you for your business!",
    "change_reason": "Reason for change",
    "approval_text": "Please approve this change before the extra work starts.",
    "credit_for": "Credit against invoice",
    "revision": "rev.",
    "progress": "progress",
    "balance": "balance",
    "additional_work": "Additional work",
    "labour": "Labour",
    "hour_unit": "hr",
    "hour_short": "h",
}

DEFAULT_PROFILE = {
    "schema_version": 1,
    "business": {
        "name": "",
        "legal_name": "",
        "owner_name": "",
        "trade": "",
        "address": [],
        "phone": "",
        "email": "",
        "website": "",
        "registration_numbers": [],
        "logo_path": "",
        "brand_color": "#0F766E",
    },
    "locale": {
        "currency": "USD",
        "show_currency_code": "auto",
        "date_format": "D MMM YYYY",
        "paper_size": "letter",
        "language": "en",
        "timezone": "",
    },
    "tax": {
        "confirmed": False,
        "prices_include_tax": False,
        "costs_include_tax": False,
        "taxes": [],
        "note": "",
    },
    "rates": {
        "labour": [
            {"key": "standard", "name": "Labour", "rate": "0", "cost": ""},
        ],
        "default_labour_key": "standard",
        "material_markup_pct": "0",
        "equipment_markup_pct": "0",
        "subcontract_markup_pct": "0",
        "internal_labour_cost": "",
    },
    "terms": {
        "quote_valid_days": 30,
        "payment_due_days": 14,
        "deposit_percent": "0",
        "deposit_min_total": "0",
        "quote_terms": "",
        "invoice_terms": "",
        "warranty": "",
    },
    "payment": {
        "methods": [],
        "online_payment_links": False,
    },
    "numbering": {
        "quote_prefix": "Q-",
        "invoice_prefix": "INV-",
        "change_order_prefix": "CO-",
        "credit_note_prefix": "CN-",
        "job_prefix": "J-",
        "customer_prefix": "C-",
        "payment_prefix": "PAY-",
        "padding": 4,
    },
    "followups": {
        "quote_days": [2, 7, 14],
        "invoice_days": [-2, 1, 7, 14, 30],
        "review_request_days": 2,
        "review_link": "",
        "stop_after_days_overdue": 90,
    },
    "labels": {},
}

DATE_FORMATS = ("D MMM YYYY", "DD/MM/YYYY", "MM/DD/YYYY", "YYYY-MM-DD", "DD.MM.YYYY", "DD-MM-YYYY")
PAPER_SIZES = ("letter", "a4")


def new_profile(currency: str | None = None) -> dict:
    profile = copy.deepcopy(DEFAULT_PROFILE)
    if currency:
        profile["locale"]["currency"] = currency.upper()
    code = profile["locale"]["currency"]
    if code in ("USD", "CAD", "MXN", "PHP"):
        profile["locale"]["paper_size"] = "letter"
    else:
        profile["locale"]["paper_size"] = "a4"
    if code not in CURRENCY_DEFAULTS:
        profile["locale"]["currency_symbol"] = code + " "
    return profile


def merged(profile: dict) -> dict:
    """Fill in keys added by newer versions so old profiles keep working."""
    out = copy.deepcopy(DEFAULT_PROFILE)
    for key, value in (profile or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key].update(value)
        else:
            out[key] = value
    return out


def labels(profile: dict) -> dict:
    out = dict(DEFAULT_LABELS)
    out.update({k: v for k, v in (profile.get("labels") or {}).items() if isinstance(v, str) and v})
    return out


def labour_rate(profile: dict, key: str | None) -> dict:
    rates = profile["rates"].get("labour") or []
    key = key or profile["rates"].get("default_labour_key") or (rates[0]["key"] if rates else None)
    for rate in rates:
        if rate.get("key") == key:
            return rate
    known = ", ".join(r.get("key", "?") for r in rates) or "none set up"
    raise JTPError(
        f"Unknown labour rate {key!r}.",
        hint=f"Known rate keys: {known}. Add one with: jtp profile set rates.labour '<json list>'",
    )


def validate(profile: dict) -> list:
    """Hard errors that would make documents wrong. Returns a list of messages."""
    errors = []
    taxes = profile["tax"].get("taxes") or []
    codes = set()
    for tax in taxes:
        code = tax.get("code")
        if not code:
            errors.append("Every tax needs a short code, e.g. GST or VAT.")
            continue
        if code in codes:
            errors.append(f"Tax code {code} is listed twice.")
        codes.add(code)
        try:
            rate = to_decimal(tax.get("rate"), f"tax {code} rate")
            if rate < 0 or rate > 100:
                errors.append(f"Tax {code} rate {rate} must be between 0 and 100.")
        except JTPError as exc:
            errors.append(exc.message)
        for kind in tax.get("applies_to") or []:
            if kind not in PRICED_KINDS:
                errors.append(f"Tax {code} applies_to has unknown kind {kind!r}; use {', '.join(PRICED_KINDS)}.")
    if profile["tax"].get("prices_include_tax") and any(t.get("compound") for t in taxes):
        errors.append("Compound taxes can't be combined with tax-inclusive prices.")
    for rate in profile["rates"].get("labour") or []:
        if not rate.get("key"):
            errors.append("Every labour rate needs a key, e.g. standard.")
        try:
            to_decimal(rate.get("rate"), f"labour rate {rate.get('key')}")
        except JTPError as exc:
            errors.append(exc.message)
    prefixes = [
        profile["numbering"].get(k)
        for k in ("quote_prefix", "invoice_prefix", "change_order_prefix", "credit_note_prefix", "payment_prefix")
    ]
    if len(set(prefixes)) != len(prefixes):
        errors.append("Quote, invoice, change order, credit note and payment prefixes must all differ.")
    if profile["locale"].get("date_format") not in DATE_FORMATS:
        errors.append(f"locale.date_format must be one of: {', '.join(DATE_FORMATS)}.")
    if profile["locale"].get("paper_size") not in PAPER_SIZES:
        errors.append("locale.paper_size must be letter or a4.")
    if profile["locale"].get("show_currency_code", "auto") not in ("auto", "always", "never"):
        errors.append("locale.show_currency_code must be auto, always or never.")
    zone = profile["locale"].get("timezone")
    if zone and zone_name(zone) is None:
        errors.append(f"locale.timezone {zone!r} isn't a time zone name, so 'today' would quietly use the computer's "
                      "clock. Use one like America/Vancouver, Europe/London or Australia/Sydney.")  # fmt: skip
    return errors


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"\+?\(?\d[\d ().-]{5,}\d")


def follow_contact(profile: dict, key: str, old, new) -> tuple:
    """After business.email or business.phone changes, payment instructions that gave the old one give the new one.
    Returns (the names of the methods changed, warnings): other email addresses left in the instructions are named,
    since only the owner knows if those changed too (a separate address for e-Transfers, say), and so are numbers
    that end like the old phone but can't be told to be it (a local "555-0100")."""
    old, new = str(old or "").strip(), str(new or "").strip()
    changed, warnings = [], []
    if key not in ("business.email", "business.phone") or not old or not new or old == new:
        return changed, warnings
    swap = _swap_email if key == "business.email" else _swap_phone
    for method in profile["payment"].get("methods") or []:
        name = method.get("name") or "a payment method"
        text = method.get("instructions")
        if isinstance(text, str) and swap(text, old, new) != text:
            method["instructions"] = swap(text, old, new)
            changed.append(name)
        text = str(method.get("instructions") or "")
        if key == "business.email":
            others = sorted({e for e in _EMAIL.findall(text) if e.lower() != new.lower()})
            if others:
                warnings.append(
                    f"The payment instructions for {name} still give {', '.join(others)}. If that changed too, "
                    "update them: jtp profile set payment.methods '<json>'"
                )
        else:
            near = _near_phones(text, old, new)
            if near:
                warnings.append(
                    f"The payment instructions for {name} still give {', '.join(near)}, which "
                    f"{'ends' if len(near) == 1 else 'end'} like the old number {old}. If that's the old number, "
                    "change it there too: jtp profile set payment.methods '<json>'"
                )
    return changed, warnings


def _near_phones(text: str, old: str, new: str) -> list:
    """Numbers in the text that end with the old phone's last 7 digits but weren't taken to be the same number (a
    local "555-0100", an account number): only the owner can tell."""
    tail = re.sub(r"\D", "", old)[-7:]
    if len(tail) < 7:
        return []
    found = {m.group() for m in _PHONE.finditer(text)
             if re.sub(r"\D", "", m.group()).endswith(tail) and not _same_phone(m.group(), new)}  # fmt: skip
    return sorted(found)


def _swap_email(text: str, old: str, new: str) -> str:
    return re.sub(r"(?<![\w.+-])" + re.escape(old) + r"(?![\w-]|\.\w)", lambda _: new, text, flags=re.IGNORECASE)


def _swap_phone(text: str, old: str, new: str) -> str:
    """The old number, as it was typed or written another way ("+1 604 555 0100", "(604) 555-0100"), becomes the new
    one. Other numbers stay as they are, even one that ends with the same digits (an account number, a reference)."""
    return _PHONE.sub(lambda match: new if _same_phone(match.group(), old) else match.group(), text)


def _phone_digits(text: str) -> tuple:
    """(digits, international): "+1 604 555 0100" and "0044 20 7946 0100" are international; "(604) 555-0100" and
    "020 7946 0100" are national, with a leading trunk 0 left off, and so is a "(0)" after a country code
    ("+44 (0)20 7946 0100")."""
    raw = re.sub(r"\(0\)", "", text.strip())
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+"):
        return digits, True
    if digits.startswith("00"):
        return digits[2:], True
    return (digits[1:] if digits.startswith("0") else digits), False


def _same_phone(a: str, b: str) -> bool:
    """Whether two written phone numbers are the same: the same digits, or one with its country code (1 to 3 digits)
    and the other without it. Not a general suffix match: 99916045550100 isn't +1 604 555 0100."""
    (da, ia), (db, ib) = _phone_digits(a), _phone_digits(b)
    if da == db:
        return len(da) >= 7
    if ia == ib:
        return False
    full, national = (da, db) if ia else (db, da)
    return len(national) >= 7 and full.endswith(national) and 1 <= len(full) - len(national) <= 3


def check(profile: dict) -> dict:
    """What's missing before quoting and invoicing, in plain words."""
    missing_quotes = []
    missing_invoices = []
    warnings = []
    business = profile["business"]
    if not business.get("name"):
        missing_quotes.append("Business name (business.name)")
    if not (business.get("phone") or business.get("email")):
        missing_quotes.append("A phone number or email customers can reach you on (business.phone / business.email)")
    rates = profile["rates"].get("labour") or []
    if not rates or all(str(r.get("rate", "0")) in ("0", "0.00", "") for r in rates):
        missing_quotes.append("Your labour rate (rates.labour)")
    if not profile["tax"].get("confirmed"):
        missing_quotes.append(
            "Tax setup confirmed by you (tax.confirmed). Add each tax you charge, or confirm you charge none."
        )
    if not profile["payment"].get("methods"):
        missing_invoices.append("How customers can pay you (payment.methods)")
    taxes = profile["tax"].get("taxes") or []
    if taxes and not business.get("registration_numbers"):
        warnings.append(
            "You charge tax but no tax registration number is set. Most tax authorities require it on invoices "
            "(business.registration_numbers)."
        )
    if not business.get("address"):
        warnings.append("No business address set (business.address). Many places require it on invoices.")
    if not business.get("logo_path"):
        warnings.append("No logo set (business.logo_path). Documents look more trustworthy with one.")
    if not business.get("owner_name"):
        warnings.append("No owner name for signing messages (business.owner_name).")
    if not profile["locale"].get("timezone"):
        warnings.append("No time zone set (locale.timezone), so 'today' uses the computer's clock.")
    if not profile["followups"].get("review_link"):
        warnings.append("No review link set (followups.review_link), so review requests are off.")
    errors = validate(profile)
    return {
        "ready_for_quotes": not missing_quotes and not errors,
        "ready_for_invoices": not missing_quotes and not missing_invoices and not errors,
        "missing": missing_quotes + missing_invoices,
        "errors": errors,
        "warnings": warnings,
    }
