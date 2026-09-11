from app.schemas.extraction import GeminiExtraction


def test_structured_extraction_schema_accepts_grounded_fields():
    obj = GeminiExtraction.model_validate({
        "fields": [{
            "name": "total_amount",
            "value": 118.0,
            "evidence": {"source_text": "Total: 118.00", "page_number": 1},
        }],
        "line_items": [],
        "tables": [],
    })
    assert obj.fields[0].name == "total_amount"
    assert obj.fields[0].evidence.page_number == 1


def test_provider_order_defaults_to_primary_then_fallback(monkeypatch):
    from app.services import extraction_service as svc
    monkeypatch.setattr(svc.settings, "LLM_PROVIDER", "groq")
    assert svc._provider_order() == ["groq", "gemini"]


def test_groq_failure_falls_back_to_gemini(monkeypatch):
    from app.services import extraction_service as svc
    from app.services.ocr_service import OCRResult, PageText

    monkeypatch.setattr(svc.settings, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(svc.settings, "GROQ_API_KEY", "test-groq")
    monkeypatch.setattr(svc.settings, "GEMINI_API_KEY", "test-gemini")

    def fail_groq(prompt):
        raise RuntimeError("Groq HTTP 503: temporary")

    def good_gemini(prompt):
        return ('{"fields":[{"name":"total_amount","value":118,'
                '"evidence":{"source_text":"Total 118","page_number":1}}],'
                '"line_items":[],"tables":[]}', "test-gemini-model")

    monkeypatch.setattr(svc, "_call_groq", fail_groq)
    monkeypatch.setattr(svc, "_call_gemini", good_gemini)

    ocr = OCRResult([PageText(1, "Invoice Total 118", "ocr")], True)
    result = svc.extract_document(ocr, "invoice")
    assert result["total_amount"]["value"] == 118


def test_receipt_summary_enrichment_is_grounded_and_does_not_invent():
    from app.services import extraction_service as svc
    from app.services.ocr_service import OCRResult, PageText

    ocr = OCRResult([PageText(1, """ADVANCO COMPANY
Total Inclusive GST 29.00
CASH 50.00
Change 21.00
GST Summary
Code % Net Amt GST Total
SR 6% 27.36 1.64 29.00
""", "ocr")], True)
    data = {"total_amount": {"value": None, "evidence": {"source_text": None, "page_number": None}, "page_number": None},
            "tax_amount": {"value": None, "evidence": {"source_text": None, "page_number": None}, "page_number": None},
            "line_items": [], "tables": []}
    svc._enrich_invoice_from_explicit_ocr(data, ocr)
    assert data["total_amount"]["value"] == 29.0
    assert data["cash_paid"]["value"] == 50.0
    assert data["change"]["value"] == 21.0
    assert data["tax_amount"]["value"] == 1.64
    assert data["tables"][0]["name"] == "gst_summary"
    assert data["tables"][0]["rows"][0]["values"]["net_amount"] == 27.36


def test_balance_sheet_comparative_rows_are_reconstructed_from_grounded_ocr():
    from app.services import extraction_service as svc
    from app.services.ocr_service import OCRResult, PageText

    text = """Consolidated Balance Sheet
As at March 31, 2020
CAPITAL AND LIABILITIES
Capital 1 5,483,286 5,446,613
Reserves and surplus 2 1,758,103,766 1,531,279,982
Deposits 3 11,462,071,336 9,225,026,779
Total 12,928,057,065
ASSETS
Cash and balances with Reserve Bank of India 6 722,110,033 468,045,896
Total 15,808,304,373 12,928,057,065
[TABLE HEADER - ALTERNATE OCR VIEW]
As at As at
31-Mar-20 31-Mar-19
[CAPITAL AND LIABILITIES NUMERIC COLUMNS - ALTERNATE OCR VIEW]
5,483,286 5,446,613
1,758,103,766 1,531,279,982
11,462,071,336 9,225,026,779
15,808,304,373 12,928,057,065
"""
    ocr = OCRResult([PageText(1, text, "ocr")], True)
    data = {"tables": [], "line_items": []}

    svc._enrich_balance_sheet_from_explicit_ocr(data, ocr)

    assert data["capital"]["value"] == {
        "31-Mar-20": 5483286.0,
        "31-Mar-19": 5446613.0,
    }
    assert data["deposits"]["value"]["31-Mar-20"] == 11462071336.0
    assert data["total_capital_and_liabilities"]["value"] == {
        "31-Mar-20": 15808304373.0,
        "31-Mar-19": 12928057065.0,
    }
    assert data["total_assets"]["value"] == {
        "31-Mar-20": 15808304373.0,
        "31-Mar-19": 12928057065.0,
    }
    assert data["tables"][0]["columns"] == ["31-Mar-20", "31-Mar-19"]


def test_invoice_change_leading_decimal_is_preserved_from_explicit_ocr():
    from app.services import extraction_service as svc
    from app.services.ocr_service import OCRResult, PageText

    ocr = OCRResult([PageText(1, """Total Sales (Inclusive GST) RM 27.90
CASH RM 28.00
CHANGE RM .10
""", "ocr")], True)
    data = {"line_items": [], "tables": []}

    svc._enrich_invoice_from_explicit_ocr(data, ocr)

    assert data["cash_paid"]["value"] == 28.0
    assert data["change"]["value"] == 0.10


def test_balance_sheet_keeps_sub_thousand_decimal_comparative_cell():
    from app.services import extraction_service as svc
    from app.services.ocr_service import OCRResult, PageText

    text = """Consolidated Balance Sheet
As at March 31, 2026
As at 31-Mar-26 As at 31-Mar-25
CAPITAL AND LIABILITIES
Capital 1 1,539.34 765.22
Reserves and surplus 2 579,975.02 517,218.98
Total 4,908,040.84 4,392,417.42
ASSETS
Cash and balances with Reserve Bank of India 6 200,707.11 144,390.25
Total 4,908,040.84 4,392,417.42
"""
    ocr = OCRResult([PageText(1, text, "ocr")], True)
    data = {"tables": [], "line_items": []}

    svc._enrich_balance_sheet_from_explicit_ocr(data, ocr)

    assert data["capital"]["value"] == {
        "31-Mar-26": 1539.34,
        "31-Mar-25": 765.22,
    }
