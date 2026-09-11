"""Gemini Vision extraction helper.

This module is intentionally limited to *reading* the supplied document image(s).
It does not perform financial validation and it never derives missing values.
OCR text is sent alongside the page images so the model can reconcile noisy OCR
with the original visual layout while keeping row/column associations intact.
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


def _vision_prompt(document_type: str, ocr: OCRResult) -> str:
    """Strict, source-grounded prompt for multimodal extraction."""
    return f"""You are a financial-document visual extraction engine.

DOCUMENT TYPE (supplied by the user; DO NOT classify): {document_type}

You are given the ORIGINAL PAGE IMAGE(S) and OCR/native text from the SAME document.
Use BOTH together. The page image is authoritative for visual row/column association;
the OCR text is supporting evidence when tiny characters are difficult to read.

MANDATORY RULES
1. Extract only information visibly supported by the supplied document. NEVER calculate,
   infer, repair, or invent an extracted value.
2. Preserve ALL meaningful visible fields and ALL visible table/line-item rows.
3. Preserve every comparative period separately using the exact visible period labels.
4. Keep each number attached to its exact printed row label and period column. Never merge
   neighbouring rows or swap current/prior-period values.
5. Parenthesized values are negative only when parentheses are visibly printed.
6. If a value cannot be visually verified, return null. Do not use arithmetic to guess it.
7. Capture currency AND printed unit/scaling (for example INR and '000/crore) when visible.
8. Evidence source_text must be a short exact snippet from OCR/native text when available;
   page_number must correspond to the actual page. If OCR does not preserve the exact snippet,
   source_text may be null rather than fabricated.
9. For invoices/receipts, keep product rows in line_items and summary/tax tables in tables.
10. For financial statements, tables.columns must be actual visible period/value headers and
    row.values must be keyed by those exact headers. Also expose important printed totals/rows
    as top-level fields for deterministic Python validation.
11. Return no confidence scores and no commentary outside JSON.

RETURN EXACTLY THIS JSON SHAPE
{{
  "fields": [
    {{"name": "snake_case_name", "value": "string/number/object/null",
      "evidence": {{"source_text": "exact OCR snippet or null", "page_number": 1}}}}
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

OCR/NATIVE TEXT FROM THE SAME DOCUMENT
{ocr.full_text}
"""


def call_gemini_vision(raw: bytes, content_type: str, document_type: str, ocr: OCRResult) -> tuple[str, str]:
    """Read the original page image(s) + OCR text with Gemini Vision.

    Returns raw JSON text and the model name. Errors are raised to the caller so the
    existing OCR-text provider fallback can run cleanly.
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
        or "gemini-3.5-flash-lite"
    )
    attempts = max(1, int(getattr(settings, "LLM_RETRY_ATTEMPTS", 3)))
    base_delay = max(0.0, float(getattr(settings, "LLM_RETRY_BASE_DELAY_SECONDS", 1.0)))
    client = genai.Client(api_key=api_key)

    contents: list[Any] = [types.Part.from_text(text=_vision_prompt(document_type, ocr))]
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
            # Quick syntax check here; full Pydantic validation stays in extraction_service.
            json.loads(response.text)
            return response.text, model
        except Exception as exc:
            last_exc = exc
            logger.warning("Gemini Vision attempt %d failed: %s", attempt, exc)
            if attempt < attempts:
                time.sleep(base_delay * (2 ** (attempt - 1)))

    assert last_exc is not None
    raise last_exc
