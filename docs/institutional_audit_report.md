# Institutional Trading Systems Audit Report

**Repository:** `apex_xauusd_engine`
**Audit date:** 2026-05-27
**Baseline audited:** `4c5aa3a` (`main`, prior to audit repairs)
**Scope:** Source tree, runtime paths, MT5 adapter, strategy logic, state/risk/execution code, backtesting modules, infrastructure, deployment assets, and tests.

## Executive Verdict

This repository is **not an institutional-grade automated trading engine** and is **not fit for live capital deployment**. It is currently a protected MT5 demo execution harness surrounded by substantial architectural scaffolding. The code can read local MetaTrader 5 data and can submit a deliberately authorized demo order. That is useful progress for connectivity testing, but it must not be confused with a validated trading system.

The central problem is not a minor bug. The project claims deterministic SMC/ICT analysis, event-driven execution, realistic replay, risk firewalls, persistence, deployment infrastructure, and monitoring, while the operative path either bypasses those facilities or implements them as shallow heuristics. There is no evidence of statistical edge, no realistic cost-aware backtest, no durable recovery model, and no broker-grounded portfolio risk control.

**Deployment classification:** demo-only experimental prototype.
**Live-capital recommendation:** prohibited until the critical blockers in this report are resolved and independently validated.

# SECTION 1 - ARCHITECTURE ANALYSIS

## Actual Implemented Architecture

There are two architectures in the repository:

1. The advertised architecture in `docs/architecture_spec.md`: inbound data -> priority event bus -> runtime state -> analytics -> setup/confirmation -> scoring/risk -> execution router -> broker/database/telemetry.
2. The architecture that actually sends a demo order: `scripts/mt5_intelligent_demo_runner.py` directly constructs and directly calls state, detector, confirmation, scoring, risk, and `MT5BrokerGateway`.

The executable MT5 path does not start the event bus, does not use `HighSpeedExecutionOrchestrator`, does not persist trades or risk state to the SQLAlchemy model layer, and does not expose a functioning API/control-plane link. It is a single-process polling loop.

## Actual Live Demo Event Flow

```text
Local Windows MT5 terminal
        |
        | synchronous polling through MetaTrader5 Python module
        v
scripts/mt5_intelligent_demo_runner.py
        |
        | read closed M1/M15/H1/H4 candles and current quote
        v
MarketSetupOrchestrator (called directly, not through EventBus)
        |
        | M1 pivot -> liquidity pool -> quote crosses pool
        v
LiquiditySweepReversalDetector
        |
        | fixed price stop/target reversal candidate
        v
TradeConfirmationOrchestrator (called directly)
        |
        | body/wick heuristics + crude timeframe bias + session gate
        v
TradeScoringOrchestrator (called directly)
        |
        | arbitrary weighted score
        v
RiskManagementOrchestrator (called directly)
        |
        | fixed-equity/contract sizing + static spread/RR gates
        v
MT5BrokerGateway.route_order_submission()
        |
        | explicit demo authorization only
        v
XM MT5 demo account
```

## Module Responsibilities As Implemented

| Area | Claimed Purpose | Actual Capability |
| --- | --- | --- |
| `src/core/events/` | priority event-driven routing | present but bypassed by executable MT5 runner; unsafe for equal priorities |
| `src/strategy/state_manager.py` | central durable state | in-memory snapshot/journal only; restart loses operational state |
| `src/analytics/` | structure, liquidity, regimes, sessions | simple M1 pivots/sweep heuristic and time-window gate; regime engine not connected to live decision path |
| `src/strategy/` | deterministic SMC/ICT decision engine | reversal placeholder with fixed stop/target plus heuristic confirmation/scoring |
| `src/execution/` | broker-independent execution and risk | risk helpers plus unused router; active runner sends straight to MT5 gateway |
| `src/infrastructure/broker/` | MT5 broker adapter | functioning local demo connector and basic guard rails; no durable reconciliation |
| `src/backtest/`, `src/backtesting/` | realistic simulation | incomplete split namespace and optimistic fill stub |
| `src/infrastructure/database/` | persistence | broad model catalogue with no demonstrated write/read integration in trading path |
| `src/api/` | control and monitoring | lightweight endpoints mutating private fields, not proven to stop execution |
| `deploy/`, workflows | deployment and CI/CD | inconsistent with repository paths and MT5 runtime; currently not a deployable/live pipeline |

## Current Strengths

- The MT5 connector is explicitly guarded for a demo account and small volume, reducing immediate experimentation risk.
- The latest runner prevents normal submission of a second Gold position in a single broker session and keeps automatic trailing behind explicit authorization.
- Core domain records are separated from broker code, which is a reasonable basis for future testing.
- Several in-memory collections are bounded, and live polling uses completed candles rather than treating forming bars as confirmed signals.
- The code is readable enough to audit; dangerous assumptions are visible rather than concealed in opaque binaries.

## Current Weaknesses

