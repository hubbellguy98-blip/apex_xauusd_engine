from src.strategy.ict_smc_strategies.sweep_mss_fvg_entry import (
    SweepMSSFVGDirection,
    detect_fvg,
    detect_fvg_retest,
    detect_liquidity_sweep,
    detect_mss,
    generate_sweep_mss_fvg_signal,
    _select_target,
)

BASE_CONFIG = {
    "sweep_buffer": 0.05,
    "break_buffer": 0.01,
    "min_fvg_size": 0.01,
    "max_fvg_size": 0.0,
    "news_max_fvg_size": 2.0,
    "entry_mode": "balanced",
    "min_rr": 2.0,
    "minimum_setup_score": 7.5,
    "stop_atr_buffer_multiplier": 0.02,
    "max_spread_points": 0.6,
}


def test_valid_bullish_sweep_mss_fvg_signal():
    candles = _bullish_candles()
    context = {
        "symbol": "XAUUSD",
        "df": candles,
        "liquidity_pools": [
            _pool("ASIAN_LOW", "sell_side", 100.0, 100.2),
            _pool("BUY_SIDE_TARGET", "buy_side", 112.0, 112.2),
        ],
        "swings": [{"swing_id": "POST_SWEEP_HIGH", "kind": "high", "index": 3, "price": 102.5}],
        "session_context": {"session": "london_killzone"},
        "htf_bias": "bullish",
        "spread_status": {"spread_points": 0.1, "spread_safe": True},
    }

    signal = generate_sweep_mss_fvg_signal(context, BASE_CONFIG)

    assert signal["signal_status"] == "valid"
    assert signal["direction"] == "bullish"
    assert signal["entry"]["entry_type"] == "bullish_fvg_entry"
    assert signal["risk"]["stop_loss"] < signal["liquidity_sweep"]["sweep_low"]
    assert signal["risk"]["target"] >= 112.0
    assert signal["trade_allowed"] is True
    assert signal["score"]["total_score"] >= 8.0


def test_sweep_mss_fvg_respects_configured_timeframes():
    candles = _bullish_candles()
    bad_df = _bullish_candles_without_sweep()
    context = {
        "symbol": "XAUUSD",
        "df": bad_df,
        "candles_by_timeframe": {"1m": candles},
        "liquidity_pools": [
            _pool("ASIAN_LOW", "sell_side", 100.0, 100.2),
            _pool("BUY_SIDE_TARGET", "buy_side", 112.0, 112.2),
        ],
        "swings": [{"swing_id": "POST_SWEEP_HIGH", "kind": "high", "index": 3, "price": 102.5}],
        "session_context": {"session": "london_killzone"},
        "htf_bias": "bullish",
        "spread_status": {"spread_points": 0.1, "spread_safe": True},
    }

    signal = generate_sweep_mss_fvg_signal(context, {**BASE_CONFIG, "setup_timeframe": "1m", "entry_timeframe": "1m"})

    assert signal["signal_status"] == "valid"


def test_select_target_rejects_wrong_side_context_liquidity():
    pools = [_pool("BUY_SIDE_TARGET", "buy_side", 112.0, 112.2)]
    context = {"target_liquidity": _pool("WRONG", "sell_side", 96.0, 96.2)}

    target = _select_target(104.0, 100.0, pools, context, SweepMSSFVGDirection.BULLISH, 2.0)

    assert target == 112.2


def test_select_target_requires_minimum_rr():
    pools = [_pool("TOO_CLOSE", "buy_side", 105.0, 105.2)]

    target = _select_target(104.0, 100.0, pools, {}, SweepMSSFVGDirection.BULLISH, 3.0)

    assert target is None


def test_valid_bearish_sweep_mss_fvg_signal():
    candles = _bearish_candles()
    context = {
        "symbol": "XAUUSD",
        "df": candles,
        "liquidity_pools": [
            _pool("ASIAN_HIGH", "buy_side", 109.8, 110.0),
            _pool("SELL_SIDE_TARGET", "sell_side", 96.0, 96.2),
        ],
        "swings": [{"swing_id": "POST_SWEEP_LOW", "kind": "low", "index": 3, "price": 107.5}],
        "session_context": {"session": "newyork_killzone"},
        "htf_bias": "bearish",
        "spread_status": {"spread_points": 0.1, "spread_safe": True},
    }

    signal = generate_sweep_mss_fvg_signal(context, BASE_CONFIG)

    assert signal["signal_status"] == "valid"
    assert signal["direction"] == "bearish"
    assert signal["entry"]["entry_type"] == "bearish_fvg_entry"
    assert signal["risk"]["stop_loss"] > signal["liquidity_sweep"]["sweep_high"]
    assert signal["risk"]["target"] <= 96.2
    assert signal["trade_allowed"] is True
    assert signal["score"]["total_score"] >= 8.0


def test_sweep_without_mss_is_rejected():
    candles = _bullish_candles_without_mss()
    context = {
        "symbol": "XAUUSD",
        "df": candles,
        "liquidity_pools": [
            _pool("ASIAN_LOW", "sell_side", 100.0, 100.2),
            _pool("BUY_SIDE_TARGET", "buy_side", 112.0, 112.2),
        ],
        "swings": [{"swing_id": "POST_SWEEP_HIGH", "kind": "high", "index": 3, "price": 102.5}],
    }

    signal = generate_sweep_mss_fvg_signal(context, BASE_CONFIG)

    assert signal["signal_status"] == "rejected"
    assert "no_mss_after_sweep" in signal["rejection_reasons"]
    assert signal["trade_allowed"] is False


