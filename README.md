# Apex XAUUSD Algorithmic Execution Engine v1.0.0

An institutional-grade, low-latency algorithmic trading architecture for XAUUSD market state modeling, opportunity discovery, risk control, and deterministic execution routing.

## Overview

The Apex Engine models the market as a liquidity-clearing process. It separates pure trading logic from infrastructure concerns so strategy rules, data transport, storage, telemetry, and execution adapters can evolve independently.

```text
[Twelve Data API Ingest]
          |
          v
+---------------------------------------+
|      APEX LOW-LATENCY ENGINE CORE     |
|                                       |
|  1. State Management (In-Memory SSOT) |
|  2. Analytics Engine (SMC Matrix)     |
|  3. Confirmation and Scoring Matrix   |
|  4. Pre-Trade Risk Firewall           |
+---------------------------------------+
          |
          v
[Low-Latency Broker Route]
```

## Key Features

- Microstructure state tracking through an in-memory single source of truth.
- Deterministic SMC analytics for structure, liquidity, sessions, and volatility regimes.
- Pre-trade risk firewall for spread, drawdown, sizing, and concurrency checks.
- Explainable confirmation and scoring layers for ranking candidate setups.
- Historical replay and simulation modules for backtesting.
- Structured logging, metrics, and deployment scaffolding for operational visibility.

## Project Structure

```text
apex_xauusd_engine/
|-- config/       # Runtime configuration profiles
|-- deploy/       # Docker, Compose, and Prometheus setup
|-- docs/         # Architecture notes and runbooks
|-- scripts/      # Launch and database migration entrypoints
|-- src/          # Core application code
|   |-- api/      # Dashboard/control API layer
|   |-- core/     # Domain primitives, events, state, lifecycle
|   |-- analytics/# Market microstructure analytics
|   |-- strategy/ # Setup detection, confirmation, scoring
|   |-- execution/# Risk firewall and execution routing
|   |-- backtest/ # Historical replay interfaces
|   `-- shared/   # Shared utilities and exceptions
`-- tests/        # Unit, integration, property, and benchmark tests
```

## Local Setup

```powershell
python -m pip install -e ".[dev]"
python -m compileall -q config src scripts tests
python -m pytest
```

## ICT/SMC Backtest Profiles

Selector backtests are profile-driven so results are reproducible and not hidden behind code defaults. Profiles live in `config/backtest_profiles.json`.

```powershell
python scripts/run_ict_smc_backtest.py --source mt5 --symbol GOLD.i# --from 2026-06-01 --to 2026-06-14 --profile strict_intraday_xauusd
python scripts/analyze_trade_log.py .\backtest_outputs\ict_smc_backtest_mt5_YYYYMMDD_HHMMSS_trades.csv
```

The JSON/Markdown reports include git SHA, branch, command args, active profile, selector config, spread/slippage, symbol, source, date range, timeframe counts, diagnostics, and deployment-readiness warnings. The strict intraday profile requires post-cost final RR of at least 3R and supports a configurable 1:3 or 1:6 target ladder.

`v3_candidate_safety` is the current shadow/backtest candidate profile. It uses the shared profile normalizer, 1m setup/entry candles, London Open exclusion, 3R+ post-cost checks, risk-vs-cost filtering, early-entry trap filtering, and displacement verification. It is not live approval until a deployment-gated backtest is positive.

Every new trade CSV row also includes the profile name, git SHA/branch, minimum RR/score, active profile hash, selector config hash, and run ID. Strict-profile runs fail by default if completed trades violate the configured RR, killzone, disabled-killzone, or max-hold gates. Use `--allow-failed-deployment-gates` only for research.

## Runtime Notes

The live engine expects environment values such as `APEX_TWELVEDATA_KEY` and `DATABASE_URL`. For development, keep those in a local `.env` file and do not commit secrets.

## MT5 Demo Safety Checks

The MetaTrader 5 integration is currently limited to protected demo-account checks. Keep `APEX_MT5_DRY_RUN=true`, `APEX_MT5_REQUIRE_DEMO=true`, and a small `APEX_MAX_LOT` limit in the local `.env` file.

```powershell
python scripts/mt5_connection_check.py
python scripts/mt5_market_observe.py
python scripts/mt5_signal_readiness_observe.py
python scripts/mt5_pipeline_dry_run.py
```

- `mt5_market_observe.py` reads broker quotes into the analytical layer only.
- `mt5_signal_readiness_observe.py` warms structure/liquidity analysis from completed MT5 one-minute candles, then monitors live quote sweeps only; it does not invoke scoring, risk, or order routing.
- `mt5_pipeline_dry_run.py` uses a synthetic candidate to verify capped risk and MT5 `order_check`; it does not send a trade.

Automatic strategy-driven demo execution is intentionally not enabled by default. The intelligent runner now feeds broker candles and quotes through the core setup-detection, confirmation, scoring, and risk pipeline in shadow mode before any separately confirmed demo order can be routed.

## Minimal Demo Execution

The following scripts enable explicitly confirmed demo-account trades only. They require `APEX_MT5_DRY_RUN=true`, `APEX_MT5_REQUIRE_DEMO=true`, and `APEX_MAX_LOT=0.03` in `.env` by default. The hard demo safety ceiling is `0.05`.

```powershell
python scripts/mt5_demo_trade_smoke_test.py --confirm-demo-order EXECUTE_ONE_DEMO_ORDER --direction BUY
python scripts/mt5_demo_auto_trigger.py --confirm-demo-auto ENABLE_ONE_DEMO_AUTO_TRADE
```

