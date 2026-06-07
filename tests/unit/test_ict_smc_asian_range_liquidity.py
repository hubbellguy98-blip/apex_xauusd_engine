from datetime import datetime, timedelta, timezone

from src.analytics.ict_smc.asian_range_liquidity import calculate_session_range, detect_asian_range_sweep


def _row(index, minutes, open_p, high_p, low_p, close_p, timeframe="5m", is_closed=True):
    return {
        "index": index,
        "timestamp": datetime(2026, 6, 3, tzinfo=timezone.utc) + timedelta(minutes=minutes),
        "symbol": "XAUUSD",
        "timeframe": timeframe,
        "open": open_p,
        "high": high_p,
        "low": low_p,
        "close": close_p,
        "volume": 100 + index,
        "is_closed": is_closed,
    }


def test_calculate_session_range_builds_asian_liquidity_box() -> None:
    rows = [
        _row(0, 0, 2360.00, 2362.20, 2359.10, 2361.50),
        _row(1, 60, 2361.50, 2368.40, 2361.00, 2366.00),
        _row(2, 120, 2366.00, 2367.20, 2356.20, 2358.00),
        _row(3, 240, 2358.00, 2364.00, 2357.40, 2362.00),
        _row(4, 360, 2362.00, 2365.00, 2359.00, 2361.00),
        _row(5, 430, 2361.00, 2370.00, 2360.50, 2368.00),
    ]

    result = calculate_session_range(rows, "00:00", "06:00", "UTC", symbol="XAUUSD", tolerance=0.20)

    assert result["session_date"] == "2026-06-03"
    assert result["asian_high"] == 2368.40
    assert result["asian_low"] == 2356.20
    assert result["asian_midpoint"] == 2362.30
    assert result["asian_range_size"] == 12.20
    assert result["high_candle_index"] == 1
    assert result["low_candle_index"] == 2
    assert result["entry_allowed_from_asian_range_alone"] is False
    assert result["liquidity_objects"][0]["liquidity_role"] == "buy_side_liquidity"
    assert result["liquidity_objects"][1]["liquidity_role"] == "sell_side_liquidity"


def test_asian_high_sweep_with_bearish_mss_and_fvg_scores_strong() -> None:
    rows = [
        _row(5, 430, 2365.0, 2371.2, 2364.5, 2367.6),
        _row(6, 435, 2367.6, 2368.2, 2362.6, 2363.1),
        _row(7, 440, 2363.1, 2364.0, 2358.7, 2359.2),
        _row(8, 445, 2359.2, 2360.0, 2354.8, 2355.0),
        _row(9, 450, 2355.0, 2356.0, 2350.2, 2351.0),
    ]

    result = detect_asian_range_sweep(
        rows,
        asian_high=2368.40,
        asian_low=2356.20,
        asian_midpoint=2362.30,
        sweep_buffer=0.25,
        close_buffer=0.25,
        mss_events=[{"direction": "bearish", "index": 7}],
        fvg_events=[{"fvg_type": "bearish_fvg", "creation_index": 8, "zone_low": 2356.0, "zone_high": 2362.6}],
        session_label="london_killzone",
    )

    assert result["swept_side"] == "asian_high"
    assert result["reclaim_status"] == "rejected_back_inside_range"
    assert result["sweep_confirmed"] is True
    assert result["mss_confirmed"] is True
    assert result["entry_zone"]["entry_zone_type"] == "bearish_fvg"
    assert result["target_side"] == "sell_side"
    assert result["targets"]["second_target"] == "asian_low"
    assert result["risk_logic"]["entry_allowed_from_sweep_alone"] is False
    assert result["quality_score"] >= 8.0


def test_asian_low_sweep_with_bullish_mss_and_fvg_scores_strong() -> None:
    rows = [
        _row(5, 430, 2358.0, 2358.8, 2353.6, 2357.1),
        _row(6, 435, 2357.1, 2361.8, 2356.7, 2361.2),
        _row(7, 440, 2361.2, 2367.0, 2360.8, 2366.3),
        _row(8, 445, 2366.3, 2372.4, 2366.0, 2371.8),
        _row(9, 450, 2371.8, 2375.4, 2370.6, 2374.0),
    ]

    result = detect_asian_range_sweep(
        rows,
        asian_high=2368.40,
        asian_low=2356.20,
        asian_midpoint=2362.30,
        sweep_buffer=0.25,
        close_buffer=0.25,
        mss_events=[{"direction": "bullish", "index": 7}],
        fvg_events=[{"fvg_type": "bullish_fvg", "creation_index": 8, "zone_low": 2361.8, "zone_high": 2366.0}],
        session_label="newyork_killzone",
    )

    assert result["swept_side"] == "asian_low"
    assert result["reclaim_status"] == "reclaimed_back_inside_range"
    assert result["sweep_confirmed"] is True
    assert result["mss_confirmed"] is True
    assert result["entry_zone"]["entry_zone_type"] == "bullish_fvg"
    assert result["target_side"] == "buy_side"
    assert result["targets"]["second_target"] == "asian_high"
    assert result["quality_score"] >= 8.0


def test_asian_high_breakout_is_continuation_not_sweep() -> None:
    rows = [
        _row(5, 430, 2366.0, 2372.6, 2365.8, 2371.9),
        _row(6, 435, 2371.9, 2375.2, 2370.8, 2374.4),
    ]

    result = detect_asian_range_sweep(
        rows,
        asian_high=2368.40,
        asian_low=2356.20,
        sweep_buffer=0.25,
        close_buffer=0.50,
    )

    assert result["sweep_type"] == "asian_high_breakout_continuation"
    assert result["sweep_confirmed"] is False
    assert result["breakout_confirmed"] is True
    assert result["reclaim_status"] == "accepted_above_range"
    assert result["target_side"] == "buy_side"
    assert "not_an_asian_high_sweep_reversal" in result["warnings"]


def test_asian_low_breakdown_is_continuation_not_sweep() -> None:
    rows = [
        _row(5, 430, 2358.0, 2358.4, 2351.6, 2352.2),
        _row(6, 435, 2352.2, 2353.0, 2348.2, 2349.0),
    ]

    result = detect_asian_range_sweep(
        rows,
        asian_high=2368.40,
        asian_low=2356.20,
        sweep_buffer=0.25,
        close_buffer=0.50,
    )

    assert result["sweep_type"] == "asian_low_breakdown_continuation"
    assert result["sweep_confirmed"] is False
    assert result["breakout_confirmed"] is True
    assert result["reclaim_status"] == "accepted_below_range"
    assert result["target_side"] == "sell_side"
    assert "not_an_asian_low_sweep_reversal" in result["warnings"]


def test_unclear_tiny_wick_is_low_quality_no_entry_zone() -> None:
    rows = [
        _row(5, 430, 2367.8, 2368.8, 2366.5, 2368.45),
        _row(6, 435, 2368.45, 2369.0, 2367.2, 2368.2),
    ]

    result = detect_asian_range_sweep(
        rows,
        asian_high=2368.40,
        asian_low=2356.20,
        sweep_buffer=0.20,
        close_buffer=0.50,
    )

    assert result["sweep_type"] == "unclear"
    assert result["reclaim_status"] == "unclear"
    assert result["mss_confirmed"] is False
    assert result["entry_zone"] is None
    assert result["target_side"] == "unknown"
    assert result["quality_score"] <= 4.0
