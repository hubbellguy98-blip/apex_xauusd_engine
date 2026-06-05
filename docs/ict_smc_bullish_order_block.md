# ICT/SMC Bullish Order Block Layer

This layer specializes the general Order Block model for bullish setups only.

## Core Definition

A bullish order block is the last bearish closed candle before bullish
displacement that causes bullish BOS or bullish MSS.

It is a reaction zone, not a buy signal by itself.

## Best Context

A bullish OB is strongest when:

- sell-side liquidity was swept first,
- bullish displacement follows,
- bullish MSS or BOS is confirmed by candle close,
- bullish FVG is created,
- the OB is in discount,
- HTF bias or draw is bullish,
- buy-side target liquidity exists above,
- the OB is fresh.

## Functions

```python
detect_bullish_order_block(...)
```

Returns bullish OB formation objects with:

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
- `valid_bullish_ob`

```python
validate_bullish_ob_retest(...)
```

Checks whether price returned to the bullish OB and whether reaction is strong
enough to allow long-entry context.

## Retest Status

- `fresh`: no retest yet.
- `touched`: price entered the top of the zone.
- `partially_mitigated`: price reached mean threshold.
- `deep_mitigation`: price reached zone low but reclaimed.
- `confirmed_reaction`: retest plus bullish reaction evidence.
- `failed`: candle closed below zone low.

## Entry Policy

Entry is allowed only when:

- the OB has not failed,
- retest reaction is confirmed,
- buy-side target liquidity exists,
- reward-to-risk is acceptable.

Bullish OB alone never allows entry.
