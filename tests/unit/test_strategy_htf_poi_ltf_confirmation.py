from src.strategy.ict_smc_strategies.htf_poi_ltf_confirmation import (
    detect_ltf_sweep_inside_htf_poi,
    generate_htf_poi_ltf_confirmation_signal,
    map_htf_poi_to_ltf,
)


def _c(index, open_, high, low, close, closed=True):
    return {
        "index": index,
        "timestamp": f"2026-06-06T09:{index:02d}:00Z",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1000,
        "is_closed": closed,
    }


def _poi(poi_id, direction, low, high, *, quality=8.6):
    return {
        "poi_id": poi_id,
        "poi_type": f"{direction}_order_block",
        "timeframe": "1H",
        "direction": direction,
        "zone_low": low,
        "zone_high": high,
        "active_status": True,
        "fresh_status": "fresh",
        "quality_score": quality,
    }


def _pool(pool_id, side, price, *, swept_status="unswept", quality=8.5):
    return {
        "liquidity_id": pool_id,
        "side": side,
        "price": price,
        "zone_low": price - 0.2,
        "zone_high": price + 0.2,
        "liquidity_type": "external_liquidity",
        "quality_score": quality,
        "target_priority_score": quality,
        "swept_status": swept_status,
    }


def _base_config(**overrides):
    data = {
        "minimum_setup_score": 7.2,
        "min_rr": 2.0,
        "sweep_buffer": 0.02,
        "break_buffer": 0.02,
        "poi_tolerance": 0.4,
        "max_htf_poi_width": 8.0,
        "max_ltf_entry_poi_width": 4.0,
        "minimum_target_distance": 1.0,
        "stop_atr_buffer_multiplier": 0.01,
        "spread_buffer_multiplier": 0.2,
        "displacement_min_range_to_atr": 0.45,
    }
    data.update(overrides)
    return data


def _bullish_ltf_candles():
    return [
        _c(0, 107.0, 108.0, 105.0, 106.0),
        _c(1, 106.0, 107.0, 102.0, 103.0),
        _c(2, 103.0, 104.0, 99.4, 101.0),
        _c(3, 101.0, 102.5, 100.8, 102.0),
        _c(4, 102.0, 102.4, 101.6, 102.1),
        _c(5, 103.0, 108.0, 103.0, 107.2),
        _c(6, 104.8, 105.2, 102.8, 104.5),
        _c(7, 104.5, 120.0, 80.0, 119.0, closed=False),
    ]


def _bearish_ltf_candles():
    return [
        _c(0, 103.0, 105.0, 101.0, 104.0),
        _c(1, 104.0, 109.0, 103.0, 108.0),
        _c(2, 108.0, 110.7, 107.0, 108.5),
        _c(3, 108.5, 109.2, 107.2, 108.0),
        _c(4, 108.0, 108.7, 107.4, 108.2),
        _c(5, 107.0, 107.1, 101.5, 102.0),
        _c(6, 105.4, 107.4, 104.8, 105.2),
        _c(7, 105.2, 140.0, 90.0, 92.0, closed=False),
    ]


def _bullish_context(**overrides):
    context = {
        "symbol": "XAUUSD",
        "htf_poi_zones": [_poi("H1_BULLISH_OB_1", "bullish", 100.0, 103.0)],
        "ltf_candles": _bullish_ltf_candles(),
        "ltf_liquidity_pools": [_pool("SSL_1", "sell_side", 100.0)],
        "ltf_swings": [{"swing_id": "POST_SWEEP_HIGH", "kind": "high", "index": 3, "price": 102.5}],
        "htf_liquidity_targets": [_pool("BSL_TARGET", "buy_side", 116.0)],
        "htf_bias": {"bias_direction": "bullish"},
        "price_location": "discount",
        "spread_status": {"spread_points": 0.1, "average_spread": 0.1},
        "news_status": {"restricted": False},
    }
    context.update(overrides)
    return context


