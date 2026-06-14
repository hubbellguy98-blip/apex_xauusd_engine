# Power of Three / AMD Strategy Layer

This layer models the ICT Power of Three sequence:

1. Accumulation: a clean, bounded range forms first.
2. Manipulation: price raids one side of that range and then reclaims or rejects back inside.
3. Distribution: price confirms direction with MSS and displacement after the raid.

The module is a research/orchestration layer only. It does not place broker orders directly and should be routed through the existing risk and execution pipeline after forward testing.

## Public Functions

- `detect_accumulation_range()` finds a completed closed-candle range and rejects trended, too-wide, too-small, or spike-dominated ranges.
- `score_accumulation_quality()` scores range cleanliness, boundary clarity, trend balance, candle count, and dominant candle risk.
- `detect_manipulation_sweep()` detects a post-accumulation sell-side or buy-side raid plus reclaim/rejection.
- `detect_distribution_shift()` confirms MSS and displacement after the manipulation phase.
- `score_amd_setup()` scores the completed setup across accumulation, manipulation, distribution, RR, HTF alignment, timing, and XAUUSD safety.
- `generate_amd_signal()` orchestrates the full AMD decision and returns either a valid signal, rejected signal, or context-only state.

## Deterministic Rules

- Closed candles only; a currently forming candle is ignored.
- Accumulation must exist before manipulation.
- Manipulation must sweep one side of the accumulation range.
- Distribution must confirm after manipulation with MSS and displacement.
- Accumulation alone is not tradable.
- Manipulation wick alone is not tradable.
- Bullish AMD requires a sweep below accumulation low followed by upward distribution.
- Bearish AMD requires a sweep above accumulation high followed by downward distribution.
- Double-sided sweep days are rejected unless explicitly allowed and clearly resolved.
- News-restricted and high-spread states are hard filters.
- Risk is anchored beyond the manipulation extreme with ATR and spread buffer.
- Targets are selected from real opposing liquidity pools before range-bound fallback targets.

## Rejection Examples

- `invalid_accumulation_range`
- `real_breakout_not_manipulation`
- `no_distribution_confirmation`
- `no_bullish_mss_after_manipulation`
- `no_bearish_mss_after_manipulation`
- `double_sided_sweep_no_clear_direction`
- `news_restricted`
- `spread_too_high`
- `rr_below_minimum`

## Trading Impact

This strategy is intentionally stricter than a simple liquidity sweep model. It reduces false entries by requiring the market to show a full daily/session narrative: range building, liquidity raid, reclaim/rejection, and then directional expansion. That makes it slower to trigger, but safer for forward-testing than treating every wick sweep as a reversal.
