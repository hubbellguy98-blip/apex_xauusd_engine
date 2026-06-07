# ICT/SMC Equal Highs And Equal Lows

This layer detects equal highs and equal lows as objective liquidity pools. It is
not an entry model. It creates buy-side and sell-side liquidity references that
other modules can use for targets, sweep context, draw-on-liquidity selection,
and internal/external liquidity mapping.

## Core Meaning

Equal highs are two or more confirmed swing highs clustered around nearly the
same price level. They represent buy-side liquidity because stops and breakout
orders often sit above them.

Equal lows are two or more confirmed swing lows clustered around nearly the same
price level. They represent sell-side liquidity because stops and breakout
orders often sit below them.

## Required Function

```python
detect_equal_highs_lows(df, tolerance_percent, min_touches)
```

The function accepts OHLCV rows or a pandas-style dataframe. It uses confirmed
closed candles only. If external swing points are not supplied, it can detect
basic pivot swings internally, but the preferred production path is to pass
confirmed swings from `detect_swings()`.

## Equality Rules

Equality is tolerance-based because highs and lows are rarely identical.

```text
tolerance_value = reference_price * tolerance_percent / 100
```

When ATR is available, the implementation applies a small ATR floor and ATR cap
so tolerance is not unrealistically tiny or excessively loose in volatile XAUUSD
conditions.

## Status Rules

Equal highs:

- Active: no later candle trades above `zone_high + sweep_buffer`.
- Swept rejected: later candle high trades above the zone, then closes back below
  `zone_high`.
- Broken/accepted above: later candle closes above `zone_high + close_buffer`.

Equal lows:

- Active: no later candle trades below `zone_low - sweep_buffer`.
- Swept reclaimed: later candle low trades below the zone, then closes back above
  `zone_low`.
- Broken/accepted below: later candle closes below `zone_low - close_buffer`.

## Required Output Fields

Each returned liquidity object includes:

- `type`
- `zone_high`
- `zone_low`
- `touch_count`
- `swept`
- `quality_score`

It also includes richer fields such as direction, zone midpoint, sweep type,
active status, creation index, touched indexes, quality grade, reasons, warnings,
and target-use context.

## Quality Model

Quality is scored from 0 to 10 using:

- touch count
- zone cleanliness relative to ATR
- average swing strength
- timeframe importance
- active/swept/broken status
- visibility and spacing between touches
- simple chop/noise penalties
- target usefulness

Broken liquidity is capped low because it is no longer an untouched target.
Swept-and-reclaimed/rejected liquidity is still useful as a completed sweep event
but is no longer active as future untouched liquidity.

## Safety Boundary

Every output includes:

```text
entry_allowed_from_equal_liquidity_alone = false
```

Equal highs/lows are liquidity pools and sweep areas. They still require separate
bias, confirmation, risk, execution, and state-management approval before any
trade can be considered.
