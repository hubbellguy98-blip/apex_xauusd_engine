# ICT/SMC Multi-Timeframe Engine

## Architecture

The multi-timeframe engine coordinates Daily, 1H, 15M, and 5M context into one deterministic decision gate. It does not submit trades. It prepares a clean context object for scoring, entry, stop-loss, target, and execution layers.

Timeframe responsibilities:

- `Daily`: macro liquidity map, PDH/PDL, major POI zones, major blockers, external liquidity targets.
- `1H`: intraday directional bias, draw on liquidity, dealing range, premium/discount, active HTF POIs.
- `15M`: primary setup timeframe for sweeps, reclaim/rejection, MSS/BOS, displacement, FVG/OB, setup POI.
- `5M`: execution confirmation timeframe for POI retest, LTF sweep, LTF MSS, displacement, FVG/OB entry zone.

The key architectural rule is strict separation of context and timing. Higher timeframes provide the map and bias. The lower timeframe provides precise execution timing. A 5M entry signal is not enough by itself if the 15M setup is missing or HTF context is unsafe.

The implemented module is:

- `src/analytics/ict_smc/multitimeframe_engine.py`

It is intentionally local/GitHub analytics code. It is not connected to live VPS order submission until explicitly integrated later.

## Data Pipeline

Input data:

- Daily OHLCV rows.
- 1H OHLCV rows.
- 15M OHLCV rows.
- 5M OHLCV rows.

Each row should include:

- `timestamp`
- `open`
- `high`
- `low`
- `close`
- `volume`
- optional `close_time`
- optional `is_closed`

Pipeline:

1. Normalize all timeframe aliases such as `daily`, `1D`, `h1`, `1H`, `m15`, `15M`, `m5`, and `5M`.
2. Normalize candles into sorted OHLCV rows.
3. Attach deterministic close times when the source did not provide them.
4. At evaluation time, slice each timeframe to only candles with `close_time <= eval_time`.
5. Build Daily context from closed Daily candles.
6. Build 1H bias from closed 1H candles.
7. Build 15M setup from closed 15M candles and detector context.
8. Map Daily, 1H, and 15M zones onto the 5M chart only after each zone's `valid_from_time`.
9. Confirm 5M entry timing only after price enters a valid mapped POI.
10. Audit lookahead safety across all context timestamps.
11. Return a unified context and final pre-scoring trade decision.

Anti-lookahead rules:

- Do not use current Daily candle as confirmed Daily context.
- Do not use current 1H candle as confirmed 1H bias.
- Do not use current 15M candle as confirmed setup.
- Do not use current 5M candle as confirmed entry.
- Do not use a zone before its `valid_from_time`.
- Do not let LTF confirmation happen before price enters an HTF/15M POI.
- Do not force trades when timeframes conflict.

## Function List

- `prepare_timeframe_data(rows, timeframe)`: normalize and sort one timeframe's OHLCV rows.
- `get_closed_candles_asof(rows, timeframe, eval_time)`: return only confirmed closed candles at evaluation time.
- `align_timeframes(all_timeframes, eval_time)`: slice Daily, 1H, 15M, and 5M data to the same closed-candle clock.
- `build_daily_context(daily_df, override_context=None)`: build Daily liquidity and POI context.
- `build_h1_bias_context(h1_df, daily_context=None, override_context=None)`: build 1H bias and draw context.
- `detect_m15_setup(m15_df, daily_context=None, h1_context=None, override_context=None)`: represent confirmed 15M setup context.
- `map_htf_zones_to_ltf(htf_zones, ltf_df, target_timeframe="5M")`: project valid HTF zones onto the 5M chart.
- `detect_ltf_confirmation(m5_df, mapped_zones, m15_setup, h1_context=None)`: confirm whether 5M timing exists inside a mapped POI.
- `build_multitimeframe_context(all_timeframes, eval_time=None, config=None)`: produce the unified MTF context.
- `run_multitimeframe_engine(all_timeframes, eval_time=None, config=None)`: run the final MTF decision gate.

## Pseudocode

