# Document Intelligence Prototype

FastAPI + plain HTML/CSS/JS prototype for the AI Engineer Internship document-intelligence case study.

## Solution overview

The app accepts PDF/JPG/PNG financial documents, validates input constraints, extracts text using native PDF parsing or OCR, sends the page-marked text to Groq for structured extraction, with automatic Gemini fallback, performs deterministic financial validations in Python, stores the processed result, and displays it through a web dashboard and REST API.

**Pipeline:** Upload -> File validation -> Native PDF text / Poppler + Tesseract OCR -> Groq extraction -> Gemini fallback on provider failure -> Python financial validation -> SQLite/Postgres persistence -> dashboard + JSON API.

The user manually chooses one of: `invoice`, `balance_sheet`, `profit_and_loss`, `cash_flow_statement`. The application does **not** auto-classify document type.

## Architecture

See `docs/architecture.png`.

## Tech stack

- Backend: Python + FastAPI
- Frontend: plain HTML/CSS/JavaScript served by FastAPI
- Database: SQLite by default, Postgres-compatible through `DATABASE_URL`
- OCR: Tesseract OCR; Poppler is used for PDF rasterization when needed
- LLM extraction: Groq API (primary) + Gemini API (automatic fallback)
- API docs: FastAPI Swagger/OpenAPI at `/docs`
- Deployment: Dockerfile included for Render/Railway/Koyeb-style deployment

## Local setup

1. Copy `.env.example` to `.env`.
2. Put your Groq key and optional Gemini fallback key only in `.env`; never commit `.env`.
3. Install Tesseract + Poppler locally, or run the Dockerfile where they are installed automatically.
4. Python setup:

```bash
cd backend
python -m venv .venv
# Windows CMD:
# .venv\Scripts\activate.bat
# Windows PowerShell:
# .\.venv\Scripts\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

Open:

- Frontend: `http://localhost:8000/`
- Swagger: `http://localhost:8000/docs`
- Health: `http://localhost:8000/api/v1/health`

### Windows OCR path fallback

If CMD can run `pdftoppm -v` and `tesseract --version`, no extra config is required. If Windows PATH is unreliable, set these in `.env`:

```env
POPPLER_PATH=C:\path\to\poppler\Library\bin
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
```

## Environment variables

See `.env.example`. Required for real extraction:

```env
DATABASE_URL=sqlite:///./docint.db
LLM_PROVIDER=groq
GROQ_API_KEY=your_real_groq_key_here
GROQ_MODEL=llama-3.3-70b-versatile
GEMINI_API_KEY=your_optional_gemini_key_here
GEMINI_MODEL=gemini-2.5-flash-lite
```

## Mandatory API

- `POST /api/v1/documents/process` - multipart fields: `file`, `document_type`
- `GET /api/v1/documents/{document_name}` - latest stored result by name
- `GET /api/v1/documents` - list processed documents for dashboard
- `GET /api/v1/health` - health check

Example upload:

```bash
curl -X POST http://localhost:8000/api/v1/documents/process \
  -F "file=@sample_invoice.pdf" \
  -F "document_type=invoice"
```

## Extraction design

The primary provider is Groq. It receives OCR/native text with explicit page markers and returns JSON Object Mode output. The prompt asks for all meaningful visible fields and tables, `null` for missing/unreadable values, exact source values, comparative periods, and source-text/page evidence. Confidence is intentionally omitted instead of fabricating arbitrary LLM confidence numbers.

Provider failures are handled defensively: retryable 429/5xx/timeout responses are retried with exponential backoff. If Groq still fails (or returns invalid structured JSON), the application automatically tries Gemini when `GEMINI_API_KEY` is configured. Set `LLM_PROVIDER=gemini` to reverse the order.

**Multi-column GST/tax invoice tables:** plain OCR flattens a wide item table (Qty/Rate/Taxable Value/CGST/SGST/Amount) into linear text, which is not enough to reliably tell the LLM which number is which column. Before the LLM call, `ocr_service.py` detects the item table's header row from Tesseract's per-word pixel positions and re-groups every value beneath it into the correct column band by its actual position on the page — see `FIX_NOTES.md` for details. This alternate, column-aligned view is what the extraction prompt is told to trust over the flattened text when the two disagree.

## Financial validation

Financial checks run in Python, not in either LLM provider. Missing operands produce `NOT_APPLICABLE`. Tolerance uses the larger of `VALIDATION_ABS_TOLERANCE` and `VALIDATION_PCT_TOLERANCE * reported_value`.

Implemented checks cover invoice line math/totals/change, balance-sheet identity, P&L reconciliations, and cash-flow reconciliation per period where fields exist.

## Persistence

The default is SQLite (`backend/docint.db`). For durable production-style deployment, set `DATABASE_URL` to a managed Postgres URL. Results are stored as JSON blocks because fields vary by document type.

## Tests

```bash
cd backend
pytest -q
```

The suite covers file validation, OCR behavior, structured extraction schema, financial calculations, controlled invalid-upload responses, health, and a mocked end-to-end API flow without spending external LLM quota.

## Deployment

The Dockerfile installs Tesseract and Poppler and serves both frontend and backend from one FastAPI deployment.

**⚠️ Pending — fill in before final submission:**

- Frontend URL: _TODO — fill after deploying (Render/Railway/Koyeb)_
- Backend API URL: _TODO_
- Swagger URL: _TODO — usually `<backend URL>/docs`_
- Health URL: _TODO — usually `<backend URL>/api/v1/health`_
- Public GitHub repo: _TODO_

Deployment environment variables should include `GROQ_API_KEY`, `GROQ_MODEL`, optional `GEMINI_API_KEY`/`GEMINI_MODEL` for fallback, and `DATABASE_URL` if using Postgres. Do not commit real secrets.

## Included deliverables

- `docs/architecture.png`
- `docs/solution_presentation.pptx`
- `docs/solution_presentation.pdf`
- `sample_outputs/*.json`
- `.env.example`
- modular backend code and tests

## Known limitations / production improvements

This is a 3-day prototype. Production improvements would include durable object storage for originals, managed Postgres, Alembic migrations, request authentication, stricter rate limiting, async job processing, richer OCR/table extraction, observability, and a stronger evidence-verification/confidence method.

## AI/tool usage declaration

Generative AI coding assistance was used to help design, implement, review, and test the prototype. The candidate should be able to explain and modify the submitted architecture, extraction prompt, validation formulas, API flow, and deployment setup during the interview.


## Stage 8 extraction hardening
- Receipt summary labels such as Total Inclusive GST, Cash/Tendered and Change are recovered conservatively from explicit OCR label/value pairs when the LLM misses them.
- GST/VAT summary rows are preserved as structured tables and can ground tax/total fields.
- Invoice prompt now requires exact header/row alignment and tells the model not to confuse SKU/row numbers/tax-summary values with quantity or price.
- Suspicious line-item arithmetic must be re-read against the printed row; uncertain mappings should be null rather than guessed.
- Groq primary + Gemini fallback remains enabled.
