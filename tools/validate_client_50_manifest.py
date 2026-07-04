from __future__ import annotations
import csv, sys
from collections import Counter
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else 'test_cases/client_50_mixed.csv')
if not path.exists():
    raise SystemExit(f'Manifest not found: {path}')
with path.open('r', encoding='utf-8-sig', newline='') as f:
    rows = list(csv.DictReader(f))
if not rows:
    raise SystemExit('Manifest is empty')
required = ['Batch','CaseNo','Symbol','CompanyName','Year','RequestedReport']
missing = [c for c in required if c not in (rows[0].keys())]
if missing:
    raise SystemExit('Missing columns: ' + ', '.join(missing))

symbols = [r['Symbol'].strip().upper() for r in rows]
years = [str(r['Year']).strip() for r in rows]
reports = [str(r['RequestedReport']).strip() for r in rows]
batches = [str(r['Batch']).strip() for r in rows]
dup_symbols = [s for s,c in Counter(symbols).items() if c > 1]

print('Manifest:', path)
print('Total cases:', len(rows))
print('Unique companies:', len(set(symbols)))
print('Batches:', ', '.join(f'{k}={v}' for k,v in sorted(Counter(batches).items(), key=lambda kv: int(kv[0]))))
print('Years:', ', '.join(f'{k}={v}' for k,v in sorted(Counter(years).items())))
print('Reports:', ', '.join(f'{k}={v}' for k,v in sorted(Counter(reports).items())))
if len(rows) != 50:
    raise SystemExit('Expected exactly 50 cases')
if len(set(symbols)) != 50 or dup_symbols:
    raise SystemExit('Expected 50 different companies. Duplicate symbols found: ' + ', '.join(dup_symbols[:20]))
for y in ['2022','2023','2024','2025','2026']:
    if y not in years:
        raise SystemExit(f'Missing required year: {y}')
for rep in ['Annual','Q1','Half Year','Q3']:
    if rep not in reports:
        raise SystemExit(f'Missing required report type: {rep}')
if set(batches) != {'1','2','3','4','5'}:
    raise SystemExit('Expected batches 1,2,3,4,5')
print('Client-50 design: 50 different companies, mixed years 2022-2026, mixed report types Annual/Q1/Half Year/Q3.')
print('Manifest validation PASSED')