- The primary architectural promises are not the runtime reality.
- Signal generation has no demonstrated statistical edge and uses arbitrary constants.
- Risk sizing is not broker/account accurate.
- Simulation results cannot be trusted as profitability evidence.
- No durable execution state or restart reconciliation exists.
- CI and deployment definitions cannot validate the current project as written.
- MT5 ties the execution path to a local Windows terminal, contradicting the container/cloud topology.

## Hidden Architectural Risks

- The unused event-driven design and active direct-call runner can evolve independently, creating two incompatible systems with inconsistent behavior.
- Large database/API/deployment surfaces create confidence and maintenance burden without protecting an actual trade.
- A locally saved trailing plan can disappear during a crash while a broker position remains open.
- Safety controls represented in state or API are not necessarily on the order-send path.
- Performance language in docstrings implies low-latency capability that has not been measured and is incompatible with synchronous terminal polling.

# SECTION 2 - CRITICAL FLAWS

## Severity Scale

| Severity | Meaning |
| --- | --- |
| Critical | Can cause unbounded or invalid trading behavior, false edge conclusions, or loss of control; blocks any live-capital use. |
| High | Can materially distort decisions/execution or prevent safe operation; blocks trustworthy demo validation. |
| Medium | Creates maintainability, latency, or scaling failure that must be corrected before expansion. |

## Ranked Findings

### F-01: No statistically validated trading strategy exists

**Severity:** Critical
**Location:** `src/strategy/reversal_detectors.py:1-15`, `src/strategy/confirmation_core.py:61-146`, `src/strategy/scoring_matrix.py:55-147`

The signal core labels itself SMC/ICT or institutional, but its live reversal detector considers any crossed liquidity pool a reversal, uses the quote midpoint as entry, and chooses fixed `$2` stop and `$6` target distances. Confirmation assigns weighted scores from candle-body and alignment heuristics; scoring then weights more heuristics with arbitrary thresholds. These numbers are neither calibrated nor linked to out-of-sample evidence.

**Why dangerous:** A technically functioning execution loop can repeatedly submit trades based on an unproven narrative rather than an edge.
**Failure mode:** Confidence scores appear rigorous while trade expectancy is negative after spread/slippage.
**Long-term consequence:** Capital and development effort are optimized around noise; later complexity makes false logic harder to replace.
**Affects:** profitability, statistical validity, execution quality, maintainability.

### F-02: Backtesting is economically invalid

**Severity:** Critical
**Location:** `src/backtesting/fill_engine.py:8-28`, `src/backtesting/backtest_config.py:7-13`, `src/backtest/simulator_interface.py:16-58`

The fill engine calculates a slippage number but still fills at `current_mid`. It has no bid/ask execution, spread expansion, commissions, order rejection, partial fills, gap-through-stop handling, exit lifecycle, execution delay, or news volatility model. `latency_ms` exists in configuration but is not applied.

**Why dangerous:** A strategy with no edge can look profitable if buys and sells execute at an impossible midpoint without actual adverse costs.
**Failure mode:** Inflated win rate, RR, Sharpe, and expected value; optimization selects the strategy most favored by simulation defects.
**Long-term consequence:** Any parameter fitting or AI training based on these results is invalid.
**Affects:** profitability, statistical validity, maintainability.

### F-03: Position sizing is not grounded in the account or traded symbol contract

**Severity:** Critical
**Location:** `src/execution/position_sizer.py:8-34`, `src/execution/risk_firewall.py:34-49`, `scripts/mt5_intelligent_demo_runner.py:164-170`

The risk engine defaults to `$10,000` equity and a hardcoded `100` contract size. The live demo runner constructs this risk engine without passing current MT5 account equity or symbol tick size/tick value/volume step. The maximum `0.01` lot cap limits demo exposure, but does not make calculated risk correct.

**Why dangerous:** Actual currency risk can materially differ from the risk displayed by the engine.
**Failure mode:** Incorrect lots or incorrect reported risk percentage as account equity, broker contract specification, or symbol suffix changes.
**Long-term consequence:** Portfolio risk cannot be audited or safely scaled.
**Affects:** profitability, stability, execution quality, statistical validity.

### F-04: The claimed event-driven trading architecture is not the executable architecture

**Severity:** Critical
**Location:** `scripts/mt5_intelligent_demo_runner.py:143-397`, `scripts/launch_engine.py:26-67`, `src/execution/execution_router.py:51-210`, `docs/architecture_spec.md:49-71`

The MT5 runner calls each layer directly. The master launcher instantiates an event bus and lifecycle manager but registers no operational subsystems. The execution router is unused by the runner.

**Why dangerous:** Architectural safety properties, including ordering, halt routing, reconciliation, and audit event delivery, are assumed but never exercised in the order path.
**Failure mode:** A tested component pipeline differs from the process that trades; safety fixes in one route do not protect another.
**Long-term consequence:** Divergent code paths and fragile integration work.
**Affects:** stability, maintainability, scalability, execution quality.

### F-05: Event bus delivery is nondeterministic and can silently lose subscriber failures

**Severity:** High
**Location:** `src/core/events/event_bus.py:19-72`

