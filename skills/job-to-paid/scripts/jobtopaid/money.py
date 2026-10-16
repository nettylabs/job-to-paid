"""Money math: rounding, discounts, taxes and totals.

Everything is Decimal. Taxes are whatever the business configures (any names,
any rates, tax-exclusive or tax-inclusive prices, compound taxes), so nothing
here is tied to one country.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from .util import JTPError, to_decimal, trim_decimal

ZERO = Decimal(0)
HUNDRED = Decimal(100)

# ISO 4217 currencies whose minor unit is not 2 decimals.
_MINOR_UNITS = {
    "BIF": 0,
    "CLP": 0,
    "DJF": 0,
    "GNF": 0,
    "ISK": 0,
    "JPY": 0,
    "KMF": 0,
    "KRW": 0,
    "PYG": 0,
    "RWF": 0,
    "UGX": 0,
    "UYI": 0,
    "VND": 0,
    "VUV": 0,
    "XAF": 0,
    "XOF": 0,
    "XPF": 0,
    "BHD": 3,
    "IQD": 3,
    "JOD": 3,
    "KWD": 3,
    "LYD": 3,
    "OMR": 3,
    "TND": 3,
}

# Sensible display defaults. The business can override every one of them.
# (symbol, position, decimal separator, thousands separator)
CURRENCY_DEFAULTS = {
    "USD": ("$", "before", ".", ","),
    "CAD": ("$", "before", ".", ","),
    "AUD": ("$", "before", ".", ","),
    "NZD": ("$", "before", ".", ","),
    "GBP": ("£", "before", ".", ","),
    "EUR": (" €", "after", ",", "."),
    "CHF": ("CHF ", "before", ".", "'"),
    "JPY": ("¥", "before", ".", ","),
    "INR": ("₹", "before", ".", ","),
    "ZAR": ("R ", "before", ".", " "),
    "MXN": ("$", "before", ".", ","),
    "BRL": ("R$ ", "before", ",", "."),
    "SEK": (" kr", "after", ",", " "),
    "NOK": (" kr", "after", ",", " "),
    "DKK": (" kr.", "after", ",", "."),
    "PLN": (" zł", "after", ",", " "),
    "SGD": ("S$", "before", ".", ","),
    "HKD": ("HK$", "before", ".", ","),
    "AED": ("AED ", "before", ".", ","),
    "SAR": ("SAR ", "before", ".", ","),
    "PHP": ("₱", "before", ".", ","),
    "NGN": ("₦", "before", ".", ","),
    "KES": ("KSh ", "before", ".", ","),
    "ILS": ("₪", "before", ".", ","),
    "CNY": ("¥", "before", ".", ","),
    "KRW": ("₩", "before", ".", ","),
    "KWD": ("KWD ", "before", ".", ","),
}

LINE_KINDS = ("labour", "material", "equipment", "subcontract", "fee", "other", "discount", "text")
PRICED_KINDS = ("labour", "material", "equipment", "subcontract", "fee", "other")


def minor_units(currency: str) -> int:
    return _MINOR_UNITS.get((currency or "").upper(), 2)


def quantum(places: int) -> Decimal:
    return Decimal(1).scaleb(-places)


def q(amount: Decimal, places: int) -> Decimal:
    """Round half up to the currency's minor unit."""
    return amount.quantize(quantum(places), rounding=ROUND_HALF_UP)


def currency_display(locale: dict) -> dict:
    code = (locale.get("currency") or "USD").upper()
    symbol, position, dec_sep, thou_sep = CURRENCY_DEFAULTS.get(code, (code + " ", "before", ".", ","))
    symbol = locale.get("currency_symbol", symbol)
    return {
        "code": code,
        "symbol": symbol,
        "position": locale.get("symbol_position", position),
        "decimal": locale.get("decimal_separator", dec_sep),
        "thousands": locale.get("thousands_separator", thou_sep),
        "places": minor_units(code),
        "show_code": _show_code(code, symbol, locale.get("show_currency_code") or "auto"),
    }


def _show_code(code: str, symbol: str, setting: str) -> bool:
    """Print the ISO code next to headline totals? "auto" does it when other
    currencies use the same symbol ($, kr, ¥), so customers can't mix them up."""
    if symbol.strip().upper() == code or setting == "never":
        return False
    if setting == "always":
        return True
    mine = symbol.strip()
    return any(other != code and entry[0].strip() == mine for other, entry in CURRENCY_DEFAULTS.items())


def _group(digits: str, sep: str) -> str:
    if not sep or len(digits) <= 3:
        return digits
    head = len(digits) % 3 or 3
    parts = [digits[:head]] + [digits[i : i + 3] for i in range(head, len(digits), 3)]
    return sep.join(parts)


