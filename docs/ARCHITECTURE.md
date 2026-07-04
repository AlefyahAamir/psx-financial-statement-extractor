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
- Layout fallback orchestration.
- Database save support.

It is not a pure orchestration file yet. Some layout and OCR fallback logic still lives in this worker because that code is tightly coupled to PDF objects and fallback routing. The financial statement row-matching pipeline has been separated into `workers/extraction/`, but the layout engine remains a clearly identified area for future extraction and deeper unit testing.

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
