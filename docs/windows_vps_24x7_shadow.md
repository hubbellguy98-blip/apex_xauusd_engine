# Windows VPS 24/7 Shadow Runner

This runner keeps Apex active on the VPS in shadow/reporting mode. It repeatedly runs the intelligent MT5 strategy runner, records telemetry, and sends Telegram reports when Telegram is enabled.

It does not enable order submission. The scheduled task never passes `--execute-one-demo`, `--confirm-execution`, `--manage-open-demo`, or `--confirm-management`.

## Before Installing

Confirm the VPS is already working:

```powershell
cd C:\Apex\apex_xauusd_engine
.\.venv\Scripts\python.exe scripts\mt5_connection_check.py
.\.venv\Scripts\python.exe scripts\telegram_smoke_test.py
```

Keep the safe `.env` settings:

```text
APEX_MT5_DRY_RUN=true
APEX_MT5_REQUIRE_DEMO=true
APEX_MAX_LOT=0.03
```

## Install And Start

Run PowerShell as Administrator on the VPS:

```powershell
cd C:\Apex\apex_xauusd_engine
powershell -ExecutionPolicy Bypass -File .\scripts\windows_vps_install_shadow_task.ps1 -StartNow
```

Default behavior:

- Runs a 15-minute shadow session.
- Waits 5 seconds.
- Starts the next shadow session.
- Sends the daily report at `22:05 UTC`, five minutes after the configured Asian session starts.
- The report looks back 24 hours, so each report covers one Asia-to-next-Asia trading day cycle.
- Writes logs to `.apex_runtime\logs`.

`22:05 UTC` is `03:35 IST`. This timing is intentional because the strategy's session engine defines Asian accumulation as `22:00-06:00 UTC`.

## Check Status

```powershell
Get-ScheduledTask -TaskName ApexShadowReporter24x7 | Get-ScheduledTaskInfo
```

## Stop Temporarily

```powershell
Stop-ScheduledTask -TaskName ApexShadowReporter24x7
```

## Start Again

```powershell
Start-ScheduledTask -TaskName ApexShadowReporter24x7
```

## Remove Completely

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows_vps_remove_shadow_task.ps1
```

## Important VPS Behavior

MT5 is a desktop application. The Python MT5 bridge is most reliable when the Windows user is logged in and MT5 is open. You can disconnect RDP, but do not sign out. If the VPS fully restarts, log in once, open/check MT5, and the task will start again at login.
