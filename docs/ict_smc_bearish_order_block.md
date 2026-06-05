# ICT/SMC Bearish Order Block Layer

This layer specializes the general Order Block model for bearish setups only.

## Core Definition

A bearish order block is the last bullish closed candle before bearish
displacement that causes bearish BOS or bearish MSS.

It is a reaction zone, not a sell signal by itself.

## Best Context

A bearish OB is strongest when:

- buy-side liquidity was swept first,
- bearish displacement follows,
- bearish MSS or BOS is confirmed by candle close,
- bearish FVG is created,
- the OB is in premium,
- HTF bias or draw is bearish,
- sell-side target liquidity exists below,
- the OB is fresh.

## Functions

```python
detect_bearish_order_block(...)
```

Returns bearish OB formation objects with:

- `created_after_sweep`
- `bos_confirmed`
- `mss_confirmed`
- `fvg_created`
- `fvg_overlap`
- `retest_status`
- `zone_low`
- `zone_high`
- `mean_threshold`
- `quality_score`
- `valid_bearish_ob`

```python
validate_bearish_ob_retest(...)
```

Checks whether price returned upward into the bearish OB and whether rejection is
strong enough to allow short-entry context.

## Retest Status

- `fresh`: no retest yet.
- `touched`: price entered the lower edge of the zone.
- `partially_mitigated`: price reached mean threshold.
- `deep_mitigation`: price reached zone high but closed back below it.
- `confirmed_rejection`: retest plus bearish rejection evidence.
- `failed`: candle closed above zone high.

## Entry Policy

Entry is allowed only when:

- the OB has not failed,
- retest rejection is confirmed,
- sell-side target liquidity exists,
- reward-to-risk is acceptable.

Bearish OB alone never allows entry.
