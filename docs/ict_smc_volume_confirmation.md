# ICT/SMC Volume Confirmation

Volume confirmation is an optional scoring layer for existing SMC/ICT events.
It does not create a trade by itself.

For XAUUSD and forex, broker volume is usually tick volume. This means it should
be interpreted as activity confirmation, not exact centralized institutional
volume.

## Function

```python
score_volume_confirmation(df, event)
```

Required dataframe fields:

- `timestamp`
- `open`
- `high`
- `low`
- `close`
- `volume`

Required event fields:

- `event_type`
- `direction`
- `candle_indices`

Useful optional event fields:

- `event_id`
- `level_price`
- `zone_low`
- `zone_high`
- `displacement_indices`
- `retracement_indices`
- `reaction_indices`
- `structure_break_confirmed`
- `fvg_created`
- `reference_volume_lookback`
- `news_flag`

## Core Principle

Volume should only confirm, weaken, or warn about a setup that already exists
from price-action logic.

Correct order:

1. Detect liquidity, structure, displacement, FVG/OB, premium/discount, target,
   and risk context.
2. Score volume as optional confirmation.
3. Keep volume as a small part of total confidence unless the strategy is
   specifically volume-focused.

Wrong order:

1. High volume appears.
2. Bot creates a trade from volume alone.

## Event Types

Supported event types:

- `liquidity_sweep`
- `displacement`
- `fvg_retracement`
- `order_block_retest`
- `absorption`
- `rejection`
- `breakout_continuation`
- `mss_confirmation`
- `bos_confirmation`

## Scoring Meaning

- `0-2`: volume contradicts the setup
- `3-4`: weak confirmation
- `5-6`: neutral or acceptable confirmation
- `7-8`: strong confirmation
- `9-10`: excellent confirmation

## Liquidity Sweep Volume

Bullish sweep confirmation improves when:

- sweep candle volume is above recent average
- price sweeps sell-side liquidity
- price closes back above the swept level
- lower wick is large
- follow-through volume supports bullish displacement

Bearish sweep confirmation improves when:

- sweep candle volume is above recent average
- price sweeps buy-side liquidity
- price closes back below the swept level
- upper wick is large
- follow-through volume supports bearish displacement

High volume without reclaim or rejection is treated as a warning because it may
be acceptance or continuation beyond the level.

## Displacement Volume

Displacement confirmation improves when:

- event average volume is above the recent average
- directional candles dominate volume
- candles close strongly in the displacement direction
- range expands with volume
- structure break is confirmed
- FVG is created

High volume without structure break is capped because it may be noise or news.

## FVG/OB Retracement Volume

Healthy retracement confirmation improves when:

- pullback volume is lower than displacement volume
- pullback candles are smaller and corrective
- FVG or OB zone is respected
- reaction volume increases away from the zone
- continuation confirms after reaction

Aggressive high-volume pullback into the zone lowers confidence.

## Absorption And Rejection

Absorption improves when price hits liquidity with high activity but fails to
continue through the level.

Bullish absorption:

- price trades below liquidity
- volume expands
- price closes back above the level
- lower wick is meaningful
- follow-up structure confirms

Bearish absorption:

- price trades above liquidity
- volume expands
- price closes back below the level
- upper wick is meaningful
- follow-up structure confirms

## News Spike Safety

If `news_flag=True`, the score is capped and warning-heavy. Extreme volume
during news should not be treated as normal SMC confirmation unless structure
and execution conditions are clean.

## Output

Main fields:

- `volume_score`
- `interpretation`
- `confirmation_status`
- `metrics`
- `volume_pattern`
- `warnings`
- `entry_allowed_from_volume_alone`

The output always keeps `entry_allowed_from_volume_alone=False`.
