# ICT/SMC Point Of Interest Layer

This module translates the Point of Interest concept into deterministic rules.
It is intentionally isolated from live execution until the concept has enough
evidence from tests and later market review.

## Core Rule

A POI is a zone, not an exact entry price. It is not allowed to trigger a trade
by itself. Entry remains disabled until lower-timeframe confirmation appears.

## Supported POI Types

- `order_block`: last opposite candle before displacement that creates BOS or MSS.
- `FVG`: three-candle imbalance left by displacement.
- `demand_zone`: base before bullish displacement.
- `supply_zone`: base before bearish displacement.
- `order_block_candidate`: weak candidate without confirmed structure, penalized by score.
- `breaker_block`, `mitigation_block`, and `support_resistance_flip`: reserved as explicit POI types for the next refinement layer.

## Inputs

- Closed candles only.
- Timeframe and symbol.
- Optional structure events such as BOS or MSS.
- Optional liquidity sweep events.
- Optional higher-timeframe context.

The detector rejects forming candles through the `is_closed` flag.

## Output Fields

Each POI contains:

- `poi_type`
- `zone_high`
- `zone_low`
- `zone_mid`
- `direction`
- `created_by_event`
- `fresh_status`
- `quality_score`
- `quality_grade`
- `reaction_status`
- `invalidation_level`
- `entry_allowed_from_poi_alone`
- `entry_allowed_after_confirmation`
- `reasons`
- `warnings`

## Quality Model

The score improves when a POI:

- is created by BOS or MSS,
- follows a liquidity sweep,
- has displacement,
- has FVG confluence,
- aligns with premium/discount,
- aligns with higher-timeframe bias,
- remains fresh,
- has clear invalidation,
- has a reasonable zone size.

The score is reduced when a POI:

- lacks BOS or MSS,
- lacks liquidity context,
- is stale or mitigated,
- is against higher-timeframe bias,
- is too wide,
- is only a random order-block candidate.

## Entry Policy

POI entry is always blocked by default.

To mark a POI as actionable, use `confirm_poi_reaction()` with lower-timeframe
evidence such as:

- sweep inside the higher-timeframe POI,
- CHoCH or MSS,
- displacement,
- optional FVG,
- target liquidity reference.

Even after confirmation, this layer only produces permission context. It does
not submit orders.
