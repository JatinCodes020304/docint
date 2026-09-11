"""
Shared error types.

WHY: The spec requires the API to always return a consistent error shape:
    {"error": {"code": "...", "message": "..."}}
and to NEVER leak stack traces or secrets.

The pattern we use:
- Services raise a `DocIntError` (or a subclass) with a machine-readable
  `code` and a human-readable `message` when something goes wrong in a
  way the caller should be told about (bad file, OCR failure, etc).
- Anything that is NOT a `DocIntError` (a genuine bug: KeyError, None
  attribute access, etc.) is treated as an unexpected 500 by the API
  layer's exception handler, logged in full server-side, but shown to
  the user only as a generic "internal error" message — never the raw
  Python traceback.
"""


class DocIntError(Exception):
    """Base class for all expected, user-facing errors in this app."""

    code: str = "INTERNAL_ERROR"
    http_status: int = 500

    def __init__(self, message: str, code: str | None = None, http_status: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if http_status:
            self.http_status = http_status

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}


class ValidationError(DocIntError):
    """File failed input validation (wrong type, too many pages, corrupt, etc)."""

    code = "VALIDATION_ERROR"
    http_status = 400


class UnsupportedFileTypeError(ValidationError):
    code = "UNSUPPORTED_FILE_TYPE"


class EmptyFileError(ValidationError):
    code = "EMPTY_FILE"


class CorruptFileError(ValidationError):
    code = "CORRUPT_FILE"


class PageLimitExceededError(ValidationError):
    code = "PAGE_LIMIT_EXCEEDED"


class OCRError(DocIntError):
    code = "OCR_FAILED"
    http_status = 502


class ExtractionError(DocIntError):
    code = "EXTRACTION_FAILED"
    http_status = 502


class DocumentNotFoundError(DocIntError):
    code = "DOCUMENT_NOT_FOUND"
    http_status = 404
