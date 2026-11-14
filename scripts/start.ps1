# Li-OCR R1.0 local start (Windows)
# Usage: powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Port 8080
param(
  [int]$Port = 8080,
  [string]$Python = ""
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if ($Python -eq "") {
  if ($env:LIOCR_PYTHON) { $Python = $env:LIOCR_PYTHON }
  elseif (Test-Path (Join-Path $root ".venv\Scripts\python.exe")) {
    $Python = Join-Path $root ".venv\Scripts\python.exe"
  } elseif (Get-Command python -ErrorAction SilentlyContinue) { $Python = "python" }
  else { throw "python not found; pass -Python <path>" }
}

if (-not $env:LIOCR_DEVICE) { $env:LIOCR_DEVICE = "gpu:0" }
if (-not $env:LIOCR_DATA_DIR) { $env:LIOCR_DATA_DIR = Join-Path $root "data" }
if (-not $env:LIOCR_HOST) { $env:LIOCR_HOST = "127.0.0.1" }

Write-Host "python : $Python"
Write-Host "device : $env:LIOCR_DEVICE"
Write-Host "data   : $env:LIOCR_DATA_DIR"
Write-Host "url    : http://$env:LIOCR_HOST`:$Port/"

Push-Location $root
try {
  & $Python -m uvicorn backend.app.main:app --host $env:LIOCR_HOST --port $Port
} finally {
  Pop-Location
}
