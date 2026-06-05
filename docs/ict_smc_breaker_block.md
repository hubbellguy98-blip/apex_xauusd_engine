# ICT/SMC Breaker Block

## Purpose

A breaker block is a failed order block that flips into a reaction zone in the
opposite direction. It is not a generic support/resistance flip. The model only
promotes an order block into a breaker when price shows acceptance beyond the
original zone with a closed candle.

## Bullish Breaker

A bullish breaker starts as a bearish order block. If price closes above the
bearish OB high plus a configured buffer, the bearish OB is considered failed.
That failure traps sellers who entered from the bearish zone, so the original
zone can become a bullish point of interest.

Required confirmation:

- Original OB direction is bearish.
- Closed candle accepts above `zone_high + break_buffer`.
- Wick-only movement above the zone is not enough.
- A later retest should hold the breaker zone and react bullishly before entry.

## Bearish Breaker

A bearish breaker starts as a bullish order block. If price closes below the
bullish OB low plus a configured buffer, the bullish OB is considered failed.
That failure traps buyers who entered from the bullish zone, so the original
zone can become a bearish point of interest.

Required confirmation:

- Original OB direction is bullish.
- Closed candle accepts below `zone_low - break_buffer`.
- Wick-only movement below the zone is not enough.
- A later retest should hold the breaker zone and react bearishly before entry.

## Wick-Only Attempts

If price only wicks beyond the original OB but closes back inside or against the
failure level, the detector returns a breaker attempt with:

- `retest_status = wick_only_failure_attempt`
- `confirmed_breaker = false`
- warning `no_acceptance_close_beyond_ob`
- low confidence score

This protects the engine from treating liquidity probes as confirmed breaker
acceptance.

## Retest States

The detector classifies breaker retests as:

- `not_retested`
- `touched`
- `mean_threshold_retest`
- `deep_retest`
- `confirmed_reaction`
- `failed`

Entry is not allowed from the breaker alone. The breaker must be confirmed, must
not fail on retest, and should show a reaction from the zone.

## Confidence Model

Confidence is scored from 0 to 10 using:

- original OB quality
- clean acceptance close beyond the failed OB
- displacement through the failed zone
- BOS, MSS, or CHoCH after failure
- retest reaction
- liquidity sweep context
- FVG/imbalance context
- premium/discount or HTF alignment
- zone efficiency

The output is designed for review and future strategy composition, not direct
live execution by itself.
