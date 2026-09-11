"""
Tests for the OCR service.

These exercise the REAL Tesseract binary (not a mock), which is exactly
what the spec's "demonstrate processing... including at least one
scanned/image-based file" requirement is checking for. If Tesseract or
poppler isn't installed on the machine running these tests, they'll fail
with a clear OCRError rather than a confusing crash - see the Dockerfile
for how this is guaranteed to be available in the deployed environment.

Run with (from backend/):
    pytest tests/test_ocr.py -v
"""
import io

from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas

from app.services.ocr_service import run_ocr, _invoice_table_supplement_from_tesseract


def _make_text_image_bytes(lines: list[str]) -> bytes:
    img = Image.new("RGB", (500, 40 * (len(lines) + 1)), color="white")
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((10, 10 + i * 40), line, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_native_pdf_bytes(lines: list[str]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(400, 300))
    y = 260
    for line in lines:
        c.drawString(20, y, line)
        y -= 20
    c.save()
    return buf.getvalue()


def test_ocr_on_scanned_image_extracts_text():
    raw = _make_text_image_bytes(["INVOICE NO: INV-1001", "Total: USD 500.00"])
    result = run_ocr(raw, "image/png")

    assert result.ocr_used is True
    assert len(result.pages) == 1
    assert result.pages[0].page_number == 1
    assert result.pages[0].source == "ocr"
    # OCR of rendered text isn't pixel-perfect, but key tokens should survive.
    assert "INVOICE" in result.pages[0].text.upper()


def test_native_pdf_skips_ocr():
    raw = _make_native_pdf_bytes(["INVOICE NO: INV-2002", "Vendor: Test Co"])
    result = run_ocr(raw, "application/pdf")

    assert result.ocr_used is False
    assert result.pages[0].source == "native"
    assert "INV-2002" in result.pages[0].text


def test_full_text_includes_page_markers():
    raw = _make_native_pdf_bytes(["Line one"])
    result = run_ocr(raw, "application/pdf")
    assert "PAGE 1" in result.full_text


def _make_gst_table_image(font_size: int = 22) -> Image.Image:
    """Synthetic multi-column GST invoice item table with REAL column
    positions, so the test exercises actual pixel-position bucketing rather
    than any hand-written/fabricated OCR text."""
    from PIL import ImageFont

    img = Image.new("L", (1400, 500), color=255)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", font_size
        )
    except Exception:
        font = ImageFont.load_default()

    col_x = [40, 300, 470, 620, 790, 950, 1110, 1260]
    header = ["Description", "HSN", "Qty", "Rate", "Taxable", "CGST", "SGST", "Amount"]
    rows = [
        ["Widget A", "8471", "2", "500.00", "1000.00", "90.00", "90.00", "1180.00"],
        ["Widget B", "8473", "5", "200.00", "1000.00", "90.00", "90.00", "1180.00"],
        ["Gadget C", "8517", "1", "1500.00", "1500.00", "135.00", "135.00", "1770.00"],
    ]

    y = 40
    for x, text in zip(col_x, header):
        draw.text((x, y), text, fill=0, font=font)
    y += 60
    for row in rows:
        for x, text in zip(col_x, row):
            draw.text((x, y), text, fill=0, font=font)
        y += 60
    return img


def test_invoice_table_supplement_reconstructs_gst_columns():
    """Root-cause regression test: full-page OCR flattens a wide GST item
    table into text lines where Qty/Rate/Taxable/CGST/SGST/Amount can get
    merged or reordered. This checks the column-position-aware supplement
    recovers the exact header order and keeps every row's values under the
    correct column, regardless of what the flattened text looked like."""
    img = _make_gst_table_image()
    supplement = _invoice_table_supplement_from_tesseract(img, primary_text="")

    assert supplement, "Expected the GST table header to be detected"
    assert "ALTERNATE OCR VIEW" in supplement
    lines = supplement.splitlines()
    header_line = next(l for l in lines if l.startswith("Description"))
    assert [c.strip() for c in header_line.split("|")] == [
        "Description", "HSN", "Qty", "Rate", "Taxable", "CGST", "SGST", "Amount",
    ]
    # Every data row must have 8 pipe-separated cells matching the header,
    # and the Amount (last) column must be the row's final printed total,
    # not the Taxable Value or a tax column.
    data_rows = [l for l in lines if "Widget" in l or "Gadget" in l]
    assert len(data_rows) == 3
    for row in data_rows:
        cells = [c.strip() for c in row.split("|")]
        assert len(cells) == 8
    widget_a = next(c for c in data_rows if "Widget A" in c)
    cells = [c.strip() for c in widget_a.split("|")]
    assert cells[2] == "2"        # Qty
    assert cells[3] == "500.00"   # Rate
    assert cells[4] == "1000.00"  # Taxable
    assert cells[5] == "90.00"    # CGST
    assert cells[6] == "90.00"    # SGST
    assert cells[7] == "1180.00"  # Amount (final total, not taxable value)


def test_invoice_table_supplement_does_not_fire_on_unrelated_text():
    """Guards against false positives: a normal paragraph (no table headers)
    must not produce a fabricated 'table'."""
    img = Image.new("L", (1000, 200), color=255)
    draw = ImageDraw.Draw(img)
    draw.text((40, 40), "Thank you for shopping with us today", fill=0)
    draw.text((40, 80), "Please visit again soon", fill=0)

    supplement = _invoice_table_supplement_from_tesseract(img, primary_text="")
    assert supplement == ""
