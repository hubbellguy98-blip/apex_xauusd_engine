# ICT/SMC Entry Model

The entry model is the final decision layer after an ICT/SMC setup has already
been confirmed. It does not create setup context from zero.

A confirmed setup may include:

- liquidity sweep
- MSS or BOS confirmation
- displacement
- FVG creation
- order block creation
- POI validation
- premium/discount alignment
- selected target liquidity
- news, spread, and session safety

The entry model answers:

- where can the bot enter?
- should the entry be limit or market?
- where is invalidation?
- where is the target?
- is reward-to-risk acceptable?
- is the final confidence high enough?

## Function

```python
generate_entry_signal(setup, df, risk_config)
```

## Required Setup Fields

Useful setup fields:

- `setup_id`
- `confirmed`
- `direction`
- `setup_score`
- `mss_event` or `bos_event`
- `displacement`
- `fvg_zones`
- `order_blocks`
- `target_liquidity`
- `sweep_extreme`
- `invalidation_level`
- `news_filter_status`
- `spread_status`
- `killzone_status`
- `ltf_confirmation`

If `confirmed` is false, the function returns no entry.

## Risk Config

Useful risk config fields:

- `entry_mode`: `aggressive`, `balanced`, or `conservative`
- `min_rr`
- `preferred_rr`
- `minimum_entry_score`
- `minimum_zone_quality`
- `aggressive_min_setup_score`
- `aggressive_min_zone_score`
- `stop_buffer_atr_multiplier`
- `max_stop_atr_multiplier`
- `use_ltf_confirmation`
- `allow_market_order`
- `allow_limit_order`
- `cancel_if_news_restricted`

## Entry Modes

Aggressive mode:

- uses limit entry at FVG midpoint or OB mean threshold
- requires high setup score and high zone quality
- scores lower than confirmation entry because it has no reaction candle

Conservative mode:

- waits for price to retest the selected zone
- requires a closed candle reaction from the zone
- uses market order after confirmation

Balanced mode:

- prefers retest plus partial reaction
- can fall back to high-quality limit entry if setup score is strong

## Safety Rules

The model blocks entries when:

- setup is not confirmed
- direction is invalid
- MSS/BOS, displacement, entry zone, or target context is missing
- news filter is restricted
- spread is unsafe
- session or killzone filter blocks trading
- no valid FVG/OB zone exists
- price has not retested the zone in conservative mode
- candle confirmation is missing
- LTF confirmation is required but absent
- stop-loss is invalid
- stop is too wide relative to ATR
- target is missing or on the wrong side
- reward-to-risk is below `min_rr`
- final confidence is below `minimum_entry_score`

## FVG Entry

Bullish FVG entry uses:

- valid bullish setup
- active bullish FVG
- retest or midpoint limit
- stop below sweep/FVG/zone invalidation
- target buy-side liquidity

Bearish FVG entry mirrors this logic toward sell-side liquidity.

## OB Entry

Bullish OB entry uses a valid bullish order block retest or mean-threshold
limit. Bearish OB entry uses a valid bearish order block retest or mean-
threshold limit.

Order block confirmation is safer than blind limit entry because it requires a
closed candle reaction.

## Output

Main fields:

- `entry_signal`
- `position_allowed`
- `direction`
- `entry_type`
- `order_type`
- `entry_price`
- `stop_loss`
- `target`
- `rr`
- `confidence_score`
- `selected_zone`
- `risk_plan`
- `decision`
- `reasons`
- `warnings`

## Final Rule

A valid entry is not just a pattern. It needs setup confirmation, a valid zone,
retest or permitted limit logic, stop-loss, target liquidity, acceptable RR, and
safe execution filters.
