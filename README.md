# Apex XAUUSD Algorithmic Execution Engine v1.0.0

An institutional-grade, low-latency algorithmic trading architecture engineered for high-frequency XAUUSD (Gold) spot market state modeling, opportunity discovery, and deterministic position routing.

## Overview

The Apex Engine treats the market as an algorithmic liquidity-clearing machine. Operating on a single-threaded cooperative multitasking event loop backed by `uvloop` and `asyncio`, the system guarantees lock-free thread safety, predictable performance, and microsecond-level tick processing. The architecture uses a Hexagonal Architecture (Ports and Adapters) design pattern to strictly decouple trading strategies and core domain rules from underlying transport layers, persistent database engines, and WebSocket API connections.


   [ Twelve Data API Ingest ]
               â”‚ (Real-Time Tick Stream)
               â–¼

â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚     APEX LOW-LATENCY ENGINE CORE      â”‚
â”‚                                       â”‚
â”‚  1. State Management (In-Memory SSOT) â”‚
â”‚  2. Analytics Engine (SMC Matrix)     â”‚
â”‚  3. Confirmation & Scoring Matrix     â”‚
â”‚  4. Pre-Trade Risk Firewall Gateway   â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                   â”‚
                   â–¼
     [ Low-Latency Broker Route ]


## Key Features

* **Microstructure State Tracking:** In-memory Single Source of Truth (SSOT) tracking market, session, volatility regime, position, and infrastructure health states concurrently.
* **Deterministic SMC Analytics Engine:** Programmatic identification of institutional patterns including Break of Structure (BOS), Market Structure Shift (MSS), Change of Character (CHoCH), Fair Value Gaps (FVG), and Order Blocks (OB) with full breaker conversion tracking.
* **Pre-Trade Risk Firewall:** Pre-execution check array validating account equity limits, maximum allowable spread caps ($3.5 \text{ pips}$), maximum concurrency boundaries, and daily drawdown limitations ($3.0\%$) within sub-millisecond timelines.
* **Explainable AI Inference Coprocessor:** Platt-scaled logistic transformations running over stationary microstructure feature profiles to calculate calibrated setup success probabilities alongside traceable linear log-odds contribution matrices.
* **High-Fidelity Replay & Backtesting:** Deterministic playback engines streaming historical Parquet structures via PyArrow to replicate production environments down to latency injection, spread widening, and slippage distributions.
* **Observability Architecture:** Integrated asynchronous structured JSON logging, distributed trace spans, and Prometheus metric exporters to monitor tail latencies ($P95$/$P99$) under extreme market data bursts.

## Project Structure

```text
apex_xauusd_engine/
â”œâ”€â”€ config/                             # Immutably Frozen Configuration Schemas
â”œâ”€â”€ deploy/                             # Multi-Stage Production Container Layouts
â”œâ”€â”€ docs/                               # Developer Runbooks and System Specifications
â”œâ”€â”€ scripts/                            # Operational Maintenance & Boot Scripts
â”œâ”€â”€ src/                                # System Core Application Codebase
â”‚   â”œâ”€â”€ api/                            # FastAPI Gateway & WebSocket Stream Drivers
â”‚   â”œâ”€â”€ core/                           # Domain Primitives & Asynchronous Priority Bus
â”‚   â”œâ”€â”€ analytics/                      # Pure Microstructure Signal Extraction Engines
â”‚   â”œâ”€â”€ strategy/                       # Setup Generators, Confirmations & Scorers
â”‚   â”œâ”€â”€ execution/                      # Risk Firewalls, Sizers & Order Lifecycles
â”‚   â””â”€â”€ infrastructure/                 # IO Transport Clients & Storage Adapters
â””â”€â”€ tests/                              # Unit, Integration, Performance & Stress Suites

Quick Start

1. Prerequisites

Linux (Ubuntu 22.04 LTS or bare-metal optimized kernel environments preferred)
Docker Core Engine 24.x+ and Docker Compose v2.x+
Python 3.12+ (For local development environments)

2. Environmental Initialization

Clone the repository and extract the template parameter profile configuration:

cp .env.example .env.production

Configure your production variables inside .env.production, substituting placeholders with your verified TwelveData API credential keys and secure transactional relational storage passwords.

3. Launching Infrastructure Services via Makefile

Compile docker image containers and instantiate the high-availability orchestration network stack:

make build
make run

This automated bootstrap sequence initializes PostgreSQL databases, creates core relational schemas, maps persistent tracking volumes, and starts the Prometheus/Grafana infrastructure logging containers.

Testing & Validation Commands

Execute the isolated unit testing matrix inside production container wrappers:

make test

Enforce strict static quality analysis checks and Black code styling layout rules:

make lint
make format

To validate system backpressure constraints, execute high-velocity tick ingestion simulation stress tests manually:

docker compose exec trading-engine pytest tests/stress/test_event_storm.py -vv

Backtesting & Replay Execution

Run historical backtest optimization simulation runs directly from the terminal interface:

bash scripts/run_backtest.sh

Finalized performance analytical payloads, trade execution journals, and equity curve plot images are written directly to /app/backtest_outputs/.

License

This architecture is distributed under the conditions specified inside the system LICENSE parameters.