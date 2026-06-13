from src.strategy.ict_smc_strategies.silver_bullet import (
    detect_silver_bullet_fvg,
    detect_silver_bullet_sweep,
    detect_window_liquidity,
    generate_silver_bullet_signal,
    is_in_silver_bullet_window,
)

BASE_CONFIG = {
    "silver_bullet_windows": [
        {
            "window_name": "Test New York Silver Bullet",
            "session": "new_york",
            "start_time": "13:30",
            "end_time": "15:00",
            "timezone": "UTC",
            "enabled": True,
        }
    ],
    "broker_timezone": "UTC",
    "sweep_buffer": 0.05,
    "min_fvg_size": 0.01,
    "entry_mode": "balanced",
    "min_rr": 2.0,
    "minimum_setup_score": 7.5,
    "stop_atr_buffer_multiplier": 0.02,
    "max_spread_points": 0.6,
    "max_fvg_atr_multiplier": 2.5,
    "max_displacement_atr_multiplier": 3.5,
}


def test_valid_bullish_silver_bullet_signal():
    context = {
        "symbol": "XAUUSD",
        "timestamp": "2026-06-04T13:37:00+00:00",
        "df": _bullish_candles(),
        "liquidity_pools": [
            _pool("ASIAN_LOW", "sell_side", 100.0, 100.2, "asian_low"),
            _pool("BUY_TARGET", "buy_side", 112.0, 112.2, "previous_day_high"),
        ],
        "htf_bias": {"draw_on_liquidity": "buy_side", "h1_bias": "bullish"},
        "spread_status": {"spread_points": 0.1, "spread_safe": True},
        "session_context": {"status": "clean"},
    }

    signal = generate_silver_bullet_signal(context, BASE_CONFIG)

    assert signal["signal_status"] == "valid"
    assert signal["strategy"] == "ICT Silver Bullet"
    assert signal["direction"] == "bullish"
    assert signal["window"]["active_window_name"] == "Test New York Silver Bullet"
    assert signal["entry"]["entry_type"] == "bullish_silver_bullet_fvg_midpoint_entry"
    assert signal["risk"]["stop_loss"] < signal["sweep"]["sweep_low"]
    assert signal["risk"]["target"] >= 112.0
    assert signal["risk"]["rr"] >= 2.0
    assert signal["trade_allowed"] is True
    assert signal["score"]["total_score"] >= 8.0


def test_valid_bearish_silver_bullet_signal():
    context = {
        "symbol": "XAUUSD",
        "timestamp": "2026-06-04T13:37:00+00:00",
        "df": _bearish_candles(),
        "liquidity_pools": [
            _pool("ASIAN_HIGH", "buy_side", 109.8, 110.0, "asian_high"),
            _pool("SELL_TARGET", "sell_side", 96.0, 96.2, "previous_day_low"),
        ],
        "htf_bias": {"draw_on_liquidity": "sell_side", "h1_bias": "bearish"},
        "spread_status": {"spread_points": 0.1, "spread_safe": True},
        "session_context": {"status": "clean"},
    }

    signal = generate_silver_bullet_signal(context, BASE_CONFIG)

    assert signal["signal_status"] == "valid"
    assert signal["direction"] == "bearish"
    assert signal["entry"]["entry_type"] == "bearish_silver_bullet_fvg_midpoint_entry"
    assert signal["risk"]["stop_loss"] > signal["sweep"]["sweep_high"]
    assert signal["risk"]["target"] <= 96.2
    assert signal["risk"]["rr"] >= 2.0
    assert signal["trade_allowed"] is True
    assert signal["score"]["total_score"] >= 8.0


def test_silver_bullet_rejects_outside_configured_window():
    context = {
        "symbol": "XAUUSD",
        "timestamp": "2026-06-04T16:00:00+00:00",
        "df": _bullish_candles(),
        "liquidity_pools": [
            _pool("ASIAN_LOW", "sell_side", 100.0, 100.2, "asian_low"),
            _pool("BUY_TARGET", "buy_side", 112.0, 112.2, "previous_day_high"),
        ],
    }

    signal = generate_silver_bullet_signal(context, BASE_CONFIG)

    assert signal["signal_status"] == "outside_window"
    assert "outside_silver_bullet_window" in signal["rejection_reasons"]
    assert signal["trade_allowed"] is False


def test_valid_window_without_fvg_retest_waits_instead_of_chasing():
    context = {
        "symbol": "XAUUSD",
        "timestamp": "2026-06-04T13:37:00+00:00",
        "df": _bullish_no_retest_candles(),
        "liquidity_pools": [
            _pool("ASIAN_LOW", "sell_side", 100.0, 100.2, "asian_low"),
            _pool("BUY_TARGET", "buy_side", 112.0, 112.2, "previous_day_high"),
        ],
        "spread_status": {"spread_points": 0.1, "spread_safe": True},
    }

    signal = generate_silver_bullet_signal(context, BASE_CONFIG)

    assert signal["signal_status"] == "waiting_for_retest"
    assert "waiting_for_fvg_retest" in signal["rejection_reasons"]
    assert signal["trade_allowed"] is False