def test_mss_and_fvg_without_sweep_is_rejected():
    candles = _bullish_candles_without_sweep()
    context = {
        "symbol": "XAUUSD",
        "df": candles,
        "liquidity_pools": [
            _pool("ASIAN_LOW", "sell_side", 99.0, 99.2),
            _pool("BUY_SIDE_TARGET", "buy_side", 112.0, 112.2),
        ],
        "swings": [{"swing_id": "POST_RANGE_HIGH", "kind": "high", "index": 3, "price": 102.5}],
    }

    signal = generate_sweep_mss_fvg_signal(context, BASE_CONFIG)

    assert signal["signal_status"] == "rejected"
    assert "missing_required_liquidity_sweep" in signal["rejection_reasons"]
    assert signal["trade_allowed"] is False


def test_news_spike_false_setup_is_rejected_with_safety_reasons():
    candles = _news_spike_candles()
    context = {
        "symbol": "XAUUSD",
        "df": candles,
        "liquidity_pools": [
            _pool("ASIAN_LOW", "sell_side", 100.0, 100.2),
            _pool("BUY_SIDE_TARGET", "buy_side", 130.0, 130.2),
        ],
        "news_status": {"restricted": True, "first_news_spike": True},
        "spread_status": {"spread_points": 2.5, "spread_safe": False, "status": "wide"},
    }

    signal = generate_sweep_mss_fvg_signal(context, BASE_CONFIG)

    assert signal["signal_status"] == "rejected"
    assert "news_restricted" in signal["rejection_reasons"]
    assert "first_news_spike_signal" in signal["rejection_reasons"]
    assert "fvg_too_large_news_spike" in signal["rejection_reasons"]
    assert "spread_too_high" in signal["rejection_reasons"]
    assert signal["trade_allowed"] is False


def test_low_level_detectors_use_closed_candles_only():
    candles = _bullish_candles() + [
        {"index": 8, "open": 120, "high": 140, "low": 80, "close": 130, "volume": 1, "is_closed": False}
    ]
    pools = [_pool("ASIAN_LOW", "sell_side", 100.0, 100.2)]

    sweeps = detect_liquidity_sweep(candles, pools, BASE_CONFIG)
    mss = detect_mss(
        candles, [{"swing_id": "POST_SWEEP_HIGH", "kind": "high", "index": 3, "price": 102.5}], sweeps[-1], BASE_CONFIG
    )
    fvgs = detect_fvg(candles, BASE_CONFIG)
    retest = detect_fvg_retest(candles, fvgs[0], "bullish", BASE_CONFIG)

    assert all(sweep["sweep_index"] != 8 for sweep in sweeps)
    assert mss["confirmation_index"] != 8
    assert all(fvg["creation_index"] != 8 for fvg in fvgs)
    assert retest["retest_index"] != 8


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


def _bullish_candles():
    return [
        _candle(0, 103.0, 105.0, 101.0, 103.4),
        _candle(1, 102.6, 104.0, 100.8, 101.5),
        _candle(2, 101.0, 102.0, 99.4, 100.6),
        _candle(3, 101.5, 102.5, 100.8, 102.0),
        _candle(4, 101.9, 102.4, 101.4, 102.1),
        _candle(5, 103.2, 107.0, 103.2, 106.5),
        _candle(6, 104.5, 105.0, 102.8, 104.2),
        _candle(7, 104.3, 106.0, 104.0, 105.7),
    ]


def _bearish_candles():
    return [
        _candle(0, 107.0, 109.0, 105.0, 106.6),
        _candle(1, 107.4, 109.2, 106.0, 108.5),
        _candle(2, 109.0, 110.6, 108.0, 109.4),
        _candle(3, 108.8, 109.2, 107.5, 108.0),
        _candle(4, 108.1, 108.6, 107.6, 107.9),
        _candle(5, 106.8, 106.8, 103.0, 103.5),
        _candle(6, 105.2, 107.0, 105.0, 105.8),
        _candle(7, 105.5, 105.8, 103.8, 104.0),
    ]


def _bullish_candles_without_mss():
    candles = _bullish_candles()
    candles[5] = _candle(5, 101.8, 102.4, 101.4, 102.2)
    candles[6] = _candle(6, 102.1, 102.4, 101.3, 102.0)
    candles[7] = _candle(7, 102.0, 102.3, 101.2, 101.9)
    return candles


def _bullish_candles_without_sweep():
    candles = _bullish_candles()
    candles[2] = _candle(2, 101.0, 102.0, 100.4, 100.8)
    return candles


def _news_spike_candles():
    candles = _bullish_candles()
    candles[5] = _candle(5, 106.0, 120.0, 106.0, 118.0)
    candles[6] = _candle(6, 111.0, 116.0, 107.5, 112.0)
    return candles


def _candle(index, open_, high, low, close):
    return {
        "index": index,
        "timestamp": f"2026-06-04T10:{index:02d}:00",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100 + index,
        "is_closed": True,
    }
