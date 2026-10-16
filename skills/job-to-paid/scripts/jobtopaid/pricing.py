"""Turn line specs (what the agent writes) into priced lines (what documents store).

A line spec is a small JSON object, for example:

    {"kind": "labour", "description": "Install heater", "qty": 4}            # uses your labour rate
    {"kind": "material", "description": "40 gal heater", "qty": 1, "unit_cost": 980}  # cost + your markup
    {"kind": "material", "pricebook": "PB-0003", "qty": 2}                   # from your price book
    {"kind": "fee", "description": "Call-out", "unit_price": 89}             # a fixed price
    {"kind": "discount", "description": "Repeat customer", "percent": 10}
    {"kind": "text", "description": "Old tank removed and recycled"}

Costs are what the owner pays suppliers. By default they're before tax
(`tax.costs_include_tax` false). The markup is added to the before-tax cost, and
when the business shows prices including tax (`tax.prices_include_tax`) the
line's taxes are then added, so the markup is the same in every country.
"""

from __future__ import annotations

from decimal import Decimal

from . import profile as profile_mod
from .money import HUNDRED, LINE_KINDS, PRICED_KINDS, ZERO, default_tax_codes, q
from .util import JTPError, dec_str, to_decimal, trim_decimal

DEFAULT_UNITS = {"labour": "hr", "material": "ea", "equipment": "ea", "subcontract": "job", "fee": "ea", "other": "ea"}
MARKUP_KEYS = {
    "material": "material_markup_pct",
    "equipment": "equipment_markup_pct",
    "subcontract": "subcontract_markup_pct",
}
PASSTHROUGH = ("source", "section", "display_qty", "note", "internal_note", "tax_fixed")


def find_pricebook_item(items: list, ref) -> dict | None:
    ref_text = str(ref).strip().lower()
    for item in items:
        if item.get("id", "").lower() == ref_text or (item.get("sku") or "").lower() == ref_text:
            return item
    for item in items:
        if item.get("name", "").strip().lower() == ref_text:
            return item
    return None


def _check_places(value: Decimal, max_places: int, field: str) -> None:
    exponent = value.normalize().as_tuple().exponent
    if isinstance(exponent, int) and -exponent > max_places:
        raise JTPError(
            f"{field} {trim_decimal(value)} has more than {max_places} decimal places.",
            hint=f"Round it to at most {max_places} decimals.",
        )


def tax_rate_sum(profile: dict, codes: list) -> Decimal:
    """Combined rate (as a fraction) of the given tax codes, used to move between before- and after-tax."""
    return sum(
        (to_decimal(t["rate"]) / HUNDRED for t in profile["tax"].get("taxes") or [] if t["code"] in (codes or [])),
        ZERO,
    )


def cost_before_tax(profile: dict, kind: str, cost: Decimal, codes: list | None = None) -> Decimal:
    """Turn a cost as the owner entered it into a before-tax cost."""
    if kind == "labour" or not profile["tax"].get("costs_include_tax"):
        return cost
    if codes is None:
        codes = default_tax_codes(kind, profile["tax"].get("taxes") or [])
    rate = tax_rate_sum(profile, codes)
    return cost / (1 + rate) if rate else cost


def _resolve_tax_codes(spec: dict, item: dict | None, kind: str, profile: dict, index: int) -> list:
    taxes = profile["tax"].get("taxes") or []
    known = {t["code"] for t in taxes}
    if "tax_codes" in spec:
        codes = spec["tax_codes"]
        if not isinstance(codes, list):
            raise JTPError(f'Line {index}: tax_codes must be a list, e.g. ["GST"] or [] for none.')
        unknown = [c for c in codes if c not in known]
        if unknown:
            known_text = ", ".join(sorted(known)) or "none set up"
            raise JTPError(
                f"Line {index}: unknown tax code(s) {', '.join(unknown)}.", hint=f"Known codes: {known_text}"
            )
        return list(codes)
    if item and isinstance(item.get("tax_codes"), list):
        return [c for c in item["tax_codes"] if c in known]
    return default_tax_codes(kind, taxes)


