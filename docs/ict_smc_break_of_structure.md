# ICT/SMC Break Of Structure

This concept module teaches Apex how to detect Break of Structure, or BOS,
from closed OHLCV candles and already-confirmed swing highs/lows.

It is observer-only for now. It does not change live VPS execution.

## Core Rule

Bullish BOS requires a closed candle to close above a previous valid confirmed
swing high.

Bearish BOS requires a closed candle to close below a previous valid confirmed
swing low.

The detector only uses swings whose `confirmation_index` is earlier than the
break candle. That keeps BOS non-repainting.

## Wick Breaks

A wick beyond a swing level is not treated as strong BOS by default.

If `close_required = true`, a wick-only break becomes an unconfirmed wick-break
event and is warned as likely liquidity sweep behavior.

If `close_required = false`, wick-only movement can become an aggressive BOS
candidate, but its score is capped at low confidence.

## Internal Vs External BOS

Internal BOS breaks a smaller or lower-strength swing.

External BOS breaks a stronger structural swing.

Current default split:

- internal: broken swing strength below `7`
- external: broken swing strength `7` or above

## Trend Continuation

BOS is mainly a trend-continuation concept.

If bullish BOS happens while trend context is bullish, quality improves.

If bullish BOS happens while trend context is bearish, the event is not treated
as pure BOS; it is marked as possible MSS/CHoCH until later concepts confirm
the reversal model.

The same logic applies inversely for bearish BOS.

## Quality Score

The detector gives each BOS event a `quality_score` from `0` to `10`.

It considers:

- broken swing strength
- candle-close confirmation
- trend-continuation alignment
- displacement strength
- FVG creation
- possible order-block validation
- liquidity context
- higher-timeframe context
- choppy-market penalty
- wick-only penalty

Confidence grades:

- `invalid`
- `low`
- `moderate`
- `strong`
- `high_quality`

## Failed BOS

In historical batch analysis, the detector can update a confirmed BOS to
`failed` if price closes back inside the broken structure within the configured
lookahead window.

This does not repaint the original break candle. It marks what happened after
the break once later candles are available.

## Public API

Object API:

```python
from src.analytics.ict_smc.break_of_structure import ICTBOSDetector

events = ICTBOSDetector().detect(candles, swings)
```

Dataframe-style helper:

```python
from src.analytics.ict_smc.break_of_structure import detect_bos

events = detect_bos(df, swings, close_required=True)
```

Expected event fields include:

- `direction`
- `break_type`
- `bos_scope`
- `status`
- `broken_swing`
- `confirmation_candle`
- `break_validation`
- `displacement`
- `fvg_context`
- `order_block_context`
- `liquidity_context`
- `quality_score`
- `confidence_grade`
- `reasons`
- `warnings`

## Future Links

This BOS module is designed to feed later concepts:

- MSS
- CHoCH
- FVG
- order blocks
- liquidity sweeps
- premium/discount
- continuation entries after pullback
