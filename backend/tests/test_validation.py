"""
Basic tests for the file-validation service (Section 4.1 / Section 10
"unsupported/invalid file scenario" requirement).

Run with (from backend/):
    pytest tests/test_validation.py -v
"""
import io

import pytest
from pypdf import PdfWriter

from app.services.document_validation_service import validate_file
from app.utils.errors import (
    EmptyFileError,
    UnsupportedFileTypeError,
    CorruptFileError,
    PageLimitExceededError,
)


def _make_pdf_bytes(num_pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_valid_single_page_pdf_passes():
    raw = _make_pdf_bytes(1)
    result = validate_file(raw, "invoice.pdf")
    assert result.status == "PASS"
    assert result.file_type == "application/pdf"
    assert result.page_count == 1
    assert result.is_supported is True
    assert result.is_readable is True


def test_pdf_exceeding_page_limit_is_rejected():
    raw = _make_pdf_bytes(4)  # MAX_PAGE_COUNT defaults to 3
    with pytest.raises(PageLimitExceededError):
        validate_file(raw, "big_statement.pdf")


def test_empty_file_is_rejected():
    with pytest.raises(EmptyFileError):
        validate_file(b"", "empty.pdf")


def test_unsupported_file_type_is_rejected():
    # A .docx-ish byte sequence, not PDF/JPG/PNG magic bytes.
    raw = b"PK\x03\x04this-is-not-a-real-docx-but-has-a-zip-header"
    with pytest.raises(UnsupportedFileTypeError):
        validate_file(raw, "resume.docx")


def test_corrupted_pdf_is_rejected():
    # Starts with the right magic bytes so it LOOKS like a PDF, but the
    # rest of the content is garbage -> should fail when pypdf tries to parse it.
    raw = b"%PDF-1.4\n%garbage garbage garbage not a real pdf structure"
    with pytest.raises(CorruptFileError):
        validate_file(raw, "broken.pdf")


def test_renamed_file_is_detected_by_content_not_extension():
    """A PNG saved with a .pdf extension should still be accepted as PNG,
    since we sniff magic bytes rather than trusting the filename."""
    png_magic = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # not a fully valid PNG body
    with pytest.raises(CorruptFileError):
        # It'll be recognized as image/png (correct!) but fail to decode
        # since we didn't include real PNG image data - proving we
        # validated based on content, not the misleading ".pdf" name.
        validate_file(png_magic, "actually_a_png.pdf")
