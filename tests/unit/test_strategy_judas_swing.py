from src.strategy.ict_smc_strategies.judas_swing import (
    calculate_session_range,
    detect_judas_sweep,
    detect_range_reclaim,
    generate_judas_swing_signal,
    score_session_range_quality,
)

BASE_CONFIG = {
    "session_range_config": {
        "session_name": "Asian",
        "start_time": "00:00",
        "end_time": "00:06",
        "timezone": "UTC",
        "min_candles_required": 6,
    },
    "broker_timezone": "UTC",
    "sweep_buffer": 0.05,
    "break_buffer": 0.01,
    "min_fvg_size": 0.01,
    "entry_mode": "balanced",
    "min_rr": 2.0,
    "minimum_setup_score": 7.5,
    "minimum_range_quality_score": 6.0,
    "stop_atr_buffer_multiplier": 0.02,
    "max_spread_points": 0.6,
    "max_reclaim_wait_candles": 5,
    "max_mss_wait_candles": 10,
}

MANIPULATION_WINDOW = [
    {
        "window_name": "London Judas Window",
        "start_time": "07:00",
        "end_time": "09:00",
        "timezone": "UTC",
        "enabled": True,
    }
]


def test_valid_bullish_judas_swing_signal():
    candles = _bullish_judas_candles()
    context = {
        "symbol": "XAUUSD",
        "timestamp": "2026-06-04T07:08:00+00:00",
        "df": candles,
        "manipulation_window": MANIPULATION_WINDOW,
        "liquidity_pools": [_pool("PDH", "buy_side", 112.0, 112.2)],
        "swings": [{"swing_id": "POST_SWEEP_HIGH", "kind": "high", "index": 8, "price": 102.4}],
        "htf_bias": "bullish",
        "spread_status": {"spread_points": 0.1, "spread_safe": True},
    }

    signal = generate_judas_swing_signal(context, BASE_CONFIG)

    assert signal["signal_status"] == "valid"
    assert signal["strategy"] == "Judas Swing / Session Manipulation"
    assert signal["direction"] == "bullish"
    assert signal["session_range"]["clean_range"] is True
    assert signal["manipulation"]["swept_side"] == "range_low"
    assert signal["reclaim"]["reclaim_confirmed"] is True
    assert signal["risk"]["stop_loss"] < signal["manipulation"]["sweep_low"]
    assert signal["risk"]["final_target"] >= 112.0
    assert signal["risk"]["rr_to_final_target"] >= 2.0
    assert signal["score"]["total_score"] >= 8.0
    assert signal["trade_allowed"] is True


def test_valid_bearish_judas_swing_signal():
    candles = _bearish_judas_candles()
    context = {
        "symbol": "XAUUSD",
        "timestamp": "2026-06-04T07:08:00+00:00",
        "df": candles,
        "manipulation_window": MANIPULATION_WINDOW,
        "liquidity_pools": [_pool("PDL", "sell_side", 92.0, 92.2)],
        "swings": [{"swing_id": "POST_SWEEP_LOW", "kind": "low", "index": 8, "price": 107.6}],
        "htf_bias": "bearish",
        "spread_status": {"spread_points": 0.1, "spread_safe": True},
    }

    signal = generate_judas_swing_signal(context, BASE_CONFIG)

    assert signal["signal_status"] == "valid"
    assert signal["direction"] == "bearish"
    assert signal["manipulation"]["swept_side"] == "range_high"
    assert signal["entry"]["entry_type"] == "bearish_judas_fvg_midpoint_entry"
    assert signal["risk"]["stop_loss"] > signal["manipulation"]["sweep_high"]
    assert signal["risk"]["final_target"] <= 92.2
    assert signal["risk"]["rr_to_final_target"] >= 2.0
    assert signal["score"]["total_score"] >= 8.0