Queue entries use `(priority, (event_type, payload))`. When two events have the same priority, Python may need to compare event type/payload values that are not guaranteed orderable. Subscriber exceptions are captured by `asyncio.gather(..., return_exceptions=True)` and ignored.

**Why dangerous:** Equal-priority market/execution traffic can break processing or fail silently.
**Failure mode:** A risk or execution subscriber crashes and the rest of the engine continues as if validation occurred.
**Long-term consequence:** Intermittent, hard-to-reproduce failures under normal traffic.
**Affects:** latency, stability, maintainability, execution quality.

### F-06: MT5 execution and recovery are fragile under ambiguous failures

**Severity:** Critical
**Location:** `src/infrastructure/broker/mt5_gateway.py:101-240`, `src/execution/execution_router.py:123-210`, `scripts/mt5_intelligent_demo_runner.py:288-329`

The gateway uses a single fixed IOC filling mode, no demonstrated broker-specific filling-mode selection, no durable idempotency, no transactional record linking send to observed position, and no recovery if a terminal/network error occurs after the broker accepted an order. The router expects streamed execution lifecycle events, but the MT5 implementation does not provide them.

**Why dangerous:** During volatility or terminal instability the system can be uncertain whether it holds risk.
**Failure mode:** Duplicate retry, untracked position, unprotected position, or rejected modification at the worst time.
**Long-term consequence:** The system cannot be safely unattended.
**Affects:** profitability, latency, stability, execution quality.

### F-07: Risk controls reset on restart and are not fed by broker outcomes

**Severity:** Critical
**Location:** `src/execution/drawdown_protection.py:6-26`, `src/execution/risk_firewall.py:125-130`, `src/strategy/state_manager.py:25-130`

Daily loss and consecutive loss rules live in volatile memory. No live path demonstrates restoration from broker trade history or persistence. The MT5 runner does not call the outcome notification logic when trades close.

**Why dangerous:** Restarting the process can clear the very risk lockouts intended to prevent loss spirals.
**Failure mode:** System resumes trading after daily drawdown or consecutive-loss limit should have halted it.
**Long-term consequence:** Risk limits become cosmetic rather than enforceable.
**Affects:** profitability, stability, execution quality.

### F-08: SMC/ICT concepts are not formally defined

**Severity:** Critical
**Location:** `src/analytics/structure_engine.py:31-89`, `src/analytics/liquidity_engine.py:24-106`, `src/strategy/reversal_detectors.py:6-15`

The code implements only a three-candle pivot and a midpoint crossing a small band. It does not deterministically implement FVG, order blocks, inducement, displacement relative to volatility, a properly stateful BOS/MSS transition model, or a sweep-and-reclaim condition. A crossed pool is consumed immediately.

**Why dangerous:** Discretionary chart vocabulary becomes untestable code; equivalent market situations can be labelled inconsistently.
**Failure mode:** Random price crossings produce "institutional" signals with no causal or statistical meaning.
**Long-term consequence:** Strategy research cannot be reproduced, falsified, or improved responsibly.
**Affects:** profitability, statistical validity, maintainability.

### F-09: Multi-timeframe state is crude and potentially contradictory

**Severity:** High
**Location:** `src/strategy/setup_detector.py:45-53` and directional-bias logic; `src/strategy/confirmation_core.py:92-105`

Only M1 runs actual structure/liquidity detection. Higher-timeframe "bias" is effectively determined from small recent close buffers, with equal vote weight and no temporal synchronization or hierarchy. There is no conflict-resolution policy for higher-timeframe context versus entry-timeframe reversal.

**Why dangerous:** The same entry may pass or fail solely because arbitrary buffer endpoints differ.
**Failure mode:** Unstable bias flips and contradictory trade gating around candle updates.
**Long-term consequence:** Difficult-to-reproduce results and curve-fitting temptation.
**Affects:** profitability, statistical validity, stability.

### F-10: Market-structure/liquidity processing leaks memory and scales poorly

**Severity:** High
**Location:** `src/analytics/structure_engine.py:18-29`, `src/analytics/liquidity_engine.py:18-106`, `src/strategy/setup_detector.py:49-54`

Candles are bounded, but high pivots, low pivots, and the orchestrator structural-pivot list are not. Tick sweep detection linearly scans active pools. Pool retirement is based on crossing, not expiry or a bounded relevance horizon.

**Why dangerous:** Long-running sessions accumulate stale structures and increase per-tick work.
**Failure mode:** Memory growth and increasing quote-processing delays as historical pools accumulate.
**Long-term consequence:** Degradation becomes most visible in persistent services and active markets.
**Affects:** latency, scalability, stability, maintainability.

### F-11: Live market regime and news protection are effectively unwired

**Severity:** High
**Location:** `src/analytics/regime_detection.py:16-113`, `src/strategy/confirmation_core.py:69-75`, `scripts/mt5_intelligent_demo_runner.py:164-266`

There is a regime classifier, but the active runner does not drive it into central state. The confirmation rule checks for `POST_NEWS_CHAOS`, while runtime state normally remains `UNKNOWN`. The classifier also has no economic calendar and cannot truly identify CPI/FOMC risk.

