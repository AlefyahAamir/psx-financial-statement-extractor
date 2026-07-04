# Limitations

This tool is designed to reduce manual extraction effort, but financial PDF extraction still requires validation.

## Automated PDF matching is not full ground truth

A value may appear in the PDF but still belong to the wrong field or wrong period column. This is especially true for interim reports with quarter, half-year and comparative columns.

## Manual validation is required for final accuracy claims

The automated benchmark provides a fast regression signal. Manual validation provides stronger evidence because a human checks the actual financial line item and reporting period.

## OCR fallback has limits

Scanned PDFs or PDFs with broken text layers may require OCR. OCR is slower and can misread numbers or labels.

## PSX availability affects coverage

If the PSX financials site times out or a report cannot be discovered, that is a coverage/report-discovery problem, not necessarily an extraction-value mismatch.

## Rough PBT formula is advisory

The rough ProfitBeforeTax walk-down is advisory only because companies may include legitimate extra P&L lines not represented in the simplified extraction table.
