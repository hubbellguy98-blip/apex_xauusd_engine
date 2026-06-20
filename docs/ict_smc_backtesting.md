# ICT/SMC Backtesting Correctly

## Purpose

This layer provides conservative backtesting primitives for ICT/SMC strategy work. The goal is not to make historical results look profitable. The goal is to replay candles in order and only allow decisions that could realistically have been made at that moment.

The implementation lives in:

- `src/backtest/ict_smc_backtest.py`

It is not connected to live VPS execution. It is a research/simulation layer for safer strategy validation.

## Core Rules

- Use closed candles only for confirmed logic.
- Never use future candles to validate past setups.
- Do not use HTF candles before their `close_time`.
- Do not retroactively fill limit orders before they were placed.
- Market orders fill from the next candle open, not the best price inside the signal candle.
- Spread and slippage must be applied to executable prices.
- Same-candle stop/target ambiguity uses conservative stop-first logic.
- Targets and stops must be known before or at entry.
- Partial take profits must be weighted by position percentage.
- R-multiple must use the original risk, even if the stop later moves to breakeven.
- News/session filters should log skipped trades, not disappear from the audit trail.
- Broad sessions and exact killzones are separate fields. The full London session is not treated as a killzone.
- Strict profiles reject trades whose post-fill, post-cost final RR is below the configured minimum.
- Intraday profiles can force exits for max hold time, session close, Friday cutoff, and stale trades.

## Function List

- `clean_ohlcv_data()`: normalizes OHLCV rows and removes invalid candles.
- `apply_spread_slippage()`: adjusts mid prices to conservative executable prices.
- `place_pending_order()`: normalizes a confirmed setup signal into an order record.
- `simulate_order_fill()`: fills market or limit orders using closed candle data.
- `simulate_trade_management()`: checks stops, targets, partials, breakeven, and conservative ambiguity.
- `record_trade()`: normalizes a completed trade log.
- `record_skipped_setup()`: logs blocked/skipped setups.
- `calculate_performance_metrics()`: calculates win rate, profit factor, expectancy, net R, drawdown, target rates, and ambiguity count.
- `generate_backtest_report()`: creates a compact report with warnings.
- `run_backtest()`: replays prepared setup signals through conservative order and management rules.

## Data Pipeline

1. Load OHLCV candles.
2. Normalize timestamps, sort candles, and remove invalid rows.
3. Use execution timeframe candles, usually `5M`, as the master replay clock.
4. At every candle close, manage open positions first.
5. Then check pending orders.
6. Then admit new confirmed setup signals whose signal time is now known.
7. Block new trades during news/session restrictions.
8. Place limit orders or schedule market orders for next-candle open.
9. Log trades and skipped setups.
10. Calculate R-based metrics and reliability warnings.

## Conservative Fill Rules

Limit order:

- Bullish limit fills only if candle low touches the entry after the order is active.
- Bearish limit fills only if candle high touches the entry after the order is active.
- A candle that touched the entry before the order was placed does not count.
- If entry and stop are touched in the same candle, the backtester flags ambiguity.

Market order:

- Fill at next candle open.
- Long entry = open + spread half + slippage.
- Short entry = open - spread half - slippage.

Exit:

- Long stop is hit when candle low <= stop.
- Long target is hit when candle high >= target.
- Short stop is hit when candle high >= stop.
- Short target is hit when candle low <= target.
- If stop and target are both touched in the same OHLC candle, conservative mode assumes stop first.

## Partial Take Profit Logic

The simulator supports target ladders such as:

- 50% at `target_1`
- 25% at `target_2`
- 25% at `final_target`

The full-system runner can also force a fixed-R ladder from the active backtest profile. For example, a `1:6` ladder uses six milestones while a strict intraday validation profile can require a final 3R target. This keeps the report consistent with the intended trade-management model instead of silently splitting whatever final target a strategy returned.

Each partial logs:

- target name
- exit price
- exit time
- closed percent
- realized R

Total trade result is the weighted sum of partial R values. If stop moves to breakeven after TP1, the remaining exit is still measured using the original initial risk.

## Required Metrics