**Why dangerous:** The presence of a protection rule creates false security during exactly the periods when spread and slippage explode.
**Failure mode:** Entries remain possible in abnormal or news-driven volatility.
**Long-term consequence:** Tail-loss exposure is understated.
**Affects:** profitability, stability, execution quality.

### F-12: State is not a safe single source of truth

**Severity:** High
**Location:** `src/strategy/state_manager.py:25-130`, `src/api/v1/operational_control.py:15-70`, `scripts/mt5_intelligent_demo_runner.py:38-49`

State is in-memory only, health updates explicitly bypass its lock, API halt endpoints write undeclared private attributes rather than setting the trading halt field used by risk checks, and broker profit currency is mapped into a property named `floating_pnl_pips`.

**Why dangerous:** Monitoring and safeguards can disagree with broker reality.
**Failure mode:** Dashboard indicates halt while order route remains enabled; risk metrics mix dollars and pips.
**Long-term consequence:** Invalid audits and unsafe operator controls.
**Affects:** stability, maintainability, execution quality, statistical validity.

### F-13: Deployment and continuous integration are nonfunctional representations

**Severity:** High
**Location:** `.github/workflows/test_suite.yml:3-29`, `.github/workflows/deployment_pipeline.yml:3-25`, `deploy/docker-compose.yml:59-130`, `deploy/Dockerfile.app:17-53`

Workflows target `master` while active work is on `main`, install a missing `requirements.txt`, and build a root `Dockerfile` that does not exist. Compose refers to missing paths and assumes a Linux container trading service, while the MT5 execution adapter needs a local terminal environment.

**Why dangerous:** No automatic verification protects main, and deployment assets falsely imply operational readiness.
**Failure mode:** Defective changes merge unnoticed; deployment fails or runs a process unrelated to MT5 trading.
**Long-term consequence:** Operational risk and architectural distraction.
**Affects:** stability, scalability, maintainability.

### F-14: Test coverage tests vocabulary and trivial mechanics rather than trading safety

**Severity:** High
**Location:** `tests/`, particularly `tests/benchmark/test_latency_paths.py`, `tests/integration/test_pipeline_integration.py`, `tests/unit/test_risk.py`

There are no tests for broker ambiguity, rejection/retry reconciliation, restart recovery, order filling modes, true account-based lot sizing, spread/slippage PnL, exit fills, multi-timeframe conflicts, SMC definitions, or out-of-sample statistics. The benchmark times only a metrics recorder, not the market-to-order path. The checked environment does not currently have `pytest` installed.

**Why dangerous:** Passing tests can create confidence without testing a trade-loss failure mode.
**Failure mode:** Critical behavior regresses silently.
**Long-term consequence:** System becomes harder to change safely.
**Affects:** profitability, latency, stability, maintainability, execution quality, statistical validity.

# SECTION 3 - PERFORMANCE ANALYSIS

## Latency Bottlenecks

| Path | Bottleneck | Effect |
| --- | --- | --- |
| MT5 runner loop | synchronous terminal calls inside async loop | blocks all processing during terminal IPC delay |
| Candle refresh | up to four timeframe history reads after each unique tick | repetitive I/O; expensive relative to candle close frequency |
| Liquidity scan | linear scan of every active pool for each tick | runtime cost grows with unbounded pool history |
| State journal | immutable snapshots appended for frequent updates | memory churn and allocation cost |
| Event bus if enabled | unbounded queue and concurrent subscriber execution without observability | backpressure/failure blindness under load |
| Logging | synchronous structured output in hot analytical paths | potential I/O amplification during volatile event bursts |

## Async and Concurrency Model

The apparent asynchronous architecture is misleading. The live MT5 path is a single polling coroutine making synchronous calls to a terminal API. There is no separated market-data ingestion task, decision queue, execution acknowledgement service, or durable reconciliation worker in the trading runner. In one process, this limits races but also means a delayed terminal call delays protection and signal processing. Across two runner processes, the in-memory single-position logic cannot provide an atomic account lock.

## Expected Latency Estimates

These are engineering estimates based on control flow, not measured production benchmarks:

| Measure | Expected Baseline | During Terminal/Volatility Stress |
| --- | --- | --- |
| Quote observation delay | roughly 0 to 250 ms because runner defaults to a 250 ms poll | 250 ms to seconds when terminal IPC or repeated calls stall |
| Setup detection after a triggering quote | low milliseconds after quote is received for small histories | grows linearly with active liquidity pools and logging volume |
| Candle-confirmed rule availability | inherently waits for M1 close, up to nearly 60 seconds by design | additional terminal polling delay |
| Order send/acknowledgement | unknown; uninstrumented terminal/broker round trip, plausibly tens to hundreds of ms | rejection, requote, timeout, or ambiguous execution; potentially seconds |
| Stop-management response | only on newly observed closed M1 candle while runner is alive | delayed or absent if runner exits, terminal stalls, or plan file is lost |

No meaningful "low latency" claim can be made until end-to-end market-event-to-broker-acknowledgement percentiles are instrumented under realistic stress.

## Scalability Ceiling

