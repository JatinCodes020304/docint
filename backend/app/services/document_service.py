"""Orchestrates the end-to-end pipeline without mixing implementation details."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.repositories.document_repository import DocumentRepository
from app.services.document_validation_service import validate_file
from app.services.ocr_service import run_ocr
from app.services.extraction_service import extract_document
from app.services.financial_validation_service import validate_financials
from app.utils.errors import DocumentNotFoundError

logger = get_logger(__name__)


def _row_to_result(row) -> dict[str, Any]:
    return {
        "document_name": row.document_name,
        "document_type": row.document_type,
        "processing_status": row.processing_status,
        "overall_confidence": row.overall_confidence,
        "file_validation": row.file_validation,
        "extracted_data": row.extracted_data,
        "validation": row.validation,
        "processing_metadata": row.processing_metadata,
    }


def process_document(db: Session, raw: bytes, filename: str, document_type: str) -> dict[str, Any]:
    started = time.perf_counter()
    logger.info("Processing started: filename=%s document_type=%s", filename, document_type)

    fv = validate_file(raw=raw, filename=filename)
    ocr = run_ocr(raw=raw, content_type=fv.file_type)
    extracted = extract_document(ocr=ocr, document_type=document_type)
    grounding_warnings = extracted.pop("_grounding_warnings", [])
    validation = validate_financials(extracted=extracted, document_type=document_type)

    # A financial inconsistency is a failed validation, so surface FAILED at
    # document level too. NOT_APPLICABLE is not a processing failure.
    processing_status = "FAILED" if validation["overall_status"] == "FAIL" else "PASS"
    processed_at = datetime.now(timezone.utc)
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    metadata = {
        "ocr_used": ocr.ocr_used,
        "processed_at": processed_at.isoformat().replace("+00:00", "Z"),
        "processing_time_ms": elapsed_ms,
        # Field names whose value came back from the LLM without evidence text
        # that's actually findable in the OCR/native source -- e.g. a total the
        # model quietly summed from components instead of reading it. Worth a
        # manual look; the value itself is left in place since removing it could
        # just as easily delete a correct-but-loosely-quoted extraction.
        "grounding_warnings": grounding_warnings,
    }
    row = DocumentRepository.create(
        db,
        document_name=filename,
        document_type=document_type,
        processing_status=processing_status,
        overall_confidence=None,
        file_validation={"file_type": fv.file_type, "is_supported": fv.is_supported, "is_readable": fv.is_readable, "page_count": fv.page_count, "status": fv.status},
        extracted_data=extracted,
        validation=validation,
        processing_metadata=metadata,
    )
    logger.info("Processing complete: filename=%s status=%s duration_ms=%d", filename, processing_status, elapsed_ms)
    return _row_to_result(row)


def get_document(db: Session, document_name: str) -> dict[str, Any]:
    row = DocumentRepository.latest_by_name(db, document_name)
    if row is None:
        raise DocumentNotFoundError(f"No processed document named '{document_name}' was found.")
    return _row_to_result(row)


def list_documents(db: Session) -> list[dict[str, Any]]:
    rows = DocumentRepository.list_latest(db)
    return [
        {
            "document_name": row.document_name,
            "document_type": row.document_type,
            "processing_status": row.processing_status,
            "overall_confidence": row.overall_confidence,
            "processed_at": row.processing_metadata.get("processed_at") or row.created_at,
        }
        for row in rows
    ]
