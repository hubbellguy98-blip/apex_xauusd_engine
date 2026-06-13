# ICT/SMC False-Positive Filter

This layer filters already-detected ICT/SMC setups before they reach entry,
risk, or execution logic.

It does not create new setups. It prevents weak pattern detections from being
treated as real trade opportunities.

## Pipeline Position

```text
Market data
  -> basic ICT/SMC setup detection
  -> false-positive filter
  -> entry model
  -> risk management
  -> execution
```

The filter separates detected setups into:

- `valid_setups`: enough confluence to continue toward entry/risk logic
- `context_only_setups`: useful market context, but not tradable yet
- `rejected_setups`: unsafe, weak, stale, duplicated, or structurally invalid

## Public Function

```python
filter_false_smc_signals(setups, context)
```

Required output:

- `valid_setups`
- `rejected_setups`
- `rejection_reasons`

Additional output:

- `context_only_setups`
- `warnings`
- `filter_summary`
- `highest_quality_setup`

## Hard Rejections

The module rejects setups immediately when the condition is dangerous or invalid.

Examples:

- setup uses unclosed/live forming candles
- missing entry, stop, target, or RR
- random FVG without displacement
- FVG already filled or inactive
- weak order block without displacement or structure break
- required liquidity sweep is missing
- sweep has no reclaim or rejection
- reversal model has no MSS in conservative mode
- continuation model has no BOS
- strong HTF POI blocks the target path
- RR is below minimum
- market is choppy and setup quality is not high enough
- news blackout is active
- first news spike is being treated as displacement
- spread or slippage is too high
- target liquidity is already swept
- setup is stale, duplicated, or inside cooldown

## Soft Penalties

Some weaknesses reduce confidence but do not always reject.

Examples:

- FVG is partially filled
- order-block quality is near the minimum
- HTF bias is opposite but not blocking
- spread is near the maximum
- liquidity sweep is optional but absent
- price is near equilibrium with some valid confluence
- signal frequency is elevated but not extreme

If soft penalties reduce the filtered score below the tradable threshold, the
setup becomes `context_only`.

## Context-Only Setups

Context-only means the pattern can still inform market reading, but should not
be sent to execution.

Example:

```text
Sell-side sweep exists and price reclaimed the level, but bullish MSS has not
confirmed yet.
```

In balanced or permissive mode this can be kept as context. In conservative
mode it is rejected.

## Output Example

```python
{
    "function": "filter_false_smc_signals",
    "valid_setups": [
        {
            "setup_id": "SETUP_BULL_001",
            "status": "valid",
            "passed_filters": [
                "closed_candle_only",
                "liquidity_sweep_confirmed",
                "mss_confirmed",
                "fvg_valid",
                "rr_valid",
            ],
        }
    ],
    "context_only_setups": [
        {
            "setup_id": "SETUP_SWEEP_002",
            "status": "context_only",
            "rejection_reasons": ["no_mss_for_reversal"],
        }
    ],
    "rejected_setups": [
        {
            "setup_id": "SETUP_FVG_003",
            "status": "rejected",
            "rejection_category": "fvg_failure",
            "rejection_reasons": [
                "random_fvg_no_displacement",
                "random_fvg_no_structure_confirmation",
                "price_in_middle_of_range",
            ],
        }
    ],
}
```

## Design Principle

The bot should not trade isolated concepts. It should only trade high-quality
combinations of liquidity, structure, displacement, POI, timing, target clarity,
and risk. This filter is the layer that stops chart noise from becoming orders.
