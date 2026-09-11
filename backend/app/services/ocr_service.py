"""
OCR service — Section 4 "Text Extraction / OCR" stage of the pipeline.

Design goals:
1. Handle BOTH native PDFs (text is already embedded, no OCR needed) and
   scanned/image-based PDFs/JPGs/PNGs (need actual OCR).
2. Always return text broken down PER PAGE, because the extraction step
   needs to attach a page_number to each extracted value (Section 4.3
   "evidence/grounding").
3. Never silently swallow a failure — if Tesseract isn't installed, or a
   PDF page fails to rasterize, raise OCRError so the API layer returns a
   clean error instead of crashing with a raw traceback.
4. Provider-swappable: OCR_PROVIDER=tesseract (default, free, local, no
   API key) or OCR_PROVIDER=ocr_space (free-tier HTTP API, useful if a
   deployment target can't install system binaries — see note in
   config.py). Both return the exact same PageText shape so nothing else
   in the pipeline needs to know which one ran.

How native-PDF text extraction works here: we first try pypdf's built-in
text extraction (fast, exact, zero OCR errors) for each page. Only pages
where that comes back empty/near-empty (i.e. the page is actually a scanned
image with no embedded text layer) get rasterized and passed through
Tesseract. This mirrors the spec's "native or scanned" requirement without
wasting OCR time on documents that don't need it.
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass

from app.core.config import settings
from app.core.logging import get_logger
from app.utils.errors import OCRError

logger = get_logger(__name__)

# A page with fewer than this many extracted characters from pypdf is
# treated as "no real text layer" -> falls back to OCR for that page.
_MIN_NATIVE_TEXT_CHARS = 20


@dataclass
class PageText:
    page_number: int          # 1-indexed, matches "page_number" in the API response
    text: str
    source: str                # "native" (embedded PDF text) or "ocr" (Tesseract)


@dataclass
class OCRResult:
    pages: list[PageText]
    ocr_used: bool             # True if ANY page needed OCR (goes into processing_metadata)

    @property
    def full_text(self) -> str:
        """Convenience: all pages concatenated with clear page markers, handy
        for feeding straight into the LLM extraction prompt."""
        return "\n\n".join(f"--- PAGE {p.page_number} ---\n{p.text}" for p in self.pages)


def _extract_native_pdf_text(raw: bytes) -> list[str]:
    """Returns a list of strings, one per page, using pypdf's text layer.
    Empty string for a page means it likely has no embedded text (scanned)."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw))
    texts = []
    for page in reader.pages:
        try:
            texts.append((page.extract_text() or "").strip())
        except Exception:
            logger.warning("pypdf failed to extract text from a page; will fall back to OCR")
            texts.append("")
    return texts


def _rasterize_pdf(raw: bytes) -> list:
    """Converts each PDF page to a PIL Image for OCR. Requires poppler
    (pdftoppm) to be installed on the host - see docs/Dockerfile."""
    from pdf2image import convert_from_bytes

    try:
        poppler_path = settings.POPPLER_PATH or None
        return convert_from_bytes(raw, dpi=300, poppler_path=poppler_path)
    except Exception as exc:
        logger.exception("Failed to rasterize PDF for OCR")
        raise OCRError(
            "Could not convert the PDF into images for OCR processing."
        ) from exc


