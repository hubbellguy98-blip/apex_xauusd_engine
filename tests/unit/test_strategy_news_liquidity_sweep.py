from src.strategy.ict_smc_strategies.news_liquidity_sweep import (
    detect_news_spike,
    detect_post_news_liquidity_sweep,
    detect_post_news_mss,
    generate_news_sweep_signal,
    is_news_restricted_time,
    score_news_sweep_setup,
    wait_for_post_news_stabilization,
)


def _c(index, minute, open_, high, low, close, closed=True):
    return {
        "index": index,
        "timestamp": f"2026-06-05T08:{minute:02d}:00Z",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1000,
        "is_closed": closed,
    }


def _event(**overrides):
    data = {
        "event_id": "CPI_20260605",
        "event_name": "CPI",
        "currency": "USD",
        "impact_level": "high",
        "scheduled_time": "2026-06-05T08:30:00Z",
        "restriction_minutes_before": 30,
        "stabilization_minutes": 15,
    }
    data.update(overrides)
    return data


def _pool(pool_id, side, price, *, swept_status="unswept", quality=8.5):
    return {
        "liquidity_id": pool_id,
        "id": pool_id,
        "liquidity_type": "previous_day_low" if side == "sell_side" else "previous_day_high",
        "side": side,
        "price": price,
        "zone_low": price - 0.2,
        "zone_high": price + 0.2,
        "quality_score": quality,
        "target_priority_score": quality,
        "swept_status": swept_status,
    }


def _base_config(**overrides):
    data = {
        "high_impact_restriction_minutes_before": 30,
        "active_news_minutes": 1,
        "post_news_stabilization_minutes": 15,
        "min_candles_after_news": 4,
        "news_spike_lookahead_candles": 2,
        "news_spike_atr_multiplier": 2.0,
        "max_news_candle_atr_multiplier": 4.0,
        "stable_range_atr_multiplier": 2.0,
        "sweep_buffer": 0.05,
        "mss_break_buffer": 0.05,
        "max_mss_wait_candles": 8,
        "minimum_target_distance": 1.0,
        "min_rr": 2.0,
        "max_spread": 0.6,
        "average_spread": 0.2,
        "spread_multiplier_limit": 3.5,
        "expected_slippage": 0.25,
        "max_allowed_slippage": 0.7,
        "minimum_setup_score": 7.5,
    }
    data.update(overrides)
    return data


def _bullish_candles():
    return [
        _c(0, 20, 100.0, 100.5, 99.5, 100.1),
        _c(1, 25, 100.1, 100.4, 99.6, 100.0),
        _c(2, 30, 100.0, 100.2, 94.0, 96.2),
        _c(3, 35, 96.2, 98.6, 95.8, 97.8),
        _c(4, 40, 97.8, 99.2, 96.8, 98.7),
        _c(5, 45, 98.7, 101.6, 98.4, 101.2),
        _c(6, 50, 101.2, 103.4, 100.8, 102.9),
        _c(7, 55, 102.9, 103.2, 101.0, 101.8),
        _c(8, 59, 101.8, 102.4, 101.3, 102.1),
        _c(9, 59, 102.1, 120.0, 80.0, 118.0, closed=False),
    ]


def _bearish_candles():
    return [
        _c(0, 20, 100.0, 100.4, 99.5, 100.1),
        _c(1, 25, 100.1, 100.6, 99.8, 100.2),
        _c(2, 30, 100.2, 106.5, 99.8, 104.8),
        _c(3, 35, 104.8, 105.5, 101.8, 103.0),
        _c(4, 40, 103.0, 104.0, 100.9, 101.6),
        _c(5, 45, 101.6, 101.9, 98.4, 98.9),
        _c(6, 50, 98.9, 99.8, 96.8, 97.2),
        _c(7, 55, 97.2, 99.5, 96.9, 98.0),
    ]


def _bullish_context(**overrides):
    context = {
        "symbol": "XAUUSD",
        "timestamp": "2026-06-05T08:59:00Z",
        "candles": _bullish_candles(),
        "news_calendar": [_event()],
        "spread_status": {"spread_points": 0.28, "average_spread": 0.20},
        "expected_slippage": 0.25,
        "liquidity_pools": [
            _pool("PDL_20260604", "sell_side", 95.0),
            _pool("PDH_20260604", "buy_side", 124.0),
        ],
        "entry_poi": {
            "entry_poi_detected": True,
            "poi_type": "bullish_fvg",
            "entry_price": 101.0,
            "retest_status": "retested",
            "reaction_confirmed": True,
        },
        "htf_bias": {"bias_direction": "bullish"},
    }
    context.update(overrides)
    return context


def _bearish_context(**overrides):
    context = {
        "symbol": "XAUUSD",
        "timestamp": "2026-06-05T08:59:00Z",
        "candles": _bearish_candles(),
        "news_calendar": [_event(event_name="NFP", event_id="NFP_20260605")],
        "spread_status": {"spread_points": 0.28, "average_spread": 0.20},
        "expected_slippage": 0.25,
        "liquidity_pools": [
            _pool("PDH_20260604", "buy_side", 105.0),
            _pool("PDL_20260604", "sell_side", 75.0),
        ],
        "entry_poi": {
            "entry_poi_detected": True,
            "poi_type": "bearish_order_block",
            "entry_price": 99.0,
            "retest_status": "retested",
            "reaction_confirmed": True,
        },
        "htf_bias": {"bias_direction": "bearish"},
    }
    context.update(overrides)
    return context


def test_pre_news_window_blocks_new_trades():
    status = is_news_restricted_time("2026-06-05T08:10:00Z", [_event()], _base_config())

    assert status["restricted"] is True
    assert status["restriction_type"] == "pre_news"
    assert status["reason"] == "pre_news_restricted"


