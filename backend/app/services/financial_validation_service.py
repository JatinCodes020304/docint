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


def _make_check(name: str, formula: str, operands: dict[str, float | None], calculated: float | None, reported: float | None) -> ValidationCheck:
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
    status = "PASS" if abs(variance) <= _tolerance(reported) else "FAIL"
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
        checks.append(_make_check(
            f"line_item_{idx}_quantity_x_unit_price",
            "quantity * unit_price",
            {"quantity": q, "unit_price": u}, calc, amount,
        ))

    amounts = [_num(item.get("amount")) for item in items]
    amounts_present = bool(amounts) and all(v is not None for v in amounts)
    line_sum = sum(amounts) if amounts_present else None
    subtotal = _num(_raw(extracted, "subtotal", "taxable_amount"))
    total = _num(_raw(extracted, "total_amount", "grand_total", "amount_due"))
    target = subtotal if subtotal is not None else total
    checks.append(_make_check(
        "invoice_line_items_sum",
        "sum(line_item.amount) ≈ subtotal (or total when subtotal absent)",
        {"line_items_sum": line_sum}, line_sum, target,
    ))

    tax = _num(_raw(extracted, "tax_amount", "tax", "gst_amount"))
    discount = _num(_raw(extracted, "discount", "discount_amount"))
    # Only use discount when it is actually present; do not invent 0.
    if subtotal is not None and tax is not None and total is not None:
        if discount is not None:
            calc = subtotal + tax - discount
            operands = {"subtotal": subtotal, "tax_amount": tax, "discount": discount}
            formula = "subtotal + tax_amount - discount"
        else:
            calc = subtotal + tax
            operands = {"subtotal": subtotal, "tax_amount": tax}
            formula = "subtotal + tax_amount"
        checks.append(_make_check("invoice_total_check", formula, operands, calc, total))
    else:
        checks.append(_make_check(
            "invoice_total_check", "subtotal + tax_amount - discount (where shown)",
            {"subtotal": subtotal, "tax_amount": tax}, None, total,
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


def _profit_and_loss(extracted: dict[str, Any]) -> list[ValidationCheck]:
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


def _cash_flow(extracted: dict[str, Any]) -> list[ValidationCheck]:
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
