# Evaluation Audit — Final Code Candidate

This audit maps the repository to the case-study scoring areas. Items that require an external account, live deployment, or public repository URL are deliberately marked as final manual submission steps rather than falsely claimed complete.

| Area | Weight | Current code status | Final manual check |
|---|---:|---|---|
| End-to-end working solution & deployment | 15% | READY | Deploy this exact build and verify the live upload/result flow. |
| Extraction completeness & accuracy | 25% | HARDENED | Vision-primary for scanned/image docs, native-text fast path, OCR fallback, all-fields/tables prompts, strict comparative-period binding, one guarded visual audit retry. Run the supplied representative samples once on the deployed build. |
| API implementation & quality | 15% | READY | Multipart POST, GET by name, GET list, health, Swagger, controlled input errors and persistence are implemented. |
| Frontend/dashboard/database | 10% | READY | Upload UI, database-backed dashboard, extracted fields/tables, validation display and raw JSON view are implemented. Verify production persistence after redeploy. |
| Financial validation correctness | 10% | HARDENED / TESTED | Deterministic Python checks; missing operands => NOT_APPLICABLE; GST-inclusive vs tax-exclusive invoice handling; strict line-item arithmetic; period-wise statement checks. |
| Code quality/logging/errors/security | 15% | READY | Modular services/repository/schema structure, logs, exception handlers, environment-only secrets, `.env` ignored. |
| Testing/docs/public GitHub | 5% | CODE/DOCS READY | **33 tests pass** locally; README, architecture, sample outputs and presentation are present. Push to a PUBLIC GitHub repo and fill its URL. |
| Candidate understanding | 5% | MANUAL | Be ready to explain routing, Vision vs OCR fallback, audit retry guard, validations, API/database and known limitations. |

## Known regressions explicitly covered

- GST-inclusive receipt: item total can reconcile to final inclusive total while `Net Amt + GST = Total` validates separately.
- Tax-exclusive invoice: item totals reconcile to explicit subtotal, then subtotal + tax reconciles to total.
- OCR decimal slip such as `29.06` vs printed/reported `29.00` no longer passes under the broader 0.5% statement tolerance.
- Audit retry cannot improve its score merely by nulling disputed fields and turning failed checks into `NOT_APPLICABLE`.
- Balance-sheet/P&L/cash-flow validations remain period-keyed; prompts require exact visible period/header binding and cash-flow continuation-page consistency.
- Financial validation FAIL is separate from pipeline processing status.

## Submission-only blockers

1. Deploy this exact final ZIP/build.
2. Run at least one representative Invoice, Balance Sheet, P&L and Cash Flow file on the live build and visually compare critical totals/periods.
3. Confirm `/api/v1/health`, `/docs`, `GET /api/v1/documents`, and GET-by-name live.
4. Confirm DB persistence survives refresh/redeploy according to the chosen production DB.
5. Push to a PUBLIC GitHub repository and replace the GitHub TODO in README/presentation.
6. Never commit `.env` or API keys.

## Final documentation refresh (2026-09-11)
- Public deployment references updated from Render to Railway.
- Public GitHub URL added.
- Local automated test status: 35 passed, 3 warnings.
- P&L table-to-validator fallback and cash-flow amalgamation adjustment are covered by regression tests.
- Sample P&L and cash-flow JSON fixtures refreshed with grounded comparative-period values.
- Architecture diagram refreshed to show Railway hosting and SQLite-vs-managed-Postgres persistence.
- README expanded with AI/tool declaration, known limitations, production improvements, and final live-verification checklist.