def _deskew(gray_arr):
    """Estimate the dominant text-line angle and rotate to correct it.
    A skewed scan (even 2-3 degrees) is one of the biggest causes of
    Tesseract silently dropping or garbling whole lines."""
    import cv2
    import numpy as np

    inverted = cv2.bitwise_not(gray_arr)
    thresh = cv2.threshold(inverted, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thresh > 0))
    if coords.shape[0] < 50:
        return gray_arr  # not enough foreground pixels to estimate an angle safely

    angle = cv2.minAreaRect(coords)[-1]
    angle = -(90 + angle) if angle < -45 else -angle
    if abs(angle) < 0.5:
        return gray_arr  # negligible skew — don't introduce interpolation artifacts

    h, w = gray_arr.shape
    matrix = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(
        gray_arr, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


def _preprocess_for_ocr(image):
    """
    Clean up a page image before handing it to Tesseract. Raw scans/photos
    fed straight into Tesseract are exactly why OCR output comes back
    inconsistent — wrong characters, missing lines, garbage on noisy scans.
    This step fixes the most common causes:
      1. EXIF rotation (phone photos of invoices)
      2. Low resolution (small invoice fonts need upscaling)
      3. Skew (crooked scans drop whole lines)
      4. Uneven lighting / scanner shadows (global threshold misreads these;
         adaptive threshold + denoising handles them properly)
    """
    import cv2
    import numpy as np
    from PIL import Image as PILImage
    from PIL import ImageOps

    image = ImageOps.exif_transpose(image)

    w, h = image.size
    if max(w, h) < 1800:
        scale = 1800 / max(w, h)
        image = image.resize((int(w * scale), int(h * scale)), PILImage.LANCZOS)

    arr = np.array(image.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    gray = _deskew(gray)

    denoised = cv2.fastNlMeansDenoising(gray, h=10)
    thresh = cv2.adaptiveThreshold(
        denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 31, 15,
    )
    return PILImage.fromarray(thresh)


def _ocr_quality_score(text: str) -> tuple[int, int, int]:
    """Score an OCR candidate for document/table usefulness.

    Financial documents often look "long enough" under one Tesseract page
    segmentation mode while silently losing an entire numeric column.  A
    simple character-count retry therefore is not sufficient.  Prefer the
    candidate that preserves more numeric/table-looking lines, then more
    numeric tokens, then more total text.
    """
    import re

    money_like = re.compile(r"\(?[-+]?\d[\d,]*(?:\.\d+)?\)?")
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    table_lines = sum(1 for line in lines if len(money_like.findall(line)) >= 2)
    numeric_tokens = sum(len(money_like.findall(line)) for line in lines)
    return table_lines, numeric_tokens, len(text or "")


def _table_supplement_from_tesseract(processed, primary_text: str) -> str:
    """Recover table headers/number columns that full-page OCR can miss.

    This is deliberately generic and source-grounded: no expected values are
    hardcoded.  When a balance-sheet-like page is detected, we locate the
    CAPITAL/LIABILITIES and ASSETS section headings using Tesseract bounding
    boxes, then OCR the right-hand numeric area separately.  Vertical rules
    and shaded/table borders are a common reason full-page Tesseract drops a
    column; cropping the numeric region makes those values readable again.

    The supplemental OCR is appended to the same page text so the LLM can use
    it as an alternate *view of the same source page*, not as invented data.
    """
    import re
    import pytesseract
    from pytesseract import Output

    lowered = (primary_text or "").lower()
    if not ("assets" in lowered and ("liabilities" in lowered or "capital" in lowered)):
        return ""

    try:
        data = pytesseract.image_to_data(
            processed,
            lang="eng",
            config="--oem 3 --psm 4",
            output_type=Output.DICT,
        )
    except Exception:
        logger.warning("Could not collect positional OCR for table supplement", exc_info=True)
        return ""

    # Build OCR lines with their bounding boxes.
    grouped: dict[tuple[int, int, int], list[int]] = {}
    for i, raw_text in enumerate(data.get("text", [])):
        token = (raw_text or "").strip()
        if not token:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        grouped.setdefault(key, []).append(i)

    line_boxes: list[tuple[str, int, int, int, int]] = []
    for idxs in grouped.values():
        idxs.sort(key=lambda i: data["left"][i])
        text = " ".join((data["text"][i] or "").strip() for i in idxs).strip()
        left = min(data["left"][i] for i in idxs)
        top = min(data["top"][i] for i in idxs)
        right = max(data["left"][i] + data["width"][i] for i in idxs)
        bottom = max(data["top"][i] + data["height"][i] for i in idxs)
        line_boxes.append((text, left, top, right, bottom))

    capital_box = next(
        (b for b in line_boxes if "capital" in b[0].lower() and "liabil" in b[0].lower()),
        None,
    )
    assets_box = next((b for b in line_boxes if re.fullmatch(r"\s*assets\s*", b[0], re.I)), None)
    if not capital_box or not assets_box or assets_box[2] <= capital_box[2]:
        return ""

    width, height = processed.size
    # Numeric columns are normally on the right half.  Start slightly before
    # the schedule/value columns and include both comparative-period columns.
    x0 = max(0, int(width * 0.625))
    x1 = min(width, int(width * 0.96))

    # Header region: captures exact period labels/currency-unit headings.
    header_y0 = max(0, int(capital_box[2] - height * 0.044))
    header_y1 = min(height, int(capital_box[4] + height * 0.016))

    # Capital/liability numeric region: cropped separately because vertical
    # table rules frequently hide the reported Total in full-page OCR.
    cap_y0 = max(0, capital_box[2])
    cap_y1 = min(height, assets_box[2])

    pieces: list[str] = []
    for label, box, psm in (
        ("TABLE HEADER", (x0, header_y0, x1, header_y1), 6),
        ("CAPITAL AND LIABILITIES NUMERIC COLUMNS", (x0, cap_y0, x1, cap_y1), 4),
    ):
        crop = processed.crop(box)
        text = pytesseract.image_to_string(
            crop, lang="eng", config=f"--oem 3 --psm {psm}"
        ).strip()
        if text and any(ch.isdigit() for ch in text):
            pieces.append(f"[{label} - ALTERNATE OCR VIEW]\n{text}")

    return "\n\n".join(pieces)



def _invoice_table_supplement_from_tesseract(processed, primary_text: str) -> str:
    """Recover a column-aligned view of a multi-column invoice/GST item table.

    Full-page OCR flattens a wide item table (e.g. Qty | Rate | Discount |
    Taxable Value | CGST% | CGST Amt | SGST% | SGST Amt | Amount) into plain
    text lines, so numbers can end up merged, dropped, or reordered relative
    to their real column -- and the LLM then has to guess which number is
    quantity vs. rate vs. a tax column. Linear text alone cannot fix that;
    the actual pixel position of each word is the only reliable signal for
    which column it belongs to.

    This locates the table's header row using Tesseract's per-word bounding
    boxes (only when the row contains several invoice-table-looking header
    words, so it never fires on unrelated pages), derives one x-pixel band
    per header column, then re-buckets every word on the rows beneath it
    into the nearest column band by horizontal position. The output is a
    strict pipe-delimited table appended as an alternate OCR view of the
    SAME page -- it never adds, removes, or corrects a printed value, only
    re-groups already-read words by position.
    """
    import pytesseract
    from pytesseract import Output

    header_keywords = (
        "qty", "quantity", "rate", "price", "amount", "gst", "cgst", "sgst",
        "igst", "tax", "discount", "taxable", "hsn", "sac", "particulars",
        "description", "item", "value",
    )

    try:
        data = pytesseract.image_to_data(
            processed, lang="eng", config="--oem 3 --psm 6", output_type=Output.DICT,
        )
    except Exception:
        logger.warning("Could not collect positional OCR for invoice table supplement", exc_info=True)
        return ""

    # Group words into their OCR lines, in top-to-bottom reading order.
    grouped: dict[tuple[int, int, int], list[int]] = {}
    for i, raw_text in enumerate(data.get("text", [])):
        if not (raw_text or "").strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        grouped.setdefault(key, []).append(i)

    if not grouped:
        return ""

    line_groups = list(grouped.values())
    line_tops = [min(data["top"][i] for i in idxs) for idxs in line_groups]
    order = sorted(range(len(line_groups)), key=lambda k: line_tops[k])

    # The header line is the first line containing several distinct
    # invoice-table header words -- a real item table, not a stray line
    # that happens to mention one of these words.
    header_line_idx = None
    for k in order:
        words = [(data["text"][i] or "").strip().lower() for i in line_groups[k]]
        distinct_hits = {kw for w in words for kw in header_keywords if kw in w}
        if len(distinct_hits) >= 3:
            header_line_idx = k
            break
    if header_line_idx is None:
        return ""

    header_idxs = sorted(line_groups[header_line_idx], key=lambda i: data["left"][i])
    header_words = [
        ((data["text"][i] or "").strip(), data["left"][i], data["left"][i] + data["width"][i])
        for i in header_idxs
    ]
    if len(header_words) < 3:
        return ""

    # One column band per header word, split at the midpoint between
    # neighbouring header-word centers so a value under any header word
    # (even a wide one) lands in the right band.
    centers = [(left + right) / 2 for _, left, right in header_words]
    bounds: list[tuple[str, float, float]] = []
    for idx, (text, _left, _right) in enumerate(header_words):
        lo = float("-inf") if idx == 0 else (centers[idx - 1] + centers[idx]) / 2
        hi = float("inf") if idx == len(header_words) - 1 else (centers[idx] + centers[idx + 1]) / 2
        bounds.append((text, lo, hi))

    header_top = min(data["top"][i] for i in header_idxs)
    body_line_indices = [k for k in order if line_tops[k] > header_top]
    if not body_line_indices:
        return ""

    def _bucket(center: float) -> int:
        for col, (_text, lo, hi) in enumerate(bounds):
            if lo <= center < hi:
                return col
        return len(bounds) - 1

    out_rows = [" | ".join(b[0] for b in bounds)]
    # A generous cap: real item tables rarely exceed this many rows, and it
    # keeps a false-positive header match from sweeping in unrelated
    # footer/signature text below the real table.
    for k in body_line_indices[:60]:
        idxs = line_groups[k]
        cells = ["" for _ in bounds]
        for i in idxs:
            token = (data["text"][i] or "").strip()
            if not token:
                continue
            center = data["left"][i] + data["width"][i] / 2
            col = _bucket(center)
            cells[col] = f"{cells[col]} {token}".strip()
        if any(cells):
            out_rows.append(" | ".join(cells))

    if len(out_rows) <= 1:
        return ""

    return (
        "[INVOICE LINE ITEMS TABLE - ALTERNATE OCR VIEW: columns are pipe-separated "
        "in the header's left-to-right order; every following row's cells are "
        "aligned under that SAME column order by their actual position on the page. "
        "Trust this column mapping over the plain OCR text above when they disagree.]\n"
        + "\n".join(out_rows)
    )


def _receipt_monetary_supplement_from_tesseract(processed, primary_text: str) -> str:
    """Re-read receipt summary lines at line level to preserve tiny punctuation.

    Thermal receipts often print decimal points as one or two tiny pixels. Full-page
    OCR may read ``.10`` as ``10`` even though the amount is visible. This pass is
    generic: it finds common monetary-summary labels in positional OCR, crops each
    matching source line, and re-runs single-line OCR. No merchant, amount, or expected
    answer is encoded here. The alternate view is appended as source-grounded OCR text.
    """
    import re
    import pytesseract
    from pytesseract import Output

    lowered = (primary_text or "").lower()
    if not any(word in lowered for word in ("invoice", "receipt", "cash", "change", "total")):
        return ""

    try:
        data = pytesseract.image_to_data(
            processed, lang="eng", config="--oem 3 --psm 6", output_type=Output.DICT
        )
    except Exception:
        logger.warning("Could not collect positional OCR for receipt supplement", exc_info=True)
        return ""

    grouped: dict[tuple[int, int, int], list[int]] = {}
    for i, raw_text in enumerate(data.get("text", [])):
        if not (raw_text or "").strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        grouped.setdefault(key, []).append(i)

    label_re = re.compile(
        r"\b(?:grand\s+total|net\s+total|total\s+sales|amount\s+due|subtotal|cash|tendered|change|tax|gst|vat)\b",
        re.I,
    )
    width, height = processed.size
    recovered: list[str] = []
    for idxs in grouped.values():
        idxs.sort(key=lambda i: data["left"][i])
        line = " ".join((data["text"][i] or "").strip() for i in idxs).strip()
        if not label_re.search(line):
            continue
        left = max(0, min(data["left"][i] for i in idxs) - 40)
        top = max(0, min(data["top"][i] for i in idxs) - 18)
        right = min(width, max(data["left"][i] + data["width"][i] for i in idxs) + 60)
        bottom = min(height, max(data["top"][i] + data["height"][i] for i in idxs) + 18)
        crop = processed.crop((left, top, right, bottom))
        alt = pytesseract.image_to_string(
            crop, lang="eng", config="--oem 3 --psm 7"
        ).strip()

        # If Tesseract grouped a tiny leading decimal point into a digits-only
        # token (classic thermal-receipt case: printed ".10" -> OCR "10"),
        # verify the punctuation directly from pixels inside that token box.
        # This is visual recovery, not arithmetic inference or value hardcoding.
        for i in idxs:
            token = (data["text"][i] or "").strip()
            if not re.fullmatch(r"\d{1,2}", token):
                continue
            tx, ty = data["left"][i], data["top"][i]
            tw, th = data["width"][i], data["height"][i]
            if tw <= 0 or th <= 0:
                continue
            try:
                import cv2
                import numpy as np
                token_img = np.array(processed.convert("L"))[
                    max(0, ty - 3): min(height, ty + th + 4),
                    max(0, tx - 3): min(width, tx + tw + 4),
                ]
                binary = (token_img < 128).astype("uint8") * 255
                count, _, stats, centers = cv2.connectedComponentsWithStats(binary, 8)
                dot_seen = False
                for component in range(1, count):
                    cx, cy, cw, ch, area = stats[component]
                    center_x, center_y = centers[component]
                    # A decimal point is a small isolated component in the lower
                    # left of the amount token, distinct from the tall digits.
                    if (
                        2 <= area <= max(120, int(th * th * 0.20))
                        and ch <= max(12, int(th * 0.45))
                        and cw <= max(12, int(th * 0.45))
                        and center_x <= tw * 0.35
                        and center_y >= th * 0.60
                    ):
                        dot_seen = True
                        break
                if dot_seen:
                    # Prefer the labelled line reconstructed from positional OCR
                    # because it preserves the exact source token order.
                    repaired_tokens = [(data["text"][j] or "").strip() for j in idxs]
                    repaired_tokens[idxs.index(i)] = "." + token
                    recovered.append(" ".join(repaired_tokens).strip())
            except Exception:
                logger.debug("Receipt decimal pixel check failed", exc_info=True)

        if alt and any(ch.isdigit() for ch in alt):
            recovered.append(alt)

    if not recovered:
        return ""
    # Keep order but remove duplicates.
    unique = list(dict.fromkeys(recovered))
    return "[RECEIPT MONETARY LINES - ALTERNATE OCR VIEW]\n" + "\n".join(unique)

def _ocr_image_tesseract(image) -> str:
    import pytesseract

    # A Windows-only path in a local .env must not break Linux deployment.
    # Use the configured binary only when it actually exists; otherwise let
    # pytesseract resolve `tesseract` from PATH.
    configured_cmd = (settings.TESSERACT_CMD or "").strip()
    if configured_cmd and os.path.exists(configured_cmd):
        pytesseract.pytesseract.tesseract_cmd = configured_cmd
    elif configured_cmd:
        logger.warning(
            "Configured TESSERACT_CMD does not exist on this host (%s); using PATH instead",
            configured_cmd,
        )
        pytesseract.pytesseract.tesseract_cmd = "tesseract"

    try:
        try:
            processed = _preprocess_for_ocr(image)
        except Exception:
            logger.warning(
                "OCR preprocessing (deskew/threshold) failed; "
                "falling back to the raw image for this page",
                exc_info=True,
            )
            processed = image

        # Always compare psm 4 and psm 6.  On financial statements psm 6 may
        # return plenty of text while still dropping one numeric column; psm 4
        # often preserves table rows much better.  Pick by table/numeric
        # coverage rather than by character count alone.
        candidates: list[str] = []
        for psm in (4, 6):
            candidate = pytesseract.image_to_string(
                processed, lang="eng", config=f"--oem 3 --psm {psm}"
            ).strip()
            if candidate:
                candidates.append(candidate)

        if not candidates:
            return ""

        text = max(candidates, key=_ocr_quality_score)

        # For balance-sheet-like pages, add a second OCR view of the right-hand
        # table area.  This recovers period headers and totals hidden by table
        # borders without hardcoding any company, date, row name or amount.
        supplement = _table_supplement_from_tesseract(processed, text)
        if supplement:
            text = f"{text}\n\n{supplement}"

        # For invoice/GST-style item tables, add a column-position-aligned
        # view so the LLM doesn't have to guess Qty/Rate/Amount/GST mapping
        # from flattened linear text (see function docstring for why).
        invoice_table_supplement = _invoice_table_supplement_from_tesseract(processed, text)
        if invoice_table_supplement:
            text = f"{text}\n\n{invoice_table_supplement}"

        receipt_supplement = _receipt_monetary_supplement_from_tesseract(processed, text)
        if receipt_supplement:
            text = f"{text}\n\n{receipt_supplement}"

        return text
    except Exception as exc:
        logger.exception("Tesseract OCR failed on an image")
        raise OCRError(
            "OCR failed while reading text from the document image. "
            "Make sure the Tesseract binary is installed on this host."
        ) from exc


def _ocr_image_ocr_space(image) -> str:
    """Fallback provider: OCR.Space free-tier HTTP API. Useful if the
    deployment host can't install the Tesseract system binary."""
    import httpx

    try:
        processed = _preprocess_for_ocr(image)
    except Exception:
        logger.warning("Preprocessing failed before OCR.Space call; using raw image")
        processed = image

    buf = io.BytesIO()
    processed.save(buf, format="PNG")
    buf.seek(0)

    try:
        response = httpx.post(
            "https://api.ocr.space/parse/image",
            files={"file": ("page.png", buf, "image/png")},
            data={"apikey": settings.OCR_SPACE_API_KEY, "OCREngine": 2},
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("IsErroredOnProcessing"):
            raise OCRError(f"OCR.Space error: {data.get('ErrorMessage')}")
        return "\n".join(
            r.get("ParsedText", "") for r in data.get("ParsedResults", [])
        ).strip()
    except OCRError:
        raise
    except Exception as exc:
        logger.exception("OCR.Space request failed")
        raise OCRError("OCR.Space request failed.") from exc


def _ocr_image(image) -> str:
    if settings.OCR_PROVIDER == "ocr_space":
        return _ocr_image_ocr_space(image)
    return _ocr_image_tesseract(image)



def try_native_pdf_text(raw: bytes) -> OCRResult | None:
    """Fast path for native PDFs.

    Returns an OCRResult only when every page has a usable embedded text layer.
    Returns None for scanned/mixed PDFs without invoking Tesseract. This lets the
    document pipeline try Vision first and keep Tesseract as a fallback.
    """
    try:
        native_texts = _extract_native_pdf_text(raw)
    except Exception:
        logger.warning("Native PDF text probe failed; treating document as vision/OCR candidate", exc_info=True)
        return None

    if not native_texts or any(len(t) < _MIN_NATIVE_TEXT_CHARS for t in native_texts):
        return None

    pages = [
        PageText(page_number=i, text=text, source="native")
        for i, text in enumerate(native_texts, start=1)
    ]
    return OCRResult(pages=pages, ocr_used=False)

def run_ocr(raw: bytes, content_type: str) -> OCRResult:
    """
    Entry point for the pipeline. `content_type` comes from the validation
    stage's sniffed MIME type ("application/pdf", "image/jpeg", "image/png").
    """
    logger.info("Starting OCR (provider=%s, content_type=%s)", settings.OCR_PROVIDER, content_type)

    if content_type == "application/pdf":
        native_texts = _extract_native_pdf_text(raw)
        needs_ocr = any(len(t) < _MIN_NATIVE_TEXT_CHARS for t in native_texts)

        pages: list[PageText] = []
        ocr_used = False

        if needs_ocr:
            images = _rasterize_pdf(raw)
            for i, (native_text, image) in enumerate(zip(native_texts, images), start=1):
                if len(native_text) >= _MIN_NATIVE_TEXT_CHARS:
                    pages.append(PageText(page_number=i, text=native_text, source="native"))
                else:
                    ocr_text = _ocr_image(image)
                    pages.append(PageText(page_number=i, text=ocr_text, source="ocr"))
                    ocr_used = True
        else:
            for i, text in enumerate(native_texts, start=1):
                pages.append(PageText(page_number=i, text=text, source="native"))

    else:
        # Single image upload (JPG/PNG) - always needs OCR, always page 1.
        from PIL import Image

        image = Image.open(io.BytesIO(raw))
        ocr_text = _ocr_image(image)
        pages = [PageText(page_number=1, text=ocr_text, source="ocr")]
        ocr_used = True

    total_chars = sum(len(p.text) for p in pages)
    if total_chars == 0:
        logger.warning("OCR/extraction produced no text at all for this document")

    logger.info(
        "OCR complete: %d page(s), ocr_used=%s, total_chars=%d",
        len(pages), ocr_used, total_chars,
    )

    return OCRResult(pages=pages, ocr_used=ocr_used)
