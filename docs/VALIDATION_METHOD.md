# Validation Method

The project uses three validation layers.

## Unit tests

Focused tests cover extraction rules that should not require live PDFs, including:

- Taxation row selection.
- ProfitBeforeTax row selection.
- Arithmetic sanity checks.
- Modular pipeline behavior on controlled statement text.

## Benchmark

The benchmark runs the full extraction pipeline against real PSX PDFs. This checks end-to-end behavior, report discovery, PDF download, extraction, evidence creation, and audit output.

The benchmark is not a substitute for unit tests. It tells us whether the full pipeline worked, but unit tests tell us exactly which rule failed.

## Manual validation

Manual validation compares extracted values against human-read expected values from the actual PDF. This is needed because a number appearing in a PDF is not always the correct number for the requested field and period.
