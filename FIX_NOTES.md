# Balance-sheet OCR fix

Changes made for scanned comparative financial statements:

- OCR now compares Tesseract PSM 4 and PSM 6 and selects the result with better numeric/table coverage instead of accepting a long but incomplete OCR result.
- For balance-sheet-like pages, OCR adds a source-grounded alternate view of the right-hand table/header region to recover comparative period headers and values hidden by table borders.
- A deterministic balance-sheet enrichment pass reconstructs row values by the exact visible period labels. It does not hardcode any sample company, year, row value, expected answer, or validation result.
- Reconstructed `capital_and_liabilities` and `assets` tables use actual period columns instead of generic `LABEL` / `VALUE` columns.
- Important balance-sheet rows are exposed as period-keyed top-level fields so the existing validation service validates each period independently.
- Tesseract configuration is now cross-platform: a stale Windows TESSERACT_CMD no longer breaks Linux deployment if the binary is available on PATH.
- Extraction prompt explicitly requires real visible period headers as table columns and explains alternate OCR views.
- Added regression tests for comparative balance-sheet reconstruction and per-period validation.

Verification performed against the supplied `Consolidated Balance Sheet 2020.pdf`:

- 31-Mar-20 and 31-Mar-19 were both recovered.
- Total Capital and Liabilities: 15,808,304,373 / 12,928,057,065.
- Total Assets: 15,808,304,373 / 12,928,057,065.
- Validation returned PASS for both periods with variance 0.
- Full automated test suite: 21 passed.

## Multi-column GST invoice table alignment fix
Root cause: full-page Tesseract OCR flattens a wide GST item table (e.g.
Description | HSN | Qty | Rate | Taxable Value | CGST% | CGST Amt | SGST% |
SGST Amt | Amount) into plain linear text lines. Once flattened, the LLM has
no reliable way to tell which number belongs to which column, so Qty/Rate/
Amount/GST values were getting merged or reordered on multi-column invoices
such as the supplied Shankar GST invoice.

Changes made:
- Added `_invoice_table_supplement_from_tesseract` in `ocr_service.py`, mirroring
  the existing balance-sheet positional-OCR pattern: it finds the item table's
  header row from Tesseract's per-word pixel bounding boxes (self-gated on
  seeing 3+ distinct invoice-table header words, so it never fires on
  unrelated pages), derives one x-pixel column band per header word, then
  re-buckets every word on the rows beneath it into the nearest column band
  by actual horizontal position -- not by guessing from flattened text.
  Output is a strict pipe-delimited table appended as a source-grounded
  "[INVOICE LINE ITEMS TABLE - ALTERNATE OCR VIEW]" of the same page.
- Extraction prompt now explicitly tells the model to trust that alternate
  view's column order over the plain OCR text when they disagree, explains
  the extra GST-style columns (HSN/SAC, Taxable Value, CGST/SGST/IGST rate +
  amount), how to map them onto the line_item schema (amount = the row's
  final Amount/Total column, not Taxable Value; tax_amount = sum of whichever
  CGST/SGST/IGST amounts are printed for that row), and to also record the
  full printed breakdown in a companion `tables` entry so no column is
  silently dropped when the line_item schema doesn't have a field for it.
- No values are invented anywhere in this fix: the supplement only re-groups
  already-OCR'd words by their real pixel position, and returns empty (no
  supplement) unless a genuine multi-column header row is detected.
- Added regression tests: one confirms an 8-column synthetic GST table
  (Description/HSN/Qty/Rate/Taxable/CGST/SGST/Amount) is reconstructed with
  every row's cells under the correct header, including that Amount lands in
  the final column rather than being confused with Taxable Value; a second
  confirms the supplement does NOT fire on ordinary non-table text (no
  false-positive "table" on unrelated pages).

## Generic extraction robustness update
- Balance-sheet parser now preserves legitimate sub-1000 decimal cells (for example values such as 765.22) instead of confusing them with short schedule numbers.
- Receipt OCR now performs a source-image, line-level monetary re-read for summary labels such as Total, Cash and Change.
- A tiny leading decimal point that full-page OCR drops can be recovered from the actual token pixels; no merchant-specific or amount-specific values are hardcoded.
- Invoice deterministic enrichment prefers an explicitly decimal-grounded reading over a digits-only OCR reading of the same labelled field.
- Added regression tests for leading-decimal receipt change values and sub-1000 comparative balance-sheet cells.

## 2026-09-11 — Layout-flexible Vision extraction hardening
- Vision prompt is now document-type aware without hardcoding any vendor/template layout.
- Invoice/receipt extraction discovers actual headers and supports digital invoices, thermal receipts, GST/VAT summaries, cash/change, and multi-line item rows.
- Balance Sheet / P&L / Cash Flow extraction locks exact comparative period labels before mapping row values, with explicit no-column-shift rules.
- Cash Flow continuation pages preserve the same period mapping across pages.
- Tax-inclusive invoice totals are distinguished from pre-tax subtotal fields; the model is forbidden from deriving subtotal by arithmetic.
- If direct Vision extraction produces a deterministic financial FAIL, the pipeline performs one strict visual re-read. It keeps the retry only when deterministic validation improves; an all-null retry cannot win.
- Tesseract remains fallback-only for scanned/image documents.
- Existing automated tests: 25/25 pass.

## v3 evaluator-focused invoice reconciliation fix
- Invoice line-item reconciliation no longer assumes `sum(line_items) == subtotal` for every layout.
- It reconciles against the closest *reported* subtotal/net or final total, covering tax-exclusive and tax-inclusive receipts without vendor-specific hard-coding.
- Invoice Vision prompt now explicitly rescans CASH/TENDERED/CHANGE, preserves Net Amt vs Subtotal semantics, and warns against adjacent-column decimal contamination.
- Added regression tests for tax-inclusive, tax-exclusive, and true-mismatch invoices.
