# ICT/SMC Dealing Range Layer

This layer turns the discretionary ICT/SMC idea of a dealing range into a deterministic structural map.

## Purpose

A dealing range answers where price sits inside the current structural leg:

- `range_low` and `range_high` define the active swing boundary.
- `equilibrium` splits the range into premium and discount.
- `discount_zone` supports bullish POI filtering.
- `premium_zone` supports bearish POI filtering.
- `internal_liquidity` tracks liquidity inside the range.
- `external_liquidity` tracks buy-side liquidity above the range and sell-side liquidity below the range.

The dealing range is not an entry trigger. It only gives location context for other concepts such as liquidity sweeps, MSS/BOS, displacement, FVG, order blocks, POIs, and risk/reward.

## Selection Rules

`identify_dealing_range(df, swings, timeframe)` evaluates confirmed swing pairs:

- Bullish range: confirmed swing low followed by confirmed swing high.
- Bearish range: confirmed swing high followed by confirmed swing low.
- MSS-linked ranges score higher than BOS-linked ranges.
- HTF ranges can control LTF POI quality when passed as context.
- Tiny, weak, unconfirmed, or compressed ranges are rejected as noisy local ranges.

## Output Contract

The function returns a dictionary containing:

- `range_low`, `range_high`, `range_size`
- `equilibrium`, `premium_zone`, `discount_zone`
- `range_type`, `range_direction`, `range_valid`
- `quality_score`, `quality_grade`
- `internal_liquidity`, `external_liquidity`
- `current_price_location`, `position_percent`
- `selected_from`, `structure_event`, `alternative_ranges`
- `htf_alignment`
- `entry_allowed_from_dealing_range_alone = false`

## Quality Meaning

- `0-4`: invalid or noisy.
- `5-6`: usable local context.
- `7-8`: strong structural range.
- `9-10`: high-quality institutional range.

## Important Safety Rule

This module must not place orders. It is intentionally isolated from the live VPS execution path until the concept has enough test and forward-observation evidence.
