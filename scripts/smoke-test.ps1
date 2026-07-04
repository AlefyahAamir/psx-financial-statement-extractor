$ErrorActionPreference = "Stop"

python -m py_compile .\workers\psx_worker.py
python -m pytest

Write-Host "Smoke test completed."
