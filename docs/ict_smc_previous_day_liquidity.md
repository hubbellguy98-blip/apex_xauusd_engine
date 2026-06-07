# ICT/SMC Previous Day High And Low Liquidity

This layer converts Previous Day High and Previous Day Low into deterministic,
testable liquidity context for the Apex XAUUSD engine.

## Purpose

PDH and PDL are major external liquidity references:

- `PDH` is previous day high and represents buy-side liquidity.
- `PDL` is previous day low and represents sell-side liquidity.
- PDH can be a long target, a bearish raid area, or bullish continuation level.
- PDL can be a short target, a bullish raid area, or bearish continuation level.

The layer is observer-only. It does not authorize an entry from PDH/PDL alone.

## Previous Day Level Calculation

`calculate_previous_day_levels(df)` uses confirmed closed candles only.

It groups candles by trading day, excludes the latest/current trading day, and
calculates the completed previous day's:

- high (`pdh`)
- low (`pdl`)
- open
- close
- range
- midpoint
- volume

The output also creates two liquidity objects:

- `previous_day_high`, `buy_side`, `external_buy_side_liquidity`
- `previous_day_low`, `sell_side`, `external_sell_side_liquidity`

Use the same daily session definition in live trading and backtests. Broker day,
UTC day, and New York close day can produce different levels.

## Raid Detection

`detect_pdh_pdl_raid(intraday_df, pdh, pdl)` classifies current-session
interaction with PDH/PDL.

PDH sweep/rejection:

- candle high trades above PDH plus buffer
- candle closes back below PDH
- bias becomes bearish possible, not automatic short

PDH breakout/acceptance:

- candle closes above PDH plus buffer
- candle body is bullish
- close location is near the high
- bias becomes bullish continuation possible

PDL sweep/reclaim:

- candle low trades below PDL plus buffer
- candle closes back above PDL
- bias becomes bullish possible, not automatic long

PDL breakdown/acceptance:

- candle closes below PDL plus buffer
- candle body is bearish
- close location is near the low
- bias becomes bearish continuation possible

## Confirmation Requirements

Highest-quality PDH/PDL models need more than a level raid:

- confirmed MSS after the raid
- displacement in expected direction
- FVG after the MSS/displacement
- retest into FVG or order block
- valid target liquidity
- valid risk management

Without MSS, score is capped. Without FVG/displacement, score is capped.

## XAUUSD Notes

For XAUUSD, prefer ATR-based buffers instead of fixed points. PDH/PDL raids are
often most useful around London and New York sessions. Be careful around news
and rollover because spreads can distort wicks and produce false displacement.
