from datetime import datetime, timedelta, timezone

from src.analytics.ict_smc.london_open_raid import detect_london_open_raid


def _row(index, minutes, open_p, high_p, low_p, close_p, is_closed=True):
    return {
        "index": index,
        "timestamp": datetime(2026, 6, 4, tzinfo=timezone.utc) + timedelta(minutes=minutes),
        "symbol": "XAUUSD",
        "timeframe": "5m",
        "open": open_p,
        "high": high_p,
        "low": low_p,
        "close": close_p,
        "volume": 100 + index,
        "is_closed": is_closed,
    }


def _asian_range(quality=8.2):
    return {
        "asian_high": 2368.40,
        "asian_low": 2356.20,
        "asian_midpoint": 2362.30,
        "asian_range_size": 12.20,
        "session_end": "2026-06-04T06:00:00+00:00",
        "timezone": "UTC",
        "range_quality_score": quality,
    }


def _london_window():
    return {
        "window_name": "london_open",
        "start_time": "07:00",
        "end_time": "10:00",
        "timezone": "UTC",
        "allowed_days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
        "strict_mode": True,
        "post_window_buffer_minutes": 30,
    }


def _detect(rows, asian=None, htf_bias="neutral"):
    return detect_london_open_raid(
        rows,
        asian or _asian_range(),
        _london_window(),
        htf_bias,
        sweep_buffer=0.25,
        close_buffer=0.25,
        break_buffer=0.25,
        stop_buffer=0.50,
        min_displacement_range_ratio=0.5,
    )


def test_valid_bullish_london_raid_sweeps_asian_low_then_confirms_mss() -> None:
    rows = [
        _row(0, 420, 2360.5, 2361.6, 2359.7, 2360.2),
        _row(1, 450, 2358.8, 2359.2, 2353.8, 2357.4),
        _row(2, 455, 2357.4, 2360.8, 2356.9, 2360.6),
        _row(3, 460, 2360.6, 2365.4, 2360.4, 2364.8),
    ]

    result = _detect(rows, htf_bias="bullish")

    assert result["raid_detected"] is True
    assert result["valid_setup"] is True
    assert result["raid_type"] == "asian_low_sweep_reversal"
    assert result["direction"] == "bullish"
    assert result["swept_side"] == "asian_low"
    assert result["reclaim_status"] == "reclaimed_back_above_asian_low"
    assert result["mss_confirmed"] is True
    assert result["entry_zone"]["entry_zone_type"] == "bullish_fvg"
    assert result["target_liquidity"]["second_target"] == "asian_high"
    assert 8.0 <= result["quality_score"] <= 10.0
    assert result["entry_allowed_from_london_raid_alone"] is False


def test_valid_bearish_london_raid_sweeps_asian_high_then_confirms_mss() -> None:
    rows = [
        _row(0, 420, 2362.8, 2364.1, 2361.9, 2363.4),
        _row(1, 480, 2367.2, 2372.6, 2365.8, 2367.6),
        _row(2, 485, 2367.6, 2368.1, 2365.5, 2365.9),
        _row(3, 490, 2364.0, 2364.2, 2359.6, 2360.2),
    ]

    result = _detect(rows, htf_bias="bearish")

    assert result["raid_detected"] is True
    assert result["valid_setup"] is True
    assert result["raid_type"] == "asian_high_sweep_reversal"
    assert result["direction"] == "bearish"
    assert result["swept_side"] == "asian_high"
    assert result["reclaim_status"] == "rejected_back_below_asian_high"
    assert result["mss_confirmed"] is True
    assert result["entry_zone"]["entry_zone_type"] == "bearish_fvg"
    assert result["target_liquidity"]["second_target"] == "asian_low"
    assert 8.0 <= result["quality_score"] <= 10.0


def test_london_sweep_without_mss_remains_candidate_not_valid_setup() -> None:
    rows = [
        _row(0, 420, 2360.5, 2361.6, 2359.7, 2360.2),
        _row(1, 450, 2358.8, 2359.2, 2353.8, 2357.4),
        _row(2, 455, 2357.4, 2358.1, 2356.4, 2356.9),
        _row(3, 460, 2356.9, 2358.3, 2356.5, 2357.8),
    ]

    result = _detect(rows, htf_bias="bullish")

    assert result["raid_detected"] is True
    assert result["valid_setup"] is False
    assert result["raid_type"] == "asian_low_sweep_candidate"
    assert result["mss_confirmed"] is False
    assert result["entry_zone"] is None
    assert "mss_not_confirmed_after_london_raid" in result["failed_requirements"]
    assert 3.0 <= result["quality_score"] <= 5.0


def test_london_acceptance_above_asian_high_is_breakout_not_bearish_reversal() -> None:
    rows = [
        _row(0, 420, 2364.0, 2365.0, 2363.4, 2364.6),
        _row(1, 450, 2368.5, 2371.0, 2368.3, 2370.2),
        _row(2, 455, 2370.2, 2371.2, 2369.8, 2371.0),
        _row(3, 460, 2371.0, 2376.2, 2371.6, 2375.6),
    ]

    result = _detect(rows, htf_bias="bullish")

    assert result["raid_detected"] is True
    assert result["raid_type"] == "asian_high_breakout_continuation"
    assert result["direction"] == "bullish_continuation"
    assert result["reclaim_status"] == "accepted_above_asian_high"
    assert result["direction"] != "bearish"


def test_messy_asian_range_rejects_london_raid_model() -> None:
    rows = [
        _row(0, 450, 2358.8, 2359.2, 2353.8, 2357.4),
        _row(1, 455, 2357.4, 2360.8, 2356.9, 2360.6),
        _row(2, 460, 2360.6, 2365.4, 2360.4, 2364.8),
    ]

    result = _detect(rows, asian=_asian_range(quality=3.2))

    assert result["raid_detected"] is False
    assert result["valid_setup"] is False
    assert result["raid_type"] == "messy_asian_range"
    assert result["quality_score"] <= 4.0
    assert "messy_asian_range_do_not_force_setup" in result["failed_requirements"]