This is intentionally a single-symbol local-terminal demo loop. Adding instruments or concurrent strategies would magnify synchronous polling, memory growth, duplicated state, and broker race risks. Scaling infrastructure before proving a single-instrument edge would be wasted engineering.

# SECTION 4 - BACKTEST VALIDITY ANALYSIS

## Feature Coverage

| Required Realism Feature | Current Status | Evidence |
| --- | --- | --- |
| Spread expansion | Not modeled | fills at midpoint; no dynamic bid/ask input |
| Slippage | Computed as a field, not charged to price | `fill_engine.py:10-21` |
| Latency | Configuration only, not used | `backtest_config.py:13` |
| Order rejection | Not modeled for fills | simulator fills market orders immediately |
| Partial fills | Not modeled | requested lot equals filled lot |
| Liquidity gaps | Not modeled | pending trigger assumes fill at entry inside candle range |
| Execution delay | Not modeled | fill uses submission timestamp |
| News volatility | Not modeled | no shock/spread/rejection simulation |
| Commission/swap | Not modeled | no cost ledger |
| Stop/TP lifecycle | Not demonstrated | no complete path from entry through exits and trade statistics |

## Validity Verdict

Backtest results from the current engine would be misleading and must not be used to claim profitability, optimize parameters, or train AI. A strategy can be made to appear profitable merely because execution occurs at an impossible midprice and exit/cost behavior is absent. Before researching SMC details, the simulation must charge realistic spread/slippage/fees, model fills and exits deterministically, and be calibrated against recorded MT5 demo executions.

# SECTION 5 - RISK ENGINE ANALYSIS

| Risk Requirement | Current Status | Assessment |
| --- | --- | --- |
| Drawdown protection | present in memory only | resets/reconstructs incorrectly after restart |
| Dynamic sizing | not broker-grounded | default equity and contract value invalidate reported risk |
| Volatility adaptation | effectively absent live | regime classifier not connected to runner |
| Exposure control | basic one-Gold-position check | useful safety, not atomic across processes/accounts |
| Session lockouts | simple killzone gate | not a proven risk control; configuration rigid |
| Spread filters | static conversion | pip/value conversion may not match broker symbol digits |
| Regime shifts | no active live state update | protection appears implemented but is not operational |
| Consecutive losses | volatile counter only | no broker-history restore or closed-trade wiring |

## Risk Verdict

The risk layer is an entry filter prototype, not an institutional risk engine. For demo use, the hard lot cap is the meaningful safety control. Before any real-money discussion, the system needs broker-derived contract sizing, persisted loss/exposure state, account reconciliation on startup and periodically, stale-quote/spread/market-state checks immediately before execution, and an enforceable hard kill switch on the broker-send route.

# SECTION 6 - SMC/ICT FORMALIZATION ANALYSIS

| Concept | Deterministic Mathematical Definition Implemented? | Current Implementation Problem |
| --- | --- | --- |
| Liquidity sweep | No | any quote crossing a pivot-derived band; no reclaim, dwell, volume, or close confirmation |
| BOS | Weak/incomplete | close beyond current pivot; transition state does not formally establish trend legs |
| MSS | Weak/incomplete | label depends on presence of opposite stored pivot rather than explicit trend-state invalidation |
| FVG | No usable live logic | database/model naming exists without decision implementation |
| Displacement | No robust definition | candle-body heuristic, not volatility-normalized impulse with tested threshold |
| Inducement | No | absent from active logic |
| Order block | No usable live logic | model/scaffolding exists without validated setup generation |
| Structure shift | No institutional formalization | pivot crossing heuristic only |

## Strategy Validity Verdict

SMC/ICT terminology is discretionary unless every concept is translated into reproducible rules with data definitions, timing rules, invalidation, costs, and falsifiable tests. At present the naming is much more mature than the signal science. That is dangerous because sophisticated vocabulary can hide an ordinary, unvalidated price-crossing reversal rule.

The correct research order is:

1. Define a minimal hypothesis in mathematical terms.
2. Define data timing and no-lookahead guarantees.
3. Define realistic fill and risk behavior.
4. Test out-of-sample with costs and robustness analysis.
5. Only retain concepts that add measurable incremental expectancy.

# SECTION 7 - OVERENGINEERING ANALYSIS

## Complexity Without Proven Benefit

| Complexity | Why It Is Premature or Misleading |
| --- | --- |
| 20+ ORM table models and re-export files | no active trading persistence/recovery path proves they protect orders or research validity |
| API/dashboard/control facade | not wired into the active MT5 runner safety path |
| Prometheus/Grafana/Postgres/Redis/Nginx topology | deployment cannot run current MT5 execution path and edge is unproven |
| Event-driven language and subsystem base classes | active runner bypasses bus and router |
| "Institutional," "microsecond," "high-speed," and "AI compatible" framing | no end-to-end performance or statistical evidence |
| Split `backtest` and `backtesting` packages | increases confusion before a valid simulator exists |

## What Should Be Simplified

