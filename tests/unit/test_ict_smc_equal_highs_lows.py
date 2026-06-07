from datetime import datetime, timedelta, timezone

from src.analytics.ict_smc.equal_highs_lows import detect_equal_highs_lows


def _row(index, open_p, high_p, low_p, close_p, timeframe="15m", is_closed=True):
    return {
        "index": index,
        "timestamp": datetime(2026, 6, 3, tzinfo=timezone.utc) + timedelta(minutes=index),
        "symbol": "XAUUSD",
        "timeframe": timeframe,
        "open": open_p,
        "high": high_p,
        "low": low_p,
        "close": close_p,
        "volume": 100 + index,
        "is_closed": is_closed,
    }


def _swing(index, price, swing_type, strength=7.5, timeframe="15m", confirmed=True):
    return {
        "index": index,
        "timestamp": datetime(2026, 6, 3, tzinfo=timezone.utc) + timedelta(minutes=index),
        "price": price,
        "type": swing_type,
        "strength_score": strength,
        "confirmed_status": confirmed,
        "timeframe": timeframe,
    }


def _base_rows():
    return [
        _row(90, 2358.0, 2360.0, 2356.0, 2359.0),
        _row(104, 2360.0, 2365.20, 2357.0, 2362.0),
        _row(112, 2362.0, 2364.0, 2359.0, 2361.0),
        _row(128, 2361.0, 2365.70, 2358.0, 2360.0),
        _row(140, 2360.0, 2363.0, 2357.0, 2361.0),
        _row(151, 2361.0, 2365.40, 2358.0, 2363.0),
        _row(160, 2363.0, 2364.8, 2360.0, 2361.5),
    ]


def test_valid_active_equal_highs_create_buy_side_liquidity_pool() -> None:
    swings = [
        _swing(104, 2365.20, "swing_high"),
        _swing(128, 2365.70, "swing_high"),
        _swing(151, 2365.40, "swing_high"),
    ]

    pools = detect_equal_highs_lows(_base_rows(), tolerance_percent=0.05, min_touches=3, swings=swings)

    assert len(pools) == 1
    pool = pools[0]
    assert pool["type"] == "equal_highs"
    assert pool["direction"] == "buy_side"
    assert pool["zone_low"] == 2365.20
    assert pool["zone_high"] == 2365.70
    assert pool["touch_count"] == 3
    assert pool["swept"] is False
    assert pool["active_status"] == "active"
    assert 7.0 <= pool["quality_score"] <= 9.0
    assert pool["entry_allowed_from_equal_liquidity_alone"] is False


def test_valid_active_equal_lows_create_sell_side_liquidity_pool() -> None:
    rows = [
        _row(70, 2350.0, 2353.0, 2345.0, 2348.0),
        _row(80, 2348.0, 2352.0, 2342.20, 2349.0),
        _row(90, 2349.0, 2354.0, 2345.0, 2351.0),
        _row(101, 2351.0, 2353.0, 2341.90, 2350.0),
        _row(118, 2350.0, 2356.0, 2345.0, 2354.0),
        _row(127, 2354.0, 2358.0, 2342.40, 2355.0),
        _row(140, 2355.0, 2360.0, 2343.5, 2358.0),
    ]
    swings = [
        _swing(80, 2342.20, "swing_low"),
        _swing(101, 2341.90, "swing_low"),
        _swing(127, 2342.40, "swing_low"),
    ]

    pools = detect_equal_highs_lows(rows, tolerance_percent=0.05, min_touches=3, swings=swings)

    assert len(pools) == 1
    pool = pools[0]
    assert pool["type"] == "equal_lows"
    assert pool["direction"] == "sell_side"
    assert pool["zone_low"] == 2341.90
    assert pool["zone_high"] == 2342.40
    assert pool["touch_count"] == 3
    assert pool["swept"] is False
    assert pool["active_status"] == "active"
    assert 7.0 <= pool["quality_score"] <= 9.0


