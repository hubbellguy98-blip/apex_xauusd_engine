# Breaker Block Strategy

The Breaker Block layer formalizes the ICT/SMC breaker model as a deterministic research signal. It does not place broker orders directly. Live usage should route the final signal through the existing risk and execution pipeline only after forward testing.

## Core Definition

A breaker block is a failed order block, not a simple support/resistance flip.

Bullish breaker sequence:

1. A valid bearish order block exists.
2. Price closes above the bearish order block high.
3. Price accepts above the failed order block.
4. A bullish structure shift is confirmed.
5. Price retests the old bearish order block from above.
6. A bullish reaction confirms entry.
7. Target, risk/reward, spread, and news filters pass.

Bearish breaker sequence:

1. A valid bullish order block exists.
2. Price closes below the bullish order block low.
3. Price accepts below the failed order block.
4. A bearish structure shift is confirmed.
5. Price retests the old bullish order block from below.
6. A bearish reaction confirms entry.
7. Target, risk/reward, spread, and news filters pass.

## Public Functions

- `detect_order_blocks()` finds original order blocks created by displacement and structure break.
- `detect_order_block_failure()` separates true close-based failure from wick-only failure.
- `detect_breaker_block()` converts a failed order block into a breaker only after acceptance and structure shift.
- `detect_breaker_retest()` waits for a retest after the breaker is valid.
- `validate_breaker_reaction()` confirms candle or lower-timeframe reaction from the breaker.
- `score_breaker_setup()` scores original OB quality, failure, acceptance, structure shift, retest, reaction, RR, and safety.
- `generate_breaker_signal()` orchestrates the full no-trade or valid-signal decision.

## Hard Rejections

The layer rejects setups for:

- `no_original_order_block`
- `original_ob_invalid`
- `no_order_block_failure`
- `wick_only_ob_failure`
- `no_acceptance_beyond_ob`
- `no_bullish_structure_shift`
- `no_bearish_structure_shift`
- `no_valid_breaker`
- `waiting_for_breaker_retest`
- `breaker_invalidated`
- `breaker_invalidated_on_retest`
- `no_breaker_reaction`
- `breaker_zone_too_wide`
- `rr_below_minimum`
- `spread_too_high_or_caution`
- `no_valid_target`
- `target_already_swept`
- `htf_poi_blocks_target`
- `news_restricted`

## XAUUSD Safety Notes

The model uses closed candles only and applies spread/slippage-aware stop buffering. For gold, wide breaker zones can make the stop too large and destroy risk/reward, so the strategy explicitly blocks zones above the configured ATR width limit.

The model also rejects target liquidity that is missing, already swept, or blocked by a higher-timeframe point of interest when the context provides that information.

## Expected Usage

Use this layer to produce structured setup candidates for backtests and forward tests. It should be combined with:

- session filter,
- higher-timeframe bias,
- news filter,
- spread filter,
- adaptive lot/risk engine,
- one-position-at-a-time execution management.

It is intentionally conservative about labeling breakers, because mislabeling ordinary resistance flips as breakers is one of the fastest ways to overtrade weak setups.
