from src.strategy.ict_smc_strategies.liquidity_to_liquidity import (
    classify_internal_external_liquidity,
    detect_liquidity_pools,
    detect_liquidity_to_liquidity_path,
    determine_draw_on_liquidity,
    generate_liquidity_to_liquidity_signal,
    rank_liquidity_targets,
    score_liquidity_to_liquidity_setup,
)


def _c(index, open_, high, low, close, volume=1000, closed=True):
    return {
        "index": index,
        "timestamp": f"2026-06-05T09:{index:02d}:00Z",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "is_closed": closed,
    }


def _pool(
    pool_id,
    side,
    price,
    *,
    liquidity_type="swing_high",
    internal_or_external="external",
    swept_status="unswept",
    quality=8.5,
    priority=8.5,
):
    return {
        "liquidity_id": pool_id,
        "id": pool_id,
        "liquidity_type": liquidity_type,
        "side": side,
        "price": price,
        "zone_low": price - 0.2,
        "zone_high": price + 0.2,
        "quality_score": quality,
        "target_priority_score": priority,
        "timeframe": "5m",
        "swept_status": swept_status,
        "internal_or_external": internal_or_external,
        "created_index": 1,
        "created_position": 1,
        "last_touched_index": 8,
    }


def _base_config(**overrides):
    data = {
        "min_total_candles": 8,
        "minimum_start_quality": 5.0,
        "minimum_target_distance": 1.0,
        "min_rr": 2.0,
        "minimum_setup_score": 7.0,
        "max_spread": 0.5,
        "max_spread_to_target_ratio": 0.25,
        "slippage_points": 0.05,
        "blocker_quality_threshold": 7.5,
    }
    data.update(overrides)
    return data


def _entry(direction, entry_price=100.0, stop_loss=None):
    if stop_loss is None:
        stop_loss = 98.0 if direction == "bullish" else 102.0
    return {
        "entry_model_valid": True,
        "entry_type": f"{direction}_fvg_retracement",
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "retest_status": "retested",
        "reaction_confirmed": True,
    }


def _bullish_context(**overrides):
    context = {
        "symbol": "XAUUSD",
        "current_price": 100.0,
        "spread_points": 0.05,
        "liquidity_pools": [
            _pool(
                "ASIAN_LOW_20260605",
                "sell_side",
                99.0,
                liquidity_type="asian_low",
                swept_status="swept_reclaimed",
            ),
            _pool("ASIAN_HIGH_20260605", "buy_side", 106.0, liquidity_type="asian_high"),
        ],
        "dealing_range": {"range_low": 98.5, "range_high": 106.5},
        "latest_mss_event": {"direction": "bullish", "confirmed": True},
        "displacement": {"direction": "bullish", "confirmed": True, "strength_score": 8.0},
        "entry_model": _entry("bullish"),
        "htf_bias": {"bias_direction": "bullish"},
        "news_status": {"restricted": False},
    }
    context.update(overrides)
    return context


def _bearish_context(**overrides):
    context = {
        "symbol": "XAUUSD",
        "current_price": 100.0,
        "spread_points": 0.05,
        "liquidity_pools": [
            _pool(
                "PDH_20260604",
                "buy_side",
                101.0,
                liquidity_type="previous_day_high",
                swept_status="swept_rejected",
            ),
            _pool("ASIAN_LOW_20260605", "sell_side", 94.0, liquidity_type="asian_low"),
        ],
        "dealing_range": {"range_low": 93.5, "range_high": 101.5},
        "latest_mss_event": {"direction": "bearish", "confirmed": True},
        "displacement": {"direction": "bearish", "confirmed": True, "strength_score": 8.0},
        "entry_model": _entry("bearish"),
        "htf_bias": {"bias_direction": "bearish"},
        "news_status": {"restricted": False},
    }
    context.update(overrides)
    return context


def test_valid_bullish_liquidity_to_liquidity_signal():
    signal = generate_liquidity_to_liquidity_signal(_bullish_context(), _base_config())

    assert signal["signal_status"] == "valid"
    assert signal["trade_allowed"] is True
    assert signal["entry_allowed_from_liquidity_path_alone"] is False
    assert signal["path_bias"] == "bullish"
    assert signal["start_liquidity"]["side"] == "sell_side"
    assert signal["target_liquidity"]["side"] == "buy_side"
    assert signal["target_liquidity"]["target_valid"] is True
    assert signal["target_liquidity"]["rr_to_target"] >= 2.0
    assert signal["score"]["total_score"] >= 8.0


def test_valid_bearish_liquidity_to_liquidity_signal():
    signal = generate_liquidity_to_liquidity_signal(_bearish_context(), _base_config())

    assert signal["signal_status"] == "valid"
    assert signal["trade_allowed"] is True
    assert signal["path_bias"] == "bearish"
    assert signal["start_liquidity"]["side"] == "buy_side"
    assert signal["target_liquidity"]["side"] == "sell_side"
    assert signal["target_liquidity"]["target_valid"] is True
    assert signal["score"]["total_score"] >= 8.0


