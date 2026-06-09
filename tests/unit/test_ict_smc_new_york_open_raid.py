from datetime import datetime, timedelta, timezone

from src.analytics.ict_smc.new_york_open_raid import detect_new_york_open_raid


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


def _levels(london_trend="unknown"):
    return {
        "ny_window": {
            "window_name": "new_york_open",
            "start_time": "13:00",
            "end_time": "16:00",
            "timezone": "UTC",
            "allowed_days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
            "strict_mode": True,
            "post_window_buffer_minutes": 30,
        },
        "liquidity_tolerance": 0.0,
        "london_high": 2372.40,
        "london_low": 2356.20,
        "asian_high": 2368.40,
        "asian_low": 2354.40,
        "pdh": 2381.40,
        "pdl": 2348.80,
        "london_high_quality_score": 8.5,
        "london_low_quality_score": 8.5,
        "pdh_quality_score": 9.0,
        "pdl_quality_score": 9.0,
        "london_high_target_priority_score": 8.6,
        "london_low_target_priority_score": 8.6,
        "pdh_target_priority_score": 9.0,
        "pdl_target_priority_score": 9.0,
        "london_trend": london_trend,
    }


def _safe_news():
    return {
        "high_impact_news_nearby": False,
        "allow_trading_during_news": False,
        "spread_high": False,
    }


def _detect(rows, levels=None, news=None, htf_bias="neutral"):
    return detect_new_york_open_raid(
        rows,
        levels or _levels(),
        news or _safe_news(),
        htf_bias,
        sweep_buffer=0.25,
        close_buffer=0.25,
        break_buffer=0.25,
        stop_buffer=0.50,
        min_displacement_range_ratio=0.5,
    )


def test_valid_bullish_ny_raid_sweeps_london_low_and_targets_buy_side() -> None:
    rows = [
        _row(0, 785, 2360.5, 2361.6, 2359.7, 2360.2),
        _row(1, 815, 2358.1, 2358.8, 2352.9, 2357.4),
        _row(2, 820, 2357.4, 2361.3, 2356.9, 2360.7),
        _row(3, 825, 2360.7, 2366.4, 2359.8, 2365.8),
    ]

    result = _detect(rows, htf_bias="bullish")

    assert result["valid_setup"] is True
    assert result["setup_type"] == "ny_sell_side_sweep_bullish_reversal"
    assert result["swept_level"]["level_type"] == "london_low"
    assert result["direction"] == "bullish"
    assert result["mss_confirmed"] is True
    assert result["fvg_entry"]["entry_zone_type"] == "bullish_fvg"
    assert result["risk_plan"]["risk_reward"] >= 0.8
    assert result["target_liquidity"]["direction"] == "buy_side"
    assert 7.0 <= result["confidence_score"] <= 10.0
    assert result["entry_allowed_from_new_york_raid_alone"] is False


def test_valid_bearish_ny_raid_sweeps_pdh_and_targets_sell_side() -> None:
    rows = [
        _row(0, 785, 2376.0, 2377.2, 2375.1, 2376.8),
        _row(1, 875, 2380.8, 2385.1, 2378.9, 2380.6),
        _row(2, 880, 2380.6, 2381.0, 2377.1, 2377.6),
        _row(3, 885, 2376.3, 2376.5, 2369.6, 2370.8),
    ]

    result = _detect(rows, htf_bias="bearish")

    assert result["valid_setup"] is True
    assert result["setup_type"] == "ny_buy_side_sweep_bearish_reversal"
    assert result["swept_level"]["level_type"] == "previous_day_high"
    assert result["direction"] == "bearish"
    assert result["mss_confirmed"] is True
    assert result["fvg_entry"]["entry_zone_type"] == "bearish_fvg"
    assert result["target_liquidity"]["direction"] == "sell_side"
    assert 7.0 <= result["confidence_score"] <= 10.0


def test_ny_continuation_after_bullish_london_trend_does_not_require_mss() -> None:
    rows = [
        _row(0, 785, 2368.2, 2370.0, 2367.8, 2369.6),
        _row(1, 820, 2372.5, 2375.1, 2372.3, 2374.7),
        _row(2, 825, 2374.7, 2375.6, 2374.2, 2375.2),
        _row(3, 830, 2375.2, 2379.8, 2375.8, 2379.2),
    ]

    result = _detect(rows, levels=_levels(london_trend="bullish"), htf_bias="bullish")

    assert result["valid_setup"] is True
    assert result["setup_type"] == "ny_buy_side_breakout_continuation"
    assert result["swept_level"]["level_type"] == "london_high"
    assert result["direction"] == "bullish_continuation"
    assert result["mss_confirmed"] is False
    assert result["mss"]["confirmation_type"] == "bos_acceptance_for_continuation"
    assert result["fvg_entry"]["entry_zone_type"] == "bullish_fvg"
    assert 6.5 <= result["confidence_score"] <= 10.0


def test_news_blackout_rejects_ny_open_raid_detection() -> None:
    rows = [
        _row(0, 815, 2358.1, 2385.1, 2352.9, 2368.2),
        _row(1, 820, 2368.2, 2370.0, 2351.2, 2362.0),
    ]
    news = {
        "high_impact_news_nearby": True,
        "allow_trading_during_news": False,
        "impact_level": "high",
        "currency": "USD",
        "news_name": "CPI",
        "minutes_to_news": 5,
    }

    result = _detect(rows, news=news, htf_bias="neutral")

    assert result["valid_setup"] is False
    assert result["classification"] == "news_blackout_no_trade"
    assert result["swept_level"] is None
    assert result["confidence_score"] <= 3.0
    assert result["news_status"] == "news_blackout"
    assert "Do not treat news spike as clean displacement" in result["warnings"]


def test_ny_sweep_without_mss_remains_candidate_not_valid_setup() -> None:
    rows = [
        _row(0, 785, 2368.2, 2370.0, 2367.8, 2369.6),
        _row(1, 835, 2371.6, 2375.1, 2370.8, 2371.9),
        _row(2, 840, 2371.9, 2372.6, 2370.9, 2371.4),
        _row(3, 845, 2371.4, 2372.2, 2370.4, 2371.0),
    ]

    result = _detect(rows, htf_bias="bearish")

    assert result["valid_setup"] is False
    assert result["setup_type"] == "ny_buy_side_sweep_candidate"
    assert result["swept_level"]["level_type"] == "london_high"
    assert result["direction"] == "bearish_candidate"
    assert result["mss_confirmed"] is False
    assert result["fvg_entry"] is None
    assert result["confidence_score"] <= 5.0
    assert "mss_not_confirmed_after_ny_raid" in result["failed_requirements"]
