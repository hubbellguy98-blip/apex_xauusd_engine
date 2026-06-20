param(
    [switch]$SkipVerification,
    [int]$ShadowSeconds = 60
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$EnvPath = Join-Path $ProjectRoot ".env"

if (-not (Test-Path ".git")) {
    throw "This update script must be run from a Git clone, not a zip copy."
}

if (-not (Test-Path $VenvPython)) {
    throw "Missing virtual environment. Run scripts\windows_vps_bootstrap.ps1 first."
}

if (-not (Test-Path $EnvPath)) {
    throw "Missing .env. Create it from .env.example and keep VPS secrets local."
}

$Status = git status --porcelain
if ($Status) {
    Write-Host "Local uncommitted changes were found:"
    $Status | ForEach-Object { Write-Host $_ }
    throw "Refusing to update because local changes could be overwritten. Commit, stash or remove them first."
}

$BeforeCommit = git rev-parse --short HEAD
$Branch = git branch --show-current

Write-Host "Apex VPS controlled update"
Write-Host "Project: $ProjectRoot"
Write-Host "Branch: $Branch"
Write-Host "Current commit: $BeforeCommit"
Write-Host ""

Write-Host "Fetching latest GitHub state..."
git fetch origin

Write-Host "Pulling latest changes..."
git pull --ff-only origin $Branch

$AfterCommit = git rev-parse --short HEAD
Write-Host "Updated commit: $AfterCommit"

Write-Host ""
Write-Host "Refreshing Python dependencies..."
& $VenvPython -m pip install --upgrade pip setuptools wheel
& $VenvPython -m pip install -e ".[dev]"

Write-Host ""
Write-Host "Compiling source files..."
& $VenvPython -m compileall -q config src scripts tests reports

if (-not $SkipVerification) {
    Write-Host ""
    Write-Host "Running safe VPS verification. This does not enable order submission."
    powershell -ExecutionPolicy Bypass -File ".\scripts\windows_vps_verify.ps1" -ShadowSeconds $ShadowSeconds
} else {
    Write-Host ""
    Write-Host "Verification skipped by request. Run scripts\windows_vps_verify.ps1 before enabling any runner."
}

Write-Host ""
Write-Host "Controlled update complete."
Write-Host "Previous commit: $BeforeCommit"
Write-Host "Current commit:  $AfterCommit"
