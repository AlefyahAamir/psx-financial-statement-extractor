# Architecture

The project has three main areas.

## Web application

The web application provides the user interface and calls the Python worker for report discovery, extraction, and database save operations.

## Python worker

`workers/psx_worker.py` is the command worker. It handles:

- PSX report discovery.
- PDF download and caching.
- Extraction orchestration.
- OCR fallback orchestration.
- Layout fallback orchestration through `workers/extraction/layout_engine.py`.
- Database save support using parameterized pyodbc execution.

It is not a pure orchestration file yet because it still contains report discovery, download/cache handling, save orchestration, and OCR routing. The primary statement row-matching pipeline and the coordinate-aware layout engine now live under `workers/extraction/`.

## Modular extraction package

`workers/extraction/` contains the primary embedded-text extraction pipeline:

```text
fields.py
text_utils.py
models.py
row_parser.py
field_matching.py
taxation.py
arithmetic_sanity.py
pipeline.py
layout_engine.py
```

This package handles:

- Text normalization.
- Row parsing.
- Field matching.
- ProfitBeforeTax and Taxation selection.
- Arithmetic sanity checks.
- Full primary schema mapping coverage.

## Validation

The repository includes focused tests for:

- Full primary schema mapping coverage.
- Extended balance-sheet field extraction.
- ProfitBeforeTax/Taxation selection.
- Current vs comparative two-number rows.
- Sign handling for loss rows.
- Continuation-page handling.
- Layout target-year column selection.
