# ICT/SMC Target Logic

This layer selects profit targets after entry and stop-loss are already known.
It is an analytics and validation layer only. It does not submit orders or
change the VPS live runner by itself.

## Purpose

ICT/SMC target selection should be based on liquidity, not random fixed points.

For bullish trades, the system targets buy-side liquidity above entry.

For bearish trades, the system targets sell-side liquidity below entry.

The function is:

```python
select_smc_targets(entry, stop, liquidity_pools, poi_zones, min_rr)
```

## Required Output

The required fields are:

```python
{
    "target_1": dict | None,
    "target_2": dict | None,
    "final_target": dict | None,
    "rr_values": dict,
    "target_quality_score": float,
}
```

The implementation also returns direction, rejected targets, blocked targets,
candidate targets, practical final target, warnings, reasons, and execution
permission.

## Direction Inference

Direction is inferred from entry and stop:

```text
entry > stop = bullish
entry < stop = bearish
entry == stop = invalid risk model
```

Bullish targets must be above entry and must be buy-side liquidity.

Bearish targets must be below entry and must be sell-side liquidity.

## Target Types

Supported liquidity target types include:

- Opposite liquidity
- Previous day high or low
- Asian high or low
- London high or low
- New York high or low
- Equal highs or lows
- Internal liquidity
- External liquidity
- Range high or low
- Session high or low
- Swing high or low
- HTF liquidity
- Opening or news range high or low

## Target Ladder

`target_1`

Usually the nearest valid internal liquidity. It can be used as partial profit,
even when its RR is below the full trade minimum.

`target_2`

Usually the next stronger session, range, or external liquidity target.

`final_target`

The best unblocked liquidity target that meets minimum RR. External liquidity
is preferred when available.

## HTF POI Blockers

A target can be real liquidity but still unsafe if a strong opposing HTF POI
stands between entry and target.

For bullish trades, a bearish HTF POI can block upside.

For bearish trades, a bullish HTF POI can block downside.

Blocked targets are downgraded and cannot justify execution until price accepts
beyond the blocker.

## Reward-To-Risk

The final target must meet the minimum RR requirement.

If only partial targets are available and no unblocked liquidity target meets
minimum RR, the trade should be skipped or the system should wait for a better
entry.

## Final Principle

A trade is not complete until entry, stop, and target all make sense. If no
liquidity target gives acceptable RR without being blocked by HTF POI, the bot
should not execute the trade.