- `mt5_demo_trade_smoke_test.py` sends one requested `0.03` lot demo Gold trade.
- `mt5_demo_auto_trigger.py` waits for a small live price movement, then sends at most the configured capped demo Gold trade size.
- The automatic trigger refuses to submit while any Gold position is already open.

## Intelligent Demo Runner

`mt5_intelligent_demo_runner.py` drives the original core strategy path: completed MT5 candles create structure and liquidity levels inside `MarketSetupOrchestrator`, newly closed candles continuously refresh that live state, live quotes detect sweeps, and existing confirmation, scoring, and capped-risk checks determine whether a trade qualifies.

```powershell
python scripts/mt5_intelligent_demo_runner.py --duration-seconds 60
```

The default command is shadow-only and cannot submit an order. After observing qualified signals, one explicitly confirmed demo execution can be enabled with:

```powershell
python scripts/mt5_intelligent_demo_runner.py --duration-seconds 300 --execute-one-demo --confirm-execution ENABLE_ONE_INTELLIGENT_DEMO_TRADE
```

The intelligent runner continues to require demo mode, limit configured volume to `0.05` lots, and refuse a new trade while a Gold position is already open.
In shadow mode it also reports the decision funnel, including live sweeps, reversal candidates, confirmation or quality rejections, the nearest active liquidity level, startup connection retries, and temporary broker quote gaps, without changing the trading thresholds. The confirmation stage enforces the configured London and New York killzone windows exactly rather than treating the full regional session as trade permission.
Live-feed readiness is confirmed from recently changing MT5 quotes rather than the broker timestamp alone, because broker server clocks may not align with the workstation clock. If quote changes stop for more than five seconds, shadow observations and any final execution gate fail closed.

## Position Protection Policy

- The MT5 gateway refuses a new Gold entry whenever an existing Gold position is open, regardless of which entry script requests it.
- A planned `1:6` trade is treated as six profit milestones while the single broker position retains its final TP.
- Trailing decisions use completed candles rather than tick touches: a candle must close beyond a milestone plus a small confirmation buffer.
- The stop is placed slightly behind the secured milestone rather than exactly on it, allowing ordinary pullbacks while locking progressively more profit.
- A local ignored protection record preserves the initial SL and completed milestone if the runner is restarted.
- Actual demo-position stop modification is separately protected and must be explicitly enabled:

```powershell
python scripts/mt5_intelligent_demo_runner.py --duration-seconds 300 --manage-open-demo --confirm-management ENABLE_BUFFERED_DEMO_TRAILING
```

When a future authorized strategy run should remain alive to protect the position it opens, use both explicit approvals:

```powershell
python scripts/mt5_intelligent_demo_runner.py --duration-seconds 1800 --execute-one-demo --confirm-execution ENABLE_ONE_INTELLIGENT_DEMO_TRADE --manage-open-demo --confirm-management ENABLE_BUFFERED_DEMO_TRAILING
```

The runner must remain active while the position is open for automatic trailing changes to be submitted to MT5.

## Windows VPS Updates

After the VPS is cloned from GitHub and verified, update it with:

```powershell
cd C:\Apex\apex_xauusd_engine
powershell -ExecutionPolicy Bypass -File .\scripts\windows_vps_update.ps1 -ShadowSeconds 60
```

The update script pulls the latest GitHub commit, refreshes dependencies, compiles the project and runs the safe VPS verification sequence without enabling order submission.

## Telegram Reporting

The Telegram reporter is observer-only: it records runtime evidence and can send session or daily summaries, but it does not change trading decisions, risk settings, or order routing.

```powershell
python scripts/telegram_smoke_test.py
python scripts/telegram_daily_report.py --lookback-hours 24
```

Configuration lives in the local `.env` file. Keep `APEX_TELEGRAM_ENABLED=false` until `APEX_TELEGRAM_BOT_TOKEN` and `APEX_TELEGRAM_CHAT_ID` are added on the VPS. Full setup notes are in `docs/telegram_reporting.md`.

## Windows VPS 24/7 Shadow Mode

The first 24/7 mode is intentionally shadow/reporting only. It keeps collecting live MT5 evidence and Telegram reports without passing any order-submission flags.

```powershell
cd C:\Apex\apex_xauusd_engine
powershell -ExecutionPolicy Bypass -File .\scripts\windows_vps_install_shadow_task.ps1 -StartNow
```

By default, the daily report is sent at `22:05 UTC`, just after the next Asian session starts, and covers the previous 24-hour Asia-to-Asia trading cycle. See `docs/windows_vps_24x7_shadow.md` for status, stop, restart, and removal commands.

## Windows VPS 24/7 Demo Execution

After shadow mode is verified, the VPS can be switched to actual demo-account execution. This mode still requires `APEX_MT5_REQUIRE_DEMO=true`, keeps the local `.env` dry-run flag set to true, defaults configured volume to `0.03`, caps volume at `0.05`, stops the shadow task, and passes the runner's explicit demo-execution confirmation flags.

```powershell
cd C:\Apex\apex_xauusd_engine
powershell -ExecutionPolicy Bypass -File .\scripts\windows_vps_install_demo_task.ps1 -StartNow
```

Demo execution does not force an immediate trade; it permits one demo trade only after the strategy, scoring, risk, quote freshness, and MT5 checks all approve. See `docs/windows_vps_24x7_demo_execution.md`.
