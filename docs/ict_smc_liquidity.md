# ICT/SMC Liquidity

Liquidity is where resting orders are likely located. In this repository it is
implemented as an observer-only market map concept, not an entry trigger.

## Core Types

Buy-side liquidity is above obvious highs:

- Swing highs.
- Equal highs.
- Previous day high.
- Session high.
- Range high.
- Optional trendline highs.

Sell-side liquidity is below obvious lows:

- Swing lows.
- Equal lows.
- Previous day low.
- Session low.
- Range low.
- Optional trendline lows.

## Zone-Based Detection

Liquidity is modeled as a price zone, not one exact tick.

Each pool has:

- `zone_low`
- `zone_mid`
- `zone_high`
- `tolerance`

The default tolerance is ATR-based so the zone adapts to changing XAUUSD
volatility.

## Status

Liquidity status is deterministic:

- `unswept`: price has not entered/taken the zone after creation.
- `touched`: price interacted with the zone but did not take it.
- `swept`: price traded beyond the zone and closed back inside.
- `broken`: price closed beyond the zone with acceptance.
- `stale`: the level has too many touches.
- `invalid`: reserved for future structural invalidation rules.

Buy-side sweep:

```text
high > zone_high
AND close < zone_high
```

Sell-side sweep:

```text
low < zone_low
AND close > zone_low
```

Breakout/acceptance is classified separately from a sweep.

## Quality Score

Each pool receives a `quality_score` from `0` to `10`.

Score considers:

- Source importance.
- Timeframe visibility.
- Touch count.
- Freshness.
- Zone cleanliness.
- Confluence.
- Distance/usefulness.
- Session relevance.
- Penalties for chop, wide zones, low timeframe noise, and prior use.

## Roles

Liquidity can act as:

- `bullish_target`
- `bearish_target`
- `buy_side_sweep_area`
- `sell_side_sweep_area`
- `possible_bearish_reversal_context`
- `possible_bullish_reversal_context`
- `bullish_continuation_or_bos_context`
- `bearish_continuation_or_bos_context`

## Public Helpers

The module exposes:

- `detect_equal_highs(df, swings)`
- `detect_equal_lows(df, swings)`
- `detect_liquidity_pools(df, swings)`

`detect_liquidity_pools` combines swing liquidity, equal highs/lows, previous
day high/low, session high/low, range high/low, and optional trendline
liquidity.

## Integration Boundary

This module does not affect VPS execution. It is available under
`src.analytics.ict_smc` for research, reporting, and future confirmation logic.

Final rule: liquidity alone is not an entry signal. Entry still requires
confirmation such as CHoCH, MSS, BOS, displacement, FVG, order-block reaction,
premium/discount context, and risk controls.
