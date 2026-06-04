from datetime import datetime, timedelta, timezone

from src.analytics.ict_smc.liquidity import (
    LiquidityDirection,
    LiquidityQualityGrade,
    LiquidityStatus,
    LiquidityType,
)
from src.analytics.ict_smc.liquidity_sweep import (
    LiquiditySweepClassification,
    LiquiditySweepConfidenceGrade,
    LiquiditySweepReclaimType,
    LiquiditySweepSetupStatus,
    LiquiditySweepType,
    detect_liquidity_sweep,
)


def _row(
    index: int,
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    session_name: str | None = None,
) -> dict:
    return {
        "index": index,
        "timestamp": datetime(2026, 6, 1, tzinfo=timezone.utc) + timedelta(minutes=index),
        "symbol": "XAUUSD",
        "timeframe": "1m",
        "open": open_p,
        "high": high_p,
        "low": low_p,
        "close": close_p,
        "volume": 100 + index,
        "is_closed": True,
        "session_name": session_name,
    }


def _pool(
    liquidity_id: str,
    direction: LiquidityDirection,
    liquidity_type: LiquidityType,
    zone_low: float,
    zone_mid: float,
    zone_high: float,
    quality_score: float = 8.5,
    first_created_index: int = 0,
) -> dict:
    return {
        "liquidity_id": liquidity_id,
        "concept_name": "Liquidity",
        "symbol": "XAUUSD",
        "liquidity_type": liquidity_type.value,
        "source": "unit_test",
        "direction": direction.value,
        "timeframe": "1m",
        "price_zone": {
            "zone_low": zone_low,
            "zone_mid": zone_mid,
            "zone_high": zone_high,
        },
        "touched_count": 3,
        "member_indexes": (0, 1, 2),
        "member_prices": (zone_mid, zone_mid, zone_mid),
        "first_created_index": first_created_index,
        "swept_status": LiquidityStatus.UNSWEPT.value,
        "quality_score": quality_score,
        "quality_grade": LiquidityQualityGrade.STRONG.value,
        "role": (),
        "reasons": (),
        "warnings": (),
    }


def _detect(rows: list[dict], pools: list[dict], mss_events=None, choch_events=None) -> list[dict]:
    return detect_liquidity_sweep(
        rows,
        pools,
        mss_events=mss_events,
        choch_events=choch_events,
        sweep_buffer_atr_multiplier=0.0,
        break_buffer_atr_multiplier=0.0,
        min_wick_ratio=0.25,
        strong_wick_ratio=0.40,
        displacement_range_atr=0.75,
    )


def test_valid_bullish_sell_side_sweep_reclaims_zone_and_waits_for_mss() -> None:
    rows = [
        _row(0, 2350.0, 2351.0, 2349.2, 2350.0),
        _row(1, 2350.1, 2351.4, 2345.8, 2350.2, "LONDON_KILLZONE"),
        _row(2, 2350.2, 2356.0, 2350.0, 2355.5),
        _row(3, 2355.5, 2358.0, 2353.0, 2357.0),
    ]
    pools = [
        _pool(
            "LQ_SELL_SIDE_EQUAL_LOWS",
            LiquidityDirection.SELL_SIDE,
            LiquidityType.EQUAL_LOWS,
            2348.0,
            2348.5,
            2349.0,
        )
    ]

    events = _detect(rows, pools, mss_events=[{"direction": "bullish", "index": 3}])

    assert len(events) == 1
    sweep = events[0]
    assert sweep["detected"] is True
    assert sweep["direction"] == "bullish"
    assert sweep["sweep_type"] == LiquiditySweepType.SELL_SIDE_SWEEP.value
    assert sweep["sweep_validation"]["reclaim_type"] == LiquiditySweepReclaimType.FULL_RECLAIM.value
    assert sweep["sweep_validation"]["classified_as"] == LiquiditySweepClassification.SWEEP_NOT_BREAKOUT.value
    assert sweep["post_sweep_confirmation"]["mss_after_sweep"] is True
    assert sweep["post_sweep_confirmation"]["displacement_after_sweep"] is True
    assert sweep["post_sweep_confirmation"]["fvg_after_sweep"] is True
    assert sweep["entry_logic"]["entry_allowed_from_sweep_alone"] is False
    assert sweep["entry_logic"]["entry_allowed_after_confirmation"] is True
    assert sweep["setup_status"] == LiquiditySweepSetupStatus.CONFIRMED_BY_MSS.value
    assert sweep["quality_score"] >= 8.5


