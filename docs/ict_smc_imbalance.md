# ICT/SMC Imbalance Layer

The imbalance layer converts the broad ICT/SMC idea of inefficient price
delivery into deterministic candle rules. It is intentionally separate from the
live execution path until the concept is validated against market evidence.

## What It Detects

- `bullish_fvg_imbalance` and `bearish_fvg_imbalance` using the strict
  three-candle FVG rule.
- `bullish_displacement_imbalance` and `bearish_displacement_imbalance` from a
  single expansion candle with a large body, ATR expansion, and close near the
  candle extreme.
- `bullish_multi_candle_imbalance` and `bearish_multi_candle_imbalance` from a
  conservative 2-5 candle displacement sequence with contextual support.

## Core Rules

- Only closed candles are used.
- An imbalance is a price zone, not a single price.
- FVG is treated as one objective subtype of imbalance.
- Displacement-only imbalance is scored lower unless structure, liquidity, or
  FVG context supports it.
- The detector never allows entry from imbalance alone.

## Fill And Invalidation

Bullish imbalance:

- A later low entering the zone increases fill percentage.
- A close below `zone_low - invalidation_buffer` invalidates the zone.

Bearish imbalance:

- A later high entering the zone increases fill percentage.
- A close above `zone_high + invalidation_buffer` invalidates the zone.

Statuses:

- `unfilled`
- `partially_filled`
- `half_filled`
- `fully_filled`
- `respected`
- `invalidated`
- `stale`

## Quality Model

Quality improves when the imbalance has:

- strong displacement,
- strict FVG boundaries,
- BOS/MSS/CHOCH context,
- matching liquidity sweep,
- OB/POI/FVG overlap,
- premium/discount alignment,
- higher timeframe alignment,
- target liquidity reference.

Quality is capped when:

- there is no structure context,
- the imbalance is subjective/non-FVG without confirmation,
- the zone is invalidated,
- the zone fully fills without a close-confirmed invalidation.

## Public API

```python
from src.analytics.ict_smc.imbalance import detect_imbalances

imbalances = detect_imbalances(candles, structure_events=events, liquidity_sweeps=sweeps)
```

The output includes `imbalance_id`, `imbalance_type`, `direction`,
`detection_method`, `zone_low`, `zone_high`, `zone_mid`, `creation_index`,
`displacement_strength`, `filled_percent`, `active_status`, `respected`,
`quality_score`, `reasons`, and `warnings`.
