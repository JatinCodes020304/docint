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
- Database: SQLite locally; managed PostgreSQL on Railway through `DATABASE_URL`
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
DATABASE_URL=sqlite:///./docint.db  # local development
LLM_PROVIDER=groq
GROQ_API_KEY=your_real_groq_key_here
GROQ_MODEL=openai/gpt-oss-120b
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

Local development defaults to SQLite (`backend/docint.db`). The Railway deployment should use **managed PostgreSQL** so processed-document history survives container restarts/redeploys. The backend reads `DATABASE_URL`, supports both `postgresql://...` and legacy `postgres://...` URLs, and uses SQLAlchemy JSON columns for the variable document payloads.

### Railway PostgreSQL setup

1. In the Railway project, click **+ New -> Database -> PostgreSQL**.
2. Open the `docint` service -> **Variables**.
3. Add `DATABASE_URL` as a Railway reference to the database service, typically:

```text
${{Postgres.DATABASE_URL}}
```

If Railway named the database service something other than `Postgres`, choose its `DATABASE_URL` from **Add Reference** instead of typing the service name manually.
4. Deploy/redeploy the `docint` service. On startup, SQLAlchemy creates the `processed_documents` table automatically if it does not exist.
5. Process one document, redeploy/restart the app, then confirm the record is still present on the dashboard or `GET /api/v1/documents`. That is the persistence proof for the case study.

Do **not** put the public/external database URL, password, or any API key in GitHub or the README.

## Tests

```bash
cd backend
pytest -q
```

The suite covers file validation, OCR behavior, structured extraction schema, invoice tax-inclusive/tax-exclusive regressions, strict line-item arithmetic, comparative financial calculations, audit-retry safety, controlled invalid-upload responses, health, and a mocked end-to-end API flow without spending external LLM quota. Current local result: **35 passed, 3 warnings**.

## Deployment

The Dockerfile installs Tesseract and Poppler and serves both frontend and backend from one FastAPI deployment.

**Current public deployment:**

- Frontend URL: https://docint-production.up.railway.app/
- Backend API URL: https://docint-production.up.railway.app/api/v1
- Swagger URL: https://docint-production.up.railway.app/docs
- Health URL: https://docint-production.up.railway.app/api/v1/health
- Public GitHub repo: https://github.com/JatinCodes020304/docint

Railway is deployed from the public GitHub repository. The deployed service serves the frontend and FastAPI backend from the same container.

Deployment environment variables should include `GROQ_API_KEY`, `GROQ_MODEL`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_VISION_MODEL`, and Railway-managed `DATABASE_URL`. Keep every real secret only in Railway Variables / local `.env`; never commit real values.

## Included deliverables

- `docs/architecture.png`
- `docs/solution_presentation.pptx`
- `docs/solution_presentation.pdf`
- `sample_outputs/*.json`
- `.env.example`
- modular backend code and tests

## Known limitations / production improvements

### Known limitations

- Vision/OCR quality still depends on scan resolution, skew, compression, unusual fonts and dense table layouts.
- LLM extraction is non-deterministic, so deterministic Python validation and evidence fields are used as safeguards.
- Some receipt layouts can still omit a visible unit price even when the amount/quantity are extracted; uncertain values are intentionally returned as `null` rather than invented.
- The application accepts at most 3 pages per upload by case-study design.
- The synchronous request path can take tens of seconds for scanned multi-page statements because vision/OCR and LLM calls are external network operations.
- SQLite remains the zero-setup local fallback. The submitted Railway deployment should use managed PostgreSQL; a container-local SQLite file is not treated as durable persistence.
- Confidence scoring is intentionally conservative and may be `null` when no trustworthy calibrated score is available.

### Production improvements

- Alembic migrations, automated backups/restore testing, and production-grade connection-pool tuning for PostgreSQL.
- Object storage for original documents and rendered page images.
- Background job queue for long OCR/vision requests plus polling/webhook status.
- Authentication/authorization, per-user data isolation, stricter CORS and rate limiting.
- Structured observability: request IDs, metrics, tracing, provider latency/error dashboards and alerts.
- Provider routing/circuit breakers, caching and cost controls.
- Stronger table reconstruction and field-level evidence verification.
- Malware scanning, MIME sniffing and stricter upload security.
- Expanded regression corpus covering more layouts, currencies, tax styles and multi-page statements.

## AI/tool usage declaration

AI-assisted development tools were used during implementation and review. OpenAI ChatGPT was used for coding/review assistance; Gemini Vision is part of the runtime extraction pipeline for scanned/image documents; Groq-hosted text models are used for structured text extraction with provider fallback.

All financial PASS/FAIL decisions are made by deterministic Python code rather than by an LLM. The candidate can explain and modify the architecture, prompts, validation formulas, API flow, persistence layer and deployment setup.


## Stage 8 extraction hardening
- Receipt summary labels such as Total Inclusive GST, Cash/Tendered and Change are recovered conservatively from explicit OCR label/value pairs when the LLM misses them.
- GST/VAT summary rows are preserved as structured tables and can ground tax/total fields.
- Invoice prompt now requires exact header/row alignment and tells the model not to confuse SKU/row numbers/tax-summary values with quantity or price.
- Suspicious line-item arithmetic must be re-read against the printed row; uncertain mappings should be null rather than guessed.
- Groq primary + Gemini fallback remains enabled.

## Final verification checklist

Before submission, verify on the deployed Railway URL: `/docs`, `/api/v1/health`, POST upload for all four document types, `GET /api/v1/documents`, `GET /api/v1/documents/{document_name}`, unsupported file rejection, >3-page rejection, and durable database persistence after a redeploy/restart. Also confirm `.env` is not committed and no secrets appear in Git history.
