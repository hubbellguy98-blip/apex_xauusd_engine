from src.analytics.ict_smc.liquidity_to_liquidity import map_liquidity_to_liquidity_path


def _context(**overrides):
    base = {
        "symbol": "XAUUSD",
        "timeframe": "5m",
        "current_price": 2360.7,
        "entry_price": 2360.7,
        "stop_loss": 2353.3,
        "current_index": 100,
        "latest_sweep_event": {"swept_liquidity_id": "ASIAN_LOW", "confirmed": True},
        "latest_mss_event": {"direction": "bullish", "confirmed": True},
        "latest_bos_event": {"direction": None, "confirmed": False},
        "displacement": {"direction": "bullish", "confirmed": True},
        "htf_bias": "bullish",
        "expected_draw": "buy_side",
    }
    base.update(overrides)
    return base


def _pool(
    liquidity_id,
    liquidity_type,
    direction,
    price,
    *,
    internal_or_external="external",
    swept_status="unswept",
    quality=8.0,
    priority=8.0,
    timeframe="5m",
    last_touched=90,
):
    return {
        "liquidity_id": liquidity_id,
        "liquidity_type": liquidity_type,
        "direction": direction,
        "zone_low": price - 0.4,
        "zone_mid": price,
        "zone_high": price + 0.4,
        "price": price,
        "internal_or_external": internal_or_external,
        "swept_status": swept_status,
        "quality_score": quality,
        "target_priority_score": priority,
        "timeframe": timeframe,
        "session_source": "session",
        "last_touched_index": last_touched,
    }


def _poi(poi_id, direction, low, high, quality=8.0, timeframe="5m", strength=""):
    return {
        "poi_id": poi_id,
        "poi_type": "order_block",
        "direction": direction,
        "zone_low": low,
        "zone_high": high,
        "timeframe": timeframe,
        "fresh_status": "fresh",
        "quality_score": quality,
        "invalidated_status": False,
        "blocking_strength": strength,
    }


def test_valid_bullish_liquidity_to_liquidity_path_targets_buy_side() -> None:
    pools = [
        _pool("ASIAN_LOW", "asian_low", "sell_side", 2356.2, swept_status="swept_reclaimed"),
        _pool("ASIAN_HIGH", "asian_high", "buy_side", 2368.4, quality=8.8, priority=8.6),
        _pool("PDH", "previous_day_high", "buy_side", 2381.4, quality=9.0, priority=9.0),
    ]

    result = map_liquidity_to_liquidity_path(_context(), pools, [])

    assert result["path_valid"] is True
    assert result["path_bias"] == "bullish"
    assert result["start_liquidity"]["liquidity_id"] == "ASIAN_LOW"
    assert result["target_liquidity"]["direction"] == "buy_side"
    assert result["blockers"] == []
    assert 7.0 <= result["target_score"] <= 10.0
    assert result["entry_allowed_from_liquidity_path_alone"] is False


def test_valid_bearish_liquidity_to_liquidity_path_targets_sell_side() -> None:
    pools = [
        _pool("LONDON_HIGH", "london_high", "buy_side", 2372.4, swept_status="swept_rejected"),
        _pool("LONDON_LOW", "london_low", "sell_side", 2356.2, quality=8.8, priority=8.7),
        _pool("PDL", "previous_day_low", "sell_side", 2348.8, quality=9.0, priority=9.0),
    ]
    context = _context(
        current_price=2372.4,
        entry_price=2372.4,
        stop_loss=2385.7,
        latest_sweep_event={"swept_liquidity_id": "LONDON_HIGH", "confirmed": True},
        latest_mss_event={"direction": "bearish", "confirmed": True},
        displacement={"direction": "bearish", "confirmed": True},
        htf_bias="bearish",
        expected_draw="sell_side",
    )

    result = map_liquidity_to_liquidity_path(context, pools, [])

    assert result["path_valid"] is True
    assert result["path_bias"] == "bearish"
    assert result["start_liquidity"]["liquidity_id"] == "LONDON_HIGH"
    assert result["target_liquidity"]["direction"] == "sell_side"
    assert result["blockers"] == []
    assert 7.0 <= result["target_score"] <= 10.0


def test_strong_opposing_poi_blocks_final_target_and_caps_score() -> None:
    pools = [
        _pool("PDL", "previous_day_low", "sell_side", 2348.8, swept_status="swept_reclaimed"),
        _pool("PDH", "previous_day_high", "buy_side", 2381.4, quality=9.2, priority=9.4),
    ]
    context = _context(
        current_price=2360.7,
        entry_price=2360.7,
        latest_sweep_event={"swept_liquidity_id": "PDL", "confirmed": True},
    )
    pois = [_poi("HTF_BEARISH_OB", "bearish", 2368.0, 2375.0, quality=9.0, timeframe="4h")]

    result = map_liquidity_to_liquidity_path(context, pools, pois)

    assert result["path_valid"] is False
    assert result["path_bias"] == "bullish"
    assert result["target_liquidity"]["liquidity_id"] == "PDH"
    assert result["blockers"][0]["poi_id"] == "HTF_BEARISH_OB"
    assert result["blockers"][0]["blocker_strength"] == "strong"
    assert result["target_score"] <= 4.0
    assert result["recommendation"]["use_target"] is False


def test_internal_target_only_is_mapped_as_partial_target() -> None:
    pools = [
        _pool("RANGE_LOW", "range_low", "sell_side", 2350.0, swept_status="swept_reclaimed"),
        _pool(
            "INTERNAL_EQH",
            "internal_equal_highs",
            "buy_side",
            2364.2,
            internal_or_external="internal",
            quality=7.0,
            priority=6.8,
        ),
    ]
    context = _context(
        current_price=2357.0,
        entry_price=2357.0,
        stop_loss=2352.0,
        latest_sweep_event={"swept_liquidity_id": "RANGE_LOW", "confirmed": True},
        htf_bias="neutral",
        expected_draw="",
    )

    result = map_liquidity_to_liquidity_path(context, pools, [])

    assert result["path_bias"] == "bullish"
    assert result["target_liquidity"]["liquidity_id"] == "INTERNAL_EQH"
    assert result["target_ladder"][0]["role"] == "partial_target"
    assert 5.0 <= result["target_score"] <= 7.0
    assert "internal_target_only" in result["warnings"]


def test_no_recent_start_liquidity_returns_unclear_low_confidence_path() -> None:
    pools = [
        _pool("EQH", "equal_highs", "buy_side", 2370.0, swept_status="unswept"),
        _pool("EQL", "equal_lows", "sell_side", 2350.0, swept_status="unswept"),
    ]
    context = _context(
        latest_sweep_event={},
        latest_mss_event={},
        displacement={},
        htf_bias="neutral",
    )
    pois = [
        _poi("BULLISH_FVG", "bullish", 2352.0, 2354.0, quality=7.5),
        _poi("BEARISH_OB", "bearish", 2365.0, 2368.0, quality=8.0),
    ]

    result = map_liquidity_to_liquidity_path(context, pools, pois)

    assert result["path_valid"] is False
    assert result["path_bias"] == "unclear"
    assert result["start_liquidity"] is None
    assert result["target_liquidity"] is None
    assert result["target_score"] == 0.0
    assert len(result["blockers"]) == 2
    assert "no_recent_start_liquidity_confirmed" in result["warnings"]
