"""
Database engine/session setup using SQLAlchemy.

WHY SQLAlchemy: it works identically against SQLite (zero setup, one file,
free) and Postgres (what you'd use in a real deployment) just by changing
DATABASE_URL. That satisfies "SQLite or Postgres" without writing two
codepaths.

This module ONLY sets up the *connection machinery*. The actual table
definitions live in app/models/document.py. Reading/writing rows happens
in app/repositories/document_repository.py — routes and services never
touch SQLAlchemy sessions directly, which keeps DB concerns isolated.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.core.config import settings

# `check_same_thread` is only needed for SQLite (FastAPI can use multiple
# threads for one request); it's ignored for Postgres.
connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """
    FastAPI dependency. Yields a DB session and guarantees it's closed
    afterwards, even if the request raises an exception.

    Usage in a route:
        @router.get(...)
        def handler(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """
    Creates all tables that don't exist yet. Called once on app startup.
    (For a prototype this is fine; a production system would use Alembic
    migrations instead — noted in the README's "what I'd change" section.)
    """
    # Import models here so they're registered on Base.metadata before
    # create_all runs, without causing circular imports at module load time.
    from app.models import document  # noqa: F401

    Base.metadata.create_all(bind=engine)