```text
START run_multitimeframe_engine

Normalize Daily, 1H, 15M, and 5M inputs.

If eval_time is not provided:
    use latest closed 5M candle close time.

daily_closed = get_closed_candles_asof(daily, "1D", eval_time)
h1_closed = get_closed_candles_asof(h1, "1H", eval_time)
m15_closed = get_closed_candles_asof(m15, "15M", eval_time)
m5_closed = get_closed_candles_asof(m5, "5M", eval_time)

If any timeframe has no closed candles:
    block trade with insufficient_closed_candle_data.

daily_context = build_daily_context(daily_closed)
h1_context = build_h1_bias_context(h1_closed, daily_context)
m15_context = detect_m15_setup(m15_closed, daily_context, h1_context)

If m15_context.setup_detected is false:
    block trade with no_15m_setup.

htf_zones = daily_context.poi_zones + h1_context.poi_zones + m15_context.poi_zones
mapped_zones = map_htf_zones_to_ltf(htf_zones, m5_closed, "5M")

m5_context = detect_ltf_confirmation(m5_closed, mapped_zones, m15_context, h1_context)

If LTF confirmation is required and m5_context.ltf_confirmed is false:
    block trade with waiting_for_5m_confirmation.

Run lookahead audit:
    If any context timestamp is after eval_time, block.
    If any HTF context timestamp is after that timeframe's latest closed candle, block.

Build combined_bias.

If timeframe directions conflict:
    block trade with timeframe_bias_conflict.

If Daily/HTF target blocker exists and no closer target meets RR:
    block trade with target_blocked_by_htf_poi.

If optional score_result is provided:
    allow only if score_result.trade_allowed is true.

Return:
    multi_timeframe_context
    setup_context
    entry_signal
    score_result
    trade_decision

END
```

## Example Output Context Object

```json
{
  "function": "build_multitimeframe_context",
  "concept_name": "Multi-Timeframe ICT/SMC Engine",
  "symbol": "XAUUSD",
  "eval_time": "2026-06-04T10:35:00+00:00",
  "timezone": "UTC",
  "lookahead_safe": true,
  "closed_candle_status": {
    "daily_latest_closed": "2026-06-04T00:00:00+00:00",
    "h1_latest_closed": "2026-06-04T10:00:00+00:00",
    "m15_latest_closed": "2026-06-04T10:30:00+00:00",
    "m5_latest_closed": "2026-06-04T10:35:00+00:00"
  },
  "daily_context": {
    "role": "liquidity_map_and_major_poi",
    "bias": "neutral",
    "pdh": {
      "price": 2381.4,
      "direction": "buy_side",
      "swept_status": "unswept"
    },
    "pdl": {
      "price": 2352.1,
      "direction": "sell_side",
      "swept_status": "unswept"
    },
    "poi_zones": []
  },
  "h1_context": {
    "role": "bias_and_draw_on_liquidity",
    "h1_bias": "bullish",
    "expected_draw": "buy_side",
    "poi_zones": [
      {
        "zone_id": "H1_BULLISH_FVG_004",
        "source_timeframe": "1H",
        "zone_type": "bullish_fvg",
        "direction": "bullish",
        "zone_low": 2358.4,
        "zone_high": 2361.2,
        "valid_from_time": "2026-06-04T10:00:00+00:00",
        "active_status": true
      }
    ]
  },
  "m15_context": {
    "role": "setup_and_sweep",
    "setup_detected": true,
    "confirmed": true,
    "direction": "bullish",
    "setup_type": "sell_side_liquidity_sweep_bullish_mss"
  },
  "mapped_zones_to_m5": [
    {
      "zone_id": "H1_BULLISH_FVG_004",
      "source_timeframe": "1H",
      "mapped_to_timeframe": "5M",
      "direction": "bullish",
      "zone_low": 2358.4,
      "zone_high": 2361.2,
      "touched": true,
      "retest_status": "touched"
    }
  ],
  "m5_context": {
    "role": "entry_confirmation",
    "ltf_confirmed": true,
    "direction": "bullish",
    "price_inside_poi": true,
    "ltf_sweep": true,
    "ltf_mss": true,
    "ltf_displacement": true
  },
  "combined_bias": {
    "daily_bias": "neutral",
    "h1_bias": "bullish",
    "m15_setup_direction": "bullish",
    "m5_entry_direction": "bullish",
    "alignment_status": "bullish",
    "conflict_notes": []
  },
  "trade_readiness": {
    "setup_ready": true,
    "entry_confirmation_ready": true,
    "entry_confirmation_required": true,
    "needs_rr_check": true,
    "needs_news_filter_check": true,
    "trade_allowed_before_scoring": true,
    "reason": "multi_timeframe_alignment_ready"
  }
}
```

## Test Coverage

The unit tests cover:

- Perfect bullish Daily/1H/15M/5M alignment.
- A 5M entry attempt without a confirmed 15M setup.
- A bullish intraday setup blocked by a Daily bearish POI before target.
- Lookahead bias from using an unclosed 1H candle at 10:35.
- Valid HTF POI and 15M setup that still waits for 5M confirmation.
