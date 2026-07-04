$ErrorActionPreference = "Stop"
python -m pip install --upgrade pip
python -m pip install -r workers/requirements.txt
if (Test-Path requirements-dev.txt) {
  python -m pip install -r requirements-dev.txt
}
Write-Host "Install complete."
