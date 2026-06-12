from datetime import datetime, timedelta

from src.analytics.ict_smc.volume_confirmation import score_volume_confirmation


BASE_TIME = datetime(2026, 6, 4, 9, 0)


def _row(index, open_, high, low, close, volume, *, closed=True):
    return {
        "timestamp": BASE_TIME + timedelta(minutes=5 * index),
        "index": index,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "is_closed": closed,
    }


def _baseline(count=20, volume=100, start=0):
    rows = []
    for offset in range(count):
        index = start + offset
        close = 100.0 + (offset % 3) * 0.1
        rows.append(_row(index, close - 0.2, close + 0.5, close - 0.5, close, volume))
    return rows


def test_valid_bullish_sweep_with_volume_confirmation() -> None:
    rows = _baseline()
    rows.append(_row(20, 100.0, 101.2, 98.0, 101.0, 220))
    rows.append(_row(21, 101.0, 103.2, 100.7, 102.8, 190))
    rows.append(_row(22, 102.8, 103.1, 102.4, 102.9, 0, closed=False))

    result = score_volume_confirmation(
        rows,
        {
            "event_id": "SWEEP_BULL_001",
            "event_type": "liquidity_sweep",
            "direction": "bullish",
            "candle_indices": [20],
            "level_price": 99.5,
            "follow_through_volume_confirmed": True,
        },
    )

    assert result["event_type"] == "liquidity_sweep"
    assert result["direction"] == "bullish"
    assert result["volume_score"] >= 8
    assert result["volume_pattern"]["absorption_detected"] is True
    assert result["entry_allowed_from_volume_alone"] is False
    assert "Volume supports bullish liquidity sweep" in result["interpretation"]


def test_valid_bearish_displacement_with_volume_confirmation() -> None:
    rows = _baseline()
    rows.extend(
        [
            _row(20, 100.0, 100.3, 97.3, 97.6, 180),
            _row(21, 97.5, 97.8, 94.8, 95.1, 190),
            _row(22, 95.1, 95.5, 92.7, 93.0, 170),
        ]
    )

    result = score_volume_confirmation(
        rows,
        {
            "event_id": "DISP_BEAR_001",
            "event_type": "displacement",
            "direction": "bearish",
            "candle_indices": [20, 21, 22],
            "structure_break_confirmed": True,
            "fvg_created": True,
        },
    )

    assert result["volume_score"] >= 8
    assert result["confirmation_status"] in {
        "strong_volume_confirmation",
        "excellent_volume_confirmation",
    }
    assert result["volume_pattern"]["directional_volume_support"] is True
    assert result["volume_pattern"]["structure_break_supported"] is True
    assert "bearish displacement" in result["interpretation"]


def test_low_volume_bullish_fvg_retracement() -> None:
    rows = _baseline()
    rows.extend(
        [
            _row(20, 100.0, 102.8, 99.8, 102.4, 250),
            _row(21, 102.4, 105.0, 102.2, 104.8, 260),
            _row(22, 104.8, 107.0, 104.4, 106.5, 240),
            _row(23, 106.4, 106.6, 105.8, 106.0, 120),
            _row(24, 106.0, 106.2, 105.2, 105.5, 110),
            _row(25, 105.5, 105.9, 105.0, 105.8, 125),
            _row(26, 105.8, 108.0, 105.7, 107.7, 210),
        ]
    )

    result = score_volume_confirmation(
        rows,
        {
            "event_id": "FVG_RETEST_BULL_001",
            "event_type": "fvg_retracement",
            "direction": "bullish",
            "candle_indices": [23, 24, 25],
            "displacement_indices": [20, 21, 22],
            "reaction_indices": [26],
            "zone_invalidated": False,
            "continuation_confirmed": True,
        },
    )

    assert 7 <= result["volume_score"] <= 9
    assert result["confirmation_status"] == "healthy_low_volume_pullback"
    assert result["volume_pattern"]["low_volume_pullback"] is True
    assert result["volume_pattern"]["reaction_volume_increased"] is True


def test_high_volume_breakdown_contradicts_bullish_sweep() -> None:
    rows = _baseline()
    rows.append(_row(20, 100.0, 100.4, 97.5, 98.0, 230))
    rows.append(_row(21, 98.0, 98.3, 95.0, 95.4, 210))

    result = score_volume_confirmation(
        rows,
        {
            "event_id": "SWEEP_BULL_WEAK_002",
            "event_type": "liquidity_sweep",
            "direction": "bullish",
            "candle_indices": [20],
            "level_price": 99.5,
        },
    )

    assert result["volume_score"] <= 4
    assert result["confirmation_status"] in {
        "weak_or_contradictory_volume",
        "weak_volume_confirmation",
    }
    assert result["volume_pattern"]["volume_contradiction"] is True
    assert "contradicts" in result["interpretation"]


def test_news_spike_volume_is_warning_capped() -> None:
    rows = _baseline()
    rows.append(_row(20, 100.0, 106.0, 94.0, 100.2, 520))

    result = score_volume_confirmation(
        rows,
        {
            "event_id": "NEWS_SPIKE_001",
            "event_type": "rejection",
            "direction": "bullish",
            "candle_indices": [20],
            "level_price": 97.0,
            "news_flag": True,
        },
    )

    assert result["volume_score"] <= 4
    assert result["confirmation_status"] == "news_spike_warning"
    assert "do_not_treat_news_spike_as_normal_smc_confirmation" in result["warnings"]
    assert "Abnormal news-driven volume" in result["interpretation"]
