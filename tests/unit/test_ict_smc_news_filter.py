from datetime import datetime, timedelta

from src.analytics.ict_smc.news_filter import (
    PostNewsDirection,
    PostNewsSetupStatus,
    detect_post_news_smc_setup,
    is_news_restricted_time,
)


NEWS_TIME = datetime(2026, 6, 10, 12, 30)


def _news_event(**overrides):
    event = {
        "event_id": "USD_CPI_2026_06_10",
        "event_name": "CPI Inflation",
        "currency": "USD",
        "impact": "high",
        "timestamp": NEWS_TIME,
        "timezone": "UTC",
        "affected_symbols": ["XAUUSD"],
    }
    event.update(overrides)
    return event


def _row(minutes, open_, high, low, close, *, index=None, closed=True):
    return {
        "timestamp": NEWS_TIME + timedelta(minutes=minutes),
        "index": minutes if index is None else index,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100,
        "is_closed": closed,
        "timeframe": "M5",
        "symbol": "XAUUSD",
    }


def _calm_pre_news_rows():
    rows = []
    for idx, minute in enumerate(range(-45, -5, 5)):
        base = 100.0 + (idx % 2) * 0.1
        rows.append(_row(minute, base, base + 0.3, base - 0.3, base + 0.05))
    return rows


def _safe_spread():
    return {
        "current_spread": 0.18,
        "average_spread": 0.16,
        "max_allowed_spread": 0.45,
        "estimated_slippage": 0.04,
        "max_allowed_slippage": 0.30,
    }


def _bullish_post_news_rows():
    rows = _calm_pre_news_rows()
    rows.extend(
        [
            _row(0, 100.0, 101.0, 95.0, 99.0),
            _row(5, 99.0, 100.5, 96.0, 98.4),
            _row(20, 98.4, 98.8, 97.8, 98.2),
            _row(30, 95.2, 96.2, 94.5, 95.6),
            _row(35, 95.7, 96.0, 95.4, 95.8),
            _row(40, 98.0, 100.3, 97.5, 100.0),
            _row(45, 99.9, 100.6, 99.4, 100.4),
            _row(50, 100.3, 100.9, 99.8, 100.6),
        ]
    )
    return rows


def _bearish_post_news_rows():
    rows = _calm_pre_news_rows()
    rows.extend(
        [
            _row(0, 100.0, 105.0, 99.0, 101.0),
            _row(5, 101.0, 104.5, 99.5, 102.0),
            _row(20, 102.0, 102.5, 101.2, 101.8),
            _row(30, 104.7, 105.5, 103.8, 104.4),
            _row(35, 104.1, 104.6, 103.9, 104.0),
            _row(40, 102.0, 102.8, 100.4, 101.0),
            _row(45, 101.2, 101.6, 100.2, 100.8),
            _row(50, 100.8, 101.3, 100.0, 100.4),
        ]
    )
    return rows


def test_cpi_blackout_blocks_new_entries() -> None:
    result = is_news_restricted_time(
        NEWS_TIME - timedelta(minutes=8),
        [_news_event()],
        before_minutes=15,
        after_minutes=15,
    )

    assert result["restricted"] is True
    assert result["reason"] == "inside_high_impact_news_blackout"
    assert result["minutes_to_news"] == 8
    assert result["trade_permissions"]["new_entries_allowed"] is False
    assert result["trade_permissions"]["cancel_pending_orders"] is True


def test_first_news_spike_fvg_is_blocked_until_volatility_stabilizes() -> None:
    rows = _calm_pre_news_rows()
    rows.extend(
        [
            _row(0, 100.0, 106.0, 94.0, 101.0),
            _row(5, 101.0, 104.0, 96.0, 99.0),
            _row(20, 99.0, 100.0, 98.5, 99.2),
            _row(30, 99.2, 107.8, 98.9, 107.0),
            _row(35, 107.1, 107.6, 101.0, 102.0),
            _row(40, 102.0, 108.5, 101.5, 107.8),
        ]
    )

    result = detect_post_news_smc_setup(
        rows,
        _news_event(),
        after_minutes=15,
        stabilization_minutes=10,
        news_range_minutes=5,
        spread_data=_safe_spread(),
    )

    assert result["post_news_setup_detected"] is False
    assert result["reason"] == PostNewsSetupStatus.FIRST_SPIKE_UNSAFE.value
    assert result["trade_permissions"]["new_entries_allowed"] is False
    assert "first news spike" in " ".join(result["warnings"]).lower()


def test_valid_post_news_bullish_smc_setup_after_sweep_reclaim_mss_and_fvg() -> None:
    result = detect_post_news_smc_setup(
        _bullish_post_news_rows(),
        _news_event(),
        after_minutes=15,
        stabilization_minutes=10,
        news_range_minutes=5,
        spread_data=_safe_spread(),
        htf_bias="bullish",
        min_rr=1.2,
    )

    assert result["post_news_setup_detected"] is True
    assert result["direction"] == PostNewsDirection.BULLISH.value
    assert result["sweep"]["swept_side"] == "sell_side"
    assert result["confirmation"]["mss_confirmed"] is True
    assert result["confirmation"]["entry_zone_type"] == "bullish_fvg"
    assert result["risk_plan"]["risk_reward"] >= 1.2
    assert result["trade_permissions"]["new_entries_allowed"] is True


def test_valid_post_news_bearish_smc_setup_after_sweep_rejection_mss_and_fvg() -> None:
    result = detect_post_news_smc_setup(
        _bearish_post_news_rows(),
        _news_event(),
        after_minutes=15,
        stabilization_minutes=10,
        news_range_minutes=5,
        spread_data=_safe_spread(),
        htf_bias="bearish",
        min_rr=1.2,
    )

    assert result["post_news_setup_detected"] is True
    assert result["direction"] == PostNewsDirection.BEARISH.value
    assert result["sweep"]["swept_side"] == "buy_side"
    assert result["confirmation"]["mss_confirmed"] is True
    assert result["confirmation"]["entry_zone_type"] == "bearish_fvg"
    assert result["risk_plan"]["risk_reward"] >= 1.2
    assert result["trade_permissions"]["new_entries_allowed"] is True


def test_spread_that_remains_too_high_blocks_post_news_entry() -> None:
    result = detect_post_news_smc_setup(
        _bullish_post_news_rows(),
        _news_event(),
        after_minutes=15,
        stabilization_minutes=10,
        news_range_minutes=5,
        spread_data={
            "current_spread": 0.95,
            "average_spread": 0.18,
            "max_allowed_spread": 0.45,
            "estimated_slippage": 0.10,
            "max_allowed_slippage": 0.30,
        },
        htf_bias="bullish",
    )

    assert result["post_news_setup_detected"] is False
    assert result["reason"] == PostNewsSetupStatus.SPREAD_TOO_HIGH.value
    assert result["news_filter_status"]["spread_status"] == "wide"
    assert result["confidence_score"] <= 4