def normalize_lines(profile: dict, pricebook: list, specs, places: int, prefix: str = "L") -> tuple:
    """Returns (lines, warnings, pricebook_suggestions)."""
    if not isinstance(specs, list) or not specs:
        raise JTPError(
            "Expected a non-empty list of lines.", hint='Example: [{"kind":"labour","description":"Install","qty":3}]'
        )
    inclusive = bool(profile["tax"].get("prices_include_tax"))
    lines, warnings, suggestions = [], [], []
    for index, spec in enumerate(specs, 1):
        if not isinstance(spec, dict):
            raise JTPError(f"Line {index} must be an object, got {type(spec).__name__}.")
        kind = (spec.get("kind") or "other").lower()
        if kind == "labor":
            kind = "labour"
        if kind not in LINE_KINDS:
            raise JTPError(f"Line {index}: unknown kind {kind!r}.", hint=f"Use one of: {', '.join(LINE_KINDS)}")
        description = str(spec.get("description") or "").strip()
        line = {"id": f"{prefix}{index}", "kind": kind, "description": description}
        for key in PASSTHROUGH:
            if key in spec:
                line[key] = spec[key]

        if kind == "text":
            if not description:
                raise JTPError(f"Line {index}: a text line needs a description.")
            lines.append(line)
            continue

        if kind == "discount":
            if "percent" in spec:
                pct = to_decimal(spec["percent"], f"line {index} percent")
                if not 0 < pct <= 100:
                    raise JTPError(f"Line {index}: discount percent must be between 0 and 100.")
                line["percent"] = trim_decimal(pct)
                line["description"] = description or f"Discount ({trim_decimal(pct)}%)"
            elif "amount" in spec:
                amount = q(to_decimal(spec["amount"], f"line {index} amount"), places)
                if amount <= 0:
                    raise JTPError(f"Line {index}: discount amount must be positive (it is subtracted).")
                line["amount_input"] = str(amount)
                line["description"] = description or "Discount"
            else:
                raise JTPError(
                    f"Line {index}: a discount needs percent or amount.",
                    hint='{"kind":"discount","percent":10} or {"kind":"discount","amount":50}',
                )
            lines.append(line)
            continue

        item = None
        if spec.get("pricebook"):
            item = find_pricebook_item(pricebook, spec["pricebook"])
            if item is None:
                raise JTPError(
                    f"Line {index}: no price book item {spec['pricebook']!r}.",
                    hint="Search with: jtp pricebook list --search <words>",
                )
            line["source"] = dict(line.get("source") or {}, pricebook=item["id"])
            if not description:
                description = item.get("name", "")
                line["description"] = description
            if item.get("kind") and "kind" not in spec:
                kind = item["kind"]
                line["kind"] = kind

        qty = to_decimal(spec.get("qty", 1), f"line {index} qty")
        if qty <= 0:
            raise JTPError(
                f"Line {index}: qty must be more than zero.",
                hint="Use a discount line to take money off, or a credit note after invoicing.",
            )
        _check_places(qty, 4, f"Line {index} qty")
        line["qty"] = trim_decimal(qty)
        default_unit = profile_mod.labels(profile)["hour_unit"] if kind == "labour" else DEFAULT_UNITS.get(kind, "ea")
        line["unit"] = str(spec.get("unit") or (item or {}).get("unit") or default_unit)
        if not description:
            raise JTPError(f"Line {index}: needs a description the customer will understand.")

        # Taxes first: when prices include tax, a cost-based price depends on them.
        codes = _resolve_tax_codes(spec, item, kind, profile, index)
        line["tax_codes"] = codes
        rate = tax_rate_sum(profile, codes)

        unit_cost = None  # always stored before tax
        entered_cost = None
        markup = None
        if kind == "labour" and "unit_price" not in spec and not item:
            labour = profile_mod.labour_rate(profile, spec.get("rate_key"))
            unit_price = to_decimal(labour.get("rate"), f"labour rate {labour.get('key')}")
            if unit_price <= 0:
                raise JTPError(
                    f"Labour rate {labour.get('key')!r} is not set.",
                    hint='Set it with: jtp profile set rates.labour \'[{"key":"standard","name":"Labour","rate":"95"}]\'',
                )
            line["rate_key"] = labour.get("key")
            cost = labour.get("cost") or profile["rates"].get("internal_labour_cost")
            if cost not in (None, ""):
                unit_cost = to_decimal(cost, "labour cost")
        elif "unit_price" in spec:
            unit_price = to_decimal(spec["unit_price"], f"line {index} unit_price")
            _check_places(unit_price, places + 2, f"Line {index} unit_price")
            stored_cost = spec.get("unit_cost_before_tax", spec.get("cost_before_tax"))
            if stored_cost not in (None, ""):
                unit_cost = to_decimal(stored_cost, f"line {index} unit_cost_before_tax")
            elif "unit_cost" in spec:
                entered_cost = to_decimal(spec["unit_cost"], f"line {index} unit_cost")
            elif item and item.get("cost") not in (None, ""):
                entered_cost = to_decimal(item["cost"], "price book cost")
            if entered_cost is not None:
                unit_cost = cost_before_tax(profile, kind, entered_cost, codes)
            if kind == "labour" and unit_cost is None:
                cost = profile["rates"].get("internal_labour_cost")
                if spec.get("rate_key"):
                    cost = profile_mod.labour_rate(profile, spec["rate_key"]).get("cost") or cost
                    line["rate_key"] = spec["rate_key"]
                if cost not in (None, ""):
                    unit_cost = to_decimal(cost, "labour cost")
        elif "unit_cost" in spec or (item and item.get("cost") not in (None, "")):
            entered_cost = to_decimal(spec.get("unit_cost", (item or {}).get("cost")), f"line {index} unit_cost")
            unit_cost = cost_before_tax(profile, kind, entered_cost, codes)
            if item and item.get("price") not in (None, "") and "unit_cost" not in spec and "markup_pct" not in spec:
                unit_price = to_decimal(item["price"], "price book price")
            else:
                if "markup_pct" in spec:
                    markup = to_decimal(spec["markup_pct"], f"line {index} markup_pct")
                elif item and item.get("markup_pct") not in (None, ""):
                    markup = to_decimal(item["markup_pct"], "price book markup")
                else:
                    markup = to_decimal(profile["rates"].get(MARKUP_KEYS.get(kind, ""), "0") or "0", "markup")
                price_before_tax = unit_cost * (1 + markup / HUNDRED)
                unit_price = q(price_before_tax * (1 + rate) if inclusive else price_before_tax, places)
        elif item and item.get("price") not in (None, ""):
            unit_price = to_decimal(item["price"], "price book price")
        else:
            raise JTPError(
                f"Line {index} ({description}) has no price.",
                hint="Give unit_price, unit_cost (your markup is added), a pricebook item, or for labour a rate_key.",
            )

        if unit_price < 0:
            raise JTPError(f"Line {index}: price can't be negative.", hint="Use a discount line instead.")
        line["unit_price"] = dec_str(unit_price)
        line["amount"] = str(q(qty * unit_price, places))
        if unit_cost is not None:
            line["unit_cost"] = dec_str(q(unit_cost, places + 2))
        if markup is not None:
            line["markup_pct"] = trim_decimal(markup)

        if spec.get("estimate"):
            line["estimate"] = True
            warnings.append(
                f"Line {index} ({description}): marked as an estimate. Confirm the real figure with the owner "
                "before this goes to the customer."
            )
        if kind in ("material", "equipment") and not item and entered_cost is not None:
            suggestions.append(
                {
                    "name": description,
                    "kind": kind,
                    "unit": line["unit"],
                    "cost": dec_str(entered_cost),
                    "price": line["unit_price"],
                }
            )
        lines.append(line)

    if not any(ln["kind"] in PRICED_KINDS for ln in lines):
        raise JTPError("There are no priced lines.", hint="Add at least one labour, material, fee or other line.")
    return lines, warnings, suggestions