def test_equal_highs_swept_and_rejected_become_bearish_sweep_context() -> None:
    rows = _base_rows() + [_row(170, 2363.0, 2368.00, 2361.0, 2364.70)]
    swings = [
        _swing(104, 2365.20, "swing_high"),
        _swing(128, 2365.70, "swing_high"),
        _swing(151, 2365.40, "swing_high"),
    ]

    pools = detect_equal_highs_lows(rows, tolerance_percent=0.05, min_touches=3, swings=swings)
    pool = pools[0]

    assert pool["type"] == "equal_highs"
    assert pool["swept"] is True
    assert pool["active_status"] == "swept_rejected"
    assert pool["sweep_type"] == "buy_side_sweep_and_rejection"
    assert pool["status"]["swept_at_index"] == 170
    assert pool["liquidity_context"]["target_use"] == "sweep_event_for_bearish_MSS_or_reversal_context"
    assert pool["quality_score"] >= 5.0


def test_equal_lows_swept_and_reclaimed_become_bullish_sweep_context() -> None:
    rows = [
        _row(80, 2348.0, 2352.0, 2342.20, 2349.0),
        _row(101, 2351.0, 2353.0, 2341.90, 2350.0),
        _row(127, 2354.0, 2358.0, 2342.40, 2355.0),
        _row(140, 2355.0, 2360.0, 2344.0, 2358.0),
        _row(156, 2358.0, 2360.0, 2339.80, 2343.10),
    ]
    swings = [
        _swing(80, 2342.20, "swing_low"),
        _swing(101, 2341.90, "swing_low"),
        _swing(127, 2342.40, "swing_low"),
    ]

    pools = detect_equal_highs_lows(rows, tolerance_percent=0.05, min_touches=3, swings=swings)
    pool = pools[0]

    assert pool["type"] == "equal_lows"
    assert pool["swept"] is True
    assert pool["active_status"] == "swept_reclaimed"
    assert pool["sweep_type"] == "sell_side_sweep_and_reclaim"
    assert pool["status"]["swept_at_index"] == 156
    assert pool["liquidity_context"]["target_use"] == "sweep_event_for_bullish_MSS_or_reversal_context"
    assert pool["quality_score"] >= 5.0


def test_accepted_breakout_marks_equal_highs_inactive_not_unswept_liquidity() -> None:
    rows = _base_rows() + [_row(170, 2363.0, 2368.00, 2361.0, 2367.20)]
    swings = [
        _swing(104, 2365.20, "swing_high"),
        _swing(128, 2365.70, "swing_high"),
        _swing(151, 2365.40, "swing_high"),
    ]

    pools = detect_equal_highs_lows(rows, tolerance_percent=0.05, min_touches=3, swings=swings)
    pool = pools[0]

    assert pool["swept"] is True
    assert pool["active_status"] == "broken_or_accepted_above"
    assert pool["sweep_type"] == "accepted_breakout"
    assert pool["quality_score"] <= 3.0
    assert "do_not_treat_as_unswept_liquidity_target" in pool["warnings"]


def test_false_equal_highs_from_noise_are_filtered_to_low_quality() -> None:
    rows = [
        _row(0, 100.0, 100.5, 99.8, 100.1, timeframe="1m"),
        _row(1, 100.1, 100.6, 99.8, 100.0, timeframe="1m"),
        _row(2, 100.0, 100.55, 99.7, 100.05, timeframe="1m"),
        _row(3, 100.05, 100.58, 99.75, 100.02, timeframe="1m"),
        _row(4, 100.02, 100.7, 99.9, 100.1, timeframe="1m"),
        _row(5, 100.1, 100.65, 99.85, 100.0, timeframe="1m"),
        _row(6, 100.0, 100.4, 99.75, 99.95, timeframe="1m"),
    ]
    swings = [
        _swing(1, 100.60, "swing_high", strength=4.1, timeframe="1m"),
        _swing(2, 100.55, "swing_high", strength=4.0, timeframe="1m"),
    ]

    pools = detect_equal_highs_lows(
        rows,
        tolerance_percent=0.10,
        min_touches=2,
        swings=swings,
        min_swing_strength=4.0,
        min_touch_spacing=1,
        max_zone_width_atr=0.10,
    )

    assert len(pools) == 1
    assert pools[0]["type"] == "equal_highs"
    assert 0.0 <= pools[0]["quality_score"] <= 3.0
    assert "noisy_equal_highs_lows_filtered" in pools[0]["warnings"]