The current metrics include:

- total trades
- wins
- losses
- breakevens
- win rate
- loss rate
- average win R
- average loss R
- average trade R
- median trade R
- net R
- gross profit R
- gross loss R
- profit factor
- expectancy R
- max drawdown R
- max consecutive wins
- max consecutive losses
- target 1 hit rate
- final target hit rate
- ambiguous candle count

## Backtest Warnings

The report warns when:

- news calendar is missing for XAUUSD
- sample size is too small
- ambiguous OHLC exits were resolved with stop-first logic
- profit factor is below `1.2`
- expectancy or net R is not positive
- max drawdown breaches the configured deployment gate
- any completed trade has final post-cost RR below the configured minimum
- completed trades contain duration outliers
- session-level profit factor is below `1`
- score buckets are non-monotonic

These warnings are important. A backtest without historical news data or enough trades is not reliable proof of edge.

## Common ICT/SMC Traps Prevented

- Repainting swing and structure logic by using future candles.
- Using current 1H/Daily candles before close.
- Filling limits before the order existed.
- Entering at the best price inside the confirmation candle.
- Assuming target hits before stop in an ambiguous candle.
- Ignoring spread and slippage.
- Ignoring skipped setups.
- Measuring breakeven/trailing trades against the moved stop instead of original risk.

## Example Usage

```python
from src.backtest.ict_smc_backtest import run_backtest

result = run_backtest(
    {"m5": m5_candles},
    {
        "execution_timeframe": "5M",
        "signals": confirmed_setup_signals,
        "spread_model": {"spread": 0.20},
        "slippage_model": {"slippage": 0.05},
        "report": {
            "news_calendar_loaded": False,
            "minimum_sample_trades": 30,
        },
    },
)

print(result["performance_metrics"])
print(result["skipped_setup_log"])
```

## Full-System ICT/SMC Selector Backtest Runner

For pre-deployment testing of the current ICT/SMC selector, use:

```powershell
.\.venv\Scripts\python.exe scripts\run_ict_smc_backtest.py --source mt5 --symbol GOLD.i# --from 2026-06-01 --to 2026-06-14 --profile strict_intraday_xauusd
```

Profiles live in `config/backtest_profiles.json`:

- `broad_research` keeps the selector broad and diagnostic.
- `strict_intraday_xauusd` requires stricter score/RR, exact killzone handling, 3R post-cost acceptance, and intraday time exits.
- `session_filtered_experiment` focuses on the stronger NY Open and Silver Bullet AM windows without hardcoding those filters into live strategy code.
- `v3_candidate_safety` is a sweep-only candidate profile with London Open disabled, 3R post-cost acceptance, early-trap filtering, and strict displacement verification.

If a trade log contains sub-3R completed trades, no-killzone trades under a require-killzone profile, disabled killzones, or holds beyond the configured max hold, it was not strict-profile compliant. That usually means the wrong profile was run, an older commit was used, or the output lacks run provenance.

This runner:

- loads historical candles from MT5 using the local `.env` MT5 settings;
- replays closed 1m candles in chronological order;
- feeds the current ICT/SMC strategy selector;
- enforces one simulated position at a time;
- keeps evaluating strategy candidates while a position is open, then logs them as `blocked_existing_position` instead of hiding them;
- preserves the strategy's intended market or limit order style;
- rejects fills that drift too far away from the original setup entry;
- rejects fills whose actual post-cost final RR falls below the active profile minimum;
- applies spread and slippage assumptions;
- applies the same demo stop-hardening layer unless `--no-stop-hardening` is passed;
- writes the git SHA, branch, command args, active profile, selector config, spread/slippage, data source, symbol, date range, and timeframe counts into the JSON/Markdown report;
- writes a `run_manifest.json` next to every output;
- stamps every trade CSV row with profile name, git SHA/branch, minimum RR/score, active profile hash, selector config hash, and run ID;
- fails strict-profile runs by default when completed trades violate post-cost RR, killzone, disabled-killzone, or max-hold gates;
- separates completed-trade metrics from open-at-end mark-to-market positions;
- writes Markdown, JSON, and CSV reports into `backtest_outputs/`.

