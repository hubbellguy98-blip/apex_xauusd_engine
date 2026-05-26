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
- `mt5_signal_readiness_observe.py` reports real structure/liquidity readiness only; it does not invoke scoring, risk, or order routing.
- `mt5_pipeline_dry_run.py` uses a synthetic candidate to verify capped risk and MT5 `order_check`; it does not send a trade.

Automatic demo execution is intentionally not enabled. The current live setup-detection workflow still requires verified real analytical inputs in place of its placeholder discovery inputs before it can be considered for order routing.
