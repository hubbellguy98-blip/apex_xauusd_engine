param(
    [int]$SessionSeconds = 1800,
    [double]$PollSeconds = 0.25,
    [int]$WarmupBars = 50,
    [int]$RestSeconds = 5,
    [int]$DailyReportHourUtc = 22,
    [int]$DailyReportMinuteUtc = 5,
    [int]$DailyReportLookbackHours = 24,
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

function Assert-SafeDemoConfiguration {
    if (-not (Test-Path $VenvPython)) {
        throw "Missing virtual environment. Run scripts\windows_vps_bootstrap.ps1 first."
    }
    $EnvValues = Read-ApexEnv -Path $EnvPath
    if ($EnvValues["APEX_MT5_DRY_RUN"] -ne "true") {
        throw "Keep APEX_MT5_DRY_RUN=true. The runner itself temporarily unlocks one explicitly confirmed demo order."
    }
    if ($EnvValues["APEX_MT5_REQUIRE_DEMO"] -ne "true") {
        throw "Demo execution loop requires APEX_MT5_REQUIRE_DEMO=true."
    }
    if ([double]$EnvValues["APEX_MAX_LOT"] -gt 0.01) {
        throw "Demo execution loop requires APEX_MAX_LOT=0.01 or lower."
    }
    if ($EnvValues["APEX_TELEGRAM_ENABLED"] -ne "true") {
        throw "Demo execution loop requires APEX_TELEGRAM_ENABLED=true so trade activity is reported."
    }
}

function Write-LoopLog {
    param([string]$Message)
    $Stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$Stamp] $Message"
}

function Get-NextDailyReportDueUtc {
    param(
        [DateTime]$ReferenceUtc,
        [int]$HourUtc,
        [int]$MinuteUtc
    )
    $Candidate = [DateTime]::SpecifyKind(
        [DateTime]::new($ReferenceUtc.Year, $ReferenceUtc.Month, $ReferenceUtc.Day, $HourUtc, $MinuteUtc, 0),
        [DateTimeKind]::Utc
    )
    if ($Candidate -le $ReferenceUtc) {
        $Candidate = $Candidate.AddDays(1)
    }
    return $Candidate
}

Assert-SafeDemoConfiguration

$NextDailyReportAtUtc = Get-NextDailyReportDueUtc `
    -ReferenceUtc ([DateTime]::UtcNow) `
    -HourUtc $DailyReportHourUtc `
    -MinuteUtc $DailyReportMinuteUtc

Write-LoopLog "Apex 24/7 DEMO EXECUTION supervisor started."
Write-LoopLog "Project: $ProjectRoot"
Write-LoopLog "SessionSeconds=$SessionSeconds PollSeconds=$PollSeconds WarmupBars=$WarmupBars RestSeconds=$RestSeconds"
Write-LoopLog "Daily report due UTC: $($NextDailyReportAtUtc.ToString('yyyy-MM-dd HH:mm:ss'))"
Write-LoopLog "Actual DEMO order submission is enabled only through explicit runner confirmation flags."
Write-LoopLog "The strategy still enforces one Gold position at a time and max 0.01 lots."

if ($DailyReportOnStart) {
    $ReportLog = Join-Path $LogRoot "telegram_daily_report_$((Get-Date).ToString('yyyyMMdd_HHmmss')).log"
    Write-LoopLog "Generating startup Telegram daily report. Log: $ReportLog"
    & $VenvPython scripts\telegram_daily_report.py --lookback-hours $DailyReportLookbackHours 2>&1 | Tee-Object -FilePath $ReportLog
}

while ($true) {
    $StartedAt = Get-Date
    $RunStamp = $StartedAt.ToString("yyyyMMdd_HHmmss")
    $RunLog = Join-Path $LogRoot "demo_runner_$RunStamp.log"

    Write-LoopLog "Starting demo execution runner cycle. Log: $RunLog"
    try {
        & $VenvPython scripts\mt5_intelligent_demo_runner.py `
            --duration-seconds $SessionSeconds `
            --poll-seconds $PollSeconds `
            --warmup-bars $WarmupBars `
            --execute-one-demo `
            --confirm-execution ENABLE_ONE_INTELLIGENT_DEMO_TRADE `
            --manage-open-demo `
            --confirm-management ENABLE_BUFFERED_DEMO_TRAILING 2>&1 | Tee-Object -FilePath $RunLog
        $ExitCode = $LASTEXITCODE
        Write-LoopLog "Demo execution runner cycle finished with exit code $ExitCode."
    }
    catch {
        Write-LoopLog "Demo execution runner cycle crashed: $($_.Exception.Message)"
        $_ | Out-File -FilePath $RunLog -Append
    }

    if ([DateTime]::UtcNow -ge $NextDailyReportAtUtc) {
        $ReportLog = Join-Path $LogRoot "telegram_daily_report_$((Get-Date).ToString('yyyyMMdd_HHmmss')).log"
        Write-LoopLog "Generating Telegram daily report. Log: $ReportLog"
        try {
            & $VenvPython scripts\telegram_daily_report.py --lookback-hours $DailyReportLookbackHours 2>&1 | Tee-Object -FilePath $ReportLog
            $NextDailyReportAtUtc = Get-NextDailyReportDueUtc `
                -ReferenceUtc ([DateTime]::UtcNow) `
                -HourUtc $DailyReportHourUtc `
                -MinuteUtc $DailyReportMinuteUtc
            Write-LoopLog "Next daily report due UTC: $($NextDailyReportAtUtc.ToString('yyyy-MM-dd HH:mm:ss'))"
        }
        catch {
            Write-LoopLog "Daily report failed: $($_.Exception.Message)"
            $_ | Out-File -FilePath $ReportLog -Append
        }
    }

    Start-Sleep -Seconds $RestSeconds
}
