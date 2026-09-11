# Document Intelligence Prototype

FastAPI + plain HTML/CSS/JS prototype for the AI Engineer Internship document-intelligence case study.

## Solution overview

The app accepts PDF/JPG/PNG financial documents, validates input constraints, uses native PDF text when a reliable text layer exists, and uses Gemini Vision directly for scanned PDFs/JPG/PNG. Tesseract + text-LLM extraction is retained as a fallback. Financial validations run deterministically in Python, results are persisted, and the frontend exposes extracted fields, tables, validation checks and raw JSON.

**Pipeline:** Upload -> File validation -> native-text fast path OR Gemini Vision primary -> Tesseract + text-LLM fallback only when needed -> deterministic Python financial validation -> SQLite/Postgres persistence -> dashboard + JSON API.

The user manually chooses one of: `invoice`, `balance_sheet`, `profit_and_loss`, `cash_flow_statement`. The application does **not** auto-classify document type.

## Architecture

See `docs/architecture.png`.

## Tech stack

- Backend: Python + FastAPI
- Frontend: plain HTML/CSS/JavaScript served by FastAPI
- Database: SQLite by default, Postgres-compatible through `DATABASE_URL`
- Visual extraction: Gemini Vision (`gemini-2.5-flash`) for scanned PDFs/JPG/PNG
- Native/text extraction: native PDF text -> Groq text extraction; Tesseract + Groq/Gemini text extraction is the fallback path
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

Routing is intentionally document/layout agnostic. A PDF with a usable embedded text layer takes the fast native-text path. Scanned PDFs and image uploads go directly to Gemini Vision so table/column geometry is preserved instead of being flattened by OCR. If Vision is unavailable or fails, the existing Tesseract + text-LLM path remains as a fallback.

The extraction prompts require all meaningful visible fields and tables, exact comparative-period labels, `null` for missing/unreadable values, source evidence/page numbers where possible, and explicitly forbid calculating or repairing extracted values. Invoice prompts separately scan item tables, GST/VAT summaries, totals and cash/change areas. Statement prompts bind every row value to the period header directly above it and preserve the same mapping across continuation pages.

A single strict visual audit retry may run when deterministic financial validation finds an inconsistency. The retry is accepted only if it preserves at least as many applicable validation checks and genuinely improves PASS/FAIL results; it cannot win simply by turning disputed fields into `null` / `NOT_APPLICABLE`.

The Tesseract fallback still contains multi-column table alignment logic for difficult invoice OCR. Provider calls use retries/backoff, and no LLM is allowed to decide the final financial PASS/FAIL status.

## Financial validation

Financial checks run in Python, not in either LLM provider. Missing operands produce `NOT_APPLICABLE`. Tolerance uses the larger of `VALIDATION_ABS_TOLERANCE` and `VALIDATION_PCT_TOLERANCE * reported_value`.

Implemented checks cover invoice line math/totals/change, balance-sheet identity, P&L reconciliations, and cash-flow reconciliation per period where fields exist. Invoice quantity × unit-price uses a strict absolute money tolerance so OCR slips such as `29.06` versus printed `29.00` do not pass under the wider statement-level percentage tolerance. Tax-summary net/pre-tax values are kept separate from invoice line-total reconciliation, which prevents GST-inclusive receipts from being falsely failed.

`processing_status` represents whether the document pipeline completed successfully. Financial reconciliation is reported independently under `validation.overall_status`, so a successfully extracted document with a genuine accounting inconsistency is not mislabeled as a processing failure.

## Persistence

The default is SQLite (`backend/docint.db`). For durable production-style deployment, set `DATABASE_URL` to a managed Postgres URL. Results are stored as JSON blocks because fields vary by document type.

## Tests

```bash
cd backend
pytest -q
```

The suite covers file validation, OCR behavior, structured extraction schema, invoice tax-inclusive/tax-exclusive regressions, strict line-item arithmetic, comparative financial calculations, audit-retry safety, controlled invalid-upload responses, health, and a mocked end-to-end API flow without spending external LLM quota. Current local result: **33 passed**.

## Deployment

The Dockerfile installs Tesseract and Poppler and serves both frontend and backend from one FastAPI deployment.

**⚠️ Pending — fill in before final submission:**

- Frontend URL: https://docint.onrender.com/
- Backend API URL: https://docint.onrender.com/api/v1
- Swagger URL: https://docint.onrender.com/docs
- Health URL: https://docint.onrender.com/api/v1/health
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
