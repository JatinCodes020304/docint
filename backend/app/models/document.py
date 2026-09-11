"""
ORM model for a processed document.

Design choice: rather than a fully normalized relational schema (separate
tables for line_items, validation_checks, etc.), we store the
`extracted_data`, `validation`, `file_validation`, and `processing_metadata`
blocks as JSON columns. Why: the shape of "extracted fields" genuinely
varies per document type (an invoice has line_items, a balance sheet has
per-period totals) — modeling every possible field as a rigid SQL column
would fight the spec's "extract ALL meaningful fields" requirement. JSON
columns let us persist exactly the structured object we already validated
with Pydantic, and SQLite/Postgres both support querying into JSON if
needed later.

Every process call inserts a NEW row (even for a document name we've seen
before) so history isn't lost — the "GET latest by name" endpoint just
orders by created_at descending and takes the first match, per the spec's
"keeping older versions is optional [but allowed]" note.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Float, Integer, DateTime, JSON

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Document(Base):
    __tablename__ = "documents"

    id = Column(String, primary_key=True, default=_uuid)

    document_name = Column(String, nullable=False, index=True)
    document_type = Column(String, nullable=False)
    processing_status = Column(String, nullable=False)  # PASS | FAILED
    overall_confidence = Column(Float, nullable=True)

    file_validation = Column(JSON, nullable=False)
    extracted_data = Column(JSON, nullable=True)     # null if processing FAILED before extraction
    validation = Column(JSON, nullable=True)         # null if processing FAILED before validation
    processing_metadata = Column(JSON, nullable=False)

    error_code = Column(String, nullable=True)        # populated when processing_status == FAILED
    error_message = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow, index=True)
