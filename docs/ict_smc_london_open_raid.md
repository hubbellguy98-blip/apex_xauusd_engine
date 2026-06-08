# ICT/SMC London Open Liquidity Raid

The London Open Liquidity Raid layer converts the trader-defined London raid
model into deterministic analytics. It uses a completed Asian range as the
liquidity map, then classifies London-window price action as a reversal raid,
breakout continuation, weak candidate, or invalid setup.

This module is research and signal-context infrastructure only. A London raid
is not an entry signal by itself.

## Core Model

The detector expects:

- A completed Asian range before the London window starts.
- A clean Asian high/low liquidity box.
- Closed candles only.
- A configurable London window and timezone.
- HTF bias as context, not as a forced decision.

The core sequence is:

1. Asian range forms buy-side liquidity at the Asian high.
2. Asian range forms sell-side liquidity at the Asian low.
3. London sweeps or accepts beyond one side of that range.
4. Reclaim/rejection decides whether the move is a reversal candidate.
5. Candle-close MSS confirms direction.
6. Displacement confirms aggressive repricing.
7. FVG or order block defines a possible entry zone.
8. Asian midpoint and opposite range boundary become target references.

## Function

```python
detect_london_open_raid(df, asian_range, london_window, htf_bias)
```

The function returns a dictionary with:

- `raid_detected`
- `valid_setup`
- `raid_type`
- `direction`
- `swept_side`
- `reclaim_status`
- `mss_confirmed`
- `displacement_confirmed`
- `entry_zone`
- `target_liquidity`
- `quality_score`
- `failed_requirements`
- `warnings`

## Classification

Supported raid types:

- `asian_low_sweep_reversal`
- `asian_high_sweep_reversal`
- `asian_low_sweep_candidate`
- `asian_high_sweep_candidate`
- `asian_high_breakout_continuation`
- `asian_low_breakdown_continuation`
- `unclear_asian_high_raid`
- `unclear_asian_low_raid`
- `messy_asian_range`
- `outside_london_window`

The detector separates `raid_detected` from `valid_setup`:

- `raid_detected=true` means London interacted with Asian liquidity in a
  meaningful way.
- `valid_setup=true` means the raid also passed confirmation, entry-zone,
  target, and risk-reward gates.

This distinction prevents the bot from treating a simple sweep as a complete
trade idea.

## Reversal Rules

Bullish reversal:

- London sweeps below Asian low.
- Candle closes back above Asian low.
- Later candle closes above the post-sweep high.
- Bullish displacement appears.
- Bullish FVG or bullish order block is available.
- Target is Asian midpoint, then Asian high or external buy-side liquidity.

Bearish reversal:

- London sweeps above Asian high.
- Candle closes back below Asian high.
- Later candle closes below the post-sweep low.
- Bearish displacement appears.
- Bearish FVG or bearish order block is available.
- Target is Asian midpoint, then Asian low or external sell-side liquidity.

## Continuation Rules

Bullish continuation:

- London closes above Asian high with acceptance.
- Bullish displacement confirms continuation pressure.
- The model must not label this as a bearish reversal.

Bearish continuation:

- London closes below Asian low with acceptance.
- Bearish displacement confirms continuation pressure.
- The model must not label this as a bullish reversal.

## Rejection Rules

The model rejects or downgrades setups when:

- Asian range quality is below the configured threshold.
- Candles are outside the London window.
- The sweep is only a tiny wick beyond the level.
- MSS is not confirmed by candle close.
- Displacement is missing.
- No FVG or order block entry zone exists.
- Risk-to-reward to the mapped target is too weak.

## Safety Rule

The output always includes:

```python
"entry_allowed_from_london_raid_alone": False
```

The layer exists to teach the engine session-liquidity context. It should only
be promoted into execution logic after enough VPS evidence proves that the
sequence improves trade quality.
