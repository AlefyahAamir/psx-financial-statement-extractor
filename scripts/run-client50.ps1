$ErrorActionPreference = "Stop"

python .\tools\run_manifest_cases.py --manifest .\test_cases\client_50_mixed.csv --batch all

python .\tools\pdf_audit_client_50.py

if (Test-Path ".\tools\create_client_50_result_zip.py") {
  python .\tools\create_client_50_result_zip.py
}

Write-Host "Client benchmark completed."
