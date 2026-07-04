# Changelog

## Engineering cleanup

- Added focused unit tests for taxation mapping and arithmetic sanity.
- Consolidated shared arithmetic sanity logic into the extraction package.
- Moved ProfitBeforeTax/Taxation row-selection rules into a dedicated module.
- Added release checklist and validation-method documentation.
- Cleaned client-facing documentation and removed internal patch-version language.
- Added scripts for setup, smoke testing and benchmark execution.

## Extraction improvements included

- Layout-aware extraction for multi-column reports.
- Arithmetic sanity checks for financial consistency.
- Manual validation workflow for stronger ground-truth checks.
- Taxation mapping rules for levy/minimum/final-tax statement presentation.
- SQL Server save path aligned with identity primary key and duplicate-report constraint.

## Review-readiness cleanup

- Renamed the two-number plain-text pipeline test so it clearly documents the first-column current-period assumption instead of implying the edge case is fully solved.
- Added engineering notes explaining that plain embedded-text extraction lacks coordinate awareness and that the layout fallback is the safer path for multi-column interim reports.
- Fixed package import consistency so the worker and tests both use the `workers.extraction` package path.
- Removed duplicate limitations documentation and stale self-review summary documentation.
- Retired old numbered batch runners except the smoke-test convenience wrapper; PowerShell scripts are now the main automation path.
- Added focused tests for sign handling, two-number plain-text rows, continuation pages, full schema mapping coverage, and layout target-year column selection.

## Security, CI, and layout modularization

- Moved coordinate-aware layout extraction from the worker into `workers/extraction/layout_engine.py`.
- Kept the worker responsible for command routing, PSX discovery, PDF download/cache, OCR routing, and database save orchestration.
- Replaced executable raw SQL review generation with a parameterized review template.
- Confirmed direct database writes use pyodbc parameter binding for INSERT and UPDATE paths.
- Added a SQL parameterization regression test.
- Added GitHub Actions CI to compile Python files and run unit tests.
- Cleaned copy-paste text in the manual validation test-case CSV.
