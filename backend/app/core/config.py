"""
Central configuration for the app.

WHY THIS FILE EXISTS (for the beginner reading this):
Instead of scattering `os.environ["SOME_KEY"]` calls all over the codebase,
we read every environment variable ONCE here, in one place, with sane
defaults. Every other module imports `settings` from here instead of
touching `os.environ` directly. This makes it obvious what config the app
needs, and makes testing easier (you can monkeypatch `settings`).

Nothing here is a secret by itself — the actual secret VALUES live in a
`.env` file (which is git-ignored) or in your hosting provider's
"environment variables" dashboard. `.env.example` in the repo root shows
which variables exist, with placeholder values only.
"""
import os
from pathlib import Path
from functools import lru_cache

# Load a local .env file if python-dotenv is installed and a .env exists.
# This is only for local development convenience — in production
# (Render/Railway/Koyeb) you set real environment variables in their
# dashboard and this step is a no-op.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # backend/


class Settings:
    # --- General ---
    APP_NAME: str = "Document Intelligence API"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")

    # --- Database ---
    # Defaults to a local SQLite file so the project runs with zero setup.
    # For Postgres in production, set DATABASE_URL to something like:
    # postgresql://user:password@host:5432/dbname
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'docint.db'}"
    )

    # --- File upload limits ---
    MAX_UPLOAD_SIZE_MB: int = int(os.getenv("MAX_UPLOAD_SIZE_MB", "15"))
    MAX_PAGE_COUNT: int = int(os.getenv("MAX_PAGE_COUNT", "3"))
    ALLOWED_CONTENT_TYPES: tuple = (
        "application/pdf",
        "image/jpeg",
        "image/png",
    )
    ALLOWED_EXTENSIONS: tuple = (".pdf", ".jpg", ".jpeg", ".png")

    # --- OCR ---
    # "tesseract" = fully free, runs locally, no API key needed.
    # "ocr_space" = free tier, needs OCR_SPACE_API_KEY (also has a public
    #   demo key "helloworld" with tight rate limits — fine for a prototype).
    OCR_PROVIDER: str = os.getenv("OCR_PROVIDER", "tesseract")
    OCR_SPACE_API_KEY: str = os.getenv("OCR_SPACE_API_KEY", "helloworld")
    # Optional Windows/local overrides. Leave blank in Docker/Linux deployments.
    # Example POPPLER_PATH: C:\\Users\\...\\poppler-25.07.0\\Library\\bin
    # Example TESSERACT_CMD: C:\\Program Files\\Tesseract-OCR\\tesseract.exe
    POPPLER_PATH: str = os.getenv("POPPLER_PATH", "")
    TESSERACT_CMD: str = os.getenv("TESSERACT_CMD", "")

    # --- LLM extraction ---
    # Provider failover: the selected primary is tried first, then the other
    # configured provider automatically. Default is Groq -> Gemini.
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "groq")

    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY") or os.getenv("LLM_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL") or os.getenv("LLM_MODEL", "gemini-2.5-flash-lite")
    GEMINI_VISION_MODEL: str = os.getenv("GEMINI_VISION_MODEL", "gemini-2.5-flash")
    GEMINI_FALLBACK_MODEL: str = os.getenv("GEMINI_FALLBACK_MODEL", "")

    LLM_RETRY_ATTEMPTS: int = int(os.getenv("LLM_RETRY_ATTEMPTS", "3"))
    LLM_RETRY_BASE_DELAY_SECONDS: float = float(os.getenv("LLM_RETRY_BASE_DELAY_SECONDS", "1.0"))

    # --- Financial validation tolerance ---
    # Real-world documents round numbers, so exact equality is too strict.
    # We treat a check as PASS if |calculated - reported| <= tolerance,
    # where tolerance is the larger of a fixed absolute value and a
    # percentage of the reported value (handles both small and huge amounts).
    VALIDATION_ABS_TOLERANCE: float = float(os.getenv("VALIDATION_ABS_TOLERANCE", "0.01"))
    VALIDATION_PCT_TOLERANCE: float = float(os.getenv("VALIDATION_PCT_TOLERANCE", "0.005"))  # 0.5%

    # --- Logging ---
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # --- CORS (so the plain HTML/JS frontend can call this API from
    # a different origin, e.g. if frontend and backend are deployed
    # as separate services) ---
    CORS_ALLOW_ORIGINS: list = os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")


@lru_cache
def get_settings() -> Settings:
    """
    Cached so we don't re-read env vars on every request.
    Import this function (not a bare Settings() instance) wherever config
    is needed, e.g.:
        from app.core.config import get_settings
        settings = get_settings()
    """
    return Settings()


# Convenience singleton for modules that just want `from app.core.config import settings`
settings = get_settings()
