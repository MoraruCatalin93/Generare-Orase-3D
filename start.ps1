# terragen-web — pornire pe Windows (PowerShell)
#   .\start.ps1            pornește aplicația pe http://127.0.0.1:8777
#   .\start.ps1 --port 8780
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) { Write-Host "Nu gasesc Python. Instaleaza Python 3.10+ de la python.org"; exit 1 }

if (-not (Test-Path ".venv")) {
  Write-Host "creez mediul virtual (.venv)..."
  & $py.Source -m venv .venv
}
& ".\.venv\Scripts\Activate.ps1"
if (-not (Test-Path ".venv\.deps-installed")) {
  Write-Host "instalez dependentele..."
  python -m pip install -q --upgrade pip
  python -m pip install -q -r requirements.txt
  New-Item -ItemType File ".venv\.deps-installed" | Out-Null
}
python -m terragen serve @args
