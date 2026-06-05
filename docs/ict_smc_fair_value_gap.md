# ICT/SMC Fair Value Gap

## Purpose

A Fair Value Gap is a three-candle imbalance zone created when price moves
aggressively enough that candle 1 and candle 3 do not overlap by wick.

It is not a normal open/close gap. Apex detects FVGs using only closed candles
and treats them as imbalance points of interest, not automatic entries.

## Three-Candle Model

For candle index `i`:

- `candle_1 = df[i - 2]`
- `candle_2 = df[i - 1]`
- `candle_3 = df[i]`

The FVG is confirmed only after candle 3 closes.

## Bullish FVG

Condition:

```text
candle_1.high < candle_3.low
```

Zone:

```text
zone_low = candle_1.high
zone_high = candle_3.low
zone_mid = (zone_low + zone_high) / 2
```

Bullish FVG is stronger when candle 2 is bullish displacement, ideally after a
sell-side sweep and bullish MSS/BOS.

## Bearish FVG

Condition:

```text
candle_1.low > candle_3.high
```

Zone:

```text
zone_low = candle_3.high
zone_high = candle_1.low
zone_mid = (zone_low + zone_high) / 2
```

Bearish FVG is stronger when candle 2 is bearish displacement, ideally after a
buy-side sweep and bearish MSS/BOS.

## Fill Tracking

For bullish FVG:

- fill starts when later candle low enters the zone
- `filled_percent = (zone_high - lowest_low_after_creation) / fvg_size * 100`
- invalidated if candle closes below `zone_low - invalidation_buffer`

For bearish FVG:

- fill starts when later candle high enters the zone
- `filled_percent = (highest_high_after_creation - zone_low) / fvg_size * 100`
- invalidated if candle closes above `zone_high + invalidation_buffer`

Statuses:

- `untouched`
- `partially_filled`
- `half_filled`
- `fully_filled`
- `respected`
- `invalidated`
- `stale`

Full fill is not automatically invalidation. Hard invalidation requires close
acceptance beyond the opposite boundary.

## Entry Logic

FVG alone never permits entry.

Entry can only become possible after:

- price retraces into the FVG
- FVG is not invalidated
- reaction confirms in the expected direction
- structure, liquidity, target, and risk context support the idea

## Quality Score

Quality is scored from 0 to 10 using:

- valid three-candle formation
- candle 2 displacement strength
- ATR-normalized FVG size
- liquidity sweep context
- BOS/MSS/CHoCH context
- OB/POI overlap
- premium/discount alignment
- HTF alignment
- fill/respect/invalidation status
- target liquidity reference

Invalidated FVGs are capped low and should not be used for entries in their
original direction.
