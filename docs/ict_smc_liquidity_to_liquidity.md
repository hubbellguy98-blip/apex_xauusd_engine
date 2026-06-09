# ICT/SMC Liquidity-to-Liquidity Model

`map_liquidity_to_liquidity_path(context, liquidity_pools, poi_zones)` maps the
likely draw from a recently swept or rejected liquidity pool toward the next
meaningful target pool.

This is not an entry signal. It is a directional path and target-selection
layer. Execution still needs a separate setup, entry model, risk approval, and
broker safety checks.

## Inputs

- `context`: current price, entry price, stop loss, latest sweep, MSS/BOS,
  displacement, HTF bias, expected draw, session state, and risk settings.
- `liquidity_pools`: buy-side/sell-side pools with zone, quality,
  internal/external role, swept status, priority, timeframe, and recency.
- `poi_zones`: order blocks, FVGs, mitigation blocks, or other POIs that can
  block the path between entry and the target.

## Core Rules

- Start liquidity should come from a recent confirmed sweep/reclaim/rejection.
- Bullish paths normally start from sell-side liquidity and target buy-side
  liquidity above price.
- Bearish paths normally start from buy-side liquidity and target sell-side
  liquidity below price.
- Fully swept or invalidated targets are ignored.
- Strong opposing POIs between entry and target cap the target score.
- Internal liquidity is usually a partial target.
- External liquidity is usually a final target.
- Reward-to-risk is calculated before a target is treated as useful.

## Output

The output includes:

- `start_liquidity`
- `target_liquidity`
- `path_bias`
- `blockers`
- `target_score`
- `target_ladder`
- `risk_to_reward`
- `path_valid`
- `path_confidence`
- `warnings`
- `reasons`

The output always includes:

```json
{
  "entry_allowed_from_liquidity_path_alone": false
}
```

That field is intentional. Liquidity-to-liquidity describes where price may
draw next; it does not authorize a trade by itself.

## Valid Bullish Path

Example:

Sell-side liquidity is swept and reclaimed, bullish MSS/displacement confirms,
and the next unswept buy-side liquidity has clean reward-to-risk and no strong
bearish POI blocker.

Expected bias:

```text
bullish
```

## Valid Bearish Path

Example:

Buy-side liquidity is swept and rejected, bearish MSS/displacement confirms,
and the next unswept sell-side liquidity has clean reward-to-risk and no strong
bullish POI blocker.

Expected bias:

```text
bearish
```

## Blocked Path

If a fresh high-quality opposing POI sits between entry and target, the model
keeps the target visible but lowers the score and recommends waiting for
invalidation or choosing a closer internal target.

## Internal Target Only

If only internal liquidity is clean, the model can still map the path, but the
target role is `partial_target` and the output warns `internal_target_only`.

## No Valid Path

If no recent start liquidity is confirmed, the model returns `path_bias` as
`unclear`, leaves target liquidity empty, and warns
`no_recent_start_liquidity_confirmed`.
