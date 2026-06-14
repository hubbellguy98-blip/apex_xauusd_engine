# News Liquidity Sweep Strategy Layer

This layer converts the ICT/SMC News Liquidity Sweep model into deterministic
bot logic for research, testing, and later orchestration.

It does not place broker orders.

## Model

News is treated as a dangerous liquidity event, not a normal trade signal. The
first news spike may identify liquidity, but it is not tradable by itself.

Bullish path:

1. High-impact USD news occurs.
2. The first spike sweeps sell-side liquidity.
3. The bot blocks instant entry.
4. Spread and candle ranges stabilize.
5. Price reclaims the swept level.
6. Bullish MSS and displacement confirm after stabilization.
7. A valid FVG or order-block entry POI appears.
8. Price retests and reacts from the POI.
9. Stop is placed beyond the news sweep low with a wider buffer.
10. Target is buy-side liquidity with valid RR.

Bearish path:

1. High-impact USD news occurs.
2. The first spike sweeps buy-side liquidity.
3. The bot blocks instant entry.
4. Spread and candle ranges stabilize.
5. Price rejects the swept level.
6. Bearish MSS and displacement confirm after stabilization.
7. A valid FVG or order-block entry POI appears.
8. Price retests and reacts from the POI.
9. Stop is placed beyond the news sweep high with a wider buffer.
10. Target is sell-side liquidity with valid RR.

## Safety Rules

- Closed candles only; forming candles are ignored.
- No trades in the pre-news restriction window.
- No entries during the active news spike.
- No post-news entry until stabilization passes.
- Spread, slippage, candle range, and both-side sweep confusion are hard
  filters.
- A news sweep without reclaim/rejection, MSS, displacement, and entry POI is
  not enough.
- Position size is reduced after high-impact news.
- Stop buffers are wider than normal.
- RR must remain valid after the wider stop and execution buffers.

## Main Functions

- `is_news_restricted_time()`
- `detect_news_spike()`
- `wait_for_post_news_stabilization()`
- `detect_post_news_liquidity_sweep()`
- `detect_post_news_mss()`
- `generate_news_sweep_signal()`
- `score_news_sweep_setup()`

## Output

The generator returns a structured dictionary with:

- signal status,
- news restriction state,
- first-spike details,
- stabilization details,
- post-news liquidity sweep,
- MSS and displacement,
- entry POI,
- target and risk plan,
- reduced-risk percentage,
- score,
- rejection reasons.

The purpose is to make news trading safer and more testable by forcing the bot
to wait for post-news market structure instead of reacting to the initial spike.