def lines_as_spec(lines: list) -> list:
    """Turn stored lines back into editable specs with explicit prices.

    Stored costs are already before tax, so they're carried as `unit_cost_before_tax`
    and never converted a second time.
    """
    specs = []
    for ln in lines:
        kind = ln.get("kind")
        if kind == "text":
            spec = {"kind": "text", "description": ln.get("description", "")}
        elif kind == "discount":
            spec = {"kind": "discount", "description": ln.get("description", "")}
            if "percent" in ln:
                spec["percent"] = ln["percent"]
            else:
                spec["amount"] = ln.get("amount_input") or str(-Decimal(ln.get("amount", "0")))
        else:
            spec = {
                "kind": kind,
                "description": ln.get("description", ""),
                "qty": ln.get("qty"),
                "unit": ln.get("unit"),
                "unit_price": ln.get("unit_price"),
                "tax_codes": ln.get("tax_codes", []),
            }
            if ln.get("unit_cost") not in (None, ""):
                spec["unit_cost_before_tax"] = ln["unit_cost"]
            if ln.get("rate_key"):
                spec["rate_key"] = ln["rate_key"]
            if ln.get("estimate"):
                spec["estimate"] = True  # stays an estimate until the owner confirms the real figure
        for key in PASSTHROUGH:
            if key in ln:
                spec[key] = ln[key]
        specs.append(spec)
    return specs
