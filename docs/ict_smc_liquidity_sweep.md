# ICT/SMC Liquidity Sweep Layer

This module formalizes liquidity sweeps as an observer-only ICT/SMC concept.
It does not place trades and it does not change the VPS execution path.

## Purpose

A liquidity sweep is a run beyond a known liquidity pool followed by a close
back inside the liquidity zone. The sweep is useful as context for a possible
reversal, but it is not an entry trigger by itself.

The detector consumes:

- Closed candle data only.
- Pre-detected liquidity pools from the liquidity layer.
- Optional post-sweep MSS and CHoCH confirmation events.

## Sweep Rules

For sell-side liquidity:

- The candle low must trade below the pool low.
- A close below the pool low is classified as bearish breakout/continuation.
- A close back above the pool low is classified as a bullish sell-side sweep.
- Reclaim quality is weak, mid, or full based on the close relative to the pool.

For buy-side liquidity:

- The candle high must trade above the pool high.
- A close above the pool high is classified as bullish breakout/continuation.
- A close back below the pool high is classified as a bearish buy-side sweep.
- Rejection quality is weak, mid, or full based on the close relative to the pool.

## Entry Gating

Sweep-only entry is always disabled.

The module marks `entry_allowed_after_confirmation=True` only when:

- The candle is classified as a sweep, not a breakout.
- MSS appears after the sweep.
- Post-sweep displacement appears.

CHoCH is tracked as useful warning/context, but MSS is the stronger confirmation.

## Quality Score

Quality is scored from 0 to 10 using:

- Quality of the swept liquidity pool.
- Wick rejection strength.
- Zone reclaim/rejection strength.
- MSS after sweep.
- CHoCH after sweep.
- Post-sweep displacement.
- FVG after sweep.
- Excessively large sweep candle penalty.

Confidence grades:

- `invalid`: not a valid sweep.
- `weak`: low-quality context only.
- `moderate`: usable context, still not an entry.
- `strong`: strong sweep context with confirmation.
- `high_quality`: high-quality sweep model with confirmation confluence.

## Output Contract

Each event includes:

- `concept_name`
- `detected`
- `direction`
- `sweep_type`
- `swept_liquidity`
- `sweep_candle`
- `sweep_validation`
- `rejection_quality`
- `post_sweep_confirmation`
- `entry_logic`
- `quality_score`
- `confidence_grade`
- `setup_status`
- `reasons`
- `warnings`

This makes the layer suitable for later composition with displacement, FVG,
order block, premium/discount, and session models.