def test_news_spike_false_silver_bullet_is_rejected():
    context = {
        "symbol": "XAUUSD",
        "timestamp": "2026-06-04T13:37:00+00:00",
        "df": _bullish_candles(),
        "liquidity_pools": [
            _pool("ASIAN_LOW", "sell_side", 100.0, 100.2, "asian_low"),
            _pool("BUY_TARGET", "buy_side", 130.0, 130.2, "previous_day_high"),
        ],
        "news_status": {"restricted": True, "first_news_spike": True, "post_news_stabilized": False},
        "spread_status": {"spread_points": 2.5, "spread_safe": False, "status": "wide"},
    }

    signal = generate_silver_bullet_signal(context, BASE_CONFIG)

    assert signal["signal_status"] == "rejected"
    assert "news_restricted" in signal["rejection_reasons"]
    assert "first_news_spike_signal" in signal["rejection_reasons"]
    assert "post_news_structure_not_stabilized" in signal["rejection_reasons"]
    assert "spread_too_high" in signal["rejection_reasons"]
    assert signal["trade_allowed"] is False


def test_low_level_detectors_use_closed_candles_only():
    candles = _bullish_candles() + [_candle(8, "2026-06-04T13:38:00+00:00", 120, 140, 80, 130, is_closed=False)]
    active_window = is_in_silver_bullet_window(candles[-2]["timestamp"], BASE_CONFIG["silver_bullet_windows"], "UTC")[
        "active_window"
    ]
    liquidity = detect_window_liquidity(
        candles,
        [_pool("ASIAN_LOW", "sell_side", 100.0, 100.2, "asian_low")],
        active_window,
        "bullish",
        BASE_CONFIG,
    )
    sweep = detect_silver_bullet_sweep(candles, liquidity, active_window, BASE_CONFIG)
    fvg_setup = detect_silver_bullet_fvg(candles, sweep, BASE_CONFIG)

    assert sweep["sweep_index"] != 8
    assert fvg_setup["fvg"]["creation_index"] != 8


def _pool(pool_id, direction, zone_low, zone_high, liquidity_type):
    return {
        "liquidity_id": pool_id,
        "direction": direction,
        "zone_low": zone_low,
        "zone_high": zone_high,
        "price": (zone_low + zone_high) / 2,
        "liquidity_type": liquidity_type,
        "quality_score": 8.0,
        "swept_status": "active",
    }


def _bullish_candles():
    return [
        _candle(0, "2026-06-04T13:30:00+00:00", 103.0, 105.0, 101.0, 103.4),
        _candle(1, "2026-06-04T13:31:00+00:00", 102.6, 104.0, 100.8, 101.5),
        _candle(2, "2026-06-04T13:32:00+00:00", 101.0, 102.0, 99.4, 100.6),
        _candle(3, "2026-06-04T13:33:00+00:00", 101.5, 102.5, 100.8, 102.0),
        _candle(4, "2026-06-04T13:34:00+00:00", 101.9, 102.4, 101.4, 102.1),
        _candle(5, "2026-06-04T13:35:00+00:00", 103.2, 107.0, 103.2, 106.5),
        _candle(6, "2026-06-04T13:36:00+00:00", 104.5, 105.0, 102.8, 104.2),
        _candle(7, "2026-06-04T13:37:00+00:00", 104.3, 106.0, 104.0, 105.7),
    ]


def _bearish_candles():
    return [
        _candle(0, "2026-06-04T13:30:00+00:00", 107.0, 109.0, 105.0, 106.6),
        _candle(1, "2026-06-04T13:31:00+00:00", 107.4, 109.2, 106.0, 108.5),
        _candle(2, "2026-06-04T13:32:00+00:00", 109.0, 110.6, 108.0, 109.4),
        _candle(3, "2026-06-04T13:33:00+00:00", 108.8, 109.2, 107.5, 108.0),
        _candle(4, "2026-06-04T13:34:00+00:00", 108.1, 108.6, 107.6, 107.9),
        _candle(5, "2026-06-04T13:35:00+00:00", 106.8, 106.8, 103.0, 103.5),
        _candle(6, "2026-06-04T13:36:00+00:00", 105.2, 107.0, 105.0, 105.8),
        _candle(7, "2026-06-04T13:37:00+00:00", 105.5, 105.8, 103.8, 104.0),
    ]


def _bullish_no_retest_candles():
    candles = _bullish_candles()
    candles[6] = _candle(6, "2026-06-04T13:36:00+00:00", 107.2, 109.0, 107.0, 108.5)
    candles[7] = _candle(7, "2026-06-04T13:37:00+00:00", 108.6, 110.0, 108.4, 109.7)
    return candles


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
