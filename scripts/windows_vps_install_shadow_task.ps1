param(
    [string]$TaskName = "ApexShadowReporter24x7",
    [int]$SessionSeconds = 900,
    [double]$PollSeconds = 0.25,
    [int]$WarmupBars = 50,
    [int]$RestSeconds = 5,
    [int]$DailyReportEveryHours = 24,
    [switch]$StartNow
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LoopScript = Join-Path $ProjectRoot "scripts\windows_vps_shadow_loop.ps1"

if (-not (Test-Path $LoopScript)) {
    throw "Missing loop script: $LoopScript"
}

$Argument = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$LoopScript`"",
    "-SessionSeconds", $SessionSeconds,
    "-PollSeconds", $PollSeconds,
    "-WarmupBars", $WarmupBars,
    "-RestSeconds", $RestSeconds,
    "-DailyReportEveryHours", $DailyReportEveryHours,
    "-DailyReportOnStart"
) -join " "

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
    -Description "Runs Apex MT5 intelligent runner continuously in shadow/reporting mode. No order submission flags are used." `
    -Force | Out-Null

Write-Host "Installed scheduled task: $TaskName"
Write-Host "Mode: shadow/reporting only. No order submission flags are used."
Write-Host "The task starts when the current Windows user logs in. Disconnecting RDP should not stop it; signing out will."

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Started scheduled task: $TaskName"
}

Write-Host ""
Write-Host "Check status:"
Write-Host "Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Host ""
Write-Host "Stop it:"
Write-Host "Stop-ScheduledTask -TaskName $TaskName"
