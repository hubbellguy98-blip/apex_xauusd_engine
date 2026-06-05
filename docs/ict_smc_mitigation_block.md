# ICT/SMC Mitigation Block

## Purpose

A mitigation block is an old order-flow zone that price revisits after a
confirmed structure shift. Because the concept is subjective, Apex treats it as
a secondary point of interest, not a standalone trade signal.

## Conservative Definition

Mitigation Block = a prior opposite-side candle or zone that price returns to
after confirmed BOS/MSS, where price reacts in the new structure direction
without invalidating the new trend.

No structure shift means no mitigation block.

## Bullish Mitigation

A bullish mitigation block is a prior bearish candle or bearish candle cluster
that price returns to after bullish MSS/BOS.

Required behavior:

- bullish MSS or BOS is confirmed by close
- structure event has moderate or strong displacement
- candidate zone is a bearish candle before the structure shift
- price later retests the zone
- price does not close below `zone_low - invalidation_buffer`
- bullish reaction appears after retest

Best context:

- sell-side liquidity sweep before shift
- bullish FVG or OB confluence
- zone in discount
- HTF bias bullish or neutral
- target buy-side liquidity above

## Bearish Mitigation

A bearish mitigation block is a prior bullish candle or bullish candle cluster
that price returns to after bearish MSS/BOS.

Required behavior:

- bearish MSS or BOS is confirmed by close
- structure event has moderate or strong displacement
- candidate zone is a bullish candle before the structure shift
- price later retests the zone
- price does not close above `zone_high + invalidation_buffer`
- bearish reaction appears after retest

Best context:

- buy-side liquidity sweep before shift
- bearish FVG or OB confluence
- zone in premium
- HTF bias bearish or neutral
- target sell-side liquidity below

## Retest States

The detector classifies mitigation retests as:

- `fresh`
- `touched`
- `partially_mitigated`
- `deep_mitigation`
- `confirmed_reaction`
- `retest_no_reaction`
- `failed`

Retest alone does not allow entry. Reaction confirmation is required.

## Safety Rules

Apex intentionally rejects loose mitigation ideas:

- no BOS/MSS -> invalid
- no displacement -> score capped
- no liquidity context -> score capped
- no retest reaction -> candidate only
- failed retest -> score capped at 3

This keeps mitigation blocks from becoming a label for every old candle on the
chart.

## Output

`detect_mitigation_blocks()` returns objects with:

- `mitigation_type`
- `direction`
- `zone_high`
- `zone_low`
- `mean_threshold`
- `created_by_event`
- `created_after_sweep`
- `fvg_confluence`
- `ob_confluence`
- `premium_discount_alignment`
- `retest_status`
- `reaction_confirmed`
- `fresh_status`
- `quality_score`
- `warnings`

Final rule: mitigation is context. It becomes useful only after structure shift,
return to a clear old order-flow zone, and confirmed reaction.