- Treat the MT5 demo runner as the only operational runtime until a second architecture is truly wired and tested.
- Keep one canonical backtest package and one executable simulation flow.
- Replace broad SMC vocabulary with a small number of fully specified candidate signals.
- Do not add services, databases, or AI until a cost-aware experiment shows durable edge.
- Keep the one-position demo guard and explicit authorization, but make risk/execution state truthful and recoverable.

## Architectural Vanity Verdict

The repository currently spends too much complexity on the appearance of a hedge-fund platform and too little on the hard foundations: data validity, unbiased simulation, broker truth, deterministic rules, and recovery after failure. Institutional systems are not defined by the number of layers; they are defined by falsifiable research and controls that still work under stress.

# SECTION 8 - IMPROVEMENT ROADMAP

## Priority 1: Statistical Validity

### 1.1 Freeze live-strategy claims and specify the signal mathematically

**Why it matters:** No infrastructure can rescue a negative-expectancy strategy.
**Expected benefit:** Reproducible research and honest pass/fail criteria.
**Implementation strategy:** Write a strategy specification defining sweep, reclaim, structure state, entries, exits, timing, and invalidation; implement only those rules; create labelled deterministic examples and no-lookahead tests.
**Tradeoff:** Fewer attractive concepts and slower initial progress; much higher truthfulness.
**Risk if ignored:** All later work optimizes a fiction.

### 1.2 Build a cost-aware experiment ledger and out-of-sample evaluation

**Why it matters:** Profit must survive spread, slippage, fees, latency, and unseen periods.
**Expected benefit:** Credible evidence for or against continued strategy investment.
**Implementation strategy:** Record broker quotes/executions in demo, create walk-forward splits, evaluate expectancy and drawdown with costs and confidence intervals.
**Tradeoff:** Requires data collection time.
**Risk if ignored:** Curve-fit results drive order execution.

## Priority 2: Execution Robustness

### 2.1 Create broker-truth reconciliation and execution ambiguity handling

**Why it matters:** A send timeout may still mean an order exists.
**Expected benefit:** Fewer duplicate/unmanaged positions.
**Implementation strategy:** Persist execution intent before send, reconcile by account/order/deal history after every ambiguous outcome and on startup, and prohibit new trades while state is uncertain.
**Tradeoff:** More conservative entry availability.
**Risk if ignored:** Unknown live exposure.

### 2.2 Validate symbol execution rules immediately before send/modify

**Why it matters:** Fill mode, stops level, freeze level, tick size, quote freshness, and volume step are broker-specific.
**Expected benefit:** Lower rejection rate and correct order constraints.
**Implementation strategy:** Read MT5 symbol specification, quantize price/volume, choose supported filling mode, pre-check stop modifications, and report retcodes.
**Tradeoff:** More broker-adapter logic.
**Risk if ignored:** Failure during fast markets.

## Priority 3: Risk Engine

### 3.1 Replace fixed sizing constants with account/symbol-derived sizing

**Why it matters:** Risk percentages must represent currency actually lost at stop.
**Expected benefit:** Truthful position sizing and auditable caps.
**Implementation strategy:** Pull account equity, tick size, tick value, volume min/step/max and calculate risk per stop distance; retain a hard demo cap.
**Tradeoff:** Dependency on reliable broker metadata; fail closed if absent.
**Risk if ignored:** Mis-sized risk.

### 3.2 Persist and restore risk locks

**Why it matters:** Restart cannot erase drawdown rules.
**Expected benefit:** Durable daily/consecutive-loss control.
**Implementation strategy:** Reconcile closed deals and open positions at startup; persist risk events atomically; enforce halt at final order boundary.
**Tradeoff:** Requires a small, tested persistence layer.
**Risk if ignored:** Loss limits are cosmetic.

## Priority 4: Backtest Realism

### 4.1 Implement deterministic adverse fills and trade lifecycle

**Why it matters:** Midprice fills invalidate edge estimates.
**Expected benefit:** Less optimistic, more transferable evaluation.
**Implementation strategy:** Execute BUY at ask plus adverse slippage and SELL at bid minus adverse slippage; model stops/targets, gap resolution, spread series, fees, latency, rejection, and partial fill policy.
**Tradeoff:** Backtests will look worse; that is information, not failure.
**Risk if ignored:** Misleading profitability.

### 4.2 Calibrate simulation from demo execution records

**Why it matters:** Assumed costs are still assumptions.
**Expected benefit:** Broker-specific realistic distributions.
**Implementation strategy:** Record quote-at-intent versus fill/retcode/latency and derive stress scenarios.
**Tradeoff:** Requires sufficient sample size.
**Risk if ignored:** Simulation remains decorative.

## Priority 5: State Consistency

### 5.1 Establish one authoritative runtime and durable state boundary

**Why it matters:** Direct-call and event-driven routes cannot both claim safety unless equivalent and tested.
**Expected benefit:** Clear recovery and operator control.
**Implementation strategy:** Choose a canonical runtime; route halt/reconciliation/order state through it; persist minimal trade/risk state; remove or mark unused facade components.
**Tradeoff:** Reduces architectural breadth temporarily.
**Risk if ignored:** Hidden coupling and safety bypass.

