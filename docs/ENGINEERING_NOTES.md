# Engineering Notes

The extraction code is split into two levels.

## Primary embedded-text pipeline

The primary pipeline lives in `workers/extraction/`. It is modular and covered by focused unit tests.

## Layout fallback

The layout fallback remains in `workers/psx_worker.py`. This is the risky path for multi-column interim reports because it uses PDF word coordinates to select the target-year/current-period column.

A focused test now covers the core layout-column selection rule, but this area should be the next refactor target if the project continues:

```text
move layout_* functions into workers/extraction/layout_engine.py
add fixtures for real multi-column interim statement rows
test quarter/half-year/current-vs-comparative selection directly
```

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
