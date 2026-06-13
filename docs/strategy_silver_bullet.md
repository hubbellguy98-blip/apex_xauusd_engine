# ICT Silver Bullet Strategy

The ICT Silver Bullet strategy is a time-window-based liquidity sweep and FVG
entry model. It is not a general-purpose signal generator and should not force a
trade every day.

## Required Sequence

1. Current evaluation time must be inside a configured Silver Bullet window.
2. Relevant liquidity must exist for that window.
3. Price must sweep sell-side or buy-side liquidity inside the window.
4. The sweep candle must reclaim or reject the swept level on a closed candle.
5. Displacement must appear after the sweep.
6. The displacement must create a valid FVG.
7. Price must retrace into the FVG.
8. Entry, stop, target, and RR must validate.
9. News, spread, chop, HTF blocker, and scoring filters must pass.

If any hard requirement fails, the model returns `trade_allowed = false`.

## Bullish Model

- Sell-side liquidity is swept inside the selected window.
- Price closes back above the swept level.
- Bullish displacement creates a bullish FVG.
- Price retraces into the FVG.
- Stop is placed below the sweep low with ATR and spread buffer.
- Target is opposite buy-side liquidity.

## Bearish Model

- Buy-side liquidity is swept inside the selected window.
- Price closes back below the swept level.
- Bearish displacement creates a bearish FVG.
- Price retraces into the FVG.
- Stop is placed above the sweep high with ATR and spread buffer.
- Target is opposite sell-side liquidity.

## XAUUSD Safety Filters

The model rejects setups when:

- High-impact news blackout is active.
- The setup is the first news spike.
- Post-news structure has not stabilized.
- Spread is high, wide, or unsafe.
- Session condition is low-liquidity chop.
- The FVG or displacement is oversized relative to ATR.
- RR is below the configured minimum.
- A higher-timeframe blocker prevents the target path.

## Configurable Windows

Windows are configurable so broker time, UTC, New York, London, and DST
differences can be handled safely. Default examples include:

- London Silver Bullet
- New York Silver Bullet
- London/New York Overlap

Backtests should store candles in a known timezone and pass the correct
`broker_timezone` to the model.

## Public Functions

- `is_in_silver_bullet_window()`
- `detect_window_liquidity()`
- `detect_silver_bullet_sweep()`
- `detect_silver_bullet_fvg()`
- `generate_silver_bullet_signal()`
- `score_silver_bullet_setup()`

## Backtesting Rule

Only confirmed closed candles are used. If a stop and target are both touched in
the same candle, conservative backtests should assume the stop was hit first
unless lower-timeframe data proves otherwise.
