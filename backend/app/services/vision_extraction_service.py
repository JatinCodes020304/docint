"""Gemini Vision extraction helper.

This module is intentionally limited to *reading* the supplied document image(s).
It does not perform financial validation and it never derives missing values.
"""
from __future__ import annotations

import io
import json
import time
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.services.ocr_service import OCRResult

logger = get_logger(__name__)


def _page_images(raw: bytes, content_type: str) -> list[bytes]:
    """Return up to the already-validated document pages as PNG bytes."""
    from PIL import Image, ImageOps

    if content_type == "application/pdf":
        from pdf2image import convert_from_bytes

        images = convert_from_bytes(
            raw,
            dpi=220,
            poppler_path=(settings.POPPLER_PATH or None),
        )
    else:
        image = Image.open(io.BytesIO(raw))
        images = [ImageOps.exif_transpose(image).convert("RGB")]

    out: list[bytes] = []
    for image in images[: int(getattr(settings, "MAX_PAGE_COUNT", 3))]:
        if image.mode != "RGB":
            image = image.convert("RGB")
        buf = io.BytesIO()
        image.save(buf, format="PNG", optimize=True)
        out.append(buf.getvalue())
    return out


def _vision_prompt(document_type: str, ocr: OCRResult | None = None, audit_context: str | None = None) -> str:
    """Strict, layout-flexible, source-grounded prompt for multimodal extraction."""
    text_context = ocr.full_text if ocr is not None else "(No OCR text supplied. Read the original page image(s) directly.)"
    text_instruction = (
        "You are given the ORIGINAL PAGE IMAGE(S) and OCR/native text from the SAME document. "
        "Use BOTH together. The page image is authoritative for visual row/column association; "
        "the OCR text is supporting evidence when tiny characters are difficult to read."
        if ocr is not None
        else
        "You are given the ORIGINAL PAGE IMAGE(S). Read them directly. Preserve visual row/column "
        "association exactly and do not infer values that are not visibly readable."
    )

    type_rules = {
        "invoice": """
INVOICE / RECEIPT RULES — LAYOUT IS UNKNOWN
- Do NOT assume a fixed invoice template. The source may be a clean digital invoice, thermal receipt,
  GST/VAT receipt, or another layout.
- Discover the actual visible line-item headers first. Headers may use variants such as Description,
  Item, Product, Qty/Quantity, Rate, U.Price/Unit Price, Cost, Amount, Total, Tax, GST, VAT, HSN/SAC.
- A logical item may span multiple printed lines. Group lines only when the visual layout clearly shows
  they belong to the same item.
- Map semantic line_items as follows when visibly supported: description, quantity, unit_price, amount,
  tax_amount, discount. Preserve extra printed columns in tables so no visible data is lost.
- Never treat SKU/item number, row number, tax rate, GST-summary amount, or page number as quantity,
  unit_price, or line amount.
- If a row visibly prints quantity and unit price but the amount is unreadable, amount must be null;
  DO NOT calculate it. Same rule for all other missing cells.
- Extract summary/payment fields wherever they occur: invoice_number, invoice_date, vendor_name, customer_name,
  currency, subtotal, pre_tax_amount/net_amount/taxable_amount, tax_amount, discount, total_amount,
  cash_paid/tendered, change. Do a final visual scan of the payment/summary area before returning JSON;
  if CASH, CASH PAID, TENDERED, AMOUNT PAID or CHANGE is visibly printed, populate the matching top-level field.
- Copy printed decimal values exactly. Re-check quantity, unit-price and amount cells independently; never
  merge a nearby GST/tax rate or adjacent-column digits into a money value.
- IMPORTANT TAX RULE: distinguish pre-tax subtotal from an explicitly tax-inclusive total. For text like
  "Total (Excluding GST)" use that as subtotal/pre_tax_amount. For "Total Sales (Inclusive GST)" use it as
  total_amount, NOT subtotal. If a GST/tax summary separately prints "Net Amt", "Taxable Amount" or an
  equivalent pre-tax value, map it to net_amount/taxable_amount rather than silently relabelling it as a
  document subtotal. Preserve its original printed label in the tables/evidence. Never derive subtotal by total-tax.
- Capture GST/VAT/Tax Summary as a separate table with its actual headers/values.
""",
        "balance_sheet": """
BALANCE SHEET RULES — LAYOUT IS UNKNOWN
- Discover the exact visible comparative period headers BEFORE reading row values (e.g. 31-Mar-18,
  March 31, 2023, 2023). Preserve those exact labels and their left-to-right order.
- Discover sections such as Capital and Liabilities / Liabilities and Equity / Assets from the document;
  do not assume exact wording.
- For EVERY row, read numeric cells left-to-right and bind cell 1 to visible period header 1, cell 2 to
  visible period header 2, etc. Never shift a prior-period value into the current-period key.
- Re-check every printed Total row against the visible vertical column alignment before returning JSON.
  Do NOT use accounting equality to guess or repair a value.
- Capture every visible row in tables. Also expose canonical fields when explicitly printed, especially
  total_assets, total_capital_and_liabilities / total_liabilities_and_equity, total_liabilities,
  total_equity, currency, unit/scaling, company_name and reporting_period.
- Top-level comparative financial fields MUST be objects keyed by the same exact period labels used in
  the table, never a single scalar if multiple periods are shown.
""",
        "profit_and_loss": """
PROFIT & LOSS RULES — LAYOUT IS UNKNOWN
- Discover exact visible period headers first and preserve their left-to-right order.
- Capture ALL visible sections/rows (for example Income, Expenditure, Profit, Appropriations, EPS), but
  do not assume those exact headings must exist.
- For EVERY row, bind each numeric cell to the period header directly above the same column. Never swap
  current/prior-period values.
- Preserve parentheses as negative values only when visibly printed.
- Expose canonical fields only when printed: interest_earned, other_income, total_income,
  interest_expended, operating_expenses, provisions_and_contingencies, total_expenditure,
  profit_before_minority_interest, minority_interest, net_profit_attributable_to_group,
  brought_forward_profit, total_available_for_appropriation, revenue/cost_of_sales/gross_profit/tax/
  net_profit where those concepts actually occur.
- Do not compute any missing subtotal/profit.
""",
        "cash_flow_statement": """
CASH FLOW RULES — LAYOUT IS UNKNOWN AND MAY SPAN MULTIPLE PAGES
- Discover exact visible period headers first and preserve their left-to-right order on every page.
- Keep continuation pages under the SAME period mapping. Do not restart or swap period order on page 2.
- Capture every visible row under operating, investing, financing and reconciliation sections (or their
  actual visible equivalents).
- Bind every numeric cell to the period header directly above its column. Parenthesized values are
  negative only when visibly printed.
- Expose canonical printed rows where present: operating_cash_flow, investing_cash_flow,
  financing_cash_flow, fx_adjustment/translation_adjustment, net_change_in_cash,
  opening_cash, cash_acquired_on_amalgamation/other_adjustments, closing_cash.
- Do not calculate any missing cash-flow amount.
""",
    }[document_type]

    audit = ""
    if audit_context:
        audit = f"""
STRICT RE-READ / AUDIT MODE
A previous extraction produced a deterministic validation inconsistency:
{audit_context}
Re-read the ORIGINAL IMAGE(S) from scratch. Do NOT change numbers merely to make an equation pass.
Correct a field/period mapping only when the printed image visibly supports the correction. If a disputed
cell cannot be verified visually, return null for that cell rather than guessing.
"""

    return f"""You are a high-precision financial-document visual extraction engine.

DOCUMENT TYPE (supplied by the user; DO NOT classify): {document_type}

{text_instruction}
{type_rules}
{audit}
GLOBAL MANDATORY RULES
1. Extract only information visibly supported by the supplied document. NEVER calculate, infer, repair,
   or invent an extracted value.
2. The DOCUMENT TYPE is known, but the LAYOUT/TEMPLATE is NOT. Never assume fixed column positions,
   vendor-specific coordinates, or a single invoice/statement template.
3. Preserve ALL meaningful visible fields and ALL visible table/line-item rows.
4. For any table, discover the actual visible headers first; `columns` must contain those actual headers.
5. For comparative statements, preserve every period separately using the exact visible period labels.
   Row `values` must be keyed by those exact labels.
6. Keep each number attached to its exact printed row label and exact printed column. Never merge
   neighbouring rows, never move a value sideways, and never copy a value from another period.
7. If a value cannot be visually verified, return null. Do not use arithmetic or accounting identities
   to guess it.
8. Capture currency AND printed unit/scaling (e.g. RM/MYR, INR, USD, ₹ in crore, ₹ in '000) when visible.
9. Evidence source_text should be a short exact visible snippet. If exact text cannot be quoted confidently,
   source_text may be null; never fabricate evidence. Page number is required when identifiable.
10. Return no confidence scores, no PASS/FAIL decision, and no commentary outside JSON. Python performs
    all financial validation later.

RETURN EXACTLY THIS JSON SHAPE
{{
  "fields": [
    {{"name": "snake_case_name", "value": "string/number/object/null",
      "evidence": {{"source_text": "exact visible snippet or null", "page_number": 1}}}}
  ],
  "line_items": [
    {{"description": null, "quantity": null, "unit_price": null, "amount": null,
      "tax_amount": null, "discount": null,
      "evidence": {{"source_text": null, "page_number": null}}}}
  ],
  "tables": [
    {{"name": "table_name", "columns": ["actual visible column header"],
      "rows": [{{"label": null, "values": {{}},
        "evidence": {{"source_text": null, "page_number": null}}}}]}}
  ]
}}

OPTIONAL OCR/NATIVE TEXT CONTEXT
{text_context}
"""


