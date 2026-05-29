# Windows VPS 24/7 Demo Execution Runner

This runner keeps Apex active on the VPS with actual demo-account execution enabled. It is intended for observing real demo fills, broker behavior, Telegram reporting, position protection, and whether the system behavior matches your trading style.

It still uses safety gates:

- `APEX_MT5_REQUIRE_DEMO=true` is required.
- `APEX_MT5_DRY_RUN=true` must remain set in `.env`; the runner only unlocks order sending through explicit command-line confirmation flags.
- `APEX_MAX_LOT` must be `0.01` or lower.
- Telegram must be enabled so trade activity is reported.
- The MT5 gateway and runner refuse a new Gold entry when a Gold position is already open.
- The managed trailing logic is enabled with its explicit confirmation flag.

## Important Behavior

Demo execution mode does not force an instant trade. It allows a trade only when the strategy detects a qualified setup, passes scoring, passes risk, passes live quote checks, and passes MT5 pre-submission validation.

## Switch From Shadow To Demo Execution

Run PowerShell as Administrator on the VPS:

```powershell
cd C:\Apex\apex_xauusd_engine
powershell -ExecutionPolicy Bypass -File .\scripts\windows_vps_install_demo_task.ps1 -StartNow
```

The installer stops the shadow task if it exists, so only one MT5 supervisor runs at a time.

## Check Status

```powershell
Get-ScheduledTask -TaskName ApexDemoTrader24x7 | Select-Object TaskName,State
```

Check newest demo logs:

```powershell
Get-ChildItem .apex_runtime\logs\demo_runner_*.log | Sort-Object LastWriteTime -Descending | Select-Object -First 5
Get-Content (Get-ChildItem .apex_runtime\logs\demo_runner_*.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName -Tail 120
```

## Stop Demo Execution

```powershell
Stop-ScheduledTask -TaskName ApexDemoTrader24x7
```

## Remove Demo Execution Task

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows_vps_remove_demo_task.ps1
```

## Return To Shadow Mode

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows_vps_remove_demo_task.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\windows_vps_install_shadow_task.ps1 -StartNow
```

## Daily Report Timing

The report is sent at `22:05 UTC`, five minutes after the configured Asian session starts, and summarizes the previous 24-hour Asia-to-next-Asia trading cycle.
