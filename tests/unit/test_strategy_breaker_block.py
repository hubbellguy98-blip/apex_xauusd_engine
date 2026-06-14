from src.strategy.ict_smc_strategies.breaker_block import (
    detect_breaker_block,
    detect_breaker_retest,
    detect_order_block_failure,
    detect_order_blocks,
    generate_breaker_signal,
    score_breaker_setup,
    validate_breaker_reaction,
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
        "break_buffer": 0.02,
        "failure_break_buffer": 0.02,
        "max_displacement_wait_candles": 4,
        "displacement_min_range_to_atr": 0.35,
        "min_body_to_range": 0.50,
        "min_acceptance_closes": 1,
        "acceptance_wait_candles": 3,
        "max_retest_wait_candles": 8,
        "reaction_wait_candles": 3,
        "max_breaker_width_atr": 3.0,
        "min_rr": 2.0,
        "minimum_setup_score": 7.0,
        "stop_buffer": 0.05,
        "stop_atr_buffer": 0.02,
    }
    data.update(overrides)
    return data


def _bullish_context():
    candles = [
        _c(0, 105.0, 106.0, 104.0, 105.5),
        _c(1, 105.5, 106.0, 101.0, 102.0),
        _c(2, 102.0, 106.0, 101.5, 105.5),
        _c(3, 105.2, 105.4, 98.0, 99.0),
        _c(4, 99.0, 108.0, 98.8, 107.2),
        _c(5, 107.1, 110.0, 106.5, 109.0),
        _c(6, 109.0, 109.5, 104.5, 105.8),
        _c(7, 105.7, 111.0, 105.2, 110.5),
        _c(8, 110.5, 111.0, 110.0, 110.8, closed=False),
    ]
    return {
        "symbol": "XAUUSD",
        "candles": candles,
        "swings": [{"id": "LOW_1", "kind": "low", "index": 1, "price": 101.0}],
        "liquidity_pools": [{"id": "BSL_TARGET", "side": "buy_side", "price": 130.0}],
        "htf_bias": {"bias_direction": "bullish", "confidence_score": 8.5},
        "news_status": {"restricted": False},
        "spread_status": {"spread_safe": True, "spread_points": 0.0},
    }


def _bearish_context():
    candles = [
        _c(0, 100.0, 101.0, 99.0, 100.5),
        _c(1, 100.5, 106.0, 100.0, 105.0),
        _c(2, 105.0, 106.5, 101.5, 102.0),
        _c(3, 102.0, 112.0, 101.8, 111.0),
        _c(4, 111.0, 111.5, 98.0, 100.0),
        _c(5, 100.0, 100.5, 96.0, 97.0),
        _c(6, 97.0, 104.0, 96.8, 102.8),
        _c(7, 102.8, 103.5, 94.0, 95.0),
        _c(8, 95.0, 95.4, 94.6, 95.2, closed=False),
    ]
    return {
        "symbol": "XAUUSD",
        "candles": candles,
        "swings": [{"id": "HIGH_1", "kind": "high", "index": 1, "price": 106.0}],
        "liquidity_pools": [{"id": "SSL_TARGET", "side": "sell_side", "price": 60.0}],
        "htf_bias": {"bias_direction": "bearish", "confidence_score": 8.5},
        "news_status": {"restricted": False},
        "spread_status": {"spread_safe": True, "spread_points": 0.0},
    }


def test_valid_bullish_breaker_signal():
    signal = generate_breaker_signal(_bullish_context(), _base_config())

    assert signal["signal_status"] == "valid"
    assert signal["breaker_type"] == "bullish_breaker"
    assert signal["original_ob_type"] == "bearish_order_block"
    assert signal["trade_allowed"] is True
    assert signal["score"]["total_score"] >= 8
    assert signal["target"]["side"] == "buy_side"


