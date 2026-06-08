from datetime import datetime, timedelta, timezone

from src.analytics.ict_smc.amd_model import detect_amd_model


def _row(index, minutes, open_p, high_p, low_p, close_p, timeframe="5m", is_closed=True):
    return {
        "index": index,
        "timestamp": datetime(2026, 6, 4, tzinfo=timezone.utc) + timedelta(minutes=minutes),
        "symbol": "XAUUSD",
        "timeframe": timeframe,
        "open": open_p,
        "high": high_p,
        "low": low_p,
        "close": close_p,
        "volume": 100 + index,
        "is_closed": is_closed,
    }


def _sessions():
    return {
        "accumulation_range": {
            "session_name": "asian_session",
            "range_high": 2368.40,
            "range_low": 2356.20,
            "range_midpoint": 2362.30,
            "range_size": 12.20,
            "start_index": 0,
            "end_index": 4,
            "session_start": "00:00",
            "session_end": "06:00",
            "timezone": "UTC",
            "range_quality_score": 8.0,
        }
    }


def _accumulation_rows():
    return [
        _row(0, 0, 2360.0, 2363.0, 2358.0, 2361.0),
        _row(1, 60, 2361.0, 2368.4, 2360.8, 2365.7),
        _row(2, 120, 2365.7, 2366.5, 2356.2, 2358.5),
        _row(3, 240, 2358.5, 2364.8, 2357.0, 2362.0),
        _row(4, 360, 2362.0, 2365.0, 2359.2, 2361.8),
    ]


def test_valid_bullish_amd_detects_all_three_phases() -> None:
    rows = _accumulation_rows() + [
        _row(5, 430, 2358.0, 2358.8, 2352.8, 2357.4),
        _row(6, 435, 2357.4, 2361.8, 2356.9, 2361.2),
        _row(7, 440, 2361.2, 2367.0, 2360.8, 2366.4),
        _row(8, 445, 2366.4, 2374.5, 2366.2, 2373.8),
        _row(9, 450, 2373.8, 2378.0, 2372.5, 2376.4),
    ]

    result = detect_amd_model(
        rows,
        _sessions(),
        htf_bias="bullish",
        sweep_buffer=0.25,
        close_buffer=0.25,
        mss_events=[{"direction": "bullish", "index": 7, "broken_level": 2361.8}],
        fvg_events=[{"fvg_type": "bullish_fvg", "creation_index": 8, "zone_low": 2361.8, "zone_high": 2366.2}],
        active_session="london_killzone",
    )

    assert result["amd_detected"] is True
    assert result["amd_type"] == "bullish_AMD"
    assert result["accumulation_range"]["valid_accumulation"] is True
    assert result["manipulation_side"] == "below_range"
    assert result["swept_liquidity"] == "sell_side"
    assert result["reclaim_status"] == "reclaimed_back_inside_range"
    assert result["distribution_direction"] == "bullish"
    assert result["mss_confirmed"] is True
    assert result["displacement_confirmed"] is True
    assert result["entry_zone"]["entry_zone_type"] == "bullish_fvg"
    assert result["target_side"] == "buy_side"
    assert result["confidence_score"] >= 8.0


def test_valid_bearish_amd_detects_all_three_phases() -> None:
    rows = _accumulation_rows() + [
        _row(5, 430, 2366.2, 2372.6, 2365.8, 2367.5),
        _row(6, 435, 2367.5, 2368.0, 2362.0, 2362.5),
        _row(7, 440, 2362.5, 2363.0, 2357.1, 2357.5),
        _row(8, 445, 2357.5, 2358.2, 2349.5, 2350.2),
        _row(9, 450, 2350.2, 2352.0, 2346.0, 2347.4),
    ]

    result = detect_amd_model(
        rows,
        _sessions(),
        htf_bias="bearish",
        sweep_buffer=0.25,
        close_buffer=0.25,
        mss_events=[{"direction": "bearish", "index": 7, "broken_level": 2362.0}],
        fvg_events=[{"fvg_type": "bearish_fvg", "creation_index": 8, "zone_low": 2358.2, "zone_high": 2362.0}],
        active_session="newyork_killzone",
    )

    assert result["amd_detected"] is True
    assert result["amd_type"] == "bearish_AMD"
    assert result["manipulation_side"] == "above_range"
    assert result["swept_liquidity"] == "buy_side"
    assert result["reclaim_status"] == "rejected_back_inside_range"
    assert result["distribution_direction"] == "bearish"
    assert result["mss_confirmed"] is True
    assert result["entry_zone"]["entry_zone_type"] == "bearish_fvg"
    assert result["target_side"] == "sell_side"
    assert result["confidence_score"] >= 8.0


def test_accumulation_only_does_not_force_amd() -> None:
    rows = _accumulation_rows() + [
        _row(5, 430, 2361.8, 2364.0, 2359.0, 2362.2),
        _row(6, 435, 2362.2, 2365.0, 2360.0, 2363.0),
    ]

    result = detect_amd_model(rows, _sessions(), htf_bias="neutral", sweep_buffer=0.25, close_buffer=0.25)

    assert result["amd_detected"] is False
    assert result["classification"] == "accumulation_only"
    assert result["manipulation_side"] == "none"
    assert result["distribution_direction"] == "unknown"
    assert result["confidence_score"] == 0.0
    assert "Do not force AMD every day" in result["warnings"]


def test_manipulation_without_distribution_remains_candidate() -> None:
    rows = _accumulation_rows() + [
        _row(5, 430, 2358.0, 2358.8, 2352.8, 2357.4),
        _row(6, 435, 2357.4, 2359.0, 2356.5, 2358.0),
        _row(7, 440, 2358.0, 2360.0, 2357.2, 2359.0),
    ]

    result = detect_amd_model(
        rows,
        _sessions(),
        htf_bias="bullish",
        sweep_buffer=0.25,
        close_buffer=0.25,
        mss_events=[],
        fvg_events=[],
    )

    assert result["amd_detected"] is False
    assert result["amd_type"] == "bullish_AMD_candidate"
    assert result["manipulation_side"] == "below_range"
    assert result["distribution_direction"] == "bullish"
    assert result["mss_confirmed"] is False
    assert result["entry_zone"] is None
    assert result["confidence_score"] <= 5.0
    assert "MSS_required_for_confirmed_distribution" in result["warnings"]


def test_breakout_continuation_is_not_forced_into_bearish_amd() -> None:
    rows = _accumulation_rows() + [
        _row(5, 430, 2367.0, 2373.8, 2366.8, 2372.9),
        _row(6, 435, 2372.9, 2378.4, 2372.0, 2377.0),
    ]

    result = detect_amd_model(
        rows,
        _sessions(),
        htf_bias="bullish",
        sweep_buffer=0.25,
        close_buffer=0.50,
    )

    assert result["amd_detected"] is False
    assert result["amd_type"] == "invalid_bearish_AMD_candidate"
    assert result["classification"] == "bullish_breakout_continuation_not_bearish_AMD"
    assert result["manipulation_side"] == "above_range"
    assert result["reclaim_status"] == "accepted_above_range"
    assert result["distribution_direction"] == "bullish"
    assert result["confidence_score"] <= 3.0
