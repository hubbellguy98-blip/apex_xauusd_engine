# Windows VPS Setup Runbook

This runbook sets up the Apex XAUUSD demo engine on a Windows VPS with XM MetaTrader 5.

The first VPS setup must stay in dry-run mode until MT5 connectivity, quote flow and reporting are confirmed.

## 1. Connect To The VPS

1. Open Remote Desktop Connection on your PC.
2. Enter the VPS IP address from InterServer.
3. Log in with the Administrator username and the password you saved when ordering.
4. Immediately change the Administrator password if the original password was visible in any screenshot.

## 2. Install Required Software

Install these inside the VPS:

- Git for Windows: https://git-scm.com/download/win
- Python 3.12 or newer: https://www.python.org/downloads/windows/
- XM MetaTrader 5 desktop terminal from XM's official member area or XM download page.

During Python installation, enable:

- Add Python to PATH
- pip

After installation, open a new PowerShell window and verify:

```powershell
git --version
python --version
python -m pip --version
```

## 3. Log In To XM MT5

1. Open XM MetaTrader 5.
2. Log in to the XM demo account.
3. Open Market Watch.
4. Show the Gold/XAUUSD CFD symbol used by this account.
5. Keep MT5 running before starting the Python engine.

The current local account resolved Gold as `GOLD.i#`, but the VPS must be verified because XM symbol names can differ by account/server.

## 4. Clone The Private Repository

Open PowerShell inside the VPS and run:

```powershell
mkdir C:\Apex
cd C:\Apex
git clone https://github.com/hubbellguy98-blip/apex_xauusd_engine.git
cd C:\Apex\apex_xauusd_engine
```

If GitHub asks for authentication, use GitHub CLI or a browser/device login. Do not paste GitHub tokens into chat.

## 5. Bootstrap Python Environment

Run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows_vps_bootstrap.ps1
```

This creates `.venv`, upgrades packaging tools, installs the repo dependencies and compiles source files.

## 6. Create The VPS `.env`

Copy the example file:

```powershell
copy .env.example .env
notepad .env
```

Fill the VPS `.env` with the demo account details. Keep dry-run enabled first:

```env
MT5_LOGIN=your_demo_account_id
MT5_PASSWORD=your_demo_password
MT5_SERVER=your_exact_xm_mt5_server
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
APEX_SYMBOL=XAUUSD
APEX_MT5_DRY_RUN=true
APEX_MT5_REQUIRE_DEMO=true
APEX_MAX_LOT=0.03
APEX_MT5_DEVIATION_POINTS=20
```

If the XM terminal path is different, right-click the MT5 desktop shortcut, choose Properties and copy the target path.

## 7. Safe VPS Verification

Run the bundled verification helper:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows_vps_verify.ps1 -ShadowSeconds 60
```

It runs these checks in order:

```powershell
.\.venv\Scripts\python.exe scripts\mt5_connection_check.py
.\.venv\Scripts\python.exe scripts\mt5_market_observe.py --duration-seconds 8 --poll-interval-seconds 0.25 --candle-seconds 2
.\.venv\Scripts\python.exe scripts\mt5_pipeline_dry_run.py
.\.venv\Scripts\python.exe scripts\mt5_intelligent_demo_runner.py --duration-seconds 60 --poll-seconds 0.25 --warmup-bars 50
```

Expected safe results:

- MT5 connection OK.
- Demo account detected.
- Symbol resolves to the correct Gold/XAUUSD instrument.
- Read-only market observation processes changing quotes.
- Dry-run pipeline reaches MT5 validation but sends no order.
- Shadow runner processes changing quotes and sends no order.

Stop and fix the VPS setup if any of these appear:

- MT5 account is not demo.
- No tick available.
- No recent tick activity.
- Wrong symbol, such as a stock named BarrickGold instead of spot Gold/XAUUSD.
- `APEX_MAX_LOT` greater than `0.05`.

## 8. Later Continuous Demo Mode

Do not leave the current strategy unattended until Telegram reporting, daily logs and restart recovery are added.

Telegram bot credentials should be added only after the MT5 checks pass. Keep bot tokens in the VPS `.env` file and do not commit them.

## 9. Controlled VPS Updates

When new code is pushed to GitHub, update the VPS from PowerShell:

```powershell
cd C:\Apex\apex_xauusd_engine
powershell -ExecutionPolicy Bypass -File .\scripts\windows_vps_update.ps1 -ShadowSeconds 60
```

This script refuses to run with uncommitted local changes, pulls the latest GitHub commit, refreshes dependencies, compiles the project and runs the safe VPS verification sequence. It does not enable order submission.

Use this faster form only when you intentionally want to skip verification:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows_vps_update.ps1 -SkipVerification
```

Do not run automatic strategy execution after an update until verification has passed.

The future controlled demo command should remain explicit and capped:

```powershell
.\.venv\Scripts\python.exe scripts\mt5_intelligent_demo_runner.py --duration-seconds 1800 --execute-one-demo --confirm-execution ENABLE_ONE_INTELLIGENT_DEMO_TRADE --manage-open-demo --confirm-management ENABLE_BUFFERED_DEMO_TRAILING
```

This still allows only one protected demo trade at a time.
