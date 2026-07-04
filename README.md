[![CI](https://github.com/AlefyahAamir/psx-financial-statement-extractor/actions/workflows/ci.yml/badge.svg)](https://github.com/AlefyahAamir/psx-financial-statement-extractor/actions/workflows/ci.yml)

# PSX Financial Extractor

This project extracts selected financial statement values from official Pakistan Stock Exchange financial report PDFs and stores the results in SQL Server.

## What it does

- Searches PSX-listed companies.
- Finds official financial report PDFs.
- Extracts key financial statement fields.
- Shows the values with evidence and warnings for review.
- Supports database save into SQL Server.
- Provides benchmark and manual validation workflows.

  Company Search Section
  <img width="959" height="508" alt="Screenshot 2026-07-03 001208" src="https://github.com/user-attachments/assets/6ed6884b-b66d-45aa-9b10-0f50829e2b4c" />


## Main components

```text
workers/psx_worker.py              Python command worker
workers/extraction/                Modular extraction logic
tools/                             Benchmark and audit tools
data/                              SQL setup scripts and company list
tests/                             Focused unit tests
docs/                              Architecture, validation, and release notes
```

## Setup

Open a terminal in the project folder.

```powershell
.\scripts\install.ps1
```

Set the SQL Server connection string:

```powershell
$env:PSX_SQL_CONNECTION_STRING="Server=localhost;Database=PSXFinancials;Trusted_Connection=True;TrustServerCertificate=True;"
```

Run the web app:

```powershell
.\scripts\run-app.ps1
```

Run smoke tests:

```powershell
.\scripts\smoke-test.ps1
```

Run unit tests:

```powershell
python -m pytest
```

Run the benchmark:

```powershell
.\scripts\run-client50.ps1
```

## Validation

The project separates three validation layers:

1. Unit tests for focused extraction rules.
2. Benchmark tests against real PDFs.
3. Manual validation against human-read expected values.

See:

```text
docs/VALIDATION_METHOD.md
docs/CASE_STUDY_TAXATION_MAPPING.md
docs/RELEASE_CHECKLIST.md
```

## Notes

Automated PDF matching is not the same as ground truth. A number can appear in a report but belong to the wrong row or reporting period. Manual validation remains the strongest accuracy check.