## Priority 6: Performance Optimization

### 6.1 Measure before optimizing

**Why it matters:** Current "low-latency" claims are unsupported and Gold demo execution is not HFT.
**Expected benefit:** Work targets real bottlenecks.
**Implementation strategy:** Record end-to-end percentiles for quote receipt, decision, order check, send, acknowledgement, reconciliation, and stop modification. Refresh candles on timeframe boundaries rather than every unique quote. Bound/prune structures.
**Tradeoff:** Adds instrumentation and initially exposes poor numbers.
**Risk if ignored:** Optimizing irrelevant code while IPC dominates.

## Priority 7: Infrastructure Scaling

### 7.1 Remove false deployment claims until architecture is deployable

**Why it matters:** MT5 local terminal execution is not the Linux-container design in compose.
**Expected benefit:** Honest operational model and working CI.
**Implementation strategy:** Repair CI on `main` first; either isolate MT5 Windows agent deployment or select a broker API suitable for services; only add database/telemetry services actually consumed.
**Tradeoff:** Smaller-looking platform.
**Risk if ignored:** Deployment failure and unsafe operational assumptions.

## Priority 8: AI Integration

AI must be deferred. There is no safe role for an AI model until deterministic data definitions, realistic simulation, risk controls, and execution recovery are trustworthy. Training or prompting against invalid backtests would industrialize error rather than create edge.

## Immediate Repair Set Authorized by This Audit

The first implementation pass should remain narrow and non-cosmetic:

1. Make event ordering deterministic and surface subscriber failures, because a claimed event architecture must not silently corrupt or discard critical decisions.
2. Make simulated fills execute adversely using explicit bid/ask spread and slippage inputs, because a backtest that does not pay its calculated costs is objectively false.
3. Add tests for these corrected invariants.

These changes do not validate the trading strategy and do not make the engine safe for live money. They remove two fundamental lies from the infrastructure while larger broker-truth risk and execution-recovery work is designed.

## Repair Pass 1 Implementation Record

### R-01: Deterministic event ordering and visible handler failure accounting

**Changed files:** `src/core/events/event_bus.py`, `tests/integration/test_pipeline_integration.py`

**Before:** Priority queue ordering depended on comparing event payload structures whenever two messages shared a priority, and subscriber exceptions were gathered then ignored.
**After:** Every publication receives a monotonic FIFO sequence after its priority, the queue has a finite capacity, and failed subscribers increment a visible failure counter while other subscribers may continue processing.

**Performance impact:** Constant-time sequence assignment adds negligible overhead; bounded queues prevent unlimited memory growth but require future backpressure health integration.
**Trading impact:** This does not generate better trades, but it prevents event ordering from depending on unorderable market payloads and makes failed validation handlers observable.
**Reliability impact:** Material improvement for any future event-driven runtime; the active MT5 direct-call runner still must be consolidated into that runtime or explicitly treated separately.

### R-02: Adverse execution-cost application in backtest fills

**Changed files:** `src/backtesting/backtest_config.py`, `src/backtesting/fill_engine.py`, `src/backtest/simulator_interface.py`, `tests/unit/test_backtest_fill_engine.py`

**Before:** A volatility slippage value was recorded, but market orders still filled at the midpoint with no spread charge.
**After:** A backtest must supply a positive spread and pip conversion. BUY fills are shifted upward and SELL fills downward by half-spread plus configured adverse slippage; volatility can add further adverse cost. The simulator creates this fill engine from explicit configuration unless a test double is intentionally injected.

**Performance impact:** A few arithmetic operations per fill; insignificant compared with replay processing.
**Trading impact:** Simulated entry economics are less optimistic and therefore more honest. This is not yet a complete realistic backtester: exits, gaps, latency, rejections, partial fills, fees, and calibration remain blockers.
**Reliability impact:** A costless default backtest now fails closed instead of silently producing impossible midpoint fills.

### Verification Completed

- Python bytecode compilation succeeded for `src`, `scripts`, `tests`, and `config`.
- Direct invariant checks succeeded for FIFO equal-priority routing, observable failed handlers, and adverse BUY/SELL fill prices.
- Full `pytest` execution is currently blocked because the local virtual environment does not contain `pytest`; CI is also not yet valid as described in F-13.

### R-03: MT5 account-currency stop-risk sizing

**Changed files:** `src/core/domain/risk_models.py`, `src/execution/position_sizer.py`, `src/execution/risk_firewall.py`, `src/infrastructure/broker/mt5_gateway.py`, `scripts/mt5_intelligent_demo_runner.py`, `scripts/mt5_pipeline_dry_run.py`, `tests/unit/test_risk.py`

**Before:** The intelligent demo route evaluated risk with default `$10,000` equity and an assumed contract size of `100`, regardless of the logged-in account and XM symbol specification. Submitted volume was capped, but the displayed currency risk was not broker-derived.
**After:** The intelligent runner obtains equity and volume constraints from the connected MT5 demo account and Gold symbol. For each candidate stop, MT5 `order_calc_profit` calculates adverse one-lot loss in account currency. Sizing is rounded down to broker lot increments, capped by both the configured demo cap and broker maximum, and rejected if the permissible risk cannot fund the broker minimum volume. The gateway independently normalizes volume before routing and reports the normalized amount.

