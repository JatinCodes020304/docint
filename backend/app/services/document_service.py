"""Orchestrates the end-to-end pipeline without mixing implementation details."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.repositories.document_repository import DocumentRepository
from app.services.document_validation_service import validate_file
from app.services.ocr_service import run_ocr, try_native_pdf_text
from app.services.extraction_service import extract_document, extract_document_vision
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



def _validation_counts(validation: dict[str, Any]) -> tuple[int, int, int]:
    """Return (passes, fails, applicable) for deterministic validation checks."""
    passes = fails = 0
    for check in validation.get("checks", []):
        status = check.get("status")
        if status == "PASS":
            passes += 1
        elif status == "FAIL":
            fails += 1
    return passes, fails, passes + fails


def _is_validation_improvement(original: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Accept an audit retry only when it improves validation without nulling evidence.

    A retry that simply turns disputed values into null/NOT_APPLICABLE must never
    beat the original extraction. The candidate therefore has to preserve at least
    as many applicable checks and either reduce FAILs or increase PASSes.
    """
    op, of, oa = _validation_counts(original)
    cp, cf, ca = _validation_counts(candidate)
    if ca < oa:
        return False
    return (cf < of) or (cf == of and cp > op)


def process_document(db: Session, raw: bytes, filename: str, document_type: str) -> dict[str, Any]:
    started = time.perf_counter()
    logger.info("Processing started: filename=%s document_type=%s", filename, document_type)

    fv = validate_file(raw=raw, filename=filename)

    # Fast routing:
    #   * Native PDF with a usable embedded text layer -> text LLM (no OCR).
    #   * Scanned PDF / JPG / PNG -> Gemini Vision directly (no Tesseract first).
    #   * If Vision fails/unavailable -> existing Tesseract + text-LLM fallback.
    ocr = None
    extraction_mode = "native_text"

    if fv.file_type == "application/pdf":
        ocr = try_native_pdf_text(raw)

    if ocr is not None:
        logger.info("Using native PDF text fast path; Tesseract skipped")
        extracted = extract_document(ocr=ocr, document_type=document_type)
    else:
        try:
            logger.info("Using Gemini Vision primary path; Tesseract skipped")
            extracted = extract_document_vision(
                raw=raw, content_type=fv.file_type, document_type=document_type
            )
            extraction_mode = "vision"
        except Exception as vision_exc:
            logger.warning(
                "Vision primary path failed; falling back to OCR + text extraction: %s",
                vision_exc,
            )
            ocr = run_ocr(raw=raw, content_type=fv.file_type)
            extracted = extract_document(ocr=ocr, document_type=document_type)
            extraction_mode = "ocr_fallback"

    grounding_warnings = extracted.pop("_grounding_warnings", [])
    validation = validate_financials(extracted=extracted, document_type=document_type)
    vision_audit_retry_used = False

    # If direct Vision produced a financial inconsistency, perform ONE strict visual
    # re-read. This is not an arithmetic repair: the audit prompt explicitly forbids
    # changing values just to make equations pass. We keep the retry only when the
    # deterministic validation score improves, so an all-null retry cannot "win".
    if extraction_mode == "vision" and validation.get("overall_status") == "FAIL":
        try:
            issues = validation.get("issues") or []
            audit_context = "\n".join(str(x) for x in issues[:8]) or "Financial validation failed."
            logger.info("Vision validation failed; running one strict visual audit retry")
            audited = extract_document_vision(
                raw=raw,
                content_type=fv.file_type,
                document_type=document_type,
                audit_context=audit_context,
            )
            audited_warnings = audited.pop("_grounding_warnings", [])
            audited_validation = validate_financials(extracted=audited, document_type=document_type)
            if _is_validation_improvement(validation, audited_validation):
                extracted = audited
                validation = audited_validation
                grounding_warnings = audited_warnings
                vision_audit_retry_used = True
                logger.info("Strict visual audit retry improved deterministic validation; using audited extraction")
            else:
                logger.info("Strict visual audit retry did not improve validation; keeping first extraction")
        except Exception as audit_exc:
            logger.warning("Strict visual audit retry failed; keeping first extraction: %s", audit_exc)

    # Processing status answers whether the pipeline successfully accepted, read,
    # extracted, validated and stored the document. Financial reconciliation has
    # its own independent PASS/FAIL/NOT_APPLICABLE status in `validation`. A valid
    # document with a genuine accounting inconsistency is still successfully
    # processed and must not be presented as a pipeline failure.
    processing_status = "PASS"
    processed_at = datetime.now(timezone.utc)
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    metadata = {
        "ocr_used": bool(ocr and ocr.ocr_used),
        "extraction_mode": extraction_mode,
        "vision_audit_retry_used": vision_audit_retry_used,
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
