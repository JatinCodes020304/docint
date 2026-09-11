"""Mandatory document REST endpoints from the case-study brief."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.schemas.document import DocumentType
from app.services.document_service import get_document, list_documents, process_document
from app.utils.errors import DocIntError

router = APIRouter()


@router.post("/process")
async def process_document_endpoint(
    file: UploadFile = File(...),
    document_type: DocumentType = Form(...),
    db: Session = Depends(get_db),
):
    raw = await file.read()
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(raw) > max_bytes:
        raise DocIntError(
            f"File exceeds the {settings.MAX_UPLOAD_SIZE_MB} MB upload limit.",
            code="FILE_TOO_LARGE", http_status=413,
        )
    return process_document(
        db=db,
        raw=raw,
        filename=file.filename or "uploaded_document",
        document_type=document_type.value,
    )


@router.get("")
def list_documents_endpoint(db: Session = Depends(get_db)):
    return list_documents(db)


@router.get("/{document_name}")
def get_document_endpoint(document_name: str, db: Session = Depends(get_db)):
    return get_document(db, document_name)
