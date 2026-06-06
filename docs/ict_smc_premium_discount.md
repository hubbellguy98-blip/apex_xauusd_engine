# ICT/SMC Premium And Discount Layer

Premium and discount divide a selected dealing range into upper and lower halves.

This layer is a location and quality filter. It is not an entry signal. The system should still require liquidity context, MSS/BOS/CHoCH, displacement, POI reaction, risk-to-reward, and broker checks before any demo or live order.

## Core Calculation

The required function is:

```python
calculate_premium_discount(swing_low, swing_high)
```

The detector normalizes the anchors so:

- `range_low = min(swing_low.price, swing_high.price)`
- `range_high = max(swing_low.price, swing_high.price)`
- `equilibrium = (range_low + range_high) / 2`
- `discount_zone = range_low` to `equilibrium`
- `premium_zone = equilibrium` to `range_high`

It also creates:

- `deep_discount_zone`: 0% to 25%
- `normal_discount_zone`: 25% to 50%
- `normal_premium_zone`: 50% to 75%
- `deep_premium_zone`: 75% to 100%

## Current Price Location

When `current_price` is supplied, the module classifies location as:

- `outside_range_below`
- `deep_discount`
- `discount`
- `equilibrium_zone`
- `premium`
- `deep_premium`
- `outside_range_above`

An equilibrium buffer prevents tiny price differences around 50% from creating false premium/discount bias.

## Dealing Range Validation

A dealing range is considered weak when:

- one or both swing anchors are unconfirmed
- swing strength is below the configured threshold
- the range is too small relative to ATR
- the selected range is random microstructure noise

Weak ranges are marked with:

- `range_valid=False`
- low `range_quality_score`
- `weak_or_invalid_dealing_range` warning

## Trade Quality Interpretation

Bullish setups are preferred when price or POI is in discount.

Bearish setups are preferred when price or POI is in premium.

Near equilibrium, both directions are treated as neutral because the location edge is weak.

## Higher Timeframe Control

Higher timeframe premium/discount can strengthen or weaken lower timeframe setup quality:

- HTF discount supports LTF bullish setups.
- HTF premium supports LTF bearish setups.
- HTF premium weakens LTF bullish setups unless continuation context is strong.
- HTF discount weakens LTF bearish setups unless continuation context is strong.

## Output Contract

Each result includes:

- `equilibrium`
- `premium_zone`
- `discount_zone`
- `current_price_location`
- `position_percent`
- `range_valid`
- `range_quality_score`
- `trade_filter`
- `poi_quality_filter`
- `warnings`
- `entry_allowed_from_premium_discount_alone=False`

This keeps the concept useful for scoring without allowing it to trade by itself.
