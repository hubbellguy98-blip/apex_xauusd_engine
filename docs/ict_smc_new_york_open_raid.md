# ICT/SMC New York Open Liquidity Raid

The New York Open Liquidity Raid layer turns the NY open raid concept into a
deterministic analytics module for XAUUSD/forex research. It scans important
pre-NY liquidity levels, classifies NY-window interaction, checks news risk,
and builds a structured risk plan.

This layer is not live execution logic. NY open volatility is aggressive, so a
simple sweep is never enough to permit an entry.

## Function

```python
detect_new_york_open_raid(df, session_levels, news_filter, htf_bias)
```

Required output fields:

- `swept_level`
- `direction`
- `mss_confirmed`
- `fvg_entry`
- `risk_plan`
- `confidence_score`

Additional fields include:

- `setup_type`
- `classification`
- `reclaim_status`
- `displacement_confirmed`
- `target_liquidity`
- `news_status`
- `valid_setup`
- `failed_requirements`
- `warnings`

## Liquidity Inputs

The detector can use:

- London high
- London low
- Asian high
- Asian low
- Previous Day High
- Previous Day Low
- Custom liquidity pools

Buy-side liquidity includes London high, Asian high, PDH, equal highs, and
swing highs. Sell-side liquidity includes London low, Asian low, PDL, equal
lows, and swing lows.

## NY Reversal Model

Bullish reversal:

- NY sweeps sell-side liquidity.
- Candle closes back above the swept level.
- Bullish MSS confirms by candle close.
- Bullish displacement forms.
- Bullish FVG or bullish order block provides entry context.
- Target is buy-side liquidity above.

Bearish reversal:

- NY sweeps buy-side liquidity.
- Candle closes back below the swept level.
- Bearish MSS confirms by candle close.
- Bearish displacement forms.
- Bearish FVG or bearish order block provides entry context.
- Target is sell-side liquidity below.

## NY Continuation Model

Bullish continuation:

- London trend is bullish.
- NY accepts above buy-side liquidity or bullish structure.
- Bullish displacement and FVG/OB appear.
- Target is higher buy-side liquidity such as PDH.

Bearish continuation:

- London trend is bearish.
- NY accepts below sell-side liquidity or bearish structure.
- Bearish displacement and FVG/OB appear.
- Target is lower sell-side liquidity such as PDL.

Continuation does not require reversal MSS, but it does require London-trend
context, displacement, an entry zone, target liquidity, and risk validation.

## News Filter

The NY open layer treats news risk as a first-class gate.

If `high_impact_news_nearby=true` and `allow_trading_during_news=false`, the
detector returns `news_blackout_no_trade` before classifying candles.

If news or spread risk is elevated but still allowed, confidence is reduced and
the candidate is treated with caution.

## Safety Rule

Every output includes:

```python
"entry_allowed_from_new_york_raid_alone": False
```

This preserves the core rule: NY open raid is a setup model, not an automatic
entry signal. A valid setup requires liquidity interaction, reclaim/rejection
or continuation acceptance, MSS/BOS context, displacement, FVG/OB, target
liquidity, news safety, HTF context, and a viable risk plan.
