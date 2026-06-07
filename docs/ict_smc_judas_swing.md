# ICT/SMC Judas Swing

Judas Swing is a session manipulation model. It is not just any fake breakout
and it is not an entry signal by itself.

The valid sequence is:

1. Clean pre-session accumulation.
2. Manipulation move beyond one side of the range.
3. Liquidity sweep.
4. Reclaim or rejection back inside the range.
5. MSS confirmed by candle close.
6. Displacement in the real direction.
7. FVG or order-block retracement context.
8. Opposite-side liquidity target.

## Required Function

`detect_judas_swing(df, session_range, htf_bias)`

The function uses confirmed closed candles only.

## Session Range Input

The detector expects a `session_range` object with:

- `session_name`
- `range_high`
- `range_low`
- `range_midpoint`
- `range_size`
- `session_start`
- `session_end`
- `timezone`
- `quality_score`

It also accepts Asian Range output aliases such as `asian_high`,
`asian_low`, and `asian_midpoint`.

## Bullish Judas Swing

Bullish Judas:

- Price sweeps below the range low.
- Sell-side liquidity is taken.
- Candle closes back above the range low.
- Bullish MSS confirms after manipulation.
- Bullish displacement follows.
- Bullish FVG or bullish order block gives entry context.
- Target is buy-side liquidity.

The output uses:

- `judas_type = bullish_judas`
- `manipulation_side = below_range`
- `swept_liquidity = sell_side`
- `reclaim_status = reclaimed_back_inside_range`
- `target_side = buy_side`

## Bearish Judas Swing

Bearish Judas:

- Price sweeps above the range high.
- Buy-side liquidity is taken.
- Candle closes back below the range high.
- Bearish MSS confirms after manipulation.
- Bearish displacement follows.
- Bearish FVG or bearish order block gives entry context.
- Target is sell-side liquidity.

The output uses:

- `judas_type = bearish_judas`
- `manipulation_side = above_range`
- `swept_liquidity = buy_side`
- `reclaim_status = rejected_back_inside_range`
- `target_side = sell_side`

## False Positives

The detector downgrades weak or invalid behavior:

- Sweep without MSS remains a candidate.
- Sweep without FVG/OB entry context remains lower quality.
- Close and acceptance beyond the range is classified as continuation, not
  Judas reversal.
- Tiny wicks or noisy ranges are weak candidates.
- HTF bias conflict reduces quality.

## Quality Score

The score rewards:

- Valid accumulation range.
- Clean liquidity sweep.
- Reclaim/rejection.
- MSS by candle close.
- Displacement.
- FVG/OB retracement context.
- HTF bias alignment.
- Opposite liquidity target.

The score is capped when major requirements are missing:

- No MSS: maximum candidate quality.
- No displacement or no entry zone: capped below strong quality.
- Breakout continuation: invalid Judas candidate.
- Unclear wick: low-quality false-positive candidate.

## Execution Safety

This layer is analytics-only. Live execution should not use Judas Swing until
the output has been integrated with risk, spread, stop-placement, retest
reaction, and session controls.