**Performance impact:** One broker calculation is added for a qualified candidate and one symbol/account specification check occurs before order routing; this is appropriate for low-frequency protected demo execution and far less costly than incorrect exposure.
**Trading impact:** Trade direction and setup logic are unchanged. If a setup reaches risk approval, its lot size and stated stop exposure now use MT5 account-currency calculations rather than a hardcoded Gold contract assumption.
**Reliability impact:** Material reduction in sizing error and invalid broker volume submission. This does not yet solve execution-price drift after sizing, durable drawdown recovery, or ambiguous order-send reconciliation.

**Read-only validation:** On 2026-05-27, the MT5 pipeline validator completed in dry-run/order-check mode only, resolved the demo Gold symbol, used broker lot step `0.01`, calculated `$5.00` applied stop risk at `0.01` lot for its synthetic check setup, and returned MT5 `ACKNOWLEDGED` with no order sent. A separate two-second intelligent-runner launch completed in `SHADOW_ONLY_NO_ORDER` mode, loaded the MT5-backed sizing rules, observed live quotes, and sent no order.

### R-04: Fail-closed startup reconciliation for managed MT5 positions

**Changed files:** `src/execution/position_tracker.py`, `scripts/mt5_intelligent_demo_runner.py`, `tests/unit/test_position_tracker.py`

**Before:** The intelligent runner loaded a local managed-trade plan without verifying that it described the sole currently open MT5 Gold position. If the file was missing while a position remained open, the runner printed a warning but did not make the startup mismatch an explicit entry-blocking state in the candidate path.
**After:** Startup produces a deterministic reconciliation result. Automatic trailing is available only when exactly one broker position matches the stored plan's ticket, symbol, direction, entry price and final take-profit, and the stop/target geometry is valid. A plan is cleared only if MT5 confirms no open position exists. A missing, mismatched, or multiple-position situation disables automatic management and blocks consideration of new entries for that run rather than reconstructing unknown risk assumptions.

**Performance impact:** Constant-time checks over the permitted one-position set at process startup only.
**Trading impact:** It does not create or improve a signal. It prevents accidental trailing of a different or manually altered position and prevents attempting another strategy entry while startup exposure is unresolved.
**Reliability impact:** Restarts are now safer for trades originally recorded by the managed runner, while unrecorded positions remain deliberately unmanaged. Complete recovery still requires durable broker deal/order history reconciliation, not only a local plan file.

**Read-only validation:** On 2026-05-27, the intelligent runner started for a short observation in `SHADOW_ONLY_NO_ORDER` mode. MT5 returned zero open Gold positions and startup reconciliation returned `NO_OPEN_POSITION`; the runner sent no order and requested no stop update.

### R-05: Final quote-time execution validation and stale-shadow rejection

**Changed files:** `src/core/domain/risk_models.py`, `src/execution/pre_submission_guard.py`, `src/infrastructure/broker/mt5_gateway.py`, `scripts/mt5_intelligent_demo_runner.py`, `scripts/mt5_pipeline_dry_run.py`, `tests/unit/test_pre_submission_guard.py`, `tests/unit/test_live_quote_freshness.py`

**Before:** A candidate could pass risk approval at its setup price while the MT5 gateway later substituted a newer executable quote without rechecking currency risk, spread or trade geometry. Shadow mode also accepted outdated broker ticks as if they represented live market movement.
**After:** Explicitly authorized intelligent submission uses a final broker-quote gate before MT5 validation/send. It rejects zero normalized volume, stale broker quotes, excessive current spread, stop exposure exceeding the approved currency-risk budget, and a live price that invalidates stop/target geometry. The immediate order payload uses that validated quote. Shadow mode now discards quotes older than five seconds and reports the count separately rather than using them as live signal observations.

**Performance impact:** One final quote read and account-currency stop calculation per candidate reaching execution, plus a timestamp comparison per observed quote. This is minor and justified for a demo swing/intraday engine.
**Trading impact:** Strategy setup decisions are unchanged. An approved setup can now be rejected before submission if broker conditions have become unsafe. Shadow observations will be smaller but truthful when the terminal feed is stale.
**Reliability impact:** Prevents executing on stale or risk-invalidated quotes and prevents false shadow-test conclusions from non-live feed data. A residual micro-window remains between local validation and broker fill; later execution reconciliation and deviation analysis are still necessary.

**Read-only validation:** On 2026-05-27, MT5 returned a `GOLD.i#` tick approximately 10,792 seconds old. The protected pipeline correctly rejected it with `BROKER_QUOTE_IS_STALE` and sent no order. A two-second intelligent shadow launch discarded eight stale quote reads, processed zero fresh live quotes, returned `SHADOW_TEST_INVALID_NO_FRESH_MARKET_QUOTES`, and sent no order or stop update. This is the correct fail-closed outcome; any scheduled strategy shadow test must first demonstrate fresh quote flow.
