from src.strategy.ict_smc_strategies.order_block_retest import (
    detect_displacement,
    detect_liquidity_sweep,
    detect_ob_retest,
    detect_order_block_after_sweep,
    generate_ob_retest_signal,
    validate_ob_reaction,
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
        "sweep_buffer": 0.05,
        "break_buffer": 0.05,
        "max_displacement_wait_candles": 6,
        "displacement_min_range_to_atr": 0.5,
        "min_rr": 2.0,
        "minimum_setup_score": 7.0,
        "max_stop_atr_multiplier": 6.0,
    }
    data.update(overrides)
    return data


def _bullish_context():
    candles = [
        _c(0, 101.0, 101.8, 100.6, 101.4),
        _c(1, 101.4, 102.3, 100.8, 101.7),
        _c(2, 101.7, 101.9, 99.4, 100.6),
        _c(3, 101.8, 102.2, 100.7, 101.0),
        _c(4, 101.2, 107.0, 101.1, 106.5),
        _c(5, 105.8, 106.2, 101.2, 102.2),
        _c(6, 102.0, 105.0, 101.8, 104.5),
        _c(7, 104.5, 106.0, 104.0, 105.5, closed=False),
    ]
    return {
        "symbol": "XAUUSD",
        "candles": candles,
        "liquidity_pools": [
            {"id": "SSL_1", "side": "sell_side", "zone_low": 100.0, "zone_high": 100.2},
            {"id": "BSL_TARGET", "side": "buy_side", "zone_low": 118.0, "zone_high": 118.2},
        ],
        "swings": [{"id": "SWING_HIGH_1", "kind": "high", "index": 1, "price": 102.3}],
        "spread_status": {"spread_safe": True, "spread_points": 0.0},
        "news_status": {"restricted": False},
    }


def _bearish_context():
    candles = [
        _c(0, 108.8, 109.2, 107.8, 108.3),
        _c(1, 108.3, 109.1, 107.7, 108.5),
        _c(2, 108.5, 110.6, 108.0, 109.4),
        _c(3, 108.2, 109.5, 107.8, 109.0),
        _c(4, 108.8, 108.9, 103.0, 103.5),
        _c(5, 104.0, 108.4, 103.8, 107.2),
        _c(6, 107.1, 107.4, 104.8, 105.0),
        _c(7, 105.0, 105.4, 104.0, 104.7, closed=False),
    ]
    return {
        "symbol": "XAUUSD",
        "candles": candles,
        "liquidity_pools": [
            {"id": "BSL_1", "side": "buy_side", "zone_low": 109.8, "zone_high": 110.0},
            {"id": "SSL_TARGET", "side": "sell_side", "zone_low": 90.0, "zone_high": 90.2},
        ],
        "swings": [{"id": "SWING_LOW_1", "kind": "low", "index": 1, "price": 107.7}],
        "spread_status": {"spread_safe": True, "spread_points": 0.0},
        "news_status": {"restricted": False},
    }


def test_valid_bullish_order_block_retest_signal():
    context = _bullish_context()
    signal = generate_ob_retest_signal(context, _base_config())

    assert signal["signal_status"] == "valid"
    assert signal["direction"] == "bullish"
    assert signal["order_block"]["ob_type"] == "bullish_order_block"
    assert signal["entry"]["entry_triggered"] is True
    assert signal["trade_allowed"] is True
    assert signal["score"]["total_score"] >= 8


def test_valid_bearish_order_block_retest_signal():
    context = _bearish_context()
    signal = generate_ob_retest_signal(context, _base_config())

    assert signal["signal_status"] == "valid"
    assert signal["direction"] == "bearish"
    assert signal["order_block"]["ob_type"] == "bearish_order_block"
    assert signal["trade_allowed"] is True
    assert signal["score"]["total_score"] >= 8


def test_random_opposite_candle_without_sweep_displacement_or_structure_is_rejected():
    context = {
        "symbol": "XAUUSD",
        "candles": [
            _c(0, 101.0, 101.5, 100.5, 101.2),
            _c(1, 101.2, 101.3, 100.8, 100.9),
            _c(2, 100.9, 101.4, 100.7, 101.1),
            _c(3, 101.1, 101.2, 100.6, 100.8),
        ],
        "liquidity_pools": [],
    }

    signal = generate_ob_retest_signal(context, _base_config())

    assert signal["signal_status"] == "rejected"
    assert "weak_ob_no_displacement" in signal["rejection_reasons"]
    assert "ob_not_validated_by_structure_break" in signal["rejection_reasons"]
    assert "missing_required_liquidity_sweep" in signal["rejection_reasons"]


def test_order_block_retest_without_ltf_confirmation_is_rejected():
    context = _bullish_context()
    context["ltf_context"] = {
        "sell_side_sweep_inside_ob": False,
        "bullish_mss_confirmed": False,
        "bearish_mss_confirmed": True,
    }

    signal = generate_ob_retest_signal(
        context,
        _base_config(confirmation_mode="ltf_mss", entry_mode="conservative"),
    )

    assert signal["signal_status"] == "rejected"
    assert signal["trade_allowed"] is False
    assert "no_ob_reaction_confirmation" in signal["rejection_reasons"]


def test_xauusd_order_block_too_wide_and_poor_rr_is_rejected():
    context = _bullish_context()
    context["liquidity_pools"] = [
        {"id": "SSL_1", "side": "sell_side", "zone_low": 100.0, "zone_high": 100.2},
        {"id": "NEAR_BSL_TARGET", "side": "buy_side", "zone_low": 105.2, "zone_high": 105.3},
    ]

    signal = generate_ob_retest_signal(
        context,
        _base_config(max_ob_atr_multiplier=0.4, max_stop_atr_multiplier=0.8),
    )

    assert signal["signal_status"] == "rejected"
    assert "ob_too_wide" in signal["rejection_reasons"]
    assert "stop_too_large" in signal["rejection_reasons"]
    assert "rr_below_minimum" in signal["rejection_reasons"]


def test_helper_functions_expose_the_full_ob_retest_pipeline():
    context = _bullish_context()
    cfg = _base_config()

    sweep = detect_liquidity_sweep(context["candles"], context["liquidity_pools"], cfg)[0]
    displacement = detect_displacement(context["candles"], sweep, context["swings"], cfg)
    order_block = detect_order_block_after_sweep(context["candles"], sweep, displacement, cfg)
    retest = detect_ob_retest(context["candles"], order_block, cfg)
    reaction = validate_ob_reaction(context["candles"], order_block, retest, {}, cfg)

    assert sweep["swept_side"] == "sell_side"
    assert displacement["structure_break_confirmed"] is True
    assert order_block["created_after_sweep"] is True
    assert retest["retest_detected"] is True
    assert reaction["confirmed"] is True
