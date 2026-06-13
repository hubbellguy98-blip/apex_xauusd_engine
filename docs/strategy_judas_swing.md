# Judas Swing / Session Manipulation Strategy

The Judas Swing layer models a false move outside a completed session range,
usually after Asian accumulation and during London or New York activity.

This strategy does not approve a trade from a wick sweep alone. A valid setup
requires the full sequence:

1. Completed session range.
2. Clean range quality.
3. One-sided manipulation sweep.
4. Reclaim or rejection back inside the range.
5. Market structure shift after the manipulation.
6. Directional displacement.
7. FVG retracement entry.
8. Stop beyond the manipulation extreme.
9. Target toward opposite range liquidity or external liquidity.
10. Minimum risk-reward and safety filters.

## Bullish Judas

Bullish Judas logic:

- Asian/session low is swept.
- Price reclaims above the range low.
- A bullish MSS closes above the post-sweep swing high.
- Bullish displacement creates a valid FVG.
- Entry triggers on retracement into the FVG.
- Stop is placed below the manipulation low.
- Target is the range high or higher buy-side liquidity.

## Bearish Judas

Bearish Judas logic:

- Asian/session high is swept.
- Price rejects back below the range high.
- A bearish MSS closes below the post-sweep swing low.
- Bearish displacement creates a valid FVG.
- Entry triggers on retracement into the FVG.
- Stop is placed above the manipulation high.
- Target is the range low or lower sell-side liquidity.

## Hard Rejections

The layer rejects:

- Incomplete session ranges.
- Messy, too wide, too narrow, or spike-driven ranges.
- Sweeps without reclaim.
- Sweeps that accept beyond the range and become real breakouts.
- Missing MSS.
- Missing displacement.
- Missing FVG/OB entry zone.
- Missing retracement entry.
- Poor risk-reward.
- News blackout conditions.
- Unsafe spread.
- Double-sweep chop without clean structure afterward.

## Public Functions

- `calculate_session_range()`
- `score_session_range_quality()`
- `detect_judas_sweep()`
- `detect_range_reclaim()`
- `detect_judas_mss()`
- `generate_judas_swing_signal()`
- `score_judas_swing_setup()`

## Safety Note

This module is a pure strategy/research layer. It does not send broker orders.
Live deployment should wire it through the existing risk firewall, position
manager, execution router, and forward-testing controls.
