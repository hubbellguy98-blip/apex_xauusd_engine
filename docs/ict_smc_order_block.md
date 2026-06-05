# ICT/SMC Order Block Layer

This layer converts Order Blocks into deterministic detection rules.

## Core Definition

A bullish order block is the last bearish closed candle before bullish
displacement that causes bullish BOS or bullish MSS.

A bearish order block is the last bullish closed candle before bearish
displacement that causes bearish BOS or bearish MSS.

The candle color alone is never enough. A valid OB must be connected to
displacement and, for high quality, to a confirmed structure break.

## Detection Inputs

- Closed OHLCV candles only.
- Confirmed BOS events.
- Confirmed MSS events.
- Optional liquidity sweeps.
- Optional premium/discount context.
- Optional higher-timeframe context.

The function is:

```python
detect_order_blocks(df, swings, bos_events, mss_events, liquidity_sweeps)
```

`swings` are accepted for API compatibility, but the current detector expects
BOS/MSS modules to have already consumed confirmed swings.

## Zone Definitions

Every OB stores three zones:

- `full_range`: candle high to candle low.
- `body_range`: candle open to candle close.
- `refined_range`: wick-to-open hybrid.

The default selected zone is `full_range` because it is safer for detection and
mitigation analysis. The mean threshold is the 50% level of the selected zone.

## Freshness And Failure

Bullish OB:

- `fresh`: no later candle enters the zone.
- `touched`: price enters upper zone.
- `partially_mitigated`: price reaches the mean threshold.
- `fully_mitigated_but_not_failed`: price reaches the low but closes back above it.
- `failed`: candle closes below zone low.

Bearish OB:

- `fresh`: no later candle enters the zone.
- `touched`: price enters lower zone.
- `partially_mitigated`: price reaches the mean threshold.
- `fully_mitigated_but_not_failed`: price reaches the high but closes back below it.
- `failed`: candle closes above zone high.

Failed bullish OBs can become bearish breaker candidates. Failed bearish OBs can
become bullish breaker candidates.

## Quality Factors

Score improves when:

- OB caused BOS or MSS.
- Displacement is present and strong.
- Liquidity was swept before displacement.
- FVG was created after displacement.
- Bullish OB is in discount.
- Bearish OB is in premium.
- OB is fresh.
- Zone size is efficient.

Score is reduced when:

- No BOS/MSS exists.
- No displacement exists.
- No liquidity sweep context exists.
- Mean threshold is already touched.
- OB is stale, fully mitigated, or failed.
- Zone is too wide.

## Entry Policy

Order Block alone is never an entry signal.

The output always includes:

```text
entry_allowed_from_ob_alone = false
```

The strategy must wait for lower-timeframe confirmation such as sweep, CHoCH,
MSS, rejection, displacement, or FVG/OB confluence before allowing execution.
