# ICT/SMC Market Structure Shift

This concept module teaches Apex how to detect Market Structure Shift, or MSS,
from closed OHLCV candles, confirmed swing points, and optional liquidity sweep
evidence.

It is observer-only for now. It does not change live VPS execution.

## Core Rule

MSS is treated as a reversal or directional-shift concept, not a normal
continuation breakout.

Bullish MSS requires:

- previous movement to be bearish or clearly weakening
- a valid confirmed swing high to act as the shift level
- preferably a sell-side liquidity sweep before the shift
- a closed candle above that swing high
- displacement strength after the break

Bearish MSS requires:

- previous movement to be bullish or clearly weakening
- a valid confirmed swing low to act as the shift level
- preferably a buy-side liquidity sweep before the shift
- a closed candle below that swing low
- displacement strength after the break

The detector only uses swings whose `confirmation_index` is earlier than the
MSS candle. That keeps MSS non-repainting.

## MSS Vs BOS

BOS is mainly continuation.

MSS is reversal evidence.

If price breaks upward during bullish movement, the event is more likely BOS.
If price breaks upward after bearish movement, the event can qualify as bullish
MSS.

The inverse applies for bearish MSS.

## Liquidity Sweep Requirement

A liquidity sweep is not strictly required, because markets can shift without a
clean sweep.

However, MSS without a matching sweep is capped to lower confidence and receives
the `no_liquidity_sweep_before_mss` warning.

The strongest MSS sequence is:

- sell-side sweep, then bullish close through a confirmed swing high
- buy-side sweep, then bearish close through a confirmed swing low

## Wick Breaks

A wick beyond the shift level is not strong MSS by default.

If `close_required = true`, wick-only movement becomes an invalidated or
unconfirmed MSS event.

This protects the engine from treating liquidity raids as confirmed structural
reversals.

## Failed MSS

In historical batch analysis, the detector can update a confirmed MSS to
`failed` if price closes back through the broken level during the configured
lookahead window.

This does not repaint the original MSS signal. It marks what happened after the
signal once later candles are available.

## Quality Score

The detector gives each MSS event a `confidence_score` from `0` to `10`.

It considers:

- previous opposite movement
- valid confirmed swing level
- candle-close confirmation
- matching liquidity sweep
- displacement strength
- fair value gap creation
- higher-timeframe or premium/discount support
- entry usefulness
- choppy-market penalty
- wick-only penalty
- missing-sweep penalty

Confidence grades:

- `invalid`
- `weak_CHoCH_style`
- `moderate`
- `strong`
- `high_quality`

## Entry Usage

The module does not tell the live engine to execute immediately.

Its output is intended to help later strategy code decide whether to wait for:

- retracement into FVG
- order block confirmation
- mitigation entry
- safer invalidation around the sweep or shift level

The `entry_confirmation_use.execute_trade_now` field remains `false` by design.

## Public API

Object API:

```python
from src.analytics.ict_smc.market_structure_shift import ICTMSSDetector

events = ICTMSSDetector().detect(candles, swings, liquidity_events)
```

Dataframe-style helper:

```python
from src.analytics.ict_smc.market_structure_shift import detect_mss

events = detect_mss(df, swings, liquidity_events, previous_movement="bearish")
```

Expected event fields include:

- `direction`
- `previous_movement`
- `status`
- `broken_level`
- `broken_swing`
- `confirmation_candle`
- `break_validation`
- `liquidity_context`
- `displacement`
- `fvg_context`
- `entry_confirmation_use`
- `confidence_score`
- `confidence_grade`
- `reasons`
- `warnings`
