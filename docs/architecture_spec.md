# Architectural Specification Manual

This manual details the deterministic design logic of the **Apex-XAUUSD Detector V1.0** engine. The platform is designed to isolate calculation steps from network constraints, keeping core strategy operations clear of processing delays.

## Hexagonal Architecture Layer Pattern

The core application utilizes a **Hexagonal Architecture (Ports and Adapters)** structure. Domain models and analytical modules represent pure synchronous functions, decoupled from external drivers like data networks, databases, and message systems.


   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
   â”‚                     INFRASTRUCTURE                      â”‚
   â”‚                                                         â”‚
   â”‚   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”         â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   â”‚
   â”‚   â”‚ Twelve Data WS    â”‚         â”‚ PostgreSQL Engine â”‚   â”‚
   â”‚   â”‚ Inbound Adapter   â”‚         â”‚ Persistent Driver â”‚   â”‚
   â”‚   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜         â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â–²â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜   â”‚
   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                 â”‚                             â”‚
                 â–¼                             â”‚
   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
   â”‚                       APPLICATION                       â”‚
   â”‚                                                         â”‚
   â”‚   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   â”‚
   â”‚   â”‚ Asynchronous Event Bus (Priority Processing)     â”‚   â”‚
   â”‚   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜   â”‚
   â”‚                            â”‚                            â”‚
   â”‚                            â–¼                            â”‚
   â”‚   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   â”‚
   â”‚   â”‚ Central State Manager Snapshot Registry (SSOT)  â”‚   â”‚
   â”‚   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜   â”‚
   â”‚                            â”‚                            â”‚
   â”‚                            â–¼                            â”‚
   â”‚   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   â”‚
   â”‚   â”‚ Pure Synchronous Microstructure Analytics Core  â”‚   â”‚
   â”‚   â”‚ (Regime -> Session -> Structure -> Discovery)   â”‚   â”‚
   â”‚   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜   â”‚
   â”‚                            â”‚                            â”‚
   â”‚                            â–¼                            â”‚
   â”‚   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   â”‚
   â”‚   â”‚ Pre-Execution Risk Management Firewall Gate     â”‚   â”‚
   â”‚   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜   â”‚
   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜


### 1. The Core Domain Layer (`src/core/domain/`)
The foundation of the architecture. This layer contains no project dependencies or internal module imports. It defines the application's base data primitivesâ€”such as `TickNode` and `CandleNode` recordsâ€”using explicit `__slots__` arrays to minimize memory allocation overhead.

### 2. The Pure Analytics Layer (`src/analytics/`)
Contains the mathematical execution rules of the strategy. It calculates market attributes like volatility regimes, session zones, and market structures (BOS/MSS) synchronously. This layer processes data profiles sequentially, matching inputs to structural constraints without introducing network tracking dependencies.

### 3. The Application Pipeline Gateway (`src/core/events/`)
Manages internal messaging across modules asynchronously. It relies on an active priority queue (`asyncio.Queue`) running via `uvloop`, ensuring high-frequency data ingestion events bypass slower database writes or telemetry updates.

### 4. The Infrastructure Storage & Network Layer (`src/infrastructure/`)
Contains low-level connection adapters and client transports. It manages data ingestion from Twelve Data WebSockets, database transactions via SQLAlchemy pools, and Prometheus performance metrics exports.

## Core Multi-Timeframe Matrix Propagation

To maintain consistent trend tracking across timeframes without lookahead leaks, data parameters propagate through a structured downsampling cascade:


[Inbound Network Tick WebSocket Event]
                 â”‚
                 â–¼
    ( Enforce Invariant Filters ) â”€â”€â–º Reject Malformed / Stale Packets
                 â”‚ Pass
                 â–¼
  [ Append 1M Pre-allocated Buffer ] â”€â”€â–º Update Tick Velocity Primitives
                 â”‚
                 â–¼
  [ Cross 15-Minute Clock Boundary ] â”€â”€â–º Recalculate ATR, Verify Local BOS/MSS Lines
                 â”‚
                 â–¼
  [ Cross 1-Hour Clock Boundary ]    â”€â”€â–º Update Macro Trend Direction Bias Vectors


This structural tracking loop ensures that multi-timeframe analytics align correctly, keeping the backtesting and live execution engines perfectly synchronized.