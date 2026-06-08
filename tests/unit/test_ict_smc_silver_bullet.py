from datetime import datetime, timedelta, timezone

from src.analytics.ict_smc.silver_bullet import detect_silver_bullet_setup


def _row(index, minutes, open_p, high_p, low_p, close_p, timeframe="5m", is_closed=True):
    return {
        "index": index,
        "timestamp": datetime(2026, 6, 4, 10, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes),
        "symbol": "XAUUSD",
        "timeframe": timeframe,
        "open": open_p,
        "high": high_p,
        "low": low_p,
        "close": close_p,
        "volume": 100 + index,
        "is_closed": is_closed,
    }


def _window(start="10:00", end="11:00"):
    return {
        "window_name": "ny_am_silver_bullet",
        "start_time": start,
        "end_time": end,
        "timezone": "UTC",
        "allowed_days": ["Thursday"],
        "strict": True,
    }


def _liquidity_pools():
    return [
        {
            "liquidity_id": "SSL_ASIA_LOW",
            "liquidity_type": "asian_low",
            "direction": "sell_side",
            "zone_low": 2356.0,
            "zone_mid": 2356.2,
            "zone_high": 2356.4,
            "swept_status": "unswept",
            "quality_score": 8.0,
            "target_priority_score": 7.0,
        },
        {
            "liquidity_id": "BSL_ASIA_HIGH",
            "liquidity_type": "asian_high",
            "direction": "buy_side",
            "zone_low": 2377.8,
            "zone_mid": 2378.0,
            "zone_high": 2378.2,
            "swept_status": "unswept",
            "quality_score": 8.0,
            "target_priority_score": 8.0,
        },
    ]


def _bearish_liquidity_pools():
    return [
        {
            "liquidity_id": "BSL_SESSION_HIGH",
            "liquidity_type": "session_high",
            "direction": "buy_side",
            "zone_low": 2370.0,
            "zone_mid": 2370.2,
            "zone_high": 2370.4,
            "swept_status": "unswept",
            "quality_score": 8.0,
            "target_priority_score": 7.0,
        },
        {
            "liquidity_id": "SSL_SESSION_LOW",
            "liquidity_type": "session_low",
            "direction": "sell_side",
            "zone_low": 2349.8,
            "zone_mid": 2350.0,
            "zone_high": 2350.2,
            "swept_status": "unswept",
            "quality_score": 8.0,
            "target_priority_score": 8.0,
        },
    ]


def _valid_bullish_rows():
    return [
        _row(0, 0, 2358.0, 2360.0, 2357.2, 2359.0),
        _row(1, 5, 2358.0, 2359.0, 2355.4, 2356.8),
        _row(2, 10, 2356.8, 2363.8, 2356.6, 2363.4),
        _row(3, 15, 2363.4, 2368.0, 2361.2, 2366.5),
        _row(4, 20, 2361.0, 2364.5, 2360.5, 2362.4),
        _row(5, 25, 2362.4, 2369.0, 2362.1, 2368.5),
    ]


def _valid_bearish_rows():
    return [
        _row(0, 0, 2368.0, 2370.0, 2367.0, 2369.2),
        _row(1, 5, 2369.2, 2371.4, 2368.8, 2369.5),
        _row(2, 10, 2369.5, 2369.7, 2361.8, 2362.2),
        _row(3, 15, 2360.9, 2366.0, 2358.2, 2359.0),
        _row(4, 20, 2367.0, 2367.2, 2361.5, 2363.0),
        _row(5, 25, 2363.0, 2363.2, 2352.0, 2353.4),
    ]