def test_messy_session_range_is_rejected_before_sweep_logic():
    candles = _messy_range_candles() + _bullish_judas_candles()[6:]
    context = {
        "symbol": "XAUUSD",
        "timestamp": "2026-06-04T07:08:00+00:00",
        "df": candles,
        "manipulation_window": MANIPULATION_WINDOW,
    }

    session_range = calculate_session_range(
        candles, BASE_CONFIG["session_range_config"], "UTC", context["timestamp"], BASE_CONFIG
    )
    quality = score_session_range_quality(session_range, candles, config=BASE_CONFIG)
    signal = generate_judas_swing_signal(context, BASE_CONFIG)

    assert quality["clean_range"] is False
    assert "asian_range_too_messy" in quality["rejection_reasons"]
    assert signal["signal_status"] == "rejected"
    assert "poor_session_range_quality" in signal["rejection_reasons"]


def test_sweep_that_accepts_below_range_is_real_breakdown_not_judas():
    candles = _real_breakdown_candles()
    session_range = calculate_session_range(
        candles, BASE_CONFIG["session_range_config"], "UTC", "2026-06-04T07:05:00+00:00", BASE_CONFIG
    )
    sweep = detect_judas_sweep(candles, session_range, MANIPULATION_WINDOW, BASE_CONFIG)
    reclaim = detect_range_reclaim(candles, sweep, session_range, BASE_CONFIG)
    signal = generate_judas_swing_signal(
        {
            "symbol": "XAUUSD",
            "timestamp": "2026-06-04T07:05:00+00:00",
            "df": candles,
            "manipulation_window": MANIPULATION_WINDOW,
        },
        BASE_CONFIG,
    )

    assert sweep["direction_bias"] == "bullish"
    assert reclaim["reclaim_confirmed"] is False
    assert reclaim["rejection_reason"] == "real_breakdown_not_manipulation"
    assert signal["signal_status"] == "rejected"
    assert "real_breakdown_not_manipulation" in signal["rejection_reasons"]


def test_news_spike_double_sweep_is_rejected_with_safety_reasons():
    candles = _news_double_sweep_candles()
    context = {
        "symbol": "XAUUSD",
        "timestamp": "2026-06-04T07:01:00+00:00",
        "df": candles,
        "manipulation_window": MANIPULATION_WINDOW,
        "news_status": {"restricted": True, "post_news_stabilized": False},
        "spread_status": {"spread_points": 2.5, "spread_safe": False, "status": "wide"},
        "double_sweep_chop": True,
    }

    signal = generate_judas_swing_signal(context, BASE_CONFIG)

    assert signal["signal_status"] == "rejected"
    assert "news_restricted" in signal["rejection_reasons"]
    assert "double_sweep_chop" in signal["rejection_reasons"]
    assert "spread_too_high" in signal["rejection_reasons"]
    assert "post_news_structure_unstable" in signal["rejection_reasons"]


def test_detectors_ignore_current_forming_candle():
    candles = _bullish_judas_candles() + [_candle(99, "2026-06-04T07:09:00+00:00", 120, 140, 80, 130, is_closed=False)]
    context = {
        "symbol": "XAUUSD",
        "timestamp": "2026-06-04T07:08:00+00:00",
        "df": candles,
        "manipulation_window": MANIPULATION_WINDOW,
        "liquidity_pools": [_pool("PDH", "buy_side", 112.0, 112.2)],
        "swings": [{"swing_id": "POST_SWEEP_HIGH", "kind": "high", "index": 8, "price": 102.4}],
        "htf_bias": "bullish",
    }

    signal = generate_judas_swing_signal(context, BASE_CONFIG)

    assert signal["manipulation"]["sweep_index"] != 99
    assert signal["entry"]["retest_index"] != 99


def _pool(pool_id, direction, zone_low, zone_high):
    return {
        "liquidity_id": pool_id,
        "direction": direction,
        "zone_low": zone_low,
        "zone_high": zone_high,
        "price": (zone_low + zone_high) / 2,
        "quality_score": 8.0,
        "swept_status": "active",
    }


