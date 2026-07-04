from __future__ import annotations
import csv
from pathlib import Path

root = Path(__file__).resolve().parents[1]
bench = root / 'App_Data' / 'benchmark' / 'client_50'
out = bench / 'labels_client_50_combined.csv'
inputs = [bench / f'labels_client_50_batch{i}.csv' for i in range(1,6)]
missing = [str(p) for p in inputs if not p.exists()]
if missing:
    raise SystemExit('Missing label files:\n' + '\n'.join(missing) + '\nRun 06_Run_Client_50_Test.bat first.')

rows = []
fields = []
for batch_no, p in enumerate(inputs, start=1):
    with p.open('r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        for col in reader.fieldnames or []:
            if col not in fields:
                fields.append(col)
        for row in reader:
            row['Batch'] = str(batch_no)
            rows.append(row)

if 'Batch' not in fields:
    fields = ['Batch'] + fields

out.parent.mkdir(parents=True, exist_ok=True)
with out.open('w', encoding='utf-8-sig', newline='') as f:
    w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
    w.writeheader()
    w.writerows(rows)
print(f'Combined Client-50 label sheet created: {out}')
print(f'Rows: {len(rows)}')
