# Order Block Retest After Liquidity Sweep

This strategy layer formalizes an ICT/SMC order-block retest model for XAUUSD research and orchestration.

It does not treat every opposite candle as an order block. A candidate must pass the full chain:

1. Liquidity sweep.
2. Reclaim or rejection back through the swept level.
3. Directional displacement.
4. Candle-close structure break.
5. Last opposite candle before displacement becomes the order block.
6. Price retests the confirmed OB.
7. Reaction confirmation appears.
8. Stop, target, and RR are valid.

## Bullish Model

- Sell-side liquidity is swept.
- Price closes back above the swept level.
- Bullish displacement closes above a valid swing high.
- The last bearish candle before displacement becomes the bullish OB.
- Price returns to the OB.
- A bullish reaction candle or LTF bullish MSS confirms.
- Stop is placed below the OB/sweep extreme.
- Target is buy-side liquidity.

## Bearish Model

- Buy-side liquidity is swept.
- Price closes back below the swept level.
- Bearish displacement closes below a valid swing low.
- The last bullish candle before displacement becomes the bearish OB.
- Price returns to the OB.
- A bearish reaction candle or LTF bearish MSS confirms.
- Stop is placed above the OB/sweep extreme.
- Target is sell-side liquidity.

## Public Functions

- `detect_liquidity_sweep()`
- `detect_displacement()`
- `detect_order_block_after_sweep()`
- `detect_ob_retest()`
- `validate_ob_reaction()`
- `generate_ob_retest_signal()`
- `score_ob_retest_setup()`

Package exports use `detect_ob_liquidity_sweep` and `detect_ob_displacement` aliases to avoid overwriting the earlier Sweep-MSS-FVG helpers.

## Confirmation Modes

- `aggressive`: OB touch is enough, but score is lower.
- `candle_reaction`: requires a closed reaction candle from the OB.
- `ltf_mss`: requires lower-timeframe sweep and MSS confirmation.

## Hard Rejections

- Missing liquidity sweep.
- Weak or missing displacement.
- Structure break not confirmed.
- OB too wide for XAUUSD.
- News-spike order block.
- OB invalidated or over-mitigated.
- Retest without reaction confirmation.
- Missing target liquidity.
- RR below the configured minimum.
- Stop distance too large.
- Unsafe news or spread state.

## Backtesting Safety

The implementation uses closed candles only and never depends on a currently forming candle. Retests are only counted after the OB has been validated by displacement and structure break.
