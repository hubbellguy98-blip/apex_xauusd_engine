from src.analytics.ict_smc.target_logic import TargetStatus, select_smc_targets


def _pool(
    liquidity_id,
    liquidity_type,
    direction,
    price,
    *,
    role="internal",
    quality=7.0,
    priority=7.0,
    timeframe="15m",
    swept_status="unswept",
    invalidated=False,
):
    return {
        "liquidity_id": liquidity_id,
        "liquidity_type": liquidity_type,
        "direction": direction,
        "zone_low": price,
        "zone_mid": price,
        "zone_high": price,
        "internal_or_external": role,
        "swept_status": swept_status,
        "invalidated_status": invalidated,
        "quality_score": quality,
        "target_priority_score": priority,
        "timeframe": timeframe,
    }


def test_valid_bullish_target_ladder() -> None:
    result = select_smc_targets(
        entry=2360.70,
        stop=2353.30,
        liquidity_pools=[
            _pool("INTERNAL_EQH_001", "internal_equal_highs", "buy_side", 2364.50),
            _pool("ASIAN_HIGH_001", "asian_high", "buy_side", 2368.40, role="external"),
            _pool(
                "PDH_001",
                "previous_day_high",
                "buy_side",
                2376.80,
                role="external",
                quality=9.0,
                priority=9.0,
                timeframe="daily",
            ),
        ],
        poi_zones=[],
        min_rr=1.5,
    )

    assert result["direction"] == "bullish"
    assert result["target_1"]["liquidity_id"] == "INTERNAL_EQH_001"
    assert result["target_2"]["liquidity_id"] in {"ASIAN_HIGH_001", "PDH_001"}
    assert result["final_target"]["liquidity_id"] == "PDH_001"
    assert result["rr_values"]["rr_to_final_target"] >= 1.5
    assert result["valid_trade_target_exists"] is True


def test_valid_bearish_target_ladder() -> None:
    result = select_smc_targets(
        entry=2372.40,
        stop=2385.70,
        liquidity_pools=[
            _pool("INTERNAL_EQL_002", "internal_equal_lows", "sell_side", 2365.20),
            _pool("LONDON_LOW_001", "london_low", "sell_side", 2356.20, role="external"),
            _pool(
                "PDL_001",
                "previous_day_low",
                "sell_side",
                2348.00,
                role="external",
                quality=9.0,
                priority=9.0,
                timeframe="daily",
            ),
        ],
        poi_zones=[],
        min_rr=1.5,
    )

    assert result["direction"] == "bearish"
    assert result["target_1"]["liquidity_id"] == "INTERNAL_EQL_002"
    assert result["final_target"]["liquidity_id"] == "PDL_001"
    assert result["target_quality_score"] >= 6.0
    assert result["valid_trade_target_exists"] is True


def test_final_target_blocked_by_strong_htf_poi() -> None:
    result = select_smc_targets(
        entry=2360.70,
        stop=2353.30,
        liquidity_pools=[
            _pool("ASIAN_HIGH_001", "asian_high", "buy_side", 2368.40, role="external"),
            _pool("INTERNAL_EQH_003", "internal_equal_highs", "buy_side", 2370.20),
            _pool(
                "PDH_BLOCKED",
                "previous_day_high",
                "buy_side",
                2378.80,
                role="external",
                quality=9.0,
                priority=9.0,
                timeframe="daily",
            ),
        ],
        poi_zones=[
            {
                "poi_id": "HTF_BEARISH_OB_4H_009",
                "poi_type": "bearish_order_block",
                "direction": "bearish",
                "zone_low": 2372.0,
                "zone_high": 2376.5,
                "quality_score": 8.8,
                "timeframe": "4h",
                "invalidated_status": False,
            }
        ],
        min_rr=1.5,
    )

    assert result["decision"]["status"] == TargetStatus.FINAL_TARGET_BLOCKED.value
    assert result["final_target"]["liquidity_id"] == "PDH_BLOCKED"
    assert result["final_target"]["status"] == "blocked"
    assert result["blocked_targets"][0]["blocked_target_id"] == "PDH_BLOCKED"
    assert result["valid_trade_target_exists"] is False


def test_targets_exist_but_no_target_meets_min_rr() -> None:
    result = select_smc_targets(
        entry=2372.40,
        stop=2385.70,
        liquidity_pools=[
            _pool("INTERNAL_EQL_001", "internal_equal_lows", "sell_side", 2367.00),
            _pool("LONDON_LOW_001", "london_low", "sell_side", 2358.00, role="external"),
        ],
        poi_zones=[],
        min_rr=1.5,
    )

    assert result["target_1"]["liquidity_id"] == "INTERNAL_EQL_001"
    assert result["target_2"]["liquidity_id"] == "LONDON_LOW_001"
    assert result["final_target"] is None
    assert result["valid_trade_target_exists"] is False
    assert result["decision"]["status"] == TargetStatus.NO_TARGET_MEETS_MIN_RR.value


def test_already_swept_target_is_rejected() -> None:
    result = select_smc_targets(
        entry=2360.70,
        stop=2353.30,
        liquidity_pools=[
            _pool(
                "EQH_SWEPT",
                "equal_highs",
                "buy_side",
                2369.0,
                swept_status="fully_swept",
            )
        ],
        poi_zones=[],
        min_rr=1.5,
    )

    assert result["valid_trade_target_exists"] is False
    assert result["final_target"] is None
    assert result["rejected_targets"][0]["reason"] == "target_already_swept"
