# Liquidity-to-Liquidity Strategy Layer

This layer converts the ICT/SMC Liquidity-to-Liquidity model into deterministic
bot logic for research, testing, and later orchestration.

It does not place broker orders and it is not an entry model by itself.

## Model

Liquidity-to-Liquidity maps the likely path after price takes one liquidity pool
and then shifts toward another pool.

Bullish path:

1. Sell-side liquidity is swept.
2. Price reclaims the swept area.
3. Bullish MSS/BOS or displacement confirms.
4. The draw becomes buy-side liquidity above price.
5. Entry must come from a separate FVG, order block, breaker, or sweep+MSS
   model.
6. Target must be unswept, reachable, unblocked, and worth the risk.

Bearish path:

1. Buy-side liquidity is swept.
2. Price rejects the swept area.
3. Bearish MSS/BOS or displacement confirms.
4. The draw becomes sell-side liquidity below price.
5. Entry must come from a separate FVG, order block, breaker, or sweep+MSS
   model.
6. Target must be unswept, reachable, unblocked, and worth the risk.

## Safety Rules

- Closed candles only; forming candles are ignored.
- Starting liquidity must be actually swept or raided, not merely approached.
- Target liquidity must be in the expected direction.
- Already swept, invalidated, or consumed targets are rejected.
- Targets blocked by a strong opposing HTF POI are rejected unless a closer
  valid target exists.
- XAUUSD spread, slippage, target distance, candle range, and news restrictions
  are checked before a signal can be valid.
- Poor RR after spread/slippage rejects the signal.
- The output explicitly marks `entry_allowed_from_liquidity_path_alone` as
  false.

## Main Functions

- `detect_liquidity_pools()`
- `classify_internal_external_liquidity()`
- `rank_liquidity_targets()`
- `determine_draw_on_liquidity()`
- `detect_liquidity_to_liquidity_path()`
- `generate_liquidity_to_liquidity_signal()`
- `score_liquidity_to_liquidity_setup()`

## Output

The generator returns a structured dictionary with:

- signal status,
- path bias,
- starting liquidity,
- draw on liquidity,
- ranked target ladder,
- selected target,
- entry model status,
- HTF blockers,
- score,
- rejection reasons.

The layer is meant to make target selection and directional bias more
institutional without allowing the system to trade liquidity alone.