def test_valid_bullish_silver_bullet_requires_full_sequence() -> None:
    result = detect_silver_bullet_setup(
        _valid_bullish_rows(),
        _window(),
        _liquidity_pools(),
        htf_bias="bullish",
        sweep_buffer=0.25,
        close_buffer=0.25,
        min_displacement_atr=0.90,
    )

    assert result["valid_setup"] is True
    assert result["classification"] == "bullish_silver_bullet"
    assert result["direction"] == "bullish"
    assert result["sweep"]["swept_side"] == "sell_side"
    assert result["sweep"]["reclaim_status"] == "strong_reclaim"
    assert result["displacement"]["displacement_confirmed"] is True
    assert result["fvg_zone"]["fvg_type"] == "bullish_fvg"
    assert result["fvg_zone"]["retest_status"] == "confirmed_reaction"
    assert result["trade_plan"]["target_liquidity_id"] == "BSL_ASIA_HIGH"
    assert result["rr"] >= 1.0
    assert result["score"] >= 7.0
    assert result["entry_allowed_from_silver_bullet_alone"] is False


def test_valid_bearish_silver_bullet_requires_full_sequence() -> None:
    result = detect_silver_bullet_setup(
        _valid_bearish_rows(),
        _window(),
        _bearish_liquidity_pools(),
        htf_bias="bearish",
        sweep_buffer=0.25,
        close_buffer=0.25,
        min_displacement_atr=0.90,
    )

    assert result["valid_setup"] is True
    assert result["classification"] == "bearish_silver_bullet"
    assert result["direction"] == "bearish"
    assert result["sweep"]["swept_side"] == "buy_side"
    assert result["sweep"]["reclaim_status"] == "strong_rejection"
    assert result["displacement"]["displacement_confirmed"] is True
    assert result["fvg_zone"]["fvg_type"] == "bearish_fvg"
    assert result["fvg_zone"]["retest_status"] == "confirmed_rejection"
    assert result["trade_plan"]["target_liquidity_id"] == "SSL_SESSION_LOW"
    assert result["rr"] >= 1.0
    assert result["score"] >= 7.0


def test_fvg_inside_window_without_sweep_is_not_silver_bullet() -> None:
    rows = [
        _row(0, 0, 2360.0, 2361.0, 2359.0, 2360.5),
        _row(1, 5, 2360.5, 2368.0, 2360.2, 2367.4),
        _row(2, 10, 2367.4, 2370.0, 2363.0, 2368.5),
    ]

    result = detect_silver_bullet_setup(
        rows,
        _window(),
        _liquidity_pools(),
        htf_bias="bullish",
        sweep_buffer=0.25,
    )

    assert result["valid_setup"] is False
    assert result["classification"] == "fvg_only_no_sweep"
    assert result["score"] <= 5.0
    assert "Silver Bullet requires a liquidity sweep before FVG analysis" in result["warnings"]


def test_sweep_without_fvg_remains_incomplete() -> None:
    rows = [
        _row(0, 0, 2358.0, 2360.0, 2357.2, 2359.0),
        _row(1, 5, 2358.0, 2359.0, 2355.4, 2356.8),
        _row(2, 10, 2356.8, 2359.5, 2356.4, 2358.0),
        _row(3, 15, 2358.0, 2359.8, 2357.4, 2358.5),
    ]

    result = detect_silver_bullet_setup(
        rows,
        _window(),
        _liquidity_pools(),
        htf_bias="bullish",
        sweep_buffer=0.25,
        close_buffer=0.25,
    )

    assert result["valid_setup"] is False
    assert result["classification"] == "sweep_without_fvg"
    assert result["fvg_zone"] is None
    assert "no_valid_fvg_created_by_displacement" in result["failed_requirements"]


def test_perfect_sequence_outside_time_window_is_rejected() -> None:
    result = detect_silver_bullet_setup(
        _valid_bullish_rows(),
        _window(start="09:00", end="09:30"),
        _liquidity_pools(),
        htf_bias="bullish",
        sweep_buffer=0.25,
        min_displacement_atr=0.90,
    )

    assert result["valid_setup"] is False
    assert result["classification"] == "outside_time_window"
    assert result["time_window"]["window_valid"] is False
    assert "valid_price_action_but_not_silver_bullet_outside_window" in result["warnings"]
