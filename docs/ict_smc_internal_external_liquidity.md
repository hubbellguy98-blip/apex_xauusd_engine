# ICT/SMC Internal And External Liquidity

This layer classifies already-detected liquidity pools against the currently
active dealing range. It is a target-mapping and context layer, not an entry
signal.

## Purpose

The classifier answers one question: is this liquidity inside the active range,
or is it sitting at/beyond the active range extreme?

- Internal liquidity sits inside the active dealing range.
- External liquidity sits at or beyond the active range high or range low.
- Internal liquidity is usually treated as a partial target, short-term sweep area, or intermediate draw.
- External liquidity is usually treated as the higher-priority final target or major draw-on-liquidity.

## Required Context

The classifier requires a valid dealing range before it will classify anything.
The range must provide confirmed boundaries:

- `range_low`
- `range_high`
- optional `range_direction`
- optional `range_type`
- optional `quality_score`

If the dealing range is missing, invalid, or inverted, the function returns no
classifications and emits
`valid_dealing_range_required_before_liquidity_classification`.

## Classification Rules

A small boundary tolerance is applied so liquidity sitting directly on the range
high or range low is treated as external liquidity instead of being misread as
internal noise.

- Buy-side liquidity at/above `range_high - tolerance` becomes `external_buy_side_liquidity`.
- Sell-side liquidity at/below `range_low + tolerance` becomes `external_sell_side_liquidity`.
- Buy-side liquidity strictly inside those adjusted boundaries becomes `internal_buy_side_liquidity`.
- Sell-side liquidity strictly inside those adjusted boundaries becomes `internal_sell_side_liquidity`.

The default tolerance uses ATR when available. If ATR is not available, it falls
back to a small percentage of the range size.

## Target Roles

- `internal_buy_side_liquidity`: partial target or internal buy-side sweep area.
- `internal_sell_side_liquidity`: internal sweep area or short partial target.
- `external_buy_side_liquidity`: final target or major buy-side sweep area.
- `external_sell_side_liquidity`: final target or major sell-side sweep area.

External liquidity receives a higher base priority score than internal liquidity
because it represents the larger range objective.

## Range Updates

When a BOS, MSS, or other structural event creates a new dealing range, the same
liquidity pool may change classification. For example, buy-side liquidity above
an old range may become internal liquidity inside the new range.

The function emits `liquidity_classification_recalculated_after_range_update`
when a previous range is supplied and the active boundaries changed.

## Safety Boundary

This layer never authorizes an entry by itself. Every result includes:

```text
entry_allowed_from_liquidity_classification_alone = false
```

Execution still requires separate confirmation, risk approval, session
filtering, broker checks, and state-management approval.
