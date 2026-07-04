# Engineering Notes

The extraction code is split into two levels.

## Primary embedded-text pipeline

The primary pipeline lives in `workers/extraction/`. It is modular and covered by focused unit tests.

## Layout fallback

The coordinate-aware layout fallback lives in `workers/extraction/layout_engine.py`.

This path is important for multi-column interim reports because it uses PDF word
coordinates to select the value closest to the target-year/current-period header.
A focused unit test covers that core column-selection rule.

Remaining work for this area is to add more real-world layout fixtures for
quarter/half-year report variants.

## Database save safety

Direct database save uses pyodbc parameter binding in the INSERT and UPDATE
paths. The SQL file generated during save is now a review-only parameterized
template with parameter values shown separately, not an executable SQL script
with extracted values interpolated into the command string.

## CI

The repository includes a GitHub Actions workflow at
`.github/workflows/ci.yml` that compiles Python files and runs the unit tests on
push and pull request.

## Why both exist

PSX PDFs are inconsistent. Some are usable as plain embedded text; others need coordinate-aware extraction. The project therefore has both a text pipeline and a layout fallback.

The important rule is that both paths must be tested against the same field semantics and benchmarked against real PDFs.


## Plain-text two-column limitation

The embedded-text pipeline does not have access to PDF word coordinates. When a
line contains two amounts, it currently treats the first amount as the current
period. This is documented by a test, but it is an assumption, not a universal
guarantee across every PSX filing.

The coordinate-aware layout fallback is the safer path for multi-column interim
reports because it selects the value closest to the target-year/current-period
header.
