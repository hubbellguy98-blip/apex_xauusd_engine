# ICT/SMC Displacement Layer

Displacement is the repo's deterministic model for aggressive directional price delivery. It converts the discretionary ICT/SMC idea of "price moved with force" into testable OHLC rules.

This layer is intentionally confirmation-only. It must not trigger a trade by itself. It is designed to support BOS, MSS, CHoCH, liquidity-sweep reversal, FVG, imbalance, order-block, breaker-block, mitigation, and continuation logic.

## Detection Inputs

`detect_displacement(df, atr_period=14, multiplier=1.5)` accepts closed candles only. The implementation ignores forming candles through the `is_closed` / `closed` flag.

Optional context can be passed through:

- `structure_events`: BOS, MSS, CHoCH, or related structure confirmations.
- `liquidity_sweeps`: buy-side or sell-side sweeps before the move.
- `fvg_events`: externally detected FVG zones, with internal three-candle FVG fallback.

## Core Rules

Bullish displacement requires upward directional intent:

- `close > open`
- body dominance, normally `body_to_range_ratio >= 0.55`
- close near high, normally `close_position_ratio >= 0.70`
- expansion versus ATR, average range, or average body
- optional but higher quality: bullish structure break, sell-side sweep before the move, bullish FVG creation

Bearish displacement mirrors the same requirements:

- `close < open`
- body dominance
- close near low
- expansion versus ATR, average range, or average body
- optional but higher quality: bearish structure break, buy-side sweep before the move, bearish FVG creation

## Single-Candle And Multi-Candle Modes

The layer detects:

- `single_candle`: one dominant displacement candle.
- `multi_candle`: a two-to-five candle sequence with majority directional closes, strong cumulative body, range expansion, shallow pullbacks, and a close near the sequence extreme.

## Output Contract

Each displacement event includes:

- `direction`
- `start_index`
- `end_index`
- `strength_score`
- `fvg_created`
- `structure_broken`
- `liquidity_sweep_before`
- `structure_event_type`
- `broken_level`
- `fvg_reference`
- `metrics`
- `reasons`
- `warnings`
- `entry_allowed_from_displacement_alone=False`

## Scoring Meaning

- `0-3`: weak or wick-driven movement, not clean displacement.
- `5-6.5`: moderate displacement confirmation without structure/FVG.
- `7-8.9`: strong displacement, usually with structure or FVG.
- `9-10`: very strong institutional-style delivery, normally after sweep plus structure break plus FVG.

## Safety Notes

Displacement is not a trade setup by itself. The strategy must still require location, liquidity context, structure alignment, risk validation, broker checks, and trade management before any live or demo execution.
