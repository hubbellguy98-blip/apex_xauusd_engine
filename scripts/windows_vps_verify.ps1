param(
    [int]$ShadowSeconds = 60
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$EnvPath = Join-Path $ProjectRoot ".env"

if (-not (Test-Path $VenvPython)) {
    throw "Missing virtual environment. Run scripts\windows_vps_bootstrap.ps1 first."
}

if (-not (Test-Path $EnvPath)) {
    throw "Missing .env. Copy .env.example to .env and fill the VPS MT5 demo settings first."
}

$EnvValues = @{}
Get-Content $EnvPath | ForEach-Object {
    $Line = $_.Trim()
    if ($Line -and -not $Line.StartsWith("#") -and $Line.Contains("=")) {
        $Key, $Value = $Line.Split("=", 2)
        $EnvValues[$Key.Trim()] = $Value.Trim().Trim('"').Trim("'")
    }
}

if ($EnvValues["APEX_MT5_DRY_RUN"] -ne "true") {
    throw "Keep APEX_MT5_DRY_RUN=true during first VPS verification."
}

if ($EnvValues["APEX_MT5_REQUIRE_DEMO"] -ne "true") {
    throw "Keep APEX_MT5_REQUIRE_DEMO=true during first VPS verification."
}

if ([double]$EnvValues["APEX_MAX_LOT"] -gt 0.01) {
    throw "Keep APEX_MAX_LOT=0.01 or lower during first VPS verification."
}

if ($EnvValues.ContainsKey("MT5_PATH") -and $EnvValues["MT5_PATH"] -and -not (Test-Path $EnvValues["MT5_PATH"])) {
    throw "MT5_PATH does not exist: $($EnvValues["MT5_PATH"])"
}

Write-Host "Apex VPS verification"
Write-Host "Project: $ProjectRoot"
Write-Host "Shadow seconds: $ShadowSeconds"
Write-Host ""

Write-Host "Environment versions"
git --version
& $VenvPython --version
& $VenvPython -m pip --version

Write-Host ""
Write-Host "1/4 MT5 connection check"
& $VenvPython scripts\mt5_connection_check.py

Write-Host ""
Write-Host "2/4 Read-only market observation"
& $VenvPython scripts\mt5_market_observe.py --duration-seconds 8 --poll-interval-seconds 0.25 --candle-seconds 2

Write-Host ""
Write-Host "3/4 Protected pipeline dry run"
& $VenvPython scripts\mt5_pipeline_dry_run.py

Write-Host ""
Write-Host "4/4 Shadow-only intelligent runner"
& $VenvPython scripts\mt5_intelligent_demo_runner.py --duration-seconds $ShadowSeconds --poll-seconds 0.25 --warmup-bars 50

Write-Host ""
Write-Host "VPS verification completed without enabling order submission."
