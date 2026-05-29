param(
    [int]$SessionSeconds = 900,
    [double]$PollSeconds = 0.25,
    [int]$WarmupBars = 50,
    [int]$RestSeconds = 5,
    [int]$DailyReportEveryHours = 24,
    [switch]$DailyReportOnStart
)

$ErrorActionPreference = "Continue"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$EnvPath = Join-Path $ProjectRoot ".env"
$LogRoot = Join-Path $ProjectRoot ".apex_runtime\logs"

New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null

function Read-ApexEnv {
    param([string]$Path)
    $Values = @{}
    if (-not (Test-Path $Path)) {
        throw "Missing .env at $Path"
    }
    Get-Content $Path | ForEach-Object {
        $Line = $_.Trim()
        if ($Line -and -not $Line.StartsWith("#") -and $Line.Contains("=")) {
            $Key, $Value = $Line.Split("=", 2)
            $Values[$Key.Trim()] = $Value.Trim().Trim('"').Trim("'")
        }
    }
    return $Values
}

function Assert-SafeShadowConfiguration {
    if (-not (Test-Path $VenvPython)) {
        throw "Missing virtual environment. Run scripts\windows_vps_bootstrap.ps1 first."
    }
    $EnvValues = Read-ApexEnv -Path $EnvPath
    if ($EnvValues["APEX_MT5_DRY_RUN"] -ne "true") {
        throw "24/7 shadow loop requires APEX_MT5_DRY_RUN=true."
    }
    if ($EnvValues["APEX_MT5_REQUIRE_DEMO"] -ne "true") {
        throw "24/7 shadow loop requires APEX_MT5_REQUIRE_DEMO=true."
    }
    if ([double]$EnvValues["APEX_MAX_LOT"] -gt 0.01) {
        throw "24/7 shadow loop requires APEX_MAX_LOT=0.01 or lower."
    }
}

function Write-LoopLog {
    param([string]$Message)
    $Stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$Stamp] $Message"
}

Assert-SafeShadowConfiguration

$LastDailyReportAt = [DateTime]::MinValue
if ($DailyReportOnStart) {
    $LastDailyReportAt = (Get-Date).AddHours(-1 * $DailyReportEveryHours)
}

Write-LoopLog "Apex 24/7 shadow supervisor started."
Write-LoopLog "Project: $ProjectRoot"
Write-LoopLog "SessionSeconds=$SessionSeconds PollSeconds=$PollSeconds WarmupBars=$WarmupBars RestSeconds=$RestSeconds"
Write-LoopLog "Order submission is NOT enabled by this supervisor."

while ($true) {
    $StartedAt = Get-Date
    $RunStamp = $StartedAt.ToString("yyyyMMdd_HHmmss")
    $RunLog = Join-Path $LogRoot "shadow_runner_$RunStamp.log"

    Write-LoopLog "Starting shadow runner cycle. Log: $RunLog"
    try {
        & $VenvPython scripts\mt5_intelligent_demo_runner.py `
            --duration-seconds $SessionSeconds `
            --poll-seconds $PollSeconds `
            --warmup-bars $WarmupBars 2>&1 | Tee-Object -FilePath $RunLog
        $ExitCode = $LASTEXITCODE
        Write-LoopLog "Shadow runner cycle finished with exit code $ExitCode."
    }
    catch {
        Write-LoopLog "Shadow runner cycle crashed: $($_.Exception.Message)"
        $_ | Out-File -FilePath $RunLog -Append
    }

    $HoursSinceReport = ((Get-Date) - $LastDailyReportAt).TotalHours
    if ($HoursSinceReport -ge $DailyReportEveryHours) {
        $ReportLog = Join-Path $LogRoot "telegram_daily_report_$((Get-Date).ToString('yyyyMMdd_HHmmss')).log"
        Write-LoopLog "Generating Telegram daily report. Log: $ReportLog"
        try {
            & $VenvPython scripts\telegram_daily_report.py --lookback-hours 24 2>&1 | Tee-Object -FilePath $ReportLog
            $LastDailyReportAt = Get-Date
        }
        catch {
            Write-LoopLog "Daily report failed: $($_.Exception.Message)"
            $_ | Out-File -FilePath $ReportLog -Append
        }
    }

    Start-Sleep -Seconds $RestSeconds
}
