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


def test_invoice_tax_inclusive_line_items_reconcile_to_total_not_net_amount():
    extracted = {
        "net_amount": F(27.36),
        "tax_amount": F(1.64),
        "total_amount": F(29.00),
        "cash_paid": F(50.00),
        "change": F(21.00),
        "line_items": [
            {"description": "CANON PG-47BK", "quantity": 1, "unit_price": 29.00, "amount": 29.00}
        ],
    }
    result = validate_financials(extracted, "invoice")
    by_name = {c["name"]: c for c in result["checks"]}
    assert by_name["line_item_1_quantity_x_unit_price"]["status"] == "PASS"
    assert by_name["invoice_line_items_sum"]["status"] == "PASS"
    assert by_name["invoice_line_items_sum"]["reported_value"] == 29.0
    assert "target: total_amount" in by_name["invoice_line_items_sum"]["formula"]
    assert by_name["invoice_total_check"]["status"] == "PASS"
    assert by_name["cash_change_check"]["status"] == "PASS"
    assert result["overall_status"] == "PASS"


def test_invoice_tax_exclusive_line_items_still_reconcile_to_subtotal():
    extracted = {
        "subtotal": F(100.0),
        "tax_amount": F(18.0),
        "total_amount": F(118.0),
        "line_items": [
            {"description": "A", "quantity": 2, "unit_price": 50.0, "amount": 100.0}
        ],
    }
    result = validate_financials(extracted, "invoice")
    check = next(c for c in result["checks"] if c["name"] == "invoice_line_items_sum")
    assert check["status"] == "PASS"
    assert check["reported_value"] == 100.0
    assert "target: subtotal" in check["formula"]


def test_invoice_line_items_fail_when_neither_subtotal_nor_total_reconciles():
    extracted = {
        "subtotal": F(80.0),
        "tax_amount": F(20.0),
        "total_amount": F(100.0),
        "line_items": [
            {"description": "A", "quantity": 1, "unit_price": 70.0, "amount": 70.0}
        ],
    }
    result = validate_financials(extracted, "invoice")
    check = next(c for c in result["checks"] if c["name"] == "invoice_line_items_sum")
    assert check["status"] == "FAIL"


def test_invoice_gst_inclusive_net_summary_reconciles_to_total_not_net():
    extracted = {
        "net_amount": F(27.36),
        "tax_amount": F(1.64),
        "total_amount": F(29.00),
        "cash_paid": F(50.00),
        "change": F(21.00),
        "line_items": [
            {"description": "CANON", "quantity": 1, "unit_price": 29.00, "amount": 29.00}
        ],
    }
    result = validate_financials(extracted, "invoice")
    by_name = {c["name"]: c for c in result["checks"]}
    assert by_name["invoice_line_items_sum"]["status"] == "PASS"
    assert "target: total_amount" in by_name["invoice_line_items_sum"]["formula"]
    assert by_name["invoice_total_check"]["status"] == "PASS"
    assert by_name["cash_change_check"]["status"] == "PASS"


def test_invoice_line_item_ocr_decimal_slip_is_not_hidden_by_percent_tolerance():
    extracted = {
        "total_amount": F(29.00),
        "line_items": [
            {"description": "CANON", "quantity": 1, "unit_price": 29.06, "amount": 29.00}
        ],
    }
    result = validate_financials(extracted, "invoice")
    check = next(c for c in result["checks"] if c["name"] == "line_item_1_quantity_x_unit_price")
    assert check["status"] == "FAIL"


def test_invoice_explicit_subtotal_is_preferred_over_total_for_item_sum():
    extracted = {
        "subtotal": F(100.00),
        "tax_amount": F(18.00),
        "total_amount": F(118.00),
        "line_items": [
            {"description": "A", "quantity": 2, "unit_price": 50.00, "amount": 100.00}
        ],
    }
    result = validate_financials(extracted, "invoice")
    check = next(c for c in result["checks"] if c["name"] == "invoice_line_items_sum")
    assert check["status"] == "PASS"
    assert "target: subtotal" in check["formula"]


def test_pnl_validator_uses_printed_table_rows_when_top_level_fields_are_missing():
    extracted = {
        "tables": [
            {"name": "income", "columns": ["2024", "2023"], "rows": [
                {"label": "Interest earned", "values": {"2024": 283649.02, "2023": 170754.05}},
                {"label": "Other income", "values": {"2024": 124345.75, "2023": 33912.05}},
                {"label": "Total Income", "values": {"2024": 407994.77, "2023": 204666.10}},
            ]},
            {"name": "expenditure", "columns": ["2024", "2023"], "rows": [
                {"label": "Interest expended", "values": {"2024": 154138.55, "2023": 77779.94}},
                {"label": "Operating expenses", "values": {"2024": 152269.34, "2023": 51533.69}},
                {"label": "Provisions and contingencies", "values": {"2024": 36140.38, "2023": 29203.77}},
                {"label": "Total Expenditure", "values": {"2024": 342548.27, "2023": 158517.40}},
            ]},
            {"name": "profit", "columns": ["2024", "2023"], "rows": [
                {"label": "Consolidated Net Profit for the year before minorities’ interest", "values": {"2024": 65446.50, "2023": 46148.70}},
                {"label": "Minority Interest", "values": {"2024": 1384.46, "2023": 151.59}},
                {"label": "Consolidated Net Profit for the year attributable to the group", "values": {"2024": 64062.04, "2023": 45997.11}},
            ]},
        ]
    }
    result = validate_financials(extracted, "profit_and_loss")
    by_name = {c["name"]: c for c in result["checks"]}
    for prefix in ("total_income", "total_expenditure", "profit_before_minority", "profit_attributable_to_group"):
        assert by_name[f"{prefix}_2024"]["status"] == "PASS"
        assert by_name[f"{prefix}_2023"]["status"] == "PASS"


def test_cash_flow_validator_uses_printed_table_rows_and_amalgamation_adjustment():
    extracted = {
        "tables": [{
            "name": "cash_flow",
            "columns": ["2025", "2024"],
            "rows": [
                {"label": "Net increase in cash and cash equivalents", "values": {"2025": 21113.39, "2024": 20504.99}},
                {"label": "Cash and cash equivalents at beginning of year", "values": {"2025": 228834.51, "2024": 197147.81}},
                {"label": "Cash and cash equivalents acquired on amalgamation", "values": {"2025": 0.0, "2024": 11181.71}},
                {"label": "Cash and cash equivalents at end of year", "values": {"2025": 249947.90, "2024": 228834.51}},
            ],
        }]
    }
    result = validate_financials(extracted, "cash_flow_statement")
    by_name = {c["name"]: c for c in result["checks"]}
    assert by_name["closing_cash_2025"]["status"] == "PASS"
    assert by_name["closing_cash_2024"]["status"] == "PASS"
    assert by_name["closing_cash_2024"]["calculated_value"] == 228834.51
    assert by_name["closing_cash_2024"]["reported_value"] == 228834.51