Example with stricter execution costs:

```powershell
.\.venv\Scripts\python.exe scripts\run_ict_smc_backtest.py --source mt5 --symbol GOLD.i# --from 2026-06-01 --to 2026-06-14 --spread-price 0.40 --slippage-price 0.10
```

Profile override examples:

```powershell
.\.venv\Scripts\python.exe scripts\run_ict_smc_backtest.py --source mt5 --symbol GOLD.i# --from 2026-06-01 --to 2026-06-14 --profile broad_research --minimum-rr 2
.\.venv\Scripts\python.exe scripts\run_ict_smc_backtest.py --source mt5 --symbol GOLD.i# --from 2026-06-01 --to 2026-06-14 --profile strict_intraday_xauusd --target-final-rr 6
.\.venv\Scripts\python.exe scripts\run_ict_smc_backtest.py --source mt5 --symbol GOLD.i# --from 2026-06-01 --to 2026-06-14 --profile v3_candidate_safety
```

Use `--allow-failed-deployment-gates` only for research. Do not treat a run with failed deployment gates as live-ready.

If the backtest shows too few trades, inspect the Strategy Diagnostics section:

- `Selected signals` means the selector found valid setups.
- `Tradeable signals observed` means strategies produced executable candidates, even if one-position-at-a-time blocked them.
- `Open-position skips` means the system found opportunities but did not take them because another trade was already active.
- `Entry-drift skips` means the next executable market price had moved too far from the intended setup entry.
- `Top rejection reasons` shows whether strict filters such as session, sweep, HTF bias, or news eligibility are suppressing the sample.

For research only, entry-drift protection can be relaxed:

```powershell
.\.venv\Scripts\python.exe scripts\run_ict_smc_backtest.py --source mt5 --symbol GOLD.i# --from 2026-06-01 --to 2026-06-14 --max-entry-drift-price -1 --max-entry-drift-risk-fraction 0
```

Do not use relaxed research results as deployment proof. They are useful for diagnosing whether the strategy layer is too strict, not for proving live profitability.

CSV fallback:

```powershell
.\.venv\Scripts\python.exe scripts\run_ict_smc_backtest.py --source csv --symbol GOLD.i# --from 2026-06-01 --to 2026-06-14 --csv-1m .\data\gold_m1.csv
```

If only 1m CSV data is provided, the runner derives 15m, 1h, and 4h candles from it so multi-timeframe strategy checks can still run.

The runner does not place live or demo broker orders. It is offline simulation only.

## Trade Log Analyzer

After a run, analyze the exported trade CSV with:

```powershell
.\.venv\Scripts\python.exe scripts\analyze_trade_log.py .\backtest_outputs\ict_smc_backtest_mt5_YYYYMMDD_HHMMSS_trades.csv
```

The analyzer writes Markdown and JSON covering overall metrics, direction, session, killzone, UTC hour, component tags, score buckets, duration buckets, displacement vs no-displacement, max drawdown, and streaks. Use this before changing VPS settings.

The analyzer also supports older CSV names such as `score`, `exit_reason`, `tp1`, `tp2`, `entry`, `exit`, and `stop`. If a CSV has no `profile_name`, it warns with `legacy_or_unprofiled_trade_log`. New reports include profile compliance, strict-profile violations, post-cost RR distribution, early 0-15m metrics, exact component-combo metrics, and exclusion simulations for London Open, displacement-tagged trades, and 0-15m trades.

## v3 Finding

The v3 trade log was not compatible with `strict_intraday_xauusd`: it contained sub-3R trades, no-killzone trades, Silver Bullet PM trades, low-score trades, and holds beyond 180 minutes. The engine now records run provenance in every row and fails strict-profile runs if those violations reach the completed ledger. This does not make the system live-ready; it makes failed backtests impossible to mistake for strict deployment proof.

## Final Principle

A good ICT/SMC backtest should be strict, conservative, and explainable. If a strategy still works after closed-candle replay, realistic fills, spread, slippage, session/news filtering, partial management, and stop-first ambiguity, then the result is much more trustworthy.
