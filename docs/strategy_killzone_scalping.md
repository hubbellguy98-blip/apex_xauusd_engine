# Kill Zone Scalping Strategy Layer

This layer converts Kill Zone Scalping into deterministic ICT/SMC bot logic for
research, testing, and future orchestration. It does not place broker orders.

## Model

Kill zones are timing filters only. A scalp is not valid just because price is
inside London, New York, or overlap windows.

The required chain is:

1. Timestamp is inside a configured kill zone.
2. Relevant buy-side or sell-side liquidity exists.
3. Liquidity is swept and reclaimed/rejected using closed candles only.
4. Price confirms MSS after the sweep.
5. MSS candle shows displacement.
6. A post-MSS FVG or order block is detected.
7. Price retraces into the entry POI.
8. The nearest scalp target has enough distance.
9. RR, spread, news, candle-size, and session limits are safe.

## Safety Rules

- Closed candles only; forming candles are ignored.
- News-restricted windows reject entries.
- Large news-spike candles reject entries.
- High spread rejects entries.
- Spread too large relative to target distance rejects entries.
- 1M setups require 5M/15M confirmation or are capped as low quality.
- One-trade-per-kill-zone style limits are enforced by session state.
- Targets are nearby liquidity pools, not distant swing targets.

## Main Functions

- `is_in_killzone()`
- `detect_killzone_liquidity_sweep()`
- `detect_killzone_mss()`
- `detect_killzone_fvg_or_ob()`
- `generate_killzone_scalp_signal()`
- `score_killzone_scalp_setup()`
- `enforce_session_trade_limit()`

## Output

The generator returns a structured dictionary with:

- signal status,
- direction,
- active kill zone,
- sweep details,
- MSS/displacement details,
- entry POI,
- target,
- risk plan,
- session limit status,
- score,
- rejection reasons.

This makes the model safe for unit tests, backtests, forward tests, and later
live orchestration through the existing risk/execution pipeline.