def format_number(value: Decimal, disp: dict, places: int | None = None, extra_places: int = 0) -> str:
    """Format with the business's separators. `extra_places` allows unit prices
    like 0.125 to show their real precision without forcing it everywhere."""
    places = disp["places"] if places is None else places
    value = Decimal(value)
    shown = places
    if extra_places:
        exact = value.normalize()
        exponent = -exact.as_tuple().exponent if exact.as_tuple().exponent < 0 else 0
        shown = max(places, min(places + extra_places, exponent))
    rounded = value.quantize(quantum(shown), rounding=ROUND_HALF_UP)
    negative = rounded < 0
    text = format(abs(rounded), "f")
    whole, _, frac = text.partition(".")
    out = _group(whole, disp["thousands"])
    if shown:
        out += disp["decimal"] + frac
    return ("-" if negative else "") + out


def format_money(value: Decimal, disp: dict, extra_places: int = 0, symbol: bool = True) -> str:
    number = format_number(value, disp, extra_places=extra_places)
    negative = number.startswith("-")
    number = number.lstrip("-")
    if symbol:
        number = disp["symbol"] + number if disp["position"] == "before" else number + disp["symbol"]
    return ("-" if negative else "") + number


def format_qty(value: Decimal, disp: dict) -> str:
    text = trim_decimal(Decimal(value))
    if disp["decimal"] != ".":
        text = text.replace(".", disp["decimal"])
    return text


def format_rate(value: Decimal) -> str:
    return trim_decimal(Decimal(value)) + "%"


# --------------------------------------------------------------------------
# Allocation helpers
# --------------------------------------------------------------------------


def allocate(total: Decimal, weights: list, places: int) -> list:
    """Split `total` across weights in minor units so the parts sum exactly."""
    unit = quantum(places)
    weight_sum = sum(weights, ZERO)
    if weight_sum == 0 or total == 0:
        return [ZERO for _ in weights]
    sign = 1 if total > 0 else -1
    units_total = int((abs(total) / unit).to_integral_value())
    raw = [abs(total) * w / weight_sum / unit for w in weights]
    floors = [int(r.to_integral_value(rounding=ROUND_DOWN)) for r in raw]
    remainder = units_total - sum(floors)
    order = sorted(range(len(weights)), key=lambda i: raw[i] - floors[i], reverse=True)
    for i in order[:remainder]:
        floors[i] += 1
    return [sign * f * unit for f in floors]


# --------------------------------------------------------------------------
# Totals
# --------------------------------------------------------------------------


def default_tax_codes(kind: str, taxes: list) -> list:
    return [t["code"] for t in taxes if kind in (t.get("applies_to") or PRICED_KINDS)]


