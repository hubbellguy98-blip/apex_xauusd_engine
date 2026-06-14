from src.strategy.ict_smc_strategies.power_of_three_amd import (
    detect_accumulation_range,
    detect_distribution_shift,
    detect_manipulation_sweep,
    generate_amd_signal,
    score_accumulation_quality,
    score_amd_setup,
)


def _c(index, open_, high, low, close, volume=1000, closed=True):
    return {
        "index": index,
        "timestamp": f"2026-06-04T00:{index:02d}:00Z",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "is_closed": closed,
    }


def _base_config(**overrides):
    data = {
        "min_total_candles": 8,
        "min_accumulation_candles": 5,
        "min_accumulation_range_size": 1.0,
        "max_accumulation_range_size": 20.0,
        "min_accumulation_range_atr": 0.5,
        "max_accumulation_range_atr": 6.0,
        "min_accumulation_quality": 6.0,
        "max_dominant_candle_ratio": 0.96,
        "max_accumulation_trend_efficiency": 0.50,
        "min_boundary_touches": 2,
        "sweep_buffer": 0.05,
        "min_sweep_depth": 0.10,
        "max_sweep_atr": 3.5,
        "max_reclaim_candles": 3,
        "max_distribution_wait_candles": 6,
        "break_buffer": 0.05,
        "min_body_to_range": 0.50,
        "displacement_min_range_to_atr": 0.50,
        "stop_buffer": 0.10,
        "stop_atr_buffer": 0.04,
        "min_rr": 2.0,
        "minimum_setup_score": 7.0,
    }
    data.update(overrides)
    return data


def _bullish_context():
    candles = [
        _c(0, 101.0, 103.8, 100.5, 102.2),
        _c(1, 102.2, 104.0, 101.0, 101.5),
        _c(2, 101.5, 103.4, 100.2, 102.8),
        _c(3, 102.8, 103.7, 100.4, 101.2),
        _c(4, 101.2, 103.5, 100.0, 102.0),
        _c(5, 102.0, 102.2, 98.7, 100.7),
        _c(6, 100.7, 102.0, 100.2, 101.5),
        _c(7, 101.5, 106.0, 101.4, 105.2),
        _c(8, 105.2, 106.0, 104.8, 105.5, closed=False),
    ]
    return {
        "symbol": "XAUUSD",
        "candles": candles,
        "accumulation_window": {"start_position": 0, "end_position": 4, "range_type": "asian_range"},
        "manipulation_window": {"start_position": 5, "end_position": 8},
        "liquidity_pools": [{"id": "PDH_BUY_SIDE", "side": "buy_side", "price": 122.0}],
        "htf_bias": {"bias_direction": "bullish", "confidence_score": 8.0},
        "news_status": {"restricted": False},
        "spread_status": {"spread_safe": True, "spread_points": 0.0},
    }


def _bearish_context():
    candles = [
        _c(0, 203.0, 204.0, 200.4, 201.8),
        _c(1, 201.8, 203.8, 200.0, 202.5),
        _c(2, 202.5, 203.6, 200.2, 201.2),
        _c(3, 201.2, 204.0, 200.5, 203.2),
        _c(4, 203.2, 203.7, 200.3, 202.0),
        _c(5, 202.0, 205.6, 201.8, 203.3),
        _c(6, 203.3, 203.6, 201.5, 202.2),
        _c(7, 202.2, 202.4, 196.8, 197.5),
        _c(8, 197.5, 198.0, 197.0, 197.4, closed=False),
    ]
    return {
        "symbol": "XAUUSD",
        "candles": candles,
        "accumulation_window": {"start_position": 0, "end_position": 4, "range_type": "asian_range"},
        "manipulation_window": {"start_position": 5, "end_position": 8},
        "liquidity_pools": [{"id": "PDL_SELL_SIDE", "side": "sell_side", "price": 180.0}],
        "htf_bias": {"bias_direction": "bearish", "confidence_score": 8.0},
        "news_status": {"restricted": False},
        "spread_status": {"spread_safe": True, "spread_points": 0.0},
    }