def call_gemini_vision(
    raw: bytes,
    content_type: str,
    document_type: str,
    ocr: OCRResult | None = None,
    audit_context: str | None = None,
) -> tuple[str, str]:
    """Read original page image(s) with Gemini Vision.

    Errors are raised to the caller so the existing OCR-text fallback can run.
    """
    api_key = getattr(settings, "GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("Gemini API key is not configured")

    from google import genai
    from google.genai import types

    images = _page_images(raw, content_type)
    if not images:
        raise RuntimeError("No page images could be prepared for vision extraction")

    model = (
        getattr(settings, "GEMINI_VISION_MODEL", "")
        or getattr(settings, "GEMINI_MODEL", "")
        or "gemini-2.5-flash"
    )
    attempts = max(1, int(getattr(settings, "LLM_RETRY_ATTEMPTS", 3)))
    base_delay = max(0.0, float(getattr(settings, "LLM_RETRY_BASE_DELAY_SECONDS", 1.0)))
    client = genai.Client(api_key=api_key)

    contents: list[Any] = [types.Part.from_text(text=_vision_prompt(document_type, ocr, audit_context))]
    contents.extend(types.Part.from_bytes(data=img, mime_type="image/png") for img in images)

    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            logger.info("Gemini Vision request: model=%s pages=%d attempt=%d/%d", model, len(images), attempt, attempts)
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                ),
            )
            if not response.text:
                raise RuntimeError("Gemini Vision returned an empty completion")
            json.loads(response.text)
            return response.text, model
        except Exception as exc:
            last_exc = exc
            logger.warning("Gemini Vision attempt %d failed: %s", attempt, exc)
            if attempt < attempts:
                time.sleep(base_delay * (2 ** (attempt - 1)))

    assert last_exc is not None
    raise last_exc
