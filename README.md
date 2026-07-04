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

Showing reports available for the search
<img width="956" height="502" alt="Screenshot 2026-07-03 001223" src="https://github.com/user-attachments/assets/6d1f7c6c-2d51-4943-a126-a21fc07e1d52" />

Extracted values table along with option to save to the database
<img width="959" height="500" alt="Screenshot 2026-07-03 001331" src="https://github.com/user-attachments/assets/def62b70-b083-4541-bb3f-929798be60e2" />

Result 
<img width="959" height="505" alt="Screenshot 2026-07-03 001351" src="https://github.com/user-attachments/assets/dfbc1ac5-5e19-40ec-a5c4-16c69ec7653d" />
<img width="959" height="505" alt="Screenshot 2026-07-03 001417" src="https://github.com/user-attachments/assets/e5b7ef42-499e-4711-8b36-fb21b87b7c03" />

Key showing what different symbols mean shown before extracted values are filled and below the extracted value table once values are filled 
<img width="959" height="502" alt="Screenshot 2026-07-03 001238" src="https://github.com/user-attachments/assets/ffa9629d-e858-453e-a14c-6bfbb6a80a02" />

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