def test_valid_bullish_amd_signal():
    signal = generate_amd_signal(_bullish_context(), _base_config())

    assert signal["signal_status"] == "valid"
    assert signal["direction"] == "bullish"
    assert signal["trade_allowed"] is True
    assert signal["score"]["total_score"] >= 8
    assert signal["accumulation"]["clean_range"] is True
    assert signal["manipulation"]["swept_side"] == "range_low_sell_side"
    assert signal["distribution"]["distribution_confirmed"] is True


def test_valid_bearish_amd_signal():
    signal = generate_amd_signal(_bearish_context(), _base_config())

    assert signal["signal_status"] == "valid"
    assert signal["direction"] == "bearish"
    assert signal["trade_allowed"] is True
    assert signal["score"]["total_score"] >= 8
    assert signal["manipulation"]["swept_side"] == "range_high_buy_side"
    assert signal["distribution"]["distribution_confirmed"] is True


def test_accumulation_and_manipulation_without_distribution_is_context_only():
    context = _bullish_context()
    context["candles"][7] = _c(7, 101.5, 102.5, 100.8, 101.9)

    signal = generate_amd_signal(context, _base_config())

    assert signal["signal_status"] == "context_only"
    assert signal["trade_allowed"] is False
    assert "no_distribution_confirmation" in signal["rejection_reasons"]
    assert "no_bullish_mss_after_manipulation" in signal["rejection_reasons"]


def test_real_breakout_is_not_manipulation():
    context = _bullish_context()
    context["candles"][5] = _c(5, 102.0, 102.2, 98.7, 99.0)
    context["candles"][6] = _c(6, 99.0, 99.6, 97.8, 98.4)
    context["candles"][7] = _c(7, 98.4, 99.0, 96.5, 97.0)

    signal = generate_amd_signal(context, _base_config())

    assert signal["signal_status"] == "rejected"
    assert "real_breakout_not_manipulation" in signal["rejection_reasons"]
    assert signal["trade_allowed"] is False


def test_news_double_sided_sweep_day_is_rejected():
    context = _bullish_context()
    context["candles"][5] = _c(5, 102.0, 107.8, 96.8, 102.2)
    context["news_status"] = {"restricted": True, "high_impact": True}
    context["spread_status"] = {"status": "high", "spread_safe": False, "spread_points": 1.2}

    signal = generate_amd_signal(context, _base_config(max_sweep_atr=10.0))

    assert signal["signal_status"] == "rejected"
    assert "news_restricted" in signal["rejection_reasons"]
    assert "spread_too_high" in signal["rejection_reasons"]
    assert "double_sided_sweep_no_clear_direction" in signal["rejection_reasons"]
    assert signal["trade_allowed"] is False


def test_required_detectors_are_usable_independently():
    context = _bullish_context()
    config = _base_config()
    accumulation = detect_accumulation_range(context["candles"], context["accumulation_window"], "UTC", config)
    quality = score_accumulation_quality(accumulation, context["candles"], None, config)
    manipulation = detect_manipulation_sweep(context["candles"], accumulation, context["manipulation_window"], config)
    distribution = detect_distribution_shift(context["candles"], manipulation, None, None, config)
    setup = {
        "direction": "bullish",
        "accumulation": accumulation,
        "manipulation": manipulation,
        "distribution": distribution,
        "entry_poi": {"quality_score": 8.0},
        "risk": {"rr": 2.5},
        "rejection_reasons": [],
    }
    score = score_amd_setup(setup, context, config)

    assert accumulation["valid_status"] is True
    assert quality["clean_range"] is True
    assert manipulation["manipulation_detected"] is True
    assert distribution["distribution_confirmed"] is True
    assert score["trade_allowed"] is True
