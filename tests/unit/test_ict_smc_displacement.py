from datetime import datetime, timedelta

from src.analytics.ict_smc.displacement import detect_displacement


def _candle(index, open_, high, low, close):
    return {
        "index": index,
        "timestamp": datetime(2026, 6, 5) + timedelta(minutes=index),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100,
        "is_closed": True,
    }


def _structure(direction, event_type, index, broken_level=None):
    return {
        "direction": direction,
        "event_type": event_type,
        "confirmation_candle_index": index,
        "broken_level": broken_level,
    }


def _sweep(side, index):
    return {
        "sweep_type": f"{side}_liquidity_sweep",
        "sweep_candle_index": index,
    }


def test_valid_bullish_single_candle_displacement_with_structure_and_fvg() -> None:
    candles = [
        _candle(0, 100.0, 101.0, 99.0, 100.5),
        _candle(1, 100.5, 101.5, 100.0, 101.0),
        _candle(2, 101.0, 102.0, 100.5, 101.5),
        _candle(3, 101.5, 102.2, 101.0, 101.8),
        _candle(4, 101.8, 102.0, 101.2, 101.7),
        _candle(5, 101.7, 110.0, 101.2, 108.8),
        _candle(6, 108.4, 111.0, 104.0, 109.0),
    ]

    event = next(
        item
        for item in detect_displacement(
            candles,
            atr_period=3,
            multiplier=1.5,
            structure_events=[_structure("bullish", "MSS", 5, broken_level=102.2)],
            symbol="XAUUSD",
            timeframe="1m",
        )
        if item["direction"] == "bullish" and item["start_index"] == 5 and item["end_index"] == 5
    )

    assert event["displacement_mode"] == "single_candle"
    assert event["structure_broken"] is True
    assert event["fvg_created"] is True
    assert event["entry_allowed_from_displacement_alone"] is False
    assert 8.0 <= event["strength_score"] <= 10.0


def test_valid_bearish_multi_candle_displacement_with_shallow_pullbacks() -> None:
    candles = [
        _candle(0, 212.0, 213.0, 211.0, 212.4),
        _candle(1, 212.4, 213.2, 211.8, 212.7),
        _candle(2, 212.7, 213.0, 211.5, 212.2),
        _candle(3, 210.0, 211.0, 207.0, 208.5),
        _candle(4, 207.5, 208.0, 204.0, 204.5),
        _candle(5, 204.5, 205.0, 201.0, 201.5),
        _candle(6, 201.5, 202.0, 198.0, 198.5),
    ]

    event = next(
        item
        for item in detect_displacement(
            candles,
            atr_period=3,
            multiplier=1.5,
            structure_events=[_structure("bearish", "MSS", 6, broken_level=200.0)],
            symbol="XAUUSD",
            timeframe="1m",
        )
        if item["direction"] == "bearish" and item["displacement_mode"] == "multi_candle" and item["start_index"] == 3 and item["end_index"] == 6
    )

    assert event["structure_broken"] is True
    assert event["fvg_created"] is True
    assert event["metrics"]["directional_candle_ratio"] >= 0.75
    assert event["metrics"]["max_pullback_ratio"] <= 0.35
    assert 8.0 <= event["strength_score"] <= 10.0


def test_large_wick_is_not_treated_as_clean_displacement() -> None:
    candles = [
        _candle(0, 100.0, 101.0, 99.0, 100.2),
        _candle(1, 100.2, 101.2, 99.8, 100.4),
        _candle(2, 100.4, 101.0, 99.9, 100.3),
        _candle(3, 100.0, 110.0, 90.0, 101.0),
    ]

    event = next(
        item
        for item in detect_displacement(candles, atr_period=3, multiplier=1.5, include_weak=True)
        if item["start_index"] == 3 and item["end_index"] == 3
    )

    assert event["strength_score"] <= 3.0
    assert "large_range_without_body_dominance" in event["warnings"]
    assert event["fvg_created"] is False
    assert event["structure_broken"] is False


def test_displacement_without_structure_or_fvg_is_capped_as_moderate_confirmation() -> None:
    candles = [
        _candle(0, 100.0, 101.0, 99.8, 100.3),
        _candle(1, 100.3, 101.2, 100.0, 100.6),
        _candle(2, 100.6, 101.0, 100.2, 100.5),
        _candle(3, 100.5, 101.2, 100.1, 100.8),
        _candle(4, 100.0, 108.0, 99.0, 106.7),
    ]

    event = next(
        item
        for item in detect_displacement(candles, atr_period=3, multiplier=1.5)
        if item["direction"] == "bullish" and item["start_index"] == 4 and item["end_index"] == 4
    )

    assert event["structure_broken"] is False
    assert event["fvg_created"] is False
    assert 5.0 <= event["strength_score"] <= 6.5
    assert "displacement_without_structure_break_or_fvg_is_confirmation_only" in event["warnings"]


def test_high_quality_post_sweep_bullish_displacement_scores_very_strong() -> None:
    candles = [
        _candle(0, 100.0, 101.0, 99.0, 100.4),
        _candle(1, 100.4, 101.2, 99.8, 100.8),
        _candle(2, 100.8, 101.6, 100.1, 100.9),
        _candle(3, 100.9, 101.4, 99.9, 100.2),
        _candle(4, 100.2, 100.5, 97.5, 99.8),
        _candle(5, 99.8, 111.0, 99.4, 110.0),
        _candle(6, 109.6, 112.0, 103.0, 111.0),
    ]

    event = next(
        item
        for item in detect_displacement(
            candles,
            atr_period=3,
            multiplier=1.5,
            structure_events=[_structure("bullish", "MSS", 5, broken_level=101.6)],
            liquidity_sweeps=[_sweep("sell_side", 4)],
            symbol="XAUUSD",
            timeframe="1m",
        )
        if item["direction"] == "bullish" and item["start_index"] == 5 and item["end_index"] == 5
    )

    assert event["liquidity_sweep_before"] is True
    assert event["sweep_type"] == "sell_side_liquidity_sweep"
    assert event["structure_event_type"] == "bullish_MSS"
    assert event["fvg_created"] is True
    assert 9.0 <= event["strength_score"] <= 10.0
