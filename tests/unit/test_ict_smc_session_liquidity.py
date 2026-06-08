from datetime import datetime, timedelta, timezone

from src.analytics.ict_smc.session_liquidity import (
    calculate_session_high_low,
    detect_session_liquidity_sweep,
)


def _row(index, minutes, open_p, high_p, low_p, close_p, is_closed=True):
    return {
        "index": index,
        "timestamp": datetime(2026, 6, 4, 0, 0, tzinfo=timezone.utc)
        + timedelta(minutes=minutes),
        "symbol": "XAUUSD",
        "timeframe": "5m",
        "open": open_p,
        "high": high_p,
        "low": low_p,
        "close": close_p,
        "volume": 100 + index,
        "is_closed": is_closed,
    }


def _asian_rows():
    return [
        _row(0, 0, 2361.1, 2362.0, 2360.2, 2361.7),
        _row(1, 60, 2361.7, 2364.2, 2359.1, 2363.0),
        _row(2, 120, 2363.0, 2368.4, 2362.4, 2364.8),
        _row(3, 180, 2364.8, 2365.3, 2356.2, 2358.4),
        _row(4, 240, 2358.4, 2363.0, 2357.6, 2360.1),
        _row(5, 300, 2360.1, 2364.8, 2358.9, 2364.8),
        _row(6, 365, 2364.8, 2365.0, 2363.8, 2364.2),
    ]


def _asian_levels():
    return calculate_session_high_low(
        _asian_rows(),
        "00:00",
        "06:00",
        "UTC",
        session_name="asian_session",
    )


def test_calculate_asian_session_high_low_liquidity_objects() -> None:
    result = _asian_levels()

    assert result["session_complete"] is True
    assert result["session_high"] == 2368.4
    assert result["session_low"] == 2356.2
    assert result["session_levels"]["session_midpoint"] == 2362.3
    assert result["session_high_liquidity"]["direction"] == "buy_side"
    assert result["session_low_liquidity"]["direction"] == "sell_side"
    assert result["entry_allowed_from_session_liquidity_alone"] is False
    assert result["quality_score"] >= 7.0


def test_london_sweeps_asian_high_and_rejects_back_inside() -> None:
    rows = _asian_rows() + [
        _row(7, 490, 2366.8, 2371.2, 2365.9, 2367.6),
        _row(8, 495, 2367.6, 2368.0, 2361.1, 2361.8),
        _row(9, 500, 2361.7, 2362.0, 2358.1, 2359.4),
    ]

    result = detect_session_liquidity_sweep(rows, _asian_levels(), sweep_buffer=0.4)
    event = result["sweep_events"][0]

    assert event["swept_level_type"] == "session_high"
    assert event["swept_side"] == "buy_side"
    assert event["sweep_status"] == "swept"
    assert event["reclaim_status"] == "rejected_back_below_session_high"
    assert event["expected_bias"] == "bearish_possible"
    assert event["target_liquidity"]["target_side"] == "sell_side"
    assert event["entry_allowed_from_session_liquidity_alone"] is False


def test_london_sweeps_asian_low_and_reclaims_back_inside() -> None:
    rows = _asian_rows() + [
        _row(7, 470, 2358.1, 2359.3, 2353.8, 2357.4),
        _row(8, 475, 2357.4, 2364.0, 2357.0, 2363.2),
        _row(9, 480, 2363.2, 2369.0, 2362.5, 2367.5),
    ]

    result = detect_session_liquidity_sweep(rows, _asian_levels(), sweep_buffer=0.4)
    event = result["sweep_events"][0]

    assert event["swept_level_type"] == "session_low"
    assert event["swept_side"] == "sell_side"
    assert event["sweep_status"] == "swept"
    assert event["reclaim_status"] == "reclaimed_back_above_session_low"
    assert event["expected_bias"] == "bullish_possible"
    assert event["target_liquidity"]["target_side"] == "buy_side"


def test_new_york_breaks_london_high_as_acceptance_not_reversal_sweep() -> None:
    london_levels = {
        "session_name": "london_session",
        "session_levels": {
            "session_high": 2372.5,
            "session_low": 2359.5,
            "session_midpoint": 2366.0,
            "session_end_timestamp": datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc),
        },
    }
    rows = [
        _row(0, 725, 2371.4, 2372.2, 2368.0, 2371.9),
        _row(1, 730, 2372.0, 2376.0, 2371.8, 2375.2),
        _row(2, 735, 2375.2, 2378.8, 2374.9, 2378.0),
    ]

    result = detect_session_liquidity_sweep(rows, london_levels, sweep_buffer=0.4)
    event = result["sweep_events"][0]

    assert event["swept_level_type"] == "session_high"
    assert event["sweep_status"] == "breakout"
    assert event["reclaim_status"] == "accepted_above_session_high"
    assert event["expected_bias"] == "bullish_continuation_possible"
    assert event["breakout_confirmed"] is True


def test_weak_tiny_wick_above_session_high_is_unclear_not_valid_sweep() -> None:
    rows = _asian_rows() + [
        _row(7, 490, 2368.2, 2368.45, 2367.9, 2368.35),
    ]

    result = detect_session_liquidity_sweep(rows, _asian_levels(), sweep_buffer=0.4)
    event = result["sweep_events"][0]

    assert event["swept_level_type"] == "session_high"
    assert event["sweep_status"] == "unclear_or_invalid"
    assert event["quality_score"] <= 3.0
    assert "sweep_too_small_no_confirmation" in event["reasons"]