def test_sweep_without_structure_shift_is_context_only():
    context = _bullish_context(
        latest_mss_event={"direction": "bullish", "confirmed": False},
        displacement={"direction": "bullish", "confirmed": False},
        htf_bias={"bias_direction": "neutral"},
    )

    signal = generate_liquidity_to_liquidity_signal(context, _base_config())

    assert signal["signal_status"] in {"context_only", "rejected"}
    assert signal["trade_allowed"] is False
    assert "no_structure_shift_after_starting_liquidity" in signal["rejection_reasons"]


def test_target_already_swept_is_rejected():
    context = _bearish_context(
        liquidity_pools=[
            _pool(
                "PDH_20260604",
                "buy_side",
                101.0,
                liquidity_type="previous_day_high",
                swept_status="swept_rejected",
            ),
            _pool(
                "ASIAN_LOW_20260605",
                "sell_side",
                94.0,
                liquidity_type="asian_low",
                swept_status="fully_swept",
            ),
        ]
    )

    signal = generate_liquidity_to_liquidity_signal(context, _base_config())

    assert signal["signal_status"] == "rejected"
    assert signal["trade_allowed"] is False
    assert "target_liquidity_already_swept" in signal["rejection_reasons"]


def test_close_target_large_spread_and_poor_rr_are_rejected():
    context = _bullish_context(
        spread_points=0.4,
        entry_model=_entry("bullish", entry_price=100.0, stop_loss=99.2),
        liquidity_pools=[
            _pool(
                "ASIAN_LOW_20260605",
                "sell_side",
                99.0,
                liquidity_type="asian_low",
                swept_status="swept_reclaimed",
            ),
            _pool("NEAR_EQUAL_HIGH", "buy_side", 100.6, liquidity_type="equal_highs"),
        ],
    )

    signal = generate_liquidity_to_liquidity_signal(context, _base_config())

    assert signal["signal_status"] == "rejected"
    assert signal["trade_allowed"] is False
    assert "target_distance_too_small" in signal["rejection_reasons"]
    assert "spread_too_large_relative_to_target" in signal["rejection_reasons"]
    assert "rr_below_minimum" in signal["rejection_reasons"]


def test_target_blocked_by_strong_htf_poi_is_rejected():
    context = _bullish_context(
        htf_pois=[
            {
                "poi_id": "H1_BEARISH_OB_007",
                "poi_type": "bearish_order_block",
                "direction": "bearish",
                "zone_low": 103.0,
                "zone_high": 104.0,
                "quality_score": 9.0,
                "active_status": True,
            }
        ]
    )

    signal = generate_liquidity_to_liquidity_signal(context, _base_config())

    assert signal["signal_status"] == "rejected"
    assert signal["trade_allowed"] is False
    assert "target_blocked_by_htf_poi" in signal["rejection_reasons"]
    assert signal["blockers"][0]["poi_id"] == "H1_BEARISH_OB_007"


def test_liquidity_pool_detection_ignores_forming_candle():
    candles = [
        _c(0, 100.0, 100.4, 99.7, 100.1),
        _c(1, 100.1, 101.0, 99.9, 100.8),
        _c(2, 100.8, 100.9, 99.6, 100.0),
        _c(3, 100.0, 100.3, 99.5, 99.8),
        _c(4, 99.8, 100.2, 99.0, 99.6),
        _c(5, 99.6, 100.0, 99.4, 99.7),
        _c(6, 99.7, 100.1, 99.5, 99.9),
        _c(7, 99.9, 100.0, 99.6, 99.8),
        _c(8, 99.8, 130.0, 80.0, 120.0, closed=False),
    ]

    pools = detect_liquidity_pools(candles, _base_config())

    assert pools
    assert all(pool["created_index"] != 8 for pool in pools)
    assert all(pool["is_closed_candle_pool"] is True for pool in pools)


def test_required_functions_are_usable_independently():
    context = _bullish_context()
    config = _base_config()
    pools = classify_internal_external_liquidity(context["liquidity_pools"], context["dealing_range"], config)
    start = pools[0]
    draw = determine_draw_on_liquidity(start, context["latest_mss_event"], context["htf_bias"], config)
    ranked = rank_liquidity_targets(
        pools,
        "bullish",
        context["current_price"],
        context["entry_model"]["entry_price"],
        context["entry_model"]["stop_loss"],
        [],
        config,
    )
    path = detect_liquidity_to_liquidity_path(context, config)
    score = score_liquidity_to_liquidity_setup(path, context, config)

    assert draw["draw_side"] == "buy_side"
    assert ranked[0]["target_valid"] is True
    assert path["path_detected"] is True
    assert score["trade_allowed"] is True