def test_valid_bullish_post_news_sweep_signal():
    signal = generate_news_sweep_signal(_bullish_context(), _base_config())

    assert signal["signal_status"] == "valid"
    assert signal["trade_allowed"] is True
    assert signal["direction"] == "bullish"
    assert signal["news_spike"]["spike_detected"] is True
    assert signal["stabilization"]["stabilized"] is True
    assert signal["liquidity_sweep"]["swept_side"] == "sell_side"
    assert signal["mss"]["mss_confirmed"] is True
    assert signal["entry_poi"]["entry_poi_detected"] is True
    assert signal["risk"]["risk_percent"] < 1.0
    assert signal["risk"]["rr"] >= 2.0
    assert signal["score"]["total_score"] >= 8.0


def test_valid_bearish_post_news_sweep_signal():
    signal = generate_news_sweep_signal(_bearish_context(), _base_config())

    assert signal["signal_status"] == "valid"
    assert signal["trade_allowed"] is True
    assert signal["direction"] == "bearish"
    assert signal["liquidity_sweep"]["swept_side"] == "buy_side"
    assert signal["mss"]["mss_confirmed"] is True
    assert signal["target"]["target_side"] == "sell_side"
    assert signal["score"]["total_score"] >= 8.0


def test_first_news_spike_that_looks_perfect_is_rejected():
    context = _bullish_context(
        timestamp="2026-06-05T08:31:00Z",
        candles=_bullish_candles()[:3],
        spread_status={"spread_points": 1.2, "average_spread": 0.2},
        entry_poi={
            "entry_poi_detected": True,
            "poi_type": "huge_news_fvg",
            "entry_price": 96.0,
            "retest_status": "retested",
            "reaction_confirmed": True,
        },
    )

    signal = generate_news_sweep_signal(context, _base_config())

    assert signal["signal_status"] in {"rejected", "no_trade"}
    assert signal["trade_allowed"] is False
    assert "post_news_not_stabilized" in signal["rejection_reasons"]
    assert "spread_too_high" in signal["rejection_reasons"]
    assert "candle_range_abnormal" in signal["rejection_reasons"]


def test_stabilization_never_occurs_rejects_trade():
    candles = [
        _c(0, 20, 100.0, 100.3, 99.7, 100.1),
        _c(1, 25, 100.1, 100.4, 99.8, 100.0),
        _c(2, 30, 100.0, 108.0, 94.0, 101.0),
        _c(3, 35, 101.0, 110.0, 95.0, 102.0),
        _c(4, 40, 102.0, 109.0, 93.0, 101.5),
        _c(5, 45, 101.5, 111.0, 92.0, 100.8),
        _c(6, 50, 100.8, 109.5, 93.5, 101.2),
    ]
    context = _bullish_context(
        candles=candles,
        timestamp="2026-06-05T08:50:00Z",
        spread_status={"spread_points": 0.9, "average_spread": 0.2},
    )

    signal = generate_news_sweep_signal(context, _base_config())

    assert signal["signal_status"] == "rejected"
    assert signal["trade_allowed"] is False
    assert "post_news_not_stabilized" in signal["rejection_reasons"]
    assert "spread_not_normalized" in signal["rejection_reasons"]
    assert "candle_range_abnormal" in signal["rejection_reasons"]
    assert "spread_too_high" in signal["rejection_reasons"]


def test_valid_sweep_but_wide_stop_and_poor_rr_is_rejected():
    context = _bearish_context(
        liquidity_pools=[
            _pool("PDH_20260604", "buy_side", 105.0),
            _pool("NEAR_SELL_SIDE", "sell_side", 96.8),
        ],
        entry_poi={
            "entry_poi_detected": True,
            "poi_type": "bearish_order_block",
            "entry_price": 99.0,
            "retest_status": "retested",
            "reaction_confirmed": True,
        },
    )

    signal = generate_news_sweep_signal(context, _base_config(post_news_stop_atr_multiplier=0.9))

    assert signal["signal_status"] == "rejected"
    assert signal["trade_allowed"] is False
    assert "rr_below_minimum" in signal["rejection_reasons"]
    assert "stop_too_wide" in signal["rejection_reasons"]
    assert "target_distance_insufficient" in signal["rejection_reasons"]


def test_required_detectors_are_usable_independently():
    context = _bullish_context()
    config = _base_config()
    event = context["news_calendar"][0]
    spike = detect_news_spike(context["candles"], event, context["spread_status"], config)
    stabilization = wait_for_post_news_stabilization(context["candles"], event, context["spread_status"], None, config)
    sweep = detect_post_news_liquidity_sweep(
        context["candles"], context["liquidity_pools"], event, stabilization, config
    )
    mss = detect_post_news_mss(context["candles"], [], sweep, stabilization, config)
    setup = {
        "news_status": is_news_restricted_time(context["timestamp"], context["news_calendar"], config),
        "news_spike": spike,
        "stabilization": stabilization,
        "liquidity_sweep": sweep,
        "mss": mss,
        "displacement": {"confirmed": True, "strength_score": 8.0},
        "entry_poi": {"entry_poi_detected": True, "reaction_confirmed": True},
        "target": {"target_valid": True, "target_quality_score": 8.0},
        "risk": {"rr_valid": True, "rr": 2.2},
        "rejection_reasons": [],
    }
    score = score_news_sweep_setup(setup, context, config)

    assert spike["spike_detected"] is True
    assert stabilization["stabilized"] is True
    assert sweep["sweep_detected"] is True
    assert mss["mss_confirmed"] is True
    assert score["trade_allowed"] is True
