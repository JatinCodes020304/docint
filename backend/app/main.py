"""
FastAPI application entrypoint.

Run locally with:
    uvicorn app.main:app --reload --port 8000
(from inside the backend/ directory, with dependencies installed)

Then open http://localhost:8000/docs for the auto-generated Swagger UI.
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.database import init_db
from app.utils.errors import DocIntError

configure_logging()
logger = get_logger(__name__)

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Uploads an invoice or financial statement, validates it, extracts "
        "structured fields with OCR + an LLM, runs financial reconciliation "
        "checks, and persists the result."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOW_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    logger.info("Starting %s (%s)", settings.APP_NAME, settings.ENVIRONMENT)
    init_db()


@app.exception_handler(DocIntError)
async def docint_error_handler(request, exc: DocIntError):
    """
    Turns any expected, user-facing error into the spec's required shape:
        {"error": {"code": "...", "message": "..."}}
    Full details were already logged inside the service that raised it.
    """
    from fastapi.responses import JSONResponse

    logger.warning("Handled error: %s - %s", exc.code, exc.message)
    return JSONResponse(status_code=exc.http_status, content=exc.to_dict())


@app.exception_handler(Exception)
async def unhandled_error_handler(request, exc: Exception):
    """
    Safety net for genuine bugs (not DocIntError subclasses). We log the
    FULL exception server-side, but the end user only ever sees a generic
    message — never a stack trace or internal details.
    """
    from fastapi.responses import JSONResponse

    logger.exception("Unhandled exception while processing request")
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "Something went wrong while processing your request.",
            }
        },
    )


@app.get("/api/v1/health", tags=["health"])
def health_check():
    """Simple liveness check — used by the deployment platform and the frontend."""
    return {"status": "ok", "service": settings.APP_NAME, "version": settings.APP_VERSION}


# Mandatory document processing routes.
from app.api.routes import documents
app.include_router(documents.router, prefix="/api/v1/documents", tags=["documents"])


# ---------------------------------------------------------------------------
# Frontend: plain HTML/CSS/JS, served directly by this same FastAPI app so
# there's only one service to deploy. project-root/frontend/ sits next to
# backend/ (see repo layout in the README), so we go up two levels from
# this file (app/main.py -> app/ -> backend/) then over to ../frontend.
# ---------------------------------------------------------------------------
_FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
_TEMPLATES_DIR = _FRONTEND_DIR / "templates"
_STATIC_DIR = _FRONTEND_DIR / "static"

if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


def _serve_page(filename: str) -> FileResponse:
    return FileResponse(_TEMPLATES_DIR / filename)


@app.get("/", include_in_schema=False)
def root_page():
    return _serve_page("upload.html")


@app.get("/upload.html", include_in_schema=False)
def upload_page():
    return _serve_page("upload.html")


@app.get("/dashboard.html", include_in_schema=False)
def dashboard_page():
    return _serve_page("dashboard.html")


@app.get("/document_result.html", include_in_schema=False)
def document_result_page():
    return _serve_page("document_result.html")


@app.get("/favicon.svg", include_in_schema=False)
def favicon():
    return FileResponse(_STATIC_DIR / "favicon.svg")
