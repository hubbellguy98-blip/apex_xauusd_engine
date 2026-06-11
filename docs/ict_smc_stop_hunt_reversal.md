# ICT/SMC Turtle Soup / Stop-Hunt Reversal

`detect_stop_hunt_reversal(df, prior_highs_lows)` detects the Turtle Soup
style stop-hunt reversal model:

- prior high taken, then bearish reversal
- prior low taken, then bullish reversal
- accepted breakout/breakdown classified separately
- weak prior levels ignored as noise

This is an analytics layer only. A stop hunt is not an entry signal by itself.

## Required Inputs

`df` should contain closed OHLCV candles:

- `timestamp`
- `open`
- `high`
- `low`
- `close`
- `volume`
- optional `index`, `symbol`, `timeframe`, `is_closed`

`prior_highs_lows` should contain meaningful levels:

- `level_id`
- `level_type`
- `direction`
- `price`
- `zone_low`
- `zone_mid`
- `zone_high`
- `index`
- `timeframe`
- `quality_score`
- `swept_status`

Only closed candles are used. Forming candles are ignored.

## Bullish Model

Prior low is swept:

```text
candle.low < prior_low.zone_low - sweep_buffer
```

Then reclaimed:

```text
candle.close > prior_low.zone_low
```

The stronger bullish model then requires:

- bullish MSS by candle close
- bullish displacement
- bullish FVG or bullish OB entry zone
- buy-side target liquidity above
- invalidation below sweep low

## Bearish Model

Prior high is swept:

```text
candle.high > prior_high.zone_high + sweep_buffer
```

Then rejected:

```text
candle.close < prior_high.zone_high
```

The stronger bearish model then requires:

- bearish MSS by candle close
- bearish displacement
- bearish FVG or bearish OB entry zone
- sell-side target liquidity below
- invalidation above sweep high

## False Positives

The detector does not call these reversals:

- tiny wick through a weak level
- price accepts beyond the prior high/low
- sweep without reclaim/rejection
- sweep without MSS confirmation
- no FVG/OB entry zone
- no opposite liquidity target

Accepted prior-high breakouts are classified as
`bullish_breakout_continuation`.

Accepted prior-low breakdowns are classified as
`bearish_breakdown_continuation`.

## Output

The output includes:

- `stop_hunt_detected`
- `stop_hunt_type`
- `swept_level`
- `swept_side`
- `reclaim_status`
- `mss_confirmed`
- `entry_zone`
- `target_liquidity`
- `invalidation_level`
- `confidence_score`
- `risk_plan`
- `false_positive_flags`
- `stop_hunt_events`

The output always includes:

```json
{
  "entry_allowed_from_stop_hunt_alone": false
}
```

That field is deliberate. Stop-hunt reversal becomes useful only after
reclaim/rejection, MSS, displacement, FVG/OB entry, target liquidity, and risk
management align.
