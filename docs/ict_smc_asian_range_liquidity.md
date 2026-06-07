# ICT/SMC Asian Range Liquidity

Asian Range Liquidity maps the high, low, midpoint, and liquidity role of a
defined Asian session. The concept is useful because London and New York often
raid the Asian high or low before the real directional move appears.

This layer is intentionally context-only. It must not authorize a live trade by
itself.

## Session Range

`calculate_session_range(df, session_start, session_end, timezone)` uses only
closed candles inside the configured session window.

It returns:

- `asian_high`: buy-side liquidity above the range.
- `asian_low`: sell-side liquidity below the range.
- `asian_midpoint`: first internal reference target.
- `asian_range_size`: high minus low.
- `liquidity_objects`: explicit high/low liquidity pools.
- `warnings`: timezone, thin-range, or expanded-range caveats.

The same timezone definition must be used in backtest and live trading. Broker
time matters for XAUUSD because the candle day/session can differ from local
clock time.

## Sweep Versus Breakout

`detect_asian_range_sweep(df, asian_high, asian_low)` scans closed candles after
the Asian range.

Rules:

- Asian high sweep: high trades above Asian high, then closes back below it.
- Asian low sweep: low trades below Asian low, then closes back above it.
- Asian high breakout: candle closes and accepts above Asian high.
- Asian low breakdown: candle closes and accepts below Asian low.
- Unclear: wick reaches beyond the level but does not cleanly reclaim or accept.

A wick outside the range is not automatically continuation. The close is what
separates a sweep/reclaim from a breakout/acceptance.

## Confirmation Model

The preferred model is:

1. Asian range sweep.
2. Reclaim or rejection back inside the range.
3. MSS confirmed by candle close.
4. Displacement in the expected direction.
5. FVG or order-block retest as the entry zone.

The detector accepts existing MSS and FVG events from upstream modules, and also
contains simple fallback heuristics for closed-candle analysis.

## Targets And Stops

Asian high sweep expects bearish continuation only after confirmation:

- First target: Asian midpoint.
- Second target: Asian low.
- Final target: PDL or external sell-side liquidity.
- Stop reference: above sweep high or above bearish entry zone.

Asian low sweep expects bullish continuation only after confirmation:

- First target: Asian midpoint.
- Second target: Asian high.
- Final target: PDH or external buy-side liquidity.
- Stop reference: below sweep low or below bullish entry zone.

Breakout continuation uses the accepted side of the range as the structural
reference, not the reversal model.

## Quality Score

The score is capped when critical confirmation is missing:

- No MSS: maximum score 5.
- No FVG or no displacement: maximum score 6.
- Unclear interaction: low-quality observer event.
- Accepted breakout against reversal idea: not treated as sweep reversal.

This makes the layer useful for analytics and future strategy logic while
protecting the live system from blindly trading an Asian high/low touch.
