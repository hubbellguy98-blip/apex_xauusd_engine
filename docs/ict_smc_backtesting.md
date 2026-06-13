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

## Final Principle

A good ICT/SMC backtest should be strict, conservative, and explainable. If a strategy still works after closed-candle replay, realistic fills, spread, slippage, session/news filtering, partial management, and stop-first ambiguity, then the result is much more trustworthy.
