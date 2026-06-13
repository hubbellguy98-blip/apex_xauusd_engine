from src.analytics.ict_smc.false_positive_filter import (
    FalsePositiveStatus,
    filter_false_smc_signals,
)


def _context(**overrides):
    base = {
        "symbol": "XAUUSD",
        "timeframe": "5m",
        "current_time": "2026-06-04T10:35:00+00:00",
        "atr": 2.0,
        "news_status": {"restricted": False},
        "spread_status": {
            "current_spread": 0.20,
            "max_allowed_spread": 0.80,
            "estimated_slippage": 0.05,
            "max_allowed_slippage": 0.50,
        },
        "premium_discount": {"current_price_location": "discount"},
        "market_condition": {"state": "trending", "overlap_ratio": 0.25},
        "recent_signals": [],
        "poi_zones": [],
        "filter_config": {
            "mode": "conservative",
            "min_rr": 1.5,
            "minimum_tradable_score": 6.0,
            "minimum_fvg_size": 0.05,
            "minimum_ob_quality": 6.0,
            "max_allowed_mitigations": 2,
            "max_recent_signals": 8,
        },
    }
    base.update(overrides)
    return base


def _base_setup(**overrides):
    setup = {
        "setup_id": "SETUP_VALID_001",
        "setup_type": "sell_side_sweep_bullish_mss_fvg",
        "symbol": "XAUUSD",
        "timeframe": "5m",
        "direction": "bullish",
        "uses_closed_candles": True,
        "requires_liquidity_sweep": True,
        "requires_structure_confirmation": True,
        "is_reversal_model": True,
        "entry": 2360.70,
        "stop": 2353.30,
        "target": 2376.80,
        "rr": 2.1,
        "setup_score": 8.4,
        "liquidity_sweep": {
            "exists": True,
            "reclaim_status": "reclaimed",
            "swept_liquidity_status": "fresh",
            "connected_to_setup": True,
            "depth": 0.30,
            "quality_score": 8.0,
            "sweep_id": "SWEEP_ASIAN_LOW_001",
        },
        "mss_bos": {
            "mss_confirmed": True,
            "confirmation_type": "close",
        },
        "displacement": {"strength_score": 8.0},
        "fvg": {
            "active_status": True,
            "filled_percent": 0,
            "created_by_displacement": True,
            "created_after_mss_bos": True,
            "size": 0.60,
        },
        "target_liquidity": {
            "liquidity_id": "PDH_001",
            "swept_status": "fresh",
        },
        "premium_discount": {"entry_location": "discount"},
    }
    setup.update(overrides)
    return setup


def _reasons(result):
    return result["rejected_setups"][0]["rejection_reasons"]


def test_valid_sweep_mss_fvg_setup_passes_filter() -> None:
    result = filter_false_smc_signals([_base_setup()], _context())

    assert result["filter_summary"]["valid_count"] == 1
    assert result["valid_setups"][0]["status"] == FalsePositiveStatus.VALID.value
    assert "liquidity_sweep_confirmed" in result["valid_setups"][0]["passed_filters"]
    assert "rr_valid" in result["valid_setups"][0]["passed_filters"]
    assert result["rejected_setups"] == []


def test_random_fvg_without_sweep_structure_or_target_is_rejected() -> None:
    setup = _base_setup(
        setup_id="SETUP_FVG_003",
        setup_type="bullish_fvg",
        setup_score=3.1,
        requires_liquidity_sweep=False,
        liquidity_sweep={},
        mss_bos={"mss_confirmed": False},
        fvg={
            "active_status": True,
            "filled_percent": 0,
            "created_by_displacement": False,
            "created_after_mss_bos": False,
            "size": 0.04,
        },
        target_liquidity={},
        has_target_liquidity=False,
        premium_discount={"entry_location": "equilibrium"},
    )
    result = filter_false_smc_signals([setup], _context(premium_discount={"current_price_location": "equilibrium"}))

    reasons = _reasons(result)
    assert result["filter_summary"]["rejected_count"] == 1
    assert "random_fvg_no_displacement" in reasons
    assert "random_fvg_no_structure_confirmation" in reasons
    assert "price_in_middle_of_range" in reasons
    assert "fvg_no_target_liquidity" in reasons


def test_valid_sweep_without_mss_becomes_context_only_in_balanced_mode() -> None:
    setup = _base_setup(
        setup_id="SETUP_SWEEP_002",
        setup_type="sell_side_sweep_without_mss",
        fvg={},
        mss_bos={"mss_confirmed": False, "confirmation_type": "close"},
    )
    context = _context(filter_config={**_context()["filter_config"], "mode": "balanced"})

    result = filter_false_smc_signals([setup], context)

    assert result["filter_summary"]["context_only_count"] == 1
    assert result["context_only_setups"][0]["status"] == FalsePositiveStatus.CONTEXT_ONLY.value
    assert "no_mss_for_reversal" in result["context_only_setups"][0]["rejection_reasons"]
    assert result["rejected_setups"] == []


def test_weak_order_block_is_rejected() -> None:
    setup = _base_setup(
        setup_id="SETUP_OB_004",
        setup_type="bullish_order_block",
        fvg={},
        order_block={
            "created_by_displacement": False,
            "validated_by_mss_bos": False,
            "mitigated_count": 3,
            "quality_score": 4.0,
            "active_status": True,
        },
    )

    result = filter_false_smc_signals([setup], _context())

    reasons = _reasons(result)
    assert "weak_ob_no_displacement" in reasons
    assert "weak_ob_no_structure_break" in reasons
    assert "ob_over_mitigated" in reasons
    assert result["rejected_setups"][0]["rejection_category"] == "poi_failure"


def test_good_setup_blocked_by_strong_htf_poi_before_target() -> None:
    setup = _base_setup(
        setup_id="SETUP_HTF_BLOCK_005",
        entry=100.0,
        stop=95.0,
        target=120.0,
        rr=4.0,
        closer_target_rr=1.2,
    )
    context = _context(
        poi_zones=[
            {
                "poi_id": "D1_BEARISH_OB_001",
                "direction": "bearish",
                "timeframe": "daily",
                "zone_low": 110.0,
                "zone_high": 112.0,
                "quality_score": 9.0,
                "invalidated": False,
            }
        ]
    )

    result = filter_false_smc_signals([setup], context)

    reasons = _reasons(result)
    assert "htf_poi_blocks_target" in reasons
    assert "no_target_meets_min_rr" in reasons


def test_news_spike_false_signal_collects_news_spread_and_fvg_reasons() -> None:
    setup = _base_setup(
        setup_id="SETUP_NEWS_005",
        setup_type="news_spike_fvg",
        news_related=True,
        fvg={
            "active_status": True,
            "filled_percent": 0,
            "created_by_displacement": True,
            "created_after_mss_bos": True,
            "size": 9.0,
        },
        news_status={"restricted": True, "first_news_spike": True},
    )
    context = _context(
        atr=2.0,
        news_status={"restricted": True, "first_news_spike": True},
        spread_status={
            "current_spread": 1.20,
            "max_allowed_spread": 0.80,
            "estimated_slippage": 0.10,
            "max_allowed_slippage": 0.50,
        },
    )

    result = filter_false_smc_signals([setup], context)

    reasons = _reasons(result)
    assert "news_restricted" in reasons
    assert "first_news_spike_signal" in reasons
    assert "spread_too_high" in reasons
    assert "fvg_too_large_news_spike" in reasons
