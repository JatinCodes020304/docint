from app.services.financial_validation_service import validate_financials


def F(value):
    return {"value": value, "evidence": {"source_text": None, "page_number": 1}, "page_number": 1}


def test_invoice_math_passes():
    extracted = {
        "subtotal": F(100.0),
        "tax_amount": F(18.0),
        "discount": F(0.0),
        "total_amount": F(118.0),
        "line_items": [
            {"description": "A", "quantity": 2, "unit_price": 50, "amount": 100}
        ],
    }
    result = validate_financials(extracted, "invoice")
    statuses = {c["name"]: c["status"] for c in result["checks"]}
    assert statuses["line_item_1_quantity_x_unit_price"] == "PASS"
    assert statuses["invoice_total_check"] == "PASS"
    assert result["overall_status"] == "PASS"


def test_missing_fields_are_not_applicable_not_invented():
    extracted = {"total_amount": F(118.0), "line_items": []}
    result = validate_financials(extracted, "invoice")
    total_check = next(c for c in result["checks"] if c["name"] == "invoice_total_check")
    assert total_check["status"] == "NOT_APPLICABLE"
    assert total_check["calculated_value"] is None


def test_calculation_is_preserved_when_reported_value_is_missing():
    extracted = {
        "line_items": [
            {"description": "A", "quantity": 2, "unit_price": 2.2, "amount": None}
        ]
    }
    result = validate_financials(extracted, "invoice")
    check = next(c for c in result["checks"] if c["name"] == "line_item_1_quantity_x_unit_price")
    assert check["status"] == "NOT_APPLICABLE"
    assert check["calculated_value"] == 4.4
    assert check["reported_value"] is None
    assert check["variance"] is None


def test_balance_sheet_comparative_periods_validate_independently():
    extracted = {
        "total_capital_and_liabilities": F({"31-Mar-20": 15808304373, "31-Mar-19": 12928057065}),
        "total_assets": F({"31-Mar-20": 15808304373, "31-Mar-19": 12928057065}),
    }
    result = validate_financials(extracted, "balance_sheet")
    assert result["overall_status"] == "PASS"
    assert len(result["checks"]) == 2
    assert all(check["status"] == "PASS" for check in result["checks"])