def compute_totals(lines: list, tax_cfg: dict, places: int) -> dict:
    """Compute discounts, taxes and totals for normalized lines.

    Mutates each priced line with `discount` and `net` (strings) so documents
    keep an auditable breakdown. Returns a totals dict of strings.
    """
    taxes = tax_cfg.get("taxes") or []
    inclusive = bool(tax_cfg.get("prices_include_tax"))
    if inclusive and any(t.get("compound") for t in taxes):
        raise JTPError(
            "Compound taxes can't be combined with tax-inclusive prices.",
            hint="Set tax.prices_include_tax to false, or make every tax non-compound.",
        )
    priced = [ln for ln in lines if ln.get("kind") in PRICED_KINDS]
    amounts = [to_decimal(ln["amount"]) for ln in priced]
    discounts = [ZERO for _ in priced]
    subtotal = sum(amounts, ZERO)

    # A discount only applies to priced lines in its own section, so a quote's
    # discount never leaks onto change orders or extra work on the same invoice.
    for ln in lines:
        if ln.get("kind") != "discount":
            continue
        scope = [i for i, p in enumerate(priced) if p.get("section") == ln.get("section")]
        scope_total = sum((amounts[i] for i in scope), ZERO)
        if "percent" in ln:
            pct = to_decimal(ln["percent"], "discount percent")
            value = q(scope_total * pct / HUNDRED, places)
        else:
            value = q(to_decimal(ln["amount_input"], "discount amount"), places)
        remaining = scope_total - sum((discounts[i] for i in scope), ZERO)
        if value > remaining:
            raise JTPError(
                f"Discount {value} is larger than what's left to discount ({remaining}).",
                hint="Lower the discount amount or percent.",
            )
        shares = allocate(value, [amounts[i] - discounts[i] for i in scope], places)
        for i, share in zip(scope, shares):
            discounts[i] += share
        ln["amount"] = str(-value)

    nets = [a - d for a, d in zip(amounts, discounts)]
    for ln, d, n in zip(priced, discounts, nets):
        ln["discount"] = str(d)
        ln["net"] = str(n)

    # Tax is worked out and rounded line by line, so documents add up: an invoice for an
    # accepted quote plus its change orders comes to exactly what the customer agreed to.
    bases = {t["code"]: ZERO for t in taxes}
    tax_by_code = {t["code"]: ZERO for t in taxes}
    used = set()
    for ln, net in zip(priced, nets):
        line_tax, line_bases, net_excl = line_tax_breakdown(ln, net, taxes, inclusive, places)
        ln["tax_amounts"] = {code: str(value) for code, value in line_tax.items()}
        ln["net_excl_tax"] = str(net_excl)
        for code, value in line_tax.items():
            tax_by_code[code] += value
            bases[code] += line_bases[code]
            used.add(code)
    tax_rows = []
    for t in taxes:
        code = t["code"]
        if code in used and not (bases[code] == 0 and tax_by_code[code] == 0):
            tax_rows.append(_tax_row(t, q(bases[code], places), tax_by_code[code]))
    tax_total = sum(tax_by_code.values(), ZERO)
    if inclusive:
        total = sum(nets, ZERO)
        net_excl_tax = total - tax_total
    else:
        net_excl_tax = sum(nets, ZERO)
        total = net_excl_tax + tax_total

    cost = ZERO
    unknown_cost = []
    hours = ZERO
    for ln in priced:
        if ln.get("unit_cost") not in (None, ""):
            cost += q(to_decimal(ln["qty"]) * to_decimal(ln["unit_cost"]), places)
        elif ln.get("kind") in ("material", "equipment", "subcontract", "labour"):
            unknown_cost.append(ln.get("description") or ln.get("kind"))
        if ln.get("kind") == "labour" and (ln.get("unit") or "hr").lower().strip(".") not in NON_HOUR_UNITS:
            hours += to_decimal(ln["qty"])

    discount_total = sum(discounts, ZERO)
    return {
        "subtotal": str(q(subtotal, places)),
        "discount": str(q(-discount_total, places)),
        "net": str(q(subtotal - discount_total, places)),
        "net_excl_tax": str(q(net_excl_tax, places)),
        "taxes": tax_rows,
        "tax_total": str(q(tax_total, places)),
        "total": str(q(total, places)),
        "prices_include_tax": inclusive,
        "cost": str(q(cost, places)),
        "cost_unknown_for": unknown_cost,
        "margin": str(q(net_excl_tax - cost, places)),
        "labour_hours": trim_decimal(hours),
    }


NON_HOUR_UNITS = ("day", "days", "d", "job", "ea", "each", "flat", "visit", "lot", "ls", "lump sum")


def line_tax_breakdown(ln: dict, net: Decimal, taxes: list, inclusive: bool, places: int) -> tuple:
    """Taxes for one priced line, each rounded to the minor unit: ({code: tax}, {code: base}, net before tax).

    A line can carry `tax_fixed` ({"net": ..., "taxes": {code: amount}}): the tax left to bill on a
    quote line after progress invoices, so all the invoices together match the quote to the cent.
    It only applies while the line's net is unchanged; edit the line and its tax is worked out again.
    """
    codes = ln.get("tax_codes") or []
    applied = [t for t in taxes if t["code"] in codes]
    fixed = ln.get("tax_fixed") or {}
    use_fixed = fixed.get("net") not in (None, "") and to_decimal(fixed["net"]) == net
    fixed_taxes = (fixed.get("taxes") or {}) if use_fixed else {}

    def rounded(code, exact):
        return q(to_decimal(fixed_taxes[code]), places) if code in fixed_taxes else q(exact, places)

    out, bases = {}, {}
    if inclusive:
        rate_sum = sum((to_decimal(t["rate"]) / HUNDRED for t in applied), ZERO)
        before_tax = net / (1 + rate_sum) if rate_sum else net
        for t in applied:
            out[t["code"]] = rounded(t["code"], before_tax * to_decimal(t["rate"]) / HUNDRED)
        net_excl = net - sum(out.values(), ZERO)
        return out, {code: net_excl for code in out}, net_excl
    prior = ZERO
    for t in applied:
        base = net + (prior if t.get("compound") else ZERO)
        out[t["code"]] = rounded(t["code"], base * to_decimal(t["rate"]) / HUNDRED)
        bases[t["code"]] = base
        prior += out[t["code"]]
    return out, bases, net


def _tax_row(tax: dict, base: Decimal, amount: Decimal) -> dict:
    return {
        "code": tax["code"],
        "name": tax.get("name") or tax["code"],
        "rate": trim_decimal(to_decimal(tax["rate"])),
        "compound": bool(tax.get("compound")),
        "base": str(base),
        "amount": str(amount),
    }
