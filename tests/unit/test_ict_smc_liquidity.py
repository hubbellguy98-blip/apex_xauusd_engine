from datetime import datetime, timedelta, timezone

from src.analytics.ict_smc.liquidity import (
    ICTLiquidityDetector,
    LiquidityDetectionConfig,
    LiquidityDirection,
    LiquidityStatus,
    LiquidityType,
    detect_equal_highs,
    detect_equal_lows,
    detect_liquidity_pools,
)
from src.analytics.ict_smc.swing_points import (
    DetectedSwingPoint,
    SwingLiquidityType,
    SwingPointStatus,
    SwingPointType,
    SwingStrengthLabel,
)


def _row(
    index: int,
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    session_name: str | None = None,
    day_offset: int = 0,
) -> dict:
    start = datetime(2026, 6, 1 + day_offset, tzinfo=timezone.utc) + timedelta(minutes=index)
    return {
        "index": index + day_offset * 1000,
        "timestamp": start,
        "symbol": "XAUUSD",
        "timeframe": "15m",
        "open": open_p,
        "high": high_p,
        "low": low_p,
        "close": close_p,
        "volume": 100 + index,
        "is_closed": True,
        "session_name": session_name,
    }


def _swing(index: int, price: float, swing_type: SwingPointType, timeframe: str = "15m") -> DetectedSwingPoint:
    ts = datetime(2026, 6, 1, tzinfo=timezone.utc) + timedelta(minutes=index)
    return DetectedSwingPoint(
        index=index,
        timestamp=ts,
        confirmation_index=index + 1,
        confirmation_timestamp=ts + timedelta(minutes=1),
        price=price,
        type=swing_type,
        strength_score=6.0,
        strength_label=SwingStrengthLabel.STRONG,
        timeframe=timeframe,
        timeframe_weight=1.5 if timeframe == "15m" else 0.5,
        liquidity_type=(
            SwingLiquidityType.BUY_SIDE if swing_type == SwingPointType.SWING_HIGH else SwingLiquidityType.SELL_SIDE
        ),
        status=SwingPointStatus.UNSWEPT,
        used_for=("liquidity",),
    )


def test_valid_equal_highs_create_unswept_buy_side_liquidity() -> None:
    rows = [
        _row(0, 2358, 2361, 2356, 2360),
        _row(4, 2360, 2368.70, 2357, 2362),
        _row(8, 2362, 2369.10, 2359, 2361),
        _row(12, 2361, 2368.95, 2358, 2360),
        _row(16, 2360, 2362, 2355, 2357),
    ]
    swings = [
        _swing(4, 2368.70, SwingPointType.SWING_HIGH),
        _swing(8, 2369.10, SwingPointType.SWING_HIGH),
        _swing(12, 2368.95, SwingPointType.SWING_HIGH),
    ]

    pools = detect_equal_highs(
        rows,
        [swing.as_dict() for swing in swings],
        equal_level_atr_multiplier=0.15,
        zone_atr_multiplier=0.1,
    )

    assert len(pools) == 1
    pool = pools[0]
    assert pool["liquidity_type"] == LiquidityType.EQUAL_HIGHS.value
    assert pool["direction"] == LiquidityDirection.BUY_SIDE.value
    assert pool["touched_count"] == 3
    assert pool["swept_status"] == LiquidityStatus.UNSWEPT.value
    assert pool["quality_score"] >= 7.0


def test_valid_equal_lows_create_sell_side_liquidity_with_session_confluence() -> None:
    rows = [
        _row(0, 2355, 2358, 2348.20, 2352, "ASIA"),
        _row(5, 2352, 2357, 2348.50, 2354, "ASIA"),
        _row(10, 2354, 2360, 2350, 2358, "ASIA"),
        _row(15, 2358, 2364, 2355, 2362, "LONDON"),
    ]
    swings = [
        _swing(0, 2348.20, SwingPointType.SWING_LOW),
        _swing(5, 2348.50, SwingPointType.SWING_LOW),
    ]

    pools = detect_liquidity_pools(
        rows,
        [swing.as_dict() for swing in swings],
        equal_level_atr_multiplier=0.15,
        zone_atr_multiplier=0.08,
        include_swing_liquidity=False,
        include_previous_day_levels=False,
        include_range_levels=False,
    )
    equal_lows = [pool for pool in pools if pool["liquidity_type"] == LiquidityType.EQUAL_LOWS.value]

    assert len(equal_lows) == 1
    assert equal_lows[0]["direction"] == LiquidityDirection.SELL_SIDE.value
    assert equal_lows[0]["swept_status"] == LiquidityStatus.UNSWEPT.value
    assert equal_lows[0]["confluence"]["has_confluence"] is True
    assert equal_lows[0]["quality_score"] >= 7.0


