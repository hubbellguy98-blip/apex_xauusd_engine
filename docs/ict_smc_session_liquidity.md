# ICT/SMC Session High And Low Liquidity

Session highs and lows are visible intraday liquidity pools. A session high is
buy-side liquidity because stops and breakout orders often sit above it. A
session low is sell-side liquidity because stops and breakout orders often sit
below it.

This layer is a liquidity map. It is not an entry signal.

## Main Functions

`calculate_session_high_low(df, session_start, session_end, timezone)`

- Uses only closed candles.
- Waits for the session window to be complete before returning levels.
- Calculates session high, low, midpoint, range size, and high/low candle
  indexes.
- Creates two liquidity objects:
  - session high as `buy_side`.
  - session low as `sell_side`.
- Treats each level as a small zone instead of a single perfect tick.

`detect_session_liquidity_sweep(df, session_levels)`

- Reads completed session levels.
- Looks only at candles after the session is complete.
- Detects:
  - session high sweep and rejection.
  - session low sweep and reclaim.
  - session high breakout/acceptance.
  - session low breakdown/acceptance.
  - weak tiny wicks that are not enough to qualify.
- Adds target-liquidity guidance and a quality score.

## Important Safety Rule

The bot must never trade because a session high or low exists.

Correct model:

```text
session liquidity sweep
+ reclaim/rejection
+ MSS/BOS/CHoCH
+ displacement
+ FVG/OB
+ target liquidity
+ risk management
= possible setup
```

Wrong model:

```text
price touched session high -> sell
price touched session low -> buy
```

## Sweep Versus Breakout

Session high sweep:

```text
candle.high > session_high + sweep_buffer
candle.close < session_high
```

This is buy-side liquidity swept and rejected. It can support bearish reversal
context if structure confirms.

Session high breakout:

```text
candle.close > session_high + close_buffer
```

This is acceptance above the level. It should be treated as possible bullish
continuation, not bearish sweep reversal.

Session low sweep:

```text
candle.low < session_low - sweep_buffer
candle.close > session_low
```

This is sell-side liquidity swept and reclaimed. It can support bullish reversal
context if structure confirms.

Session low breakdown:

```text
candle.close < session_low - close_buffer
```

This is acceptance below the level. It should be treated as possible bearish
continuation, not bullish sweep reversal.

## Target Logic

For bullish contexts, target buy-side liquidity:

- session midpoint.
- session high.
- previous-day high.
- equal highs.
- external buy-side liquidity.

For bearish contexts, target sell-side liquidity:

- session midpoint.
- session low.
- previous-day low.
- equal lows.
- external sell-side liquidity.

## Current Integration Status

This module belongs to the local ICT/SMC analytics library. It is not connected
directly to VPS execution. Deploy only after reviewing the effect on real demo
logs, especially whether it reduces low-quality stop-loss entries or improves
target selection.
