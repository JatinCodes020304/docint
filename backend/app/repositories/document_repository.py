"""Database access for processed documents. No API/business logic lives here."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.document import Document


class DocumentRepository:
    @staticmethod
    def create(db: Session, **values) -> Document:
        row = Document(**values)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    @staticmethod
    def latest_by_name(db: Session, document_name: str) -> Document | None:
        return (
            db.query(Document)
            .filter(Document.document_name == document_name)
            .order_by(Document.created_at.desc())
            .first()
        )

    @staticmethod
    def list_latest(db: Session) -> list[Document]:
        # SQLite-friendly prototype: fetch newest first then keep one row/name.
        rows = db.query(Document).order_by(Document.created_at.desc()).all()
        seen: set[str] = set()
        latest: list[Document] = []
        for row in rows:
            if row.document_name not in seen:
                latest.append(row)
                seen.add(row.document_name)
        return latest
