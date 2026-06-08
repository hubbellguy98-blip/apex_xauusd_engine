# ICT/SMC Power Of Three / AMD

Power of Three, also called AMD, describes the full intraday/session narrative:

1. Accumulation
2. Manipulation
3. Distribution

AMD is broader than a single Judas Swing. Judas Swing is often the manipulation
phase inside the wider AMD model.

This layer is analytics-only. It must not be treated as a direct live entry
trigger.

## Required Function

`detect_amd_model(df, sessions, htf_bias)`

The detector uses confirmed closed candles only and refuses to force AMD when
one of the three phases is missing.

## Accumulation

Accumulation is a valid pre-expansion range. For XAUUSD/forex, the Asian range
is often the default accumulation source.

The detector expects an accumulation range with:

- `range_high`
- `range_low`
- `range_midpoint`
- `range_size`
- `session_name`
- `start_index` / `end_index` or `session_start` / `session_end`
- `timezone`
- `range_quality_score`

The accumulation phase is stronger when most candles remain inside the range,
the range is reasonable compared with ATR, and the range quality score is high.

## Manipulation

Manipulation is the liquidity raid outside the accumulation range.

Bullish AMD manipulation:

- Price sweeps below accumulation low.
- Sell-side liquidity is taken.
- Price reclaims back above the accumulation low.

Bearish AMD manipulation:

- Price sweeps above accumulation high.
- Buy-side liquidity is taken.
- Price rejects back below the accumulation high.

If price accepts beyond the range instead of reclaiming/rejecting, the model is
classified as breakout or breakdown continuation, not AMD reversal.

## Distribution

Distribution is the real expansion after manipulation.

Bullish distribution requires:

- Bullish MSS after downside manipulation.
- Bullish displacement.
- Bullish FVG or bullish order-block entry context.
- Expansion toward buy-side liquidity.

Bearish distribution requires:

- Bearish MSS after upside manipulation.
- Bearish displacement.
- Bearish FVG or bearish order-block entry context.
- Expansion toward sell-side liquidity.

Distribution without MSS or displacement remains only a candidate.

## Targets

Bullish AMD targets:

- Accumulation midpoint.
- Accumulation high.
- PDH or external buy-side liquidity.

Bearish AMD targets:

- Accumulation midpoint.
- Accumulation low.
- PDL or external sell-side liquidity.

## Confidence Score

The score rewards:

- Valid accumulation.
- Clean manipulation sweep.
- Reclaim/rejection.
- MSS confirmation.
- Displacement.
- FVG/OB entry zone.
- Distribution follow-through.
- HTF bias alignment.
- Opposite-side target liquidity.

The score is capped when major requirements are missing:

- No MSS: cannot become confirmed AMD.
- No displacement or no entry zone: cannot become strong AMD.
- Accepted breakout/breakdown: invalid AMD reversal candidate.
- Accumulation only: no AMD.

## Safety Principle

AMD is a market narrative model, not a standalone entry signal. It should be
used to organize direction, liquidity, confirmation, entry-zone context, and
targets before any execution logic is considered.
