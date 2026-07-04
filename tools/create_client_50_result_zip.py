from __future__ import annotations
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
bench = root / 'App_Data' / 'benchmark' / 'client_50'
out = bench / 'PSX_Client_50_Test_Results.zip'
files=[]

def add(p: Path):
    p = Path(p)
    if p.exists() and p.is_file() and p.resolve() != out.resolve() and p not in files:
        files.append(p)

def add_glob(pattern: str):
    for p in root.glob(pattern):
        if p.is_file():
            add(p)

def add_tree(path: Path):
    if path.exists():
        for p in path.rglob('*'):
            if p.is_file():
                add(p)

add(root / 'test_cases' / 'client_50_mixed.csv')
add_glob('App_Data/jobs/client_50_*')
add_tree(root / 'App_Data' / 'benchmark' / 'client_50')
add_tree(root / 'App_Data' / 'downloads')

if not files:
    raise SystemExit('No Client-50 result files found. Run 06_Run_Client_50_Test.bat first.')

pdf_count = sum(1 for p in files if p.suffix.lower() == '.pdf')
job_count = sum(1 for p in files if 'App_Data/jobs' in str(p).replace('\\','/'))
label_count = sum(1 for p in files if 'App_Data/benchmark/client_50' in str(p).replace('\\','/') and p.suffix.lower() == '.csv')

out.parent.mkdir(parents=True, exist_ok=True)
if out.exists():
    out.unlink()
with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for p in files:
        z.write(p, p.relative_to(root))

print(f'Created: {out}')
print(f'Total files included: {len(files)}')
print(f'PDF files included: {pdf_count}')
print(f'Job/result files included: {job_count}')
print(f'Client-50 benchmark/review CSV files included: {label_count}')
if pdf_count == 0:
    print('WARNING: No PDFs found in App_Data\\downloads. Run 06_Run_Client_50_Test.bat first, then create the result zip again.')
