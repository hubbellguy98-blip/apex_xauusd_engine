# ICT/SMC Setup Scoring Engine

This layer scores an already-detected ICT/SMC setup from 0 to 10.

It is an analytics and validation layer only. It does not discover setups, does
not read forming candles, does not submit MT5 orders, and does not replace the
entry, stop-loss, or target modules.

## Function

```python
score_smc_setup(setup_context)
```

## Purpose

The score separates high-confluence setups from weak or unsafe setups.

The engine combines:

- HTF bias alignment
- Premium/discount location
- Liquidity sweep quality
- MSS/BOS confirmation
- Displacement strength
- FVG/OB quality
- POI freshness
- Session timing
- News and spread safety
- Reward-to-risk
- Target clarity
- Optional volume confirmation

## Weights

Without volume, the component weights total 100:

- HTF bias alignment: 10
- Premium/discount: 8
- Liquidity sweep quality: 12
- MSS/BOS confirmation: 15
- Displacement strength: 10
- FVG/OB quality: 10
- POI freshness: 7
- Session timing: 6
- News filter: 8
- Risk-reward: 8
- Target clarity: 6

With volume enabled, volume receives a small 7% weight and the core price-action
weights are reduced slightly.

## Hard Filters

Some conditions block execution even if the weighted score is high:

- Setup not confirmed
- Missing required liquidity sweep
- Missing MSS/BOS in conservative mode
- Missing valid FVG/OB entry zone
- Invalidated POI
- News blackout
- Unsafe spread
- Invalid stop
- RR below minimum
- Missing target
- Target blocked by HTF POI

Caps are also applied. For example, news blackout caps the score at 3, missing
MSS/BOS caps it at 5.5, and poor RR caps it at 5.

## Grades

- A+: 9.0 to 10
- A: 8.0 to 8.99
- B: 7.0 to 7.99
- C: 6.0 to 6.99
- D: 5.0 to 5.99
- F: below 5

Conservative XAUUSD mode uses a default minimum execution threshold of 7.5.

## Output

The function returns:

```python
{
    "total_score": 8.42,
    "uncapped_score": 8.42,
    "grade": "A",
    "trade_allowed": true,
    "trade_threshold": 7.5,
    "component_scores": {...},
    "hard_filter_failures": [],
    "caps_applied": [],
    "warnings": [...],
    "reasons": [...],
    "decision_reason": "Setup score meets threshold and no hard filters failed.",
}
```

## Final Principle

The scorer should protect the bot from forced trades. A setup should not be
allowed only because one component looks attractive. Missing MSS/BOS, invalid
POI, poor RR, unclear targets, unsafe news/spread conditions, or HTF blockers
must keep execution disabled until the context improves.
