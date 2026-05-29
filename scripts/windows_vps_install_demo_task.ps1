param(
    [string]$TaskName = "ApexDemoTrader24x7",
    [string]$ShadowTaskName = "ApexShadowReporter24x7",
    [int]$SessionSeconds = 1800,
    [double]$PollSeconds = 0.25,
    [int]$WarmupBars = 50,
    [int]$RestSeconds = 5,
    [int]$DailyReportHourUtc = 22,
    [int]$DailyReportMinuteUtc = 5,
    [int]$DailyReportLookbackHours = 24,
    [switch]$DailyReportOnStart,
    [switch]$StartNow
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LoopScript = Join-Path $ProjectRoot "scripts\windows_vps_demo_loop.ps1"

if (-not (Test-Path $LoopScript)) {
    throw "Missing loop script: $LoopScript"
}

$ExistingShadow = Get-ScheduledTask -TaskName $ShadowTaskName -ErrorAction SilentlyContinue
if ($null -ne $ExistingShadow) {
    Stop-ScheduledTask -TaskName $ShadowTaskName -ErrorAction SilentlyContinue
    Write-Host "Stopped shadow task to avoid running two MT5 supervisors at once: $ShadowTaskName"
}

$Argument = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$LoopScript`"",
    "-SessionSeconds", $SessionSeconds,
    "-PollSeconds", $PollSeconds,
    "-WarmupBars", $WarmupBars,
    "-RestSeconds", $RestSeconds,
    "-DailyReportHourUtc", $DailyReportHourUtc,
    "-DailyReportMinuteUtc", $DailyReportMinuteUtc,
    "-DailyReportLookbackHours", $DailyReportLookbackHours
) -join " "

if ($DailyReportOnStart) {
    $Argument = "$Argument -DailyReportOnStart"
}

$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Argument -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 3650) `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "Runs Apex MT5 intelligent runner continuously with explicitly confirmed demo execution and protected trailing management." `
    -Force | Out-Null

Write-Host "Installed scheduled task: $TaskName"
Write-Host "Mode: ACTUAL DEMO EXECUTION with protected management."
Write-Host "Daily report time: $($DailyReportHourUtc.ToString('00')):$($DailyReportMinuteUtc.ToString('00')) UTC"
Write-Host "Safety gates: demo account required, dry-run env must remain true, max lot must be 0.05 or lower."

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Started scheduled task: $TaskName"
}

Write-Host ""
Write-Host "Check status:"
Write-Host "Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName,State"
Write-Host ""
Write-Host "Stop it:"
Write-Host "Stop-ScheduledTask -TaskName $TaskName"