def test_valid_bearish_buy_side_sweep_rejects_zone_and_waits_for_mss() -> None:
    rows = [
        _row(0, 2367.0, 2368.0, 2366.0, 2367.5),
        _row(1, 2367.9, 2372.3, 2366.8, 2367.7, "NEWYORK_KILLZONE"),
        _row(2, 2367.7, 2368.0, 2361.0, 2361.5),
        _row(3, 2361.5, 2365.0, 2358.0, 2360.0),
    ]
    pools = [
        _pool(
            "LQ_BUY_SIDE_EQUAL_HIGHS",
            LiquidityDirection.BUY_SIDE,
            LiquidityType.EQUAL_HIGHS,
            2368.2,
            2369.0,
            2369.8,
        )
    ]

    events = _detect(rows, pools, mss_events=[{"direction": "bearish", "index": 3}])

    assert len(events) == 1
    sweep = events[0]
    assert sweep["detected"] is True
    assert sweep["direction"] == "bearish"
    assert sweep["sweep_type"] == LiquiditySweepType.BUY_SIDE_SWEEP.value
    assert sweep["sweep_validation"]["reclaim_type"] == LiquiditySweepReclaimType.FULL_REJECTION.value
    assert sweep["post_sweep_confirmation"]["mss_after_sweep"] is True
    assert sweep["entry_logic"]["entry_allowed_from_sweep_alone"] is False
    assert sweep["entry_logic"]["entry_allowed_after_confirmation"] is True
    assert sweep["quality_score"] >= 8.5


def test_close_accepted_beyond_liquidity_is_breakout_not_sweep() -> None:
    rows = [
        _row(0, 2350.0, 2351.0, 2349.5, 2350.2),
        _row(1, 2350.2, 2350.4, 2344.0, 2345.0),
        _row(2, 2345.0, 2346.0, 2341.5, 2342.0),
    ]
    pools = [
        _pool(
            "LQ_PDL",
            LiquidityDirection.SELL_SIDE,
            LiquidityType.PREVIOUS_DAY_LOW,
            2348.0,
            2348.5,
            2349.0,
        )
    ]

    events = _detect(rows, pools)

    assert len(events) == 1
    breakout = events[0]
    assert breakout["detected"] is False
    assert breakout["direction"] == "none"
    assert (
        breakout["sweep_validation"]["classified_as"]
        == LiquiditySweepClassification.BEARISH_BREAKOUT_OR_CONTINUATION.value
    )
    assert breakout["setup_status"] == LiquiditySweepSetupStatus.BREAKOUT_CONTINUATION.value
    assert "close_accepted_beyond_liquidity" in breakout["warnings"]


def test_weak_sweep_without_mss_stays_context_only_and_low_quality() -> None:
    rows = [
        _row(0, 100.0, 100.4, 99.8, 100.1),
        _row(1, 100.92, 101.05, 100.90, 100.95),
        _row(2, 100.95, 101.00, 100.80, 100.86),
        _row(3, 100.86, 100.92, 100.75, 100.88),
    ]
    pools = [
        _pool(
            "LQ_WEAK_BUY_SIDE",
            LiquidityDirection.BUY_SIDE,
            LiquidityType.SWING_HIGH,
            100.0,
            100.5,
            101.0,
            quality_score=4.0,
        )
    ]

    events = _detect(rows, pools)

    assert len(events) == 1
    sweep = events[0]
    assert sweep["detected"] is True
    assert sweep["sweep_validation"]["reclaim_type"] == LiquiditySweepReclaimType.WEAK_REJECTION.value
    assert sweep["post_sweep_confirmation"]["mss_after_sweep"] is False
    assert sweep["entry_logic"]["entry_allowed_from_sweep_alone"] is False
    assert sweep["entry_logic"]["entry_allowed_after_confirmation"] is False
    assert sweep["setup_status"] == LiquiditySweepSetupStatus.UNCONFIRMED_OR_STALE.value
    assert 3.0 <= sweep["quality_score"] <= 5.0


def test_high_quality_ny_reversal_model_requires_confirmation_before_entry() -> None:
    rows = [
        _row(0, 2380.0, 2382.0, 2378.0, 2381.0, "NEWYORK_KILLZONE"),
        _row(1, 2381.0, 2390.4, 2380.8, 2382.0, "NEWYORK_KILLZONE"),
        _row(2, 2382.0, 2382.5, 2372.0, 2373.0, "NEWYORK_KILLZONE"),
        _row(3, 2373.0, 2376.0, 2369.0, 2370.0, "NEWYORK_KILLZONE"),
        _row(4, 2370.0, 2371.0, 2364.0, 2365.0, "NEWYORK_KILLZONE"),
    ]
    pools = [
        _pool(
            "LQ_PDH_EQUAL_HIGHS_NY",
            LiquidityDirection.BUY_SIDE,
            LiquidityType.PREVIOUS_DAY_HIGH,
            2385.0,
            2386.0,
            2387.0,
            quality_score=10.0,
        )
    ]

    events = _detect(
        rows,
        pools,
        mss_events=[{"direction": "bearish", "index": 3}],
        choch_events=[{"direction": "bearish", "index": 2}],
    )

    assert len(events) == 1
    sweep = events[0]
    assert sweep["detected"] is True
    assert sweep["sweep_validation"]["reclaim_type"] == LiquiditySweepReclaimType.FULL_REJECTION.value
    assert sweep["post_sweep_confirmation"]["choch_after_sweep"] is True
    assert sweep["post_sweep_confirmation"]["mss_after_sweep"] is True
    assert sweep["post_sweep_confirmation"]["fvg_after_sweep"] is True
    assert sweep["entry_logic"]["entry_allowed_from_sweep_alone"] is False
    assert sweep["entry_logic"]["entry_allowed_after_confirmation"] is True
    assert sweep["confidence_grade"] == LiquiditySweepConfidenceGrade.HIGH_QUALITY.value
    assert sweep["quality_score"] >= 9.0
