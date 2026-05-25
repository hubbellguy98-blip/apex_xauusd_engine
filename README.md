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