def test_valid_bearish_breaker_signal():
    signal = generate_breaker_signal(_bearish_context(), _base_config())

    assert signal["signal_status"] == "valid"
    assert signal["breaker_type"] == "bearish_breaker"
    assert signal["original_ob_type"] == "bullish_order_block"
    assert signal["trade_allowed"] is True
    assert signal["score"]["total_score"] >= 8
    assert signal["target"]["side"] == "sell_side"


def test_support_resistance_flip_without_original_ob_is_rejected():
    context = {
        "symbol": "XAUUSD",
        "candles": [
            _c(0, 100.0, 101.0, 99.5, 100.5),
            _c(1, 100.5, 102.0, 100.2, 101.8),
            _c(2, 101.8, 103.0, 101.6, 102.6),
            _c(3, 102.6, 102.9, 101.7, 102.0),
            _c(4, 102.0, 104.0, 101.8, 103.5),
            _c(5, 103.5, 103.8, 102.5, 103.0),
        ],
        "order_blocks": [],
        "swings": [],
        "liquidity_pools": [{"id": "BSL", "side": "buy_side", "price": 108.0}],
    }

    signal = generate_breaker_signal(context, _base_config())

    assert signal["signal_status"] == "rejected"
    assert "no_original_order_block" in signal["rejection_reasons"]
    assert signal["trade_allowed"] is False


def test_wick_only_order_block_failure_is_rejected():
    context = _bullish_context()
    context["candles"][4] = _c(4, 99.0, 108.0, 98.8, 104.0)
    context["candles"][5] = _c(5, 104.0, 105.0, 103.0, 104.4)
    context["candles"][6] = _c(6, 104.4, 105.2, 103.8, 104.7)
    context["candles"][7] = _c(7, 104.7, 105.4, 104.1, 104.8)

    signal = generate_breaker_signal(context, _base_config())

    assert signal["signal_status"] == "rejected"
    assert "wick_only_ob_failure" in signal["rejection_reasons"]
    assert "no_acceptance_beyond_ob" in signal["rejection_reasons"]
    assert "no_bullish_structure_shift" in signal["rejection_reasons"]


def test_wide_breaker_poor_rr_and_spread_are_rejected():
    context = _bearish_context()
    context["liquidity_pools"] = [{"id": "NEAR_SSL", "side": "sell_side", "price": 93.0}]
    context["spread_status"] = {"status": "high", "spread_safe": False, "spread_points": 0.8}

    signal = generate_breaker_signal(context, _base_config(max_breaker_width_atr=0.25))

    assert signal["signal_status"] == "rejected"
    assert "breaker_zone_too_wide" in signal["rejection_reasons"]
    assert "rr_below_minimum" in signal["rejection_reasons"]
    assert "spread_too_high_or_caution" in signal["rejection_reasons"]
    assert signal["trade_allowed"] is False


def test_required_detectors_are_usable_independently():
    context = _bullish_context()
    config = _base_config()
    obs = detect_order_blocks(context["candles"], context["swings"], None, config)
    failure = detect_order_block_failure(context["candles"], obs, config)[0]
    breaker = detect_breaker_block(context["candles"], obs[-1], failure, None, config)
    retest = detect_breaker_retest(context["candles"], breaker, config)
    reaction = validate_breaker_reaction(context["candles"], breaker, retest, None, config)
    setup = {
        "direction": breaker["direction"],
        "original_order_block": obs[-1],
        "failure_event": failure,
        "breaker_block": breaker,
        "retest": retest,
        "reaction": reaction,
        "risk": {"rr": 3.0},
        "rejection_reasons": [],
    }
    score = score_breaker_setup(setup, context, config)

    assert obs[-1]["ob_type"] == "bearish_order_block"
    assert failure["close_beyond_zone"] is True
    assert breaker["breaker_type"] == "bullish_breaker"
    assert retest["retest_detected"] is True
    assert reaction["entry_triggered"] is True
    assert score["total_score"] >= 8
