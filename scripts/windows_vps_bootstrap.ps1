param(
    [string]$PythonCommand = "python"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

Write-Host "Apex Windows VPS bootstrap"
Write-Host "Project: $ProjectRoot"

& $PythonCommand --version

if (-not (Test-Path ".\.venv")) {
    Write-Host "Creating local virtual environment..."
    & $PythonCommand -m venv .venv
}

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    throw "Virtual environment Python was not created at $VenvPython"
}

Write-Host "Upgrading packaging tools..."
& $VenvPython -m pip install --upgrade pip setuptools wheel

Write-Host "Installing Apex dependencies..."
& $VenvPython -m pip install -e ".[dev]"

Write-Host "Compiling source files..."
& $VenvPython -m compileall -q config src scripts tests

if (-not (Test-Path ".\.env")) {
    Write-Host "Creating .env from .env.example. Edit it before connecting to MT5."
    Copy-Item ".\.env.example" ".\.env"
}

Write-Host ""
Write-Host "Bootstrap complete."
Write-Host "Next:"
Write-Host "1. Install and log in to XM MetaTrader 5."
Write-Host "2. Edit .env with the VPS MT5 path and demo credentials."
Write-Host "3. Run: .\.venv\Scripts\python.exe scripts\mt5_connection_check.py"
