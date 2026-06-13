# FVG Continuation Strategy Layer

The FVG Continuation model treats a Fair Value Gap as a pullback entry only after structure has already proven continuation. It is not a generic reversal model and it does not trade every imbalance.

## Required Sequence

1. Higher-timeframe bias is bullish or bearish.
2. Setup timeframe confirms BOS by candle close.
3. Directional displacement appears after or around the BOS.
4. A same-direction FVG is created by that displacement.
5. Price retraces into the FVG.
6. Price reacts from the FVG or lower-timeframe MSS confirms.
7. A next-liquidity target exists.
8. RR, news, spread, chop, and HTF blocker filters pass.

## Bullish Model

- HTF bias points to buy-side liquidity.
- Price closes above a valid swing high.
- Bullish displacement creates a bullish FVG.
- Price retraces into the FVG, ideally to midpoint.
- Bullish reaction confirms.
- Stop is below the FVG/recent structure with buffer.
- Target is next buy-side liquidity.

## Bearish Model

- HTF bias points to sell-side liquidity.
- Price closes below a valid swing low.
- Bearish displacement creates a bearish FVG.
- Price retraces into the FVG, ideally to midpoint.
- Bearish reaction confirms.
- Stop is above the FVG/recent structure with buffer.
- Target is next sell-side liquidity.

## Hard Rejections

- `htf_bias_not_aligned`
- `no_bos_for_continuation`
- `wick_only_bos`
- `no_displacement`
- `weak_displacement`
- `no_fvg`
- `fvg_direction_mismatch`
- `fvg_not_created_after_bos`
- `random_fvg_no_displacement`
- `fvg_too_large`
- `fvg_too_small`
- `fvg_invalidated`
- `choppy_market_random_fvg_risk`
- `no_valid_target`
- `target_already_swept`
- `htf_poi_blocks_target`
- `rr_below_minimum`
- `news_restricted`
- `spread_too_high`
- `random_fvg_no_stabilized_structure`

## Public API

- `detect_htf_bias()`
- `detect_bos()`
- `detect_displacement()`
- `detect_fvg()`
- `detect_fvg_retracement()`
- `validate_fvg_continuation()`
- `generate_fvg_continuation_signal()`
- `score_fvg_continuation_setup()`

The package export aliases the common detector names as `detect_fvg_continuation_displacement` and `detect_fvg_continuation_fvg` to avoid collisions with the earlier Sweep + MSS + FVG strategy.

## Deployment Note

This layer is research/orchestration code. It does not place live or demo broker orders directly. The VPS runner should only consume it after separate forward testing and risk review.