def _bearish_context(**overrides):
    context = {
        "symbol": "XAUUSD",
        "htf_poi_zones": [_poi("H1_BEARISH_OB_1", "bearish", 107.0, 110.0)],
        "ltf_candles": _bearish_ltf_candles(),
        "ltf_liquidity_pools": [_pool("BSL_1", "buy_side", 110.0)],
        "ltf_swings": [{"swing_id": "POST_SWEEP_LOW", "kind": "low", "index": 3, "price": 107.2}],
        "htf_liquidity_targets": [_pool("SSL_TARGET", "sell_side", 94.0)],
        "htf_bias": {"bias_direction": "bearish"},
        "price_location": "premium",
        "spread_status": {"spread_points": 0.1, "average_spread": 0.1},
        "news_status": {"restricted": False},
    }
    context.update(overrides)
    return context


def test_valid_bullish_htf_poi_ltf_confirmation_signal():
    signal = generate_htf_poi_ltf_confirmation_signal(_bullish_context(), _base_config())

    assert signal["signal_status"] == "valid"
    assert signal["trade_allowed"] is True
    assert signal["direction"] == "bullish"
    assert signal["ltf_sweep"]["swept_side"] == "sell_side"
    assert signal["ltf_mss"]["mss_confirmed"] is True
    assert signal["ltf_displacement"]["confirmed"] is True
    assert signal["ltf_entry_poi"]["retest_status"] == "retested"
    assert signal["risk"]["rr"] >= 2.0
    assert signal["score"]["total_score"] >= 7.2


def test_valid_bearish_htf_poi_ltf_confirmation_signal():
    signal = generate_htf_poi_ltf_confirmation_signal(_bearish_context(), _base_config())

    assert signal["signal_status"] == "valid"
    assert signal["trade_allowed"] is True
    assert signal["direction"] == "bearish"
    assert signal["ltf_sweep"]["swept_side"] == "buy_side"
    assert signal["ltf_mss"]["mss_confirmed"] is True
    assert signal["target"]["target_side"] == "sell_side"
    assert signal["score"]["total_score"] >= 7.2


def test_htf_poi_touch_only_is_context_not_trade():
    context = _bullish_context(
        ltf_candles=[
            _c(0, 107.0, 108.0, 105.0, 106.0),
            _c(1, 106.0, 107.0, 102.0, 103.0),
            _c(2, 103.0, 104.0, 102.2, 103.5),
        ]
    )

    signal = generate_htf_poi_ltf_confirmation_signal(context, _base_config())

    assert signal["signal_status"] == "context_only"
    assert signal["trade_allowed"] is False
    assert "htf_poi_touch_alone_not_tradeable" in signal["rejection_reasons"]


def test_ltf_confirmation_conflicting_with_strong_htf_bias_is_rejected():
    signal = generate_htf_poi_ltf_confirmation_signal(
        _bullish_context(htf_bias={"bias_direction": "bearish"}),
        _base_config(),
    )

    assert signal["trade_allowed"] is False
    assert "ltf_signal_conflicts_with_htf_poi" in signal["rejection_reasons"]
    assert "htf_poi_override" in signal["rejection_reasons"]


def test_huge_htf_poi_without_ltf_refinement_is_rejected():
    signal = generate_htf_poi_ltf_confirmation_signal(
        _bullish_context(htf_poi_zones=[_poi("H1_HUGE_DEMAND", "bullish", 95.0, 112.0)]),
        _base_config(max_htf_poi_width=8.0),
    )

    assert signal["trade_allowed"] is False
    assert "htf_poi_too_wide_without_ltf_refinement" in signal["rejection_reasons"]


def test_low_level_sweep_detector_ignores_unclosed_future_candle():
    pois = [_poi("H1_BULLISH_OB_1", "bullish", 100.0, 103.0)]
    candles = [
        _c(0, 107.0, 108.0, 105.0, 106.0),
        _c(1, 106.0, 107.0, 102.0, 103.0),
        _c(2, 103.0, 104.0, 102.1, 103.0),
        _c(3, 103.0, 103.5, 99.0, 101.0, closed=False),
    ]
    mapped = map_htf_poi_to_ltf(pois, candles, _base_config())[0]

    sweep = detect_ltf_sweep_inside_htf_poi(candles, [_pool("SSL_1", "sell_side", 100.0)], mapped, _base_config())

    assert sweep is None
