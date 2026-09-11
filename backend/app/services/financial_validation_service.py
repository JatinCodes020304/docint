"""Deterministic financial reconciliation checks required by the case study.

No LLM is used here. Missing operands produce NOT_APPLICABLE; values are never
invented. Comparative statements are checked independently per period when the
extracted field value is a period-keyed object.
"""
from __future__ import annotations

from numbers import Number
from typing import Any, Iterable

from app.core.config import settings
from app.core.logging import get_logger
from app.schemas.extraction import FinancialValidationResult, ValidationCheck

logger = get_logger(__name__)


def _raw(extracted: dict[str, Any], *names: str) -> Any:
    for name in names:
        field = extracted.get(name)
        if isinstance(field, dict) and "value" in field:
            if field["value"] is not None:
                return field["value"]
    return None


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Number):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace(",", "")
        if s.startswith("(") and s.endswith(")"):
            s = "-" + s[1:-1]
        for symbol in ("₹", "$", "€", "£"):
            s = s.replace(symbol, "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _period_map(value: Any) -> dict[str, float | None]:
    if isinstance(value, dict):
        return {str(k): _num(v) for k, v in value.items()}
    n = _num(value)
    return {"document": n} if n is not None else {}


def _tolerance(reported: float) -> float:
    return max(settings.VALIDATION_ABS_TOLERANCE, abs(reported) * settings.VALIDATION_PCT_TOLERANCE)


def _make_check(name: str, formula: str, operands: dict[str, float | None], calculated: float | None, reported: float | None, *, tolerance: float | None = None) -> ValidationCheck:
    # If all operands exist we may deterministically compute the formula even when the
    # document does not report a comparison value. In that case the check remains
    # NOT_APPLICABLE, but exposing calculated_value is useful and does not invent data.
    operands_complete = not any(v is None for v in operands.values())
    safe_calculated = round(calculated, 6) if calculated is not None and operands_complete else None
    if safe_calculated is None or reported is None:
        return ValidationCheck(
            name=name, formula=formula, operands=operands,
            calculated_value=safe_calculated, reported_value=reported, variance=None,
            status="NOT_APPLICABLE",
        )
    variance = safe_calculated - reported
    allowed = _tolerance(reported) if tolerance is None else tolerance
    status = "PASS" if abs(variance) <= allowed else "FAIL"
    return ValidationCheck(
        name=name, formula=formula, operands=operands,
        calculated_value=safe_calculated, reported_value=reported,
        variance=round(variance, 6), status=status,
    )


def _binary_sum_checks(name: str, formula: str, extracted: dict[str, Any], left_aliases: tuple[str, ...], right_aliases: tuple[str, ...], reported_aliases: tuple[str, ...]) -> list[ValidationCheck]:
    left, right, reported = _raw(extracted, *left_aliases), _raw(extracted, *right_aliases), _raw(extracted, *reported_aliases)
    maps = [_period_map(x) for x in (left, right, reported)]
    periods = sorted(set().union(*(m.keys() for m in maps))) or ["document"]
    checks = []
    for period in periods:
        a, b, r = (m.get(period) for m in maps)
        calc = a + b if a is not None and b is not None else None
        checks.append(_make_check(f"{name}_{period}", formula, {left_aliases[0]: a, right_aliases[0]: b}, calc, r))
    return checks


def _invoice(extracted: dict[str, Any]) -> list[ValidationCheck]:
    checks: list[ValidationCheck] = []
    items = extracted.get("line_items") or []
    for idx, item in enumerate(items, start=1):
        q, u, amount = _num(item.get("quantity")), _num(item.get("unit_price")), _num(item.get("amount"))
        calc = q * u if q is not None and u is not None else None
        # Line-item arithmetic should be much stricter than statement-level
        # reconciliation. A 0.5% tolerance can incorrectly accept OCR slips such
        # as 29.06 vs a visibly printed 29.00. Allow only the configured absolute
        # money tolerance (normally one cent) for quantity × unit-price checks.
        checks.append(_make_check(
            f"line_item_{idx}_quantity_x_unit_price",
            "quantity * unit_price",
            {"quantity": q, "unit_price": u}, calc, amount,
            tolerance=settings.VALIDATION_ABS_TOLERANCE,
        ))

    amounts = [_num(item.get("amount")) for item in items]
    amounts_present = bool(amounts) and all(v is not None for v in amounts)
    line_sum = sum(amounts) if amounts_present else None

    # Reconcile line totals without treating tax-summary net amounts as an
    # invoice subtotal. `subtotal` is used only when the document explicitly
    # exposes a subtotal field. `net_amount` / `taxable_amount` remain valid
    # bases for the separate tax-to-total equation below, but they are not
    # automatically assumed to be the item-total target. This avoids the common
    # GST-inclusive receipt failure: items sum to the final inclusive total while
    # a GST summary separately prints a pre-tax Net Amt.
    explicit_subtotal = _num(_raw(extracted, "subtotal"))
    pre_tax = _num(_raw(extracted, "pre_tax_amount", "net_amount", "taxable_amount"))
    total = _num(_raw(extracted, "total_amount", "grand_total", "amount_due", "total_inclusive_gst", "total_including_tax"))

    # Prefer an explicitly printed subtotal. If none exists, the final printed
    # total is the applicable reconciliation target. We intentionally do not
    # pick whichever number is mathematically closest: that could turn an OCR
    # mistake into a false PASS.
    if explicit_subtotal is not None:
        line_target_name, line_target = "subtotal", explicit_subtotal
    elif total is not None:
        line_target_name, line_target = "total_amount", total
    else:
        line_target_name, line_target = "none", None

    line_operands = {"line_items_sum": line_sum}
    if line_target_name == "subtotal":
        line_operands["subtotal"] = explicit_subtotal
    elif line_target_name == "total_amount":
        line_operands["total_amount"] = total
    checks.append(_make_check(
        "invoice_line_items_sum",
        f"sum(line_item.amount) ≈ applicable reported subtotal/total [target: {line_target_name}]",
        line_operands,
        line_sum, line_target,
    ))

    tax = _num(_raw(extracted, "tax_amount", "tax", "gst_amount"))
    discount = _num(_raw(extracted, "discount", "discount_amount"))
    # For the tax equation, an explicit subtotal or an explicitly printed pre-tax
    # / net / taxable amount can be the base. Nothing is derived or invented.
    tax_base = explicit_subtotal if explicit_subtotal is not None else pre_tax
    tax_base_name = "subtotal" if explicit_subtotal is not None else "pre_tax_or_net_amount"
    if tax_base is not None and tax is not None and total is not None:
        if discount is not None:
            calc = tax_base + tax - discount
            operands = {tax_base_name: tax_base, "tax_amount": tax, "discount": discount}
            formula = f"{tax_base_name} + tax_amount - discount"
        else:
            calc = tax_base + tax
            operands = {tax_base_name: tax_base, "tax_amount": tax}
            formula = f"{tax_base_name} + tax_amount"
        checks.append(_make_check("invoice_total_check", formula, operands, calc, total))
    else:
        checks.append(_make_check(
            "invoice_total_check", "reported pre-tax/subtotal + tax - discount (where shown)",
            {tax_base_name: tax_base, "tax_amount": tax}, None, total,
        ))

    cash = _num(_raw(extracted, "cash_paid", "amount_paid", "cash_tendered"))
    change = _num(_raw(extracted, "change", "change_due"))
    calc_change = cash - total if cash is not None and total is not None else None
    checks.append(_make_check(
        "cash_change_check", "cash_paid - total_amount",
        {"cash_paid": cash, "total_amount": total}, calc_change, change,
    ))
    return checks


def _period_formula_check(name: str, formula: str, terms: list[tuple[str, tuple[str, ...], float]], reported_aliases: tuple[str, ...], extracted: dict[str, Any]) -> list[ValidationCheck]:
    term_values = [(label, _raw(extracted, *aliases), sign) for label, aliases, sign in terms]
    reported = _raw(extracted, *reported_aliases)
    maps = {label: _period_map(value) for label, value, _ in term_values}
    report_map = _period_map(reported)
    periods = sorted(set(report_map) | set().union(*(set(m) for m in maps.values()))) or ["document"]
    out = []
    for period in periods:
        operands: dict[str, float | None] = {}
        calc = 0.0
        complete = True
        for label, _, sign in term_values:
            val = maps[label].get(period)
            operands[label] = val
            if val is None:
                complete = False
            else:
                calc += sign * val
        r = report_map.get(period)
        out.append(_make_check(
            f"{name}_{period}", formula, operands,
            calc if complete else None, r,
        ))
    return out


def _balance_sheet(extracted: dict[str, Any]) -> list[ValidationCheck]:
    # The strongest universal rule in the brief. Component reconciliation is
    # only safe when the source exposes explicit component groupings; Gemini
    # must not guess which arbitrary rows belong to which side, so unavailable
    # component groupings remain NOT_APPLICABLE rather than fabricated.
    assets = _raw(extracted, "total_assets")
    liabilities = _raw(extracted, "total_capital_and_liabilities", "total_liabilities_and_equity", "total_liabilities")
    equity = _raw(extracted, "total_equity", "shareholders_equity", "equity")

    # Prefer reported combined capital+liabilities if present; otherwise the
    # common accounting identity liabilities + equity = assets is supported.
    combined = _raw(extracted, "total_capital_and_liabilities", "total_liabilities_and_equity")
    if combined is not None:
        return _period_formula_check(
            "balance_sheet_balances", "total_capital_and_liabilities = total_assets",
            [("total_capital_and_liabilities", ("total_capital_and_liabilities", "total_liabilities_and_equity"), 1.0)],
            ("total_assets",), extracted,
        )
    return _period_formula_check(
        "balance_sheet_balances", "total_liabilities + total_equity = total_assets",
        [("total_liabilities", ("total_liabilities",), 1.0), ("total_equity", ("total_equity", "shareholders_equity", "equity"), 1.0)],
        ("total_assets",), extracted,
    )



def _canonical_row_label(label: Any) -> str:
    """Normalize a printed statement row label for deterministic table fallback."""
    import re
    text = str(label or "").lower().replace("&", " and ")
    text = text.replace("’", "'").replace("‘", "'")
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text


def _table_period_value(extracted: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    """Return a period-keyed value from an explicitly extracted table row.

    This is a fallback bridge only: it maps printed rows already present in `tables`
    to canonical validator concepts. It never calculates or invents a source value.
    """
    wanted = {_canonical_row_label(a) for a in aliases}
    for table in extracted.get("tables") or []:
        if not isinstance(table, dict):
            continue
        for row in table.get("rows") or []:
            if not isinstance(row, dict):
                continue
            label = _canonical_row_label(row.get("label"))
            if label not in wanted:
                continue
            values = row.get("values")
            if isinstance(values, dict) and values:
                cleaned = {str(k): v for k, v in values.items() if _num(v) is not None}
                if cleaned:
                    return cleaned
    return None


def _pnl_value(extracted: dict[str, Any], *field_aliases: str, row_aliases: tuple[str, ...] = ()) -> Any:
    value = _raw(extracted, *field_aliases)
    if value is not None:
        return value
    return _table_period_value(extracted, row_aliases or tuple(field_aliases))


def _profit_and_loss_with_table_fallback(extracted: dict[str, Any]) -> dict[str, Any]:
    """Expose printed P&L table rows to the existing canonical validator.

    Vision sometimes returns the row correctly in `tables` but omits the duplicate
    top-level field. The validator should not mark that check N/A merely because of
    that representational omission.
    """
    mapping = {
        "interest_earned": (("interest_earned", "interest_income"), ("interest earned", "interest income")),
        "other_income": (("other_income",), ("other income",)),
        "total_income": (("total_income", "revenue"), ("total income",)),
        "interest_expended": (("interest_expended", "interest_expense"), ("interest expended", "interest expense")),
        "operating_expenses": (("operating_expenses",), ("operating expenses",)),
        "provisions_and_contingencies": (("provisions_and_contingencies", "provisions"), ("provisions and contingencies", "provisions & contingencies")),
        "total_expenditure": (("total_expenditure", "total_expenses"), ("total expenditure", "total expenses")),
        "profit_before_minority_interest": (("profit_before_minority_interest", "consolidated_net_profit_before_minority_interest", "profit_before_tax"), ("consolidated net profit for the year before minorities' interest", "consolidated net profit for the year before minorities interest", "profit before minority interest")),
        "minority_interest": (("minority_interest",), ("minority interest", "less minority interest")),
        "net_profit_attributable_to_group": (("net_profit_attributable_to_group", "consolidated_net_profit_attributable_to_group", "net_profit"), ("consolidated net profit for the year attributable to the group", "net profit attributable to the group")),
        "brought_forward_profit": (("brought_forward_profit", "profit_brought_forward"), ("brought forward consolidated profit attributable to the group", "brought forward profit")),
        "total_available_for_appropriation": (("total_available_for_appropriation",), ("total available for appropriation",)),
    }
    bridged = dict(extracted)
    for canonical, (field_aliases, row_aliases) in mapping.items():
        if _raw(bridged, *field_aliases) is not None:
            continue
        value = _table_period_value(extracted, row_aliases)
        if value is not None:
            bridged[canonical] = {"value": value}
    return bridged

def _profit_and_loss(extracted: dict[str, Any]) -> list[ValidationCheck]:
    extracted = _profit_and_loss_with_table_fallback(extracted)
    checks: list[ValidationCheck] = []
    checks += _period_formula_check(
        "total_income", "interest_earned + other_income = total_income",
        [("interest_earned", ("interest_earned", "interest_income"), 1), ("other_income", ("other_income",), 1)],
        ("total_income", "revenue"), extracted,
    )
    checks += _period_formula_check(
        "total_expenditure", "interest_expended + operating_expenses + provisions_and_contingencies = total_expenditure",
        [("interest_expended", ("interest_expended", "interest_expense"), 1), ("operating_expenses", ("operating_expenses",), 1), ("provisions_and_contingencies", ("provisions_and_contingencies", "provisions",), 1)],
        ("total_expenditure", "total_expenses"), extracted,
    )
    checks += _period_formula_check(
        "profit_before_minority", "total_income - total_expenditure = profit_before_minority_interest",
        [("total_income", ("total_income", "revenue"), 1), ("total_expenditure", ("total_expenditure", "total_expenses"), -1)],
        ("profit_before_minority_interest", "consolidated_net_profit_before_minority_interest", "profit_before_tax"), extracted,
    )
    checks += _period_formula_check(
        "profit_attributable_to_group", "profit_before_minority_interest - minority_interest = net_profit_attributable_to_group",
        [("profit_before_minority_interest", ("profit_before_minority_interest", "consolidated_net_profit_before_minority_interest"), 1), ("minority_interest", ("minority_interest",), -1)],
        ("net_profit_attributable_to_group", "consolidated_net_profit_attributable_to_group", "net_profit"), extracted,
    )
    checks += _period_formula_check(
        "appropriation", "current_profit + brought_forward_profit = total_available_for_appropriation",
        [("current_profit", ("current_profit", "net_profit"), 1), ("brought_forward_profit", ("brought_forward_profit", "profit_brought_forward"), 1)],
        ("total_available_for_appropriation",), extracted,
    )
    return checks


def _cash_flow_with_table_fallback(extracted: dict[str, Any]) -> dict[str, Any]:
    """Bridge explicitly printed cash-flow table rows to canonical validator fields.

    Like the P&L bridge, this only reuses values already extracted from the source
    tables. It does not calculate, infer, or repair missing figures.
    """
    mapping = {
        "operating_cash_flow": (("operating_cash_flow", "net_cash_flow_from_operating_activities"), ("net cash flow from operating activities", "net cash generated from operating activities", "net cash from operating activities")),
        "investing_cash_flow": (("investing_cash_flow", "net_cash_flow_from_investing_activities"), ("net cash flow from investing activities", "net cash used in investing activities", "net cash from investing activities")),
        "financing_cash_flow": (("financing_cash_flow", "net_cash_flow_from_financing_activities"), ("net cash flow from financing activities", "net cash generated from financing activities", "net cash from financing activities")),
        "fx_adjustment": (("fx_adjustment", "translation_adjustment", "effect_of_exchange_rates"), ("effect of exchange rate changes on cash and cash equivalents", "effect of exchange rates on cash and cash equivalents", "fx adjustment", "translation adjustment")),
        "net_increase_in_cash": (("net_change_in_cash", "net_increase_in_cash", "net_increase_in_cash_and_cash_equivalents"), ("net increase in cash and cash equivalents", "net increase in cash & cash equivalents", "net change in cash and cash equivalents")),
        "opening_cash": (("opening_cash", "opening_cash_and_cash_equivalents"), ("cash and cash equivalents at beginning of year", "cash and cash equivalents at the beginning of year", "opening cash and cash equivalents")),
        "cash_acquired_on_amalgamation": (("cash_acquired_on_amalgamation", "other_cash_adjustments", "other_adjustments"), ("cash and cash equivalents acquired on amalgamation", "cash acquired on amalgamation")),
        "closing_cash": (("closing_cash", "closing_cash_and_cash_equivalents"), ("cash and cash equivalents at end of year", "cash and cash equivalents at the end of year", "closing cash and cash equivalents")),
    }
    bridged = dict(extracted)
    for canonical, (field_aliases, row_aliases) in mapping.items():
        if _raw(bridged, *field_aliases) is not None:
            continue
        value = _table_period_value(extracted, row_aliases)
        if value is not None:
            bridged[canonical] = {"value": value}
    return bridged


def _cash_flow(extracted: dict[str, Any]) -> list[ValidationCheck]:
    extracted = _cash_flow_with_table_fallback(extracted)
    checks: list[ValidationCheck] = []
    checks += _period_formula_check(
        "net_increase_in_cash",
        "operating_cash_flow + investing_cash_flow + financing_cash_flow + fx_adjustment = net_increase_in_cash",
        [
            ("operating_cash_flow", ("operating_cash_flow", "net_cash_flow_from_operating_activities"), 1),
            ("investing_cash_flow", ("investing_cash_flow", "net_cash_flow_from_investing_activities"), 1),
            ("financing_cash_flow", ("financing_cash_flow", "net_cash_flow_from_financing_activities"), 1),
            ("fx_adjustment", ("fx_adjustment", "translation_adjustment", "effect_of_exchange_rates"), 1),
        ],
        ("net_change_in_cash", "net_increase_in_cash", "net_increase_in_cash_and_cash_equivalents"), extracted,
    )
    # The brief says "+ other applicable adjustments". Only include one if it
    # is actually extracted; otherwise do not assume a zero adjustment.
    adjustment = _raw(extracted, "cash_acquired_on_amalgamation", "other_cash_adjustments", "other_adjustments")
    terms = [
        ("opening_cash", ("opening_cash", "opening_cash_and_cash_equivalents"), 1),
        ("net_increase_in_cash", ("net_change_in_cash", "net_increase_in_cash", "net_increase_in_cash_and_cash_equivalents"), 1),
    ]
    if adjustment is not None:
        terms.append(("other_adjustments", ("cash_acquired_on_amalgamation", "other_cash_adjustments", "other_adjustments"), 1))
    checks += _period_formula_check(
        "closing_cash",
        "opening_cash + net_increase_in_cash + applicable_adjustments = closing_cash",
        terms,
        ("closing_cash", "closing_cash_and_cash_equivalents"), extracted,
    )
    return checks


def validate_financials(extracted: dict[str, Any], document_type: str) -> dict[str, Any]:
    logger.info("Starting financial validation for document_type=%s", document_type)
    if document_type == "invoice":
        checks = _invoice(extracted)
    elif document_type == "balance_sheet":
        checks = _balance_sheet(extracted)
    elif document_type == "profit_and_loss":
        checks = _profit_and_loss(extracted)
    elif document_type == "cash_flow_statement":
        checks = _cash_flow(extracted)
    else:
        checks = []

    statuses = [c.status for c in checks]
    if "FAIL" in statuses:
        overall = "FAIL"
    elif "PASS" in statuses:
        overall = "PASS"
    else:
        overall = "NOT_APPLICABLE"

    issues = [f"{c.name} failed: variance {c.variance}" for c in checks if c.status == "FAIL"]
    result = FinancialValidationResult(checks=checks, overall_status=overall, issues=issues)
    logger.info("Financial validation complete: overall=%s checks=%d", overall, len(checks))
    return result.model_dump()
