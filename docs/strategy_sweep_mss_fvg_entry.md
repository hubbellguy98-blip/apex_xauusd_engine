# Strategy 1: Liquidity Sweep + MSS + FVG Entry

This module codifies the full ICT/SMC reversal-entry sequence:

1. Liquidity sweep.
2. Reclaim or rejection by candle close.
3. Market Structure Shift after the sweep.
4. Displacement after MSS.
5. Fair Value Gap created during the displacement leg.
6. Retracement into the FVG.
7. Stop-loss beyond the sweep extreme.
8. Target at opposite-side liquidity.
9. Reward-to-risk and XAUUSD safety validation.

The implementation is intentionally strict. A sweep alone, MSS alone, or FVG
alone cannot approve a trade.

## Location

The strategy lives in:

`src/strategy/ict_smc_strategies/sweep_mss_fvg_entry.py`

It is currently a local strategy-library layer only. It does not directly place
orders and is not automatically wired into the VPS live runner.

## Required Functions

- `detect_liquidity_sweep(df, liquidity_pools, config)`
- `detect_mss(df, swings, sweep_event, config)`
- `detect_displacement(df, start_index, direction, atr, config)`
- `detect_fvg(df, config)`
- `detect_fvg_retest(df, fvg, direction, config)`
- `generate_sweep_mss_fvg_signal(context, config)`
- `score_sweep_mss_fvg_setup(setup, context, config)`

## Backtest Safety

The detectors ignore unclosed candles. The signal generator should be used
candle-by-candle in backtests, with limit fills only allowed after the setup is
confirmed. If stop and target hit in the same candle, assume the stop hits first
unless lower-timeframe data proves otherwise.

## XAUUSD Safety Filters

The generator can reject setups for:

- high-impact news restriction,
- first news spike,
- oversized news FVG,
- wide spread,
- low-liquidity session chop,
- poor reward-to-risk.

## Validation

Focused tests cover:

- valid bullish setup,
- valid bearish setup,
- sweep without MSS,
- MSS/FVG without sweep,
- news-spike false setup,
- closed-candle-only behavior.
