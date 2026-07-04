$ErrorActionPreference = "Stop"

python .\tools\run_manifest_cases.py --manifest .\test_cases\client_manual_10.csv --batch all --output-subfolder manual_10

Write-Host "Manual validation extraction completed. Fill the generated manual label CSV, then run the scoring tool if required."
