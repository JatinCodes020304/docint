"""
Pydantic schemas for the parts of the API response that are NOT the
extracted financial fields (those live in schemas/extraction.py, built
in the next step). Keeping request/response shapes as Pydantic models
(instead of raw dicts) gives us free request validation, automatic
Swagger/OpenAPI docs, and typos get caught immediately instead of at
3am in production.
"""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    invoice = "invoice"
    balance_sheet = "balance_sheet"
    profit_and_loss = "profit_and_loss"
    cash_flow_statement = "cash_flow_statement"


class ProcessingStatus(str, Enum):
    PASS = "PASS"
    FAILED = "FAILED"


class FileValidationResult(BaseModel):
    """Result of the pre-processing input-control check (section 4.1)."""

    file_type: str = Field(..., description="Detected MIME type of the upload")
    is_supported: bool
    is_readable: bool
    page_count: Optional[int] = None
    status: ProcessingStatus
    reason: Optional[str] = Field(
        None, description="Populated when status is FAILED, explains why in plain language"
    )


class ProcessingMetadata(BaseModel):
    ocr_used: bool
    processed_at: datetime
    processing_time_ms: int


class DocumentListItem(BaseModel):
    """One row in the GET /api/v1/documents listing."""

    document_name: str
    document_type: DocumentType
    processing_status: ProcessingStatus
    overall_confidence: Optional[float] = None
    processed_at: datetime

    class Config:
        from_attributes = True  # lets us build this straight from an ORM row
