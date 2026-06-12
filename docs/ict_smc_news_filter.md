# ICT/SMC XAUUSD News Filter

The news filter is a defensive layer for high-impact XAUUSD trading events. It
is designed to stop the bot from treating macro-news chaos as ordinary ICT/SMC
price action.

It does not create trades by itself.

## Functions

```python
is_news_restricted_time(timestamp, news_calendar, before_minutes, after_minutes)
```

This checks whether the current timestamp is inside a high-impact news blackout
window.

```python
detect_post_news_smc_setup(df, news_event)
```

This checks whether price action after the blackout has stabilized enough to
allow a post-news SMC setup to be considered.

## Why This Exists

For XAUUSD, high-impact USD news such as CPI, NFP, FOMC, PCE, rate decisions,
and major Fed speeches can create abnormal spread, slippage, one-candle spikes,
and false liquidity sweeps.

A news spike can look like:

- displacement
- liquidity sweep
- FVG creation
- market structure break

But during the first spike, these signals are often execution noise rather than
clean institutional structure. The filter blocks the first reaction and waits
for cleaner post-news confirmation.

## Restricted Time Logic

A news event is relevant when:

- impact is high
- currency is USD for XAUUSD/GOLD symbols
- the event is listed for the traded symbol, if affected symbols are provided

During the blackout window:

- new entries are blocked
- pending orders are blocked
- pending orders can be cancelled
- existing positions may still be managed

The blackout can use global values or event-specific overrides:

- `blackout_before_minutes`
- `blackout_after_minutes`

## Post-News Setup Requirements

A valid post-news SMC setup needs all of the following:

1. The blackout window has ended.
2. Extra stabilization time has passed.
3. Spread and estimated slippage are back inside safe limits.
4. The first spike is not being used as the entry signal.
5. Price sweeps the news range high or low.
6. Price reclaims or rejects the swept level on candle close.
7. MSS confirms after the sweep.
8. FVG or OB-style entry zone forms after stabilization.
9. Risk-to-reward is acceptable.

## Bullish Post-News Model

A bullish setup requires:

- sell-side liquidity below the news range is swept
- price closes back above the news range low
- later candle-close confirms bullish MSS
- bullish FVG appears after stabilization
- target is usually the news range high or next external liquidity

## Bearish Post-News Model

A bearish setup requires:

- buy-side liquidity above the news range is swept
- price closes back below the news range high
- later candle-close confirms bearish MSS
- bearish FVG appears after stabilization
- target is usually the news range low or next external liquidity

## Spread And Slippage Safety

A setup is blocked if:

- current spread is above the configured maximum
- current spread is much larger than average spread
- estimated slippage is above the configured maximum

This matters because a technically correct setup can still be a bad trade if
execution quality is poor.

## Output Meaning

Main fields:

- `restricted`
- `matched_news_event`
- `trade_permissions`
- `post_news_setup_detected`
- `direction`
- `news_range`
- `sweep`
- `confirmation`
- `entry_zone`
- `risk_plan`
- `confidence_score`
- `warnings`

A valid post-news result still means only that the setup is structurally
eligible. It should be combined with the main strategy, session rules, risk
engine, and execution checks before any live order.
