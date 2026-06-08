# ICT Silver Bullet Model

The Silver Bullet layer converts the trader definition into deterministic,
testable rules. It is analytics-only and is not allowed to place trades by
itself.

## Core Sequence

The detector requires the full chain:

1. Closed candles inside a configured time window.
2. Liquidity sweep inside that window.
3. Reclaim or rejection after the sweep.
4. Displacement in the opposite direction.
5. Fair Value Gap created by displacement.
6. Retracement into that FVG.
7. Opposing unswept liquidity target.
8. Acceptable risk-to-reward.

If any required component is missing, `valid_setup` stays `false`.

## Bullish Model

A bullish Silver Bullet requires:

- sell-side liquidity swept,
- price reclaims the swept level,
- bullish displacement,
- bullish FVG,
- FVG retracement reaction,
- buy-side liquidity target above entry.

The stop is placed below the sweep extreme or below the FVG invalidation level
with an ATR-derived XAUUSD buffer.

## Bearish Model

A bearish Silver Bullet requires:

- buy-side liquidity swept,
- price rejects the swept level,
- bearish displacement,
- bearish FVG,
- FVG retracement rejection,
- sell-side liquidity target below entry.

The stop is placed above the sweep extreme or above the FVG invalidation level
with an ATR-derived XAUUSD buffer.

## Time Window Safety

Silver Bullet is time-window-based. A perfect sweep/FVG sequence outside the
configured window is rejected as `outside_time_window`.

The detector supports:

- one or more time windows,
- timezone conversion using `zoneinfo`,
- allowed day filters,
- naive timestamp warnings with UTC fallback.

## Execution Safety

The output includes:

```text
entry_allowed_from_silver_bullet_alone = false
```

That flag is intentional. This module teaches the engine the concept; execution
must still pass the existing risk firewall, spread checks, one-trade-at-a-time
rules, stop-quality controls, and broker validation.

## Function

```python
detect_silver_bullet_setup(df, time_window, liquidity_pools, htf_bias)
```

Required output fields include:

- `valid_setup`
- `direction`
- `sweep_level`
- `fvg_zone`
- `entry`
- `stop`
- `target`
- `score`

Useful extra fields include:

- `classification`
- `quality_grade`
- `sweep`
- `displacement`
- `trade_plan`
- `rr`
- `htf_alignment`
- `failed_requirements`
- `warnings`
