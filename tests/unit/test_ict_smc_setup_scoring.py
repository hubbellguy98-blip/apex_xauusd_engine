from src.analytics.ict_smc.setup_scoring import (
    SetupGrade,
    SetupScoreStatus,
    score_smc_setup,
)


def _base_setup(**overrides):
    setup = {
        "setup_id": "LONDON_RAID_BULL_001",
        "symbol": "XAUUSD",
        "timeframe": "5m",
        "setup_type": "liquidity_sweep_reversal",
        "direction": "bullish",
        "confirmed": True,
        "htf_bias": "bullish",
        "htf_draw_on_liquidity": "buy_side",
        "premium_discount": {"location": "discount"},
        "liquidity_sweep": {
            "exists": True,
            "swept_side": "sell_side",
            "swept_level_type": "asian_low",
            "reclaim_status": "reclaimed",
            "quality_score": 9.0,
        },
        "structure_confirmation": {
            "confirmed": True,
            "break_type": "mss",
            "direction": "bullish",
            "close_confirmed": True,
        },
        "displacement": {
            "exists": True,
            "direction": "bullish",
            "body_to_range_ratio": 0.70,
            "range_to_atr_ratio": 1.55,
            "close_position": 0.86,
            "fvg_created": True,
        },
        "entry_zone": {
            "zone_id": "FVG_OB_BULL_001",
            "zone_type": "bullish_fvg_ob_overlap",
            "direction": "bullish",
            "quality_score": 8.7,
            "fresh_status": "fresh",
            "created_after_mss": True,
            "created_by_displacement": True,
        },
        "poi": {"fresh_status": "fresh", "touch_count": 0},
        "session_context": {
            "session_name": "london",
            "status": "inside_killzone",
            "ideal_killzone": True,
        },
        "news_filter": {"restricted": False},
        "spread_status": {"spread_status": "normal", "spread_safe": True},
        "risk_plan": {"rr": 2.4, "stop_valid": True},
        "target_liquidity": {
            "valid_trade_target_exists": True,
            "target_quality_score": 8.5,
            "direction": "buy_side",
            "swept_status": "unswept",
            "internal_or_external": "external",
        },
        "volume_confirmation": {"volume_score": 8.0},
        "scoring_config": {
            "mode": "conservative",
            "trade_threshold": 7.5,
            "min_rr": 1.5,
            "use_volume": True,
        },
    }
    setup.update(overrides)
    return setup


def test_high_quality_bullish_setup_is_trade_allowed() -> None:
    result = score_smc_setup(_base_setup())

    assert result["trade_allowed"] is True
    assert result["status"] == SetupScoreStatus.TRADE_ALLOWED.value
    assert result["grade"] in {SetupGrade.A.value, SetupGrade.A_PLUS.value}
    assert result["total_score"] >= 8.0
    assert result["hard_filter_failures"] == []
    assert result["component_scores"]["mss_bos_confirmation"]["raw_score"] == 10.0


def test_bearish_sweep_without_mss_is_blocked_in_conservative_mode() -> None:
    result = score_smc_setup(
        _base_setup(
            setup_id="NY_RAID_BEAR_NO_MSS",
            direction="bearish",
            htf_bias="bearish",
            htf_draw_on_liquidity="sell_side",
            premium_discount={"location": "premium"},
            liquidity_sweep={
                "exists": True,
                "swept_side": "buy_side",
                "reclaim_status": "reclaimed",
                "quality_score": 8.0,
            },
            structure_confirmation={
                "confirmed": False,
                "break_type": "none",
                "direction": "bearish",
                "close_confirmed": False,
            },
            displacement={"exists": True, "direction": "bearish", "body_to_range_ratio": 0.55},
            entry_zone={"zone_type": "bearish_fvg", "direction": "bearish", "quality_score": 7.2},
            target_liquidity={
                "valid_trade_target_exists": True,
                "target_quality_score": 7.5,
                "direction": "sell_side",
            },
        )
    )

    assert result["trade_allowed"] is False
    assert result["component_scores"]["mss_bos_confirmation"]["raw_score"] == 0.0
    assert result["total_score"] <= 5.5
    assert "no_mss_or_bos_confirmation" in result["hard_filter_failures"]


def test_news_blackout_overrides_otherwise_strong_setup() -> None:
    result = score_smc_setup(
        _base_setup(
            setup_id="NY_RAID_NEWS_BLOCKED",
            news_filter={"restricted": True, "event": "CPI"},
            spread_status={"spread_status": "wide", "spread_safe": False},
        )
    )

    assert result["trade_allowed"] is False
    assert result["total_score"] <= 3.0
    assert result["component_scores"]["news_filter"]["raw_score"] == 0.0
    assert "news_restricted" in result["hard_filter_failures"]


def test_target_blocked_by_htf_poi_blocks_trade() -> None:
    result = score_smc_setup(
        _base_setup(
            setup_id="TARGET_BLOCKED_BY_4H_OB",
            target_liquidity={
                "valid_trade_target_exists": True,
                "target_quality_score": 8.5,
                "direction": "buy_side",
                "blocked_targets": [{"poi_id": "HTF_BEARISH_OB"}],
                "alternate_target_exists": False,
            },
        )
    )

    assert result["trade_allowed"] is False
    assert result["component_scores"]["target_clarity"]["raw_score"] == 2.0
    assert "target_blocked_by_htf_poi" in result["hard_filter_failures"]


def test_poor_reward_to_risk_caps_and_blocks_clean_setup() -> None:
    result = score_smc_setup(
        _base_setup(
            setup_id="CLEAN_SETUP_POOR_RR",
            risk_plan={"rr": 1.1, "stop_valid": True},
            scoring_config={"mode": "conservative", "trade_threshold": 7.5, "min_rr": 1.5},
        )
    )

    assert result["trade_allowed"] is False
    assert result["total_score"] <= 5.0
    assert result["component_scores"]["risk_reward"]["raw_score"] == 4.0
    assert "rr_below_minimum" in result["hard_filter_failures"]