def test_buy_side_liquidity_sweep_is_not_classified_as_breakout() -> None:
    rows = [
        _row(0, 2358, 2361, 2356, 2360),
        _row(4, 2360, 2368.70, 2357, 2362),
        _row(8, 2362, 2369.10, 2359, 2361),
        _row(12, 2361, 2368.95, 2358, 2360),
        _row(18, 2360, 2371.00, 2359, 2368.60),
    ]
    swings = [
        _swing(4, 2368.70, SwingPointType.SWING_HIGH),
        _swing(8, 2369.10, SwingPointType.SWING_HIGH),
        _swing(12, 2368.95, SwingPointType.SWING_HIGH),
    ]

    detector = ICTLiquidityDetector(
        LiquidityDetectionConfig(
            equal_level_atr_multiplier=0.15,
            zone_atr_multiplier=0.05,
            include_swing_liquidity=False,
            include_previous_day_levels=False,
            include_session_levels=False,
            include_range_levels=False,
        )
    )
    pools = detector.detect(rows, swings)
    pool = next(pool for pool in pools if pool.liquidity_type == LiquidityType.EQUAL_HIGHS)

    assert pool.swept_status == LiquidityStatus.SWEPT
    assert pool.sweep_details.sweep_type == "buy_side_sweep"
    assert "possible_bearish_reversal_context" in pool.role
    assert pool.broken_candle_index is None


def test_previous_day_low_close_below_zone_is_broken_not_swept() -> None:
    rows = [
        _row(0, 2355, 2360, 2350, 2358, day_offset=0),
        _row(1, 2358, 2362, 2348.20, 2352, day_offset=0),
        _row(2, 2352, 2357, 2350, 2356, day_offset=0),
        _row(0, 2354, 2356, 2344.00, 2345.20, day_offset=1),
    ]

    pools = detect_liquidity_pools(
        rows,
        [],
        zone_atr_multiplier=0.05,
        break_buffer_atr_multiplier=0.0,
        include_swing_liquidity=False,
        include_equal_levels=False,
        include_session_levels=False,
        include_range_levels=False,
    )
    pdl = next(pool for pool in pools if pool["liquidity_type"] == LiquidityType.PREVIOUS_DAY_LOW.value)

    assert pdl["direction"] == LiquidityDirection.SELL_SIDE.value
    assert pdl["swept_status"] == LiquidityStatus.BROKEN.value
    assert "bearish_continuation_or_bos_context" in pdl["role"]
    assert pdl["sweep_details"]["sweep_type"] == "none"


def test_weak_low_timeframe_equal_highs_inside_chop_are_low_quality() -> None:
    rows = [
        _row(0, 100.0, 100.5, 99.7, 100.1),
        _row(1, 100.1, 100.6, 99.8, 100.0),
        _row(2, 100.0, 100.55, 99.75, 100.05),
        _row(3, 100.05, 100.58, 99.78, 100.02),
        _row(4, 100.02, 100.7, 99.9, 100.1),
        _row(5, 100.1, 100.65, 99.85, 100.0),
        _row(6, 100.0, 100.4, 99.75, 99.95),
        _row(7, 99.95, 100.45, 99.7, 100.0),
    ]
    swings = [
        _swing(1, 100.60, SwingPointType.SWING_HIGH, timeframe="1m"),
        _swing(3, 100.58, SwingPointType.SWING_HIGH, timeframe="1m"),
    ]

    pools = detect_equal_highs(
        rows,
        [swing.as_dict() for swing in swings],
        equal_level_atr_multiplier=0.20,
        zone_atr_multiplier=0.2,
        minimum_touch_separation=1,
    )

    assert len(pools) == 1
    assert 2.0 <= pools[0]["quality_score"] <= 4.5
    assert "low_timeframe_noise" in pools[0]["warnings"]
    assert "weak_confluence" in pools[0]["warnings"]
