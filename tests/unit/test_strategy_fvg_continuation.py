from src.strategy.ict_smc_strategies.fvg_continuation import (
    detect_bos,
    detect_displacement,
    detect_fvg,
    detect_fvg_retracement,
    detect_htf_bias,
    generate_fvg_continuation_signal,
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
        "atr_period": 5,
        "break_buffer": 0.05,
        "max_displacement_wait_candles": 4,
        "displacement_min_range_to_atr": 0.45,
        "min_body_to_range": 0.55,
        "min_close_position": 0.65,
        "min_displacement_score": 6.0,
        "min_fvg_atr_multiplier": 0.02,
        "max_fvg_atr_multiplier": 3.0,
        "min_rr": 2.0,
        "minimum_setup_score": 7.0,
        "reaction_wait_candles": 2,
    }
    data.update(overrides)
    return data


def _bullish_context():
    candles = [
        _c(0, 100.0, 101.0, 99.0, 100.5),
        _c(1, 100.5, 102.0, 100.0, 101.3),
        _c(2, 101.3, 101.7, 100.8, 101.1),
        _c(3, 101.1, 102.2, 100.9, 101.6),
        _c(4, 101.7, 107.0, 101.6, 106.5),
        _c(5, 106.6, 108.0, 103.2, 107.5),
        _c(6, 107.4, 107.6, 102.6, 103.0),
        _c(7, 103.1, 106.0, 102.9, 105.5),
        _c(8, 105.5, 106.4, 105.0, 106.0, closed=False),
    ]
    return {
        "symbol": "XAUUSD",
        "htf_bias": {
            "bias_direction": "bullish",
            "structure_state": "bullish_structure",
            "draw_on_liquidity": "buy_side",
            "confidence_score": 8.4,
        },
        "candles": candles,
        "swings": [{"id": "M15_SWING_HIGH_1", "kind": "high", "index": 1, "price": 102.0}],
        "liquidity_pools": [{"id": "BSL_TARGET", "side": "buy_side", "price": 114.0}],
        "spread_status": {"spread_safe": True, "spread_points": 0.0},
        "news_status": {"restricted": False},
    }


def _bearish_context():
    candles = [
        _c(0, 110.0, 111.0, 109.0, 110.5),
        _c(1, 110.2, 110.9, 107.7, 108.6),
        _c(2, 108.7, 109.2, 108.0, 108.9),
        _c(3, 108.9, 109.4, 107.8, 108.8),
        _c(4, 108.7, 108.8, 103.0, 103.5),
        _c(5, 103.4, 106.8, 102.8, 104.0),
        _c(6, 104.0, 107.2, 103.9, 107.0),
        _c(7, 107.1, 107.3, 104.8, 105.0),
        _c(8, 105.0, 105.3, 104.3, 104.8, closed=False),
    ]
    return {
        "symbol": "XAUUSD",
        "htf_bias": {
            "bias_direction": "bearish",
            "structure_state": "bearish_structure",
            "draw_on_liquidity": "sell_side",
            "confidence_score": 8.6,
        },
        "candles": candles,
        "swings": [{"id": "M15_SWING_LOW_1", "kind": "low", "index": 1, "price": 107.7}],
        "liquidity_pools": [{"id": "SSL_TARGET", "side": "sell_side", "price": 94.0}],
        "spread_status": {"spread_safe": True, "spread_points": 0.0},
        "news_status": {"restricted": False},
    }


def test_valid_bullish_fvg_continuation_signal():
    signal = generate_fvg_continuation_signal(_bullish_context(), _base_config())

    assert signal["signal_status"] == "valid"
    assert signal["direction"] == "bullish"
    assert signal["trade_allowed"] is True
    assert signal["score"]["total_score"] >= 8
    assert signal["fvg"]["fvg_type"] == "bullish_fvg"
    assert signal["target"]["side"] == "buy_side"


def test_valid_bearish_fvg_continuation_signal():
    signal = generate_fvg_continuation_signal(_bearish_context(), _base_config())

    assert signal["signal_status"] == "valid"
    assert signal["direction"] == "bearish"
    assert signal["trade_allowed"] is True
    assert signal["score"]["total_score"] >= 8
    assert signal["fvg"]["fvg_type"] == "bearish_fvg"
    assert signal["target"]["side"] == "sell_side"


def test_random_fvg_without_bos_is_rejected():
    context = {
        "symbol": "XAUUSD",
        "htf_bias": {"bias_direction": "neutral", "structure_state": "ranging", "confidence_score": 4.0},
        "market_condition": "choppy",
        "candles": [
            _c(0, 100.0, 100.5, 99.8, 100.2),
            _c(1, 100.2, 100.3, 99.9, 100.1),
            _c(2, 100.1, 101.0, 100.7, 100.9),
            _c(3, 100.9, 101.1, 100.6, 100.8),
            _c(4, 100.8, 101.0, 100.4, 100.6),
        ],
        "liquidity_pools": [{"id": "BSL_TARGET", "side": "buy_side", "price": 105.0}],
    }

    signal = generate_fvg_continuation_signal(context, _base_config())

    assert signal["signal_status"] == "rejected"
    assert "no_bos_for_continuation" in signal["rejection_reasons"]
    assert "htf_bias_not_aligned" in signal["rejection_reasons"]
    assert "random_fvg_no_displacement" in signal["rejection_reasons"]
    assert "choppy_market_random_fvg_risk" in signal["rejection_reasons"]


def test_fvg_invalidated_before_entry_is_rejected():
    context = _bullish_context()
    context["candles"][6] = _c(6, 107.4, 107.6, 101.7, 101.8)

    signal = generate_fvg_continuation_signal(context, _base_config())

    assert signal["signal_status"] == "rejected"
    assert "fvg_invalidated" in signal["rejection_reasons"]
    assert signal["trade_allowed"] is False


def test_xauusd_news_spike_fvg_too_large_is_rejected():
    context = _bullish_context()
    context["news_status"] = {"restricted": True, "high_impact": True, "post_news_stabilized": False}
    context["spread_status"] = {"status": "unsafe", "spread_safe": False, "spread_points": 0.8}

    signal = generate_fvg_continuation_signal(context, _base_config(max_fvg_atr_multiplier=0.15))

    assert signal["signal_status"] == "rejected"
    assert "news_restricted" in signal["rejection_reasons"]
    assert "fvg_too_large" in signal["rejection_reasons"]
    assert "spread_too_high" in signal["rejection_reasons"]
    assert "random_fvg_no_stabilized_structure" in signal["rejection_reasons"]


def test_required_detectors_are_usable_independently():
    context = _bullish_context()
    bias = detect_htf_bias(context["htf_bias"])
    bos = detect_bos(context["candles"], context["swings"], _base_config())[0]
    displacement = detect_displacement(context["candles"], bos, _base_config())
    fvg = detect_fvg(context["candles"], bos, displacement, _base_config())[-1]
    retracement = detect_fvg_retracement(context["candles"], fvg, _base_config())

    assert bias["bias_direction"] == "bullish"
    assert bos["confirmed_by_close"] is True
    assert displacement["confirmed"] is True
    assert fvg["created_by_displacement"] is True
    assert retracement["entry_triggered"] is True
