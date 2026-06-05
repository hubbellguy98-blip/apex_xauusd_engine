# ICT/SMC Draw on Liquidity Layer

This module formalizes Draw on Liquidity as a deterministic target-selection
layer. It is observer-only and does not alter live VPS execution.

## Purpose

Draw on Liquidity answers:

> Which liquidity pool is price most likely being drawn toward next?

It is used for:

- Directional bias.
- Target selection.
- Reward assessment.
- Blocking-POI awareness.
- Avoiding trades with no logical liquidity destination.

It is not an entry signal.

## Inputs

`determine_draw_on_liquidity(context, liquidity_pools, poi_zones)` consumes:

- Current market context.
- Pre-detected liquidity pools.
- Optional POI zones such as order blocks, FVGs, supply, demand, breakers, and mitigation blocks.

Important context fields include:

- `current_price`
- `current_timeframe`
- `htf_trend_state`
- `itf_trend_state`
- `ltf_trend_state`
- `latest_structure_event`
- `latest_mss`
- `latest_bos`
- `latest_choch`
- `recent_liquidity_sweep`
- `premium_discount_position`
- `session_name`
- `volatility_state`
- `atr`

## Candidate Selection

The analyzer splits liquidity into:

- Buy-side candidates above current price.
- Sell-side candidates below current price.

It filters out:

- Broken liquidity.
- Invalid liquidity.
- Liquidity below minimum quality.
- Liquidity on the wrong side of current price.

## Ranking Model

Each candidate receives a target score based on:

- Liquidity quality.
- Timeframe weight.
- Freshness.
- Touch/confluence strength.
- Context alignment.
- Distance in ATR.
- Session reachability.
- Premium/discount alignment.
- Blocking POI penalty.
- Choppy-market penalty.
- HTF conflict cap.

The highest buy-side and sell-side scores are compared. If one side does not
beat the other by the configured margin, the draw is `unclear`.

## POI Blocking Logic

For buy-side draw:

- Bearish POIs between current price and target can block the path.

For sell-side draw:

- Bullish POIs between current price and target can block the path.

Strong blockers reduce confidence and mark:

- `blocked_by_poi = true`
- `blocking_poi_reference`
- warning to avoid assuming full target reach.

## Confidence Score

Confidence is scored from 0 to 10 using:

- Directional context alignment.
- Target liquidity quality.
- Freshness.
- Recent opposite-side sweep.
- Structure confirmation.
- Path clarity.
- Distance usefulness.
- Session support.
- Premium/discount logic.

Caps and penalties:

- Against HTF bias caps confidence.
- No structure confirmation caps confidence.
- Already swept target caps confidence.
- Choppy market reduces confidence.
- Strong blocking POI reduces confidence.

## Output

The decision object includes:

- `expected_draw`: `buy_side`, `sell_side`, or `unclear`
- `trade_direction_bias`
- `selected_liquidity`
- `target_price_zone`
- `confidence_score`
- `confidence_grade`
- `blocked_by_poi`
- `blocking_poi_reference`
- `best_buy_side_target`
- `best_sell_side_target`
- `alternative_targets`
- `target_selection_reason`
- `warnings`
- `entry_allowed = false`

Final principle:

Draw on Liquidity can guide trade direction and take-profit selection, but
entry still requires separate confirmation from sweep, MSS/CHoCH, displacement,
FVG/order block reaction, risk-to-reward, and higher timeframe alignment.
