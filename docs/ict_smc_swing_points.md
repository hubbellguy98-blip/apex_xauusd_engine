# ICT/SMC Swing High And Swing Low Detection

This concept module teaches Apex how to identify confirmed swing highs and
swing lows from closed OHLCV candles.

It is observer-only for now. It does not change live VPS execution.

## Core Rule

A swing high is a candle whose high is strictly greater than the highs of the
configured candles to its left and right.

A swing low is a candle whose low is strictly lower than the lows of the
configured candles to its left and right.

If `right_bars = 3`, a swing at candle `100` is only confirmed after candle
`103` has closed. This avoids repainting.

## Output Fields

Each detected swing includes:

- `index`
- `timestamp`
- `confirmation_index`
- `confirmation_timestamp`
- `price`
- `type`
- `strength_score`
- `strength_label`
- `timeframe`
- `timeframe_weight`
- `liquidity_type`
- `status`
- `used_for`
- `reasons`
- `warnings`

## Liquidity Meaning

Swing highs create `buy_side_liquidity`.

Swing lows create `sell_side_liquidity`.

The detector does not treat wick-through movement as BOS. BOS/MSS modules must
later require a closed candle beyond the swing level.

## Strength Scoring

The score is deterministic and capped from `0` to `10`.

It considers:

- confirmed left/right swing formation
- ATR reaction after the swing
- distance from the previous accepted swing
- liquidity usefulness
- session quality
- volume context
- higher-timeframe or premium/discount context
- chop/noise penalties
- possible news-spike warnings

Strength labels:

- `weak`: 0 to 2
- `minor`: above 2 to 4
- `moderate`: above 4 to 6
- `strong`: above 6 to 8.5
- `major`: above 8.5 to 10

## Timeframe Weight

The detector stores timeframe weight separately so a 5m swing is not treated
as equal to a 4h swing.

Current default weights:

- `1m`: 0.5
- `5m`: 1.0
- `15m`: 1.5
- `1h`: 2.0
- `4h`: 3.0
- `daily`: 4.0

## Edge Cases

Equal highs and equal lows are not accepted as clean swings by default. They
are better treated as liquidity pools in a future liquidity-pool module.

Large outside candles can qualify as both a swing high and swing low. The
detector allows this but marks large-range candles with a warning when the
range is abnormal versus ATR.

The latest `right_bars` candles cannot become confirmed swings because the
future confirmation candles do not exist yet.

## Public API

Use the object API when working with internal `CandleNode` data:

```python
from src.analytics.ict_smc.swing_points import ICTSwingPointDetector

swings = ICTSwingPointDetector().detect(candles)
```

Use the dataframe-style helper for pandas or row dictionaries:

```python
from src.analytics.ict_smc.swing_points import detect_swings

swings = detect_swings(df, left_bars=3, right_bars=3)
```

The helper returns dictionaries for easy logging, reporting, and later
integration with backtests.
