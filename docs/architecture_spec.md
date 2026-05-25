# Architectural Specification Manual

This document describes the deterministic design of the Apex XAUUSD engine. The project separates trading decisions from external services so market logic can be tested without needing live network, broker, or database connections.

## Architecture Pattern

The code follows a ports-and-adapters style:

```text
+------------------------- Infrastructure -------------------------+
| Twelve Data WebSocket        PostgreSQL / SQLAlchemy             |
| Historical REST fetchers     Prometheus / structured logging     |
+-------------------------------+----------------------------------+
                                |
                                v
+-------------------------- Application --------------------------+
| Priority event bus                                                |
| Runtime state manager (single source of truth)                    |
| Analytics -> confirmation -> scoring -> risk -> execution         |
+------------------------------------------------------------------+
```

## Core Layers

### Domain

`src/core/domain/` contains immutable data structures and enums used across the engine: ticks, candles, setup nodes, risk snapshots, execution reports, and runtime state containers.

### Events

`src/core/events/` provides the asynchronous priority bus that routes market ticks, candle closes, operational events, and execution updates between subsystems.

### Analytics

`src/analytics/` contains market microstructure calculations: session classification, volatility regime detection, liquidity pool tracking, and structure break detection.

### Strategy

`src/strategy/` coordinates candidate setup detection, confirmation scoring, multi-timeframe alignment, setup lifecycle tracking, and final ranking.

### Execution

`src/execution/` handles position sizing, drawdown protection, safety filters, order routing, broker abstractions, and order lifecycle validation.

### Infrastructure

`src/infrastructure/` holds transport, database, telemetry, and feed adapters. These modules should stay thin so core strategy behavior remains testable.

## Data Flow

```text
Inbound tick or candle
        |
        v
Validation and normalization
        |
        v
Runtime state update
        |
        v
Analytics engines
        |
        v
Setup detection and confirmation
        |
        v
Scoring and risk firewall
        |
        v
Execution router or rejection audit
```

## Integration Cleanup Notes

The repository now includes compatibility modules for missing domain models, state helpers, strategy validators, execution guards, database model exports, and test factories. These modules are intentionally lightweight and preserve the existing strategy files instead of rewriting their logic.
