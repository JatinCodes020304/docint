"""
Document validation service — Section 4.1 of the spec.

This is deliberately the FIRST thing that runs on any upload, before we
spend any time/quota on OCR or an LLM call. Its only job is:
    "Is this a real, readable PDF/JPG/PNG with at most MAX_PAGE_COUNT pages?"
It does NOT try to figure out what kind of document it is (that's a
manual choice the user makes in the frontend, per the spec's "do NOT
build automatic document-type classification" rule).

Design note for the beginner: we don't trust the browser-supplied
Content-Type header or the filename extension alone, because a renamed
.exe could claim to be "application/pdf". Instead we peek at the file's
actual bytes:
  - PDF: files start with the 4 bytes b"%PDF"
  - PNG: files start with a fixed 8-byte magic number
  - JPEG: files start with 0xFFD8 and end with 0xFFD9
We then go one step further for PDFs and actually open them with pypdf
to (a) confirm they're not corrupted and (b) count pages.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

from app.core.config import settings
from app.core.logging import get_logger
from app.utils.errors import (
    UnsupportedFileTypeError,
    EmptyFileError,
    CorruptFileError,
    PageLimitExceededError,
)

logger = get_logger(__name__)

_PDF_MAGIC = b"%PDF"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8"


@dataclass
class FileValidationOutcome:
    file_type: str          # detected MIME type, e.g. "application/pdf"
    is_supported: bool
    is_readable: bool
    page_count: int
    status: str              # "PASS" (raises before this if it would be FAILED)


def _sniff_content_type(raw: bytes, filename: str) -> str:
    """Detect the real file type from magic bytes, ignoring the filename."""
    if raw.startswith(_PDF_MAGIC):
        return "application/pdf"
    if raw.startswith(_PNG_MAGIC):
        return "image/png"
    if raw.startswith(_JPEG_MAGIC):
        return "image/jpeg"

    # Fall back to extension only to produce a friendlier error message;
    # it is NOT trusted for acceptance.
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    return f"unknown/{ext or 'unrecognized'}"


def _count_pdf_pages(raw: bytes) -> int:
    """Open the PDF and count pages, raising CorruptFileError if unreadable."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency should always be installed
        raise RuntimeError("pypdf is required but not installed") from exc

    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            # Try an empty password (common for "view-only" exports);
            # if that fails, treat it as unreadable rather than guessing.
            try:
                reader.decrypt("")
            except Exception:
                raise CorruptFileError(
                    "This PDF is password-protected and could not be opened.",
                )
        page_count = len(reader.pages)
        if page_count == 0:
            raise CorruptFileError("This PDF has no readable pages.")
        return page_count
    except CorruptFileError:
        raise
    except Exception as exc:
        logger.exception("Failed to parse PDF while counting pages")
        raise CorruptFileError(
            "This PDF file appears to be corrupted or is not a valid PDF."
        ) from exc


def _validate_image(raw: bytes) -> None:
    """Confirm the image actually decodes; images always count as 1 page."""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(raw))
        img.verify()  # raises if the image data is broken
    except Exception as exc:
        logger.exception("Failed to decode image file")
        raise CorruptFileError(
            "This image file appears to be corrupted or is not a valid JPG/PNG."
        ) from exc


def validate_file(raw: bytes, filename: str) -> FileValidationOutcome:
    """
    Runs the full section-4.1 input-control check.

    Raises (all subclasses of ValidationError, caught by the API layer and
    turned into a clean 400 response — see utils/errors.py):
        EmptyFileError            - zero-byte upload
        UnsupportedFileTypeError  - not a PDF/JPG/PNG
        CorruptFileError          - looks like the right type but won't open
        PageLimitExceededError    - PDF has more than MAX_PAGE_COUNT pages

    Returns a FileValidationOutcome on success (status will be "PASS";
    a FAILED outcome is represented by one of the exceptions above instead,
    since the caller needs to stop processing either way).
    """
    logger.info("Starting file validation for '%s' (%d bytes)", filename, len(raw))

    if len(raw) == 0:
        raise EmptyFileError("The uploaded file is empty.")

    content_type = _sniff_content_type(raw, filename)

    if content_type not in settings.ALLOWED_CONTENT_TYPES:
        raise UnsupportedFileTypeError(
            f"Only PDF, JPG, and PNG files are supported. "
            f"This file was detected as '{content_type}'."
        )

    if content_type == "application/pdf":
        page_count = _count_pdf_pages(raw)
    else:
        _validate_image(raw)
        page_count = 1

    if page_count > settings.MAX_PAGE_COUNT:
        raise PageLimitExceededError(
            f"Document has {page_count} pages; the maximum allowed is "
            f"{settings.MAX_PAGE_COUNT}."
        )

    logger.info(
        "File validation PASSED for '%s': type=%s, pages=%d",
        filename, content_type, page_count,
    )

    return FileValidationOutcome(
        file_type=content_type,
        is_supported=True,
        is_readable=True,
        page_count=page_count,
        status="PASS",
    )
