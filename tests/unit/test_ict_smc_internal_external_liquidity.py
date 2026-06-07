from src.analytics.ict_smc.internal_external_liquidity import classify_liquidity_internal_external


def _range(low=2340.0, high=2380.0, direction="bullish", valid=True):
    return {
        "range_valid": valid,
        "range_low": low,
        "range_high": high,
        "equilibrium": (low + high) / 2.0,
        "discount_zone": {"zone_low": low, "zone_high": (low + high) / 2.0},
        "premium_zone": {"zone_low": (low + high) / 2.0, "zone_high": high},
        "range_type": f"{direction}_MSS_dealing_range",
        "range_direction": direction,
        "timeframe": "15m",
        "quality_score": 8.2,
    }


def _pool(liquidity_id, direction, liquidity_type, mid, touched=3, quality=8.0):
    return {
        "liquidity_id": liquidity_id,
        "direction": direction,
        "liquidity_type": liquidity_type,
        "price_zone": {"zone_low": mid - 0.1, "zone_mid": mid, "zone_high": mid + 0.1},
        "swept_status": "unswept",
        "touched_count": touched,
        "quality_score": quality,
        "timeframe": "15m",
        "source": "unit_test",
    }


def _classified(result, liquidity_id):
    return next(item for item in result["classified_liquidity"] if item["liquidity_id"] == liquidity_id)


def test_internal_buy_side_liquidity_is_partial_target_inside_active_range() -> None:
    result = classify_liquidity_internal_external(
        [_pool("LIQ_INTERNAL_BUY", "buy_side", "equal_highs", 2368.0)],
        _range(),
        current_price=2352.0,
        atr=5.0,
        trade_direction="long",
        symbol="XAUUSD",
        timeframe="15m",
    )

    item = _classified(result, "LIQ_INTERNAL_BUY")

    assert item["internal_or_external"] == "internal"
    assert item["liquidity_role"] == "internal_buy_side_liquidity"
    assert item["target_role"] == "partial_target_or_internal_sweep_area"
    assert 6.0 <= item["target_priority_score"] <= 8.0
    assert item["entry_allowed_from_liquidity_classification_alone"] is False
    assert result["internal_liquidity"][0]["liquidity_id"] == "LIQ_INTERNAL_BUY"


def test_external_buy_side_liquidity_above_range_is_final_target() -> None:
    result = classify_liquidity_internal_external(
        [_pool("LIQ_EXTERNAL_BUY", "buy_side", "swing_high_liquidity", 2383.0)],
        _range(),
        current_price=2352.0,
        atr=5.0,
        trade_direction="long",
        symbol="XAUUSD",
        timeframe="15m",
    )

    item = _classified(result, "LIQ_EXTERNAL_BUY")

    assert item["internal_or_external"] == "external"
    assert item["external_side"] == "above_range"
    assert item["liquidity_role"] == "external_buy_side_liquidity"
    assert item["target_role"] == "final_target_or_major_buy_side_sweep_area"
    assert 8.0 <= item["target_priority_score"] <= 10.0
    assert result["external_liquidity"]["buy_side"][0]["liquidity_id"] == "LIQ_EXTERNAL_BUY"


def test_internal_sell_side_liquidity_is_short_partial_target_inside_active_range() -> None:
    result = classify_liquidity_internal_external(
        [_pool("LIQ_INTERNAL_SELL", "sell_side", "equal_lows", 2352.0)],
        _range(direction="bearish"),
        current_price=2372.0,
        atr=5.0,
        trade_direction="short",
        symbol="XAUUSD",
        timeframe="15m",
    )

    item = _classified(result, "LIQ_INTERNAL_SELL")

    assert item["internal_or_external"] == "internal"
    assert item["liquidity_role"] == "internal_sell_side_liquidity"
    assert item["target_role"] == "internal_sweep_area_or_short_partial_target"
    assert 5.0 <= item["target_priority_score"] <= 7.0


def test_external_sell_side_liquidity_below_range_is_final_target() -> None:
    result = classify_liquidity_internal_external(
        [_pool("LIQ_EXTERNAL_SELL", "sell_side", "swing_low_liquidity", 2337.0)],
        _range(direction="bearish"),
        current_price=2372.0,
        atr=5.0,
        trade_direction="short",
        symbol="XAUUSD",
        timeframe="15m",
    )

    item = _classified(result, "LIQ_EXTERNAL_SELL")

    assert item["internal_or_external"] == "external"
    assert item["external_side"] == "below_range"
    assert item["liquidity_role"] == "external_sell_side_liquidity"
    assert item["target_role"] == "final_target_or_major_sell_side_sweep_area"
    assert 8.0 <= item["target_priority_score"] <= 10.0
    assert result["external_liquidity"]["sell_side"][0]["liquidity_id"] == "LIQ_EXTERNAL_SELL"


def test_liquidity_is_not_classified_without_valid_dealing_range() -> None:
    result = classify_liquidity_internal_external(
        [_pool("LIQ_NO_RANGE", "buy_side", "equal_highs", 2368.0)],
        _range(valid=False),
        symbol="XAUUSD",
        timeframe="15m",
    )

    assert result["range_valid"] is False
    assert result["classified_liquidity"] == []
    assert "valid_dealing_range_required_before_liquidity_classification" in result["warnings"]
    assert result["entry_allowed_from_liquidity_classification_alone"] is False


def test_classification_recalculates_when_dealing_range_changes() -> None:
    pool = _pool("LIQ_RECLASSIFIED", "buy_side", "swing_high_liquidity", 2382.0)
    old_result = classify_liquidity_internal_external([pool], _range(2340.0, 2380.0), atr=5.0)
    new_result = classify_liquidity_internal_external(
        [pool],
        _range(2350.0, 2400.0),
        previous_dealing_range=_range(2340.0, 2380.0),
        atr=5.0,
    )

    old_item = _classified(old_result, "LIQ_RECLASSIFIED")
    new_item = _classified(new_result, "LIQ_RECLASSIFIED")

    assert old_item["internal_or_external"] == "external"
    assert new_item["internal_or_external"] == "internal"
    assert new_item["liquidity_role"] == "internal_buy_side_liquidity"
    assert "liquidity_classification_recalculated_after_range_update" in new_result["warnings"]
    assert "liquidity_classification_recalculated_after_range_update" in new_item["warnings"]