def _asian_range():
    return [
        _candle(0, "2026-06-04T00:00:00+00:00", 102.0, 103.0, 101.0, 102.5),
        _candle(1, "2026-06-04T00:01:00+00:00", 102.5, 105.0, 102.4, 104.2),
        _candle(2, "2026-06-04T00:02:00+00:00", 104.0, 104.6, 102.0, 103.0),
        _candle(3, "2026-06-04T00:03:00+00:00", 103.0, 103.4, 100.0, 101.0),
        _candle(4, "2026-06-04T00:04:00+00:00", 101.0, 102.6, 100.4, 101.8),
        _candle(5, "2026-06-04T00:05:00+00:00", 101.8, 104.0, 101.3, 103.0),
    ]


def _bullish_judas_candles():
    return _asian_range() + [
        _candle(6, "2026-06-04T07:00:00+00:00", 101.0, 101.5, 99.3, 99.8),
        _candle(7, "2026-06-04T07:01:00+00:00", 99.9, 101.2, 99.6, 100.8),
        _candle(8, "2026-06-04T07:02:00+00:00", 100.8, 102.4, 100.7, 102.0),
        _candle(9, "2026-06-04T07:03:00+00:00", 102.5, 106.8, 102.6, 106.2),
        _candle(10, "2026-06-04T07:04:00+00:00", 104.0, 105.0, 101.3, 103.0),
        _candle(11, "2026-06-04T07:05:00+00:00", 103.0, 106.0, 102.7, 105.6),
    ]


def _bearish_judas_candles():
    return _asian_range() + [
        _candle(6, "2026-06-04T07:00:00+00:00", 104.0, 105.8, 103.5, 105.2),
        _candle(7, "2026-06-04T07:01:00+00:00", 105.1, 105.4, 103.7, 104.2),
        _candle(8, "2026-06-04T07:02:00+00:00", 104.2, 104.6, 102.4, 102.8),
        _candle(9, "2026-06-04T07:03:00+00:00", 102.2, 102.3, 97.8, 98.2),
        _candle(10, "2026-06-04T07:04:00+00:00", 100.9, 103.0, 100.5, 101.5),
        _candle(11, "2026-06-04T07:05:00+00:00", 101.2, 101.5, 97.5, 98.0),
    ]


def _messy_range_candles():
    return [
        _candle(0, "2026-06-04T00:00:00+00:00", 102.0, 107.0, 97.0, 103.0),
        _candle(1, "2026-06-04T00:01:00+00:00", 103.0, 109.0, 96.0, 102.0),
        _candle(2, "2026-06-04T00:02:00+00:00", 102.0, 106.0, 95.0, 103.0),
        _candle(3, "2026-06-04T00:03:00+00:00", 103.0, 110.0, 94.0, 102.0),
        _candle(4, "2026-06-04T00:04:00+00:00", 102.0, 108.0, 95.5, 103.0),
        _candle(5, "2026-06-04T00:05:00+00:00", 103.0, 109.5, 96.0, 102.0),
    ]


def _real_breakdown_candles():
    return _asian_range() + [
        _candle(6, "2026-06-04T07:00:00+00:00", 101.0, 101.5, 99.3, 99.5),
        _candle(7, "2026-06-04T07:01:00+00:00", 99.5, 99.8, 98.5, 98.8),
        _candle(8, "2026-06-04T07:02:00+00:00", 98.8, 99.0, 97.0, 97.5),
    ]


def _news_double_sweep_candles():
    return _asian_range() + [_candle(6, "2026-06-04T07:00:00+00:00", 102.0, 112.0, 92.0, 103.0)]


def _candle(index, timestamp, open_, high, low, close, is_closed=True):
    return {
        "index": index,
        "timestamp": timestamp,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100 + index,
        "is_closed": is_closed,
    }
