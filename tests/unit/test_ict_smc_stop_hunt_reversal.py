from datetime import datetime, timedelta, timezone

from src.analytics.ict_smc.stop_hunt_reversal import detect_stop_hunt_reversal


def _row(index, open_p, high_p, low_p, close_p, is_closed=True):
    return {
        "index": index,
        "timestamp": datetime(2026, 6, 4, tzinfo=timezone.utc) + timedelta(minutes=index * 5),
        "symbol": "XAUUSD",
        "timeframe": "5m",
        "open": open_p,
        "high": high_p,
        "low": low_p,
        "close": close_p,
        "volume": 100 + index,
        "is_closed": is_closed,
    }


def _level(level_id, level_type, direction, price, index=0, quality=8.0):
    return {
        "level_id": level_id,
        "level_type": level_type,
        "direction": direction,
        "price": price,
        "zone_low": price - 0.2,
        "zone_mid": price,
        "zone_high": price + 0.2,
        "index": index,
        "timestamp": datetime(2026, 6, 4, tzinfo=timezone.utc),
        "timeframe": "5m",
        "strength_score": quality,
        "quality_score": quality,
        "swept_status": "unswept",
    }


def _detect(rows, levels):
    return detect_stop_hunt_reversal(
        rows,
        levels,
        sweep_buffer=0.1,
        close_buffer=0.1,
        break_buffer=0.1,
        stop_buffer=0.3,
        min_displacement_range_ratio=0.5,
        minimum_rr=0.8,
    )


def test_valid_bullish_stop_hunt_reversal_after_prior_low_sweep() -> None:
    rows = [
        _row(0, 101.0, 102.2, 100.5, 101.6),
        _row(1, 101.6, 102.0, 100.4, 100.8),
        _row(2, 100.8, 100.9, 99.4, 100.4),
        _row(3, 100.4, 101.2, 100.2, 100.9),
        _row(4, 101.3, 103.0, 101.5, 102.8),
    ]
    levels = [_level("PRIOR_LOW", "previous_swing_low", "sell_side", 100.0)]

    result = _detect(rows, levels)

    assert result["stop_hunt_detected"] is True
    assert result["stop_hunt_type"] == "bullish_stop_hunt_reversal"
    assert result["swept_side"] == "sell_side"
    assert result["reclaim_status"] == "reclaimed_back_above_prior_low"
    assert result["mss_confirmed"] is True
    assert result["entry_zone"]["entry_zone_type"] == "bullish_fvg"
    assert result["target_liquidity"]["target_side"] == "buy_side"
    assert result["invalidation_level"] < result["sweep"]["sweep_extreme"]
    assert 7.0 <= result["confidence_score"] <= 10.0
    assert result["entry_allowed_from_stop_hunt_alone"] is False


def test_valid_bearish_stop_hunt_reversal_after_prior_high_sweep() -> None:
    rows = [
        _row(0, 109.0, 109.7, 108.0, 108.8),
        _row(1, 108.8, 109.4, 107.6, 108.4),
        _row(2, 108.4, 110.8, 109.6, 109.8),
        _row(3, 109.8, 110.0, 108.6, 108.9),
        _row(4, 108.5, 108.7, 106.4, 106.8),
    ]
    levels = [_level("PRIOR_HIGH", "previous_swing_high", "buy_side", 110.0)]

    result = _detect(rows, levels)

    assert result["stop_hunt_detected"] is True
    assert result["stop_hunt_type"] == "bearish_stop_hunt_reversal"
    assert result["swept_side"] == "buy_side"
    assert result["reclaim_status"] == "rejected_back_below_prior_high"
    assert result["mss_confirmed"] is True
    assert result["entry_zone"]["entry_zone_type"] == "bearish_fvg"
    assert result["target_liquidity"]["target_side"] == "sell_side"
    assert result["invalidation_level"] > result["sweep"]["sweep_extreme"]
    assert 7.0 <= result["confidence_score"] <= 10.0


def test_sweep_without_mss_remains_candidate_only() -> None:
    rows = [
        _row(0, 101.0, 102.0, 100.5, 101.2),
        _row(1, 101.2, 101.6, 99.4, 100.4),
        _row(2, 100.4, 101.0, 100.1, 100.6),
        _row(3, 100.6, 101.1, 100.2, 100.7),
    ]
    levels = [_level("PRIOR_LOW", "previous_swing_low", "sell_side", 100.0)]

    result = _detect(rows, levels)

    assert result["stop_hunt_detected"] is False
    assert result["valid_setup"] is False
    assert result["stop_hunt_type"] == "bullish_stop_hunt_candidate"
    assert result["mss_confirmed"] is False
    assert result["entry_zone"] is None
    assert 3.0 <= result["confidence_score"] <= 5.0


def test_accepted_breakout_is_not_bearish_stop_hunt_reversal() -> None:
    rows = [
        _row(0, 108.5, 109.3, 107.8, 108.9),
        _row(1, 108.9, 110.9, 108.7, 110.7),
        _row(2, 110.7, 111.4, 110.4, 111.1),
    ]
    levels = [_level("PRIOR_HIGH", "previous_swing_high", "buy_side", 110.0)]

    result = _detect(rows, levels)

    assert result["stop_hunt_detected"] is False
    assert result["classification"] == "bullish_breakout_continuation"
    assert result["reclaim_status"] == "accepted_above_prior_high"
    assert result["mss_confirmed"] is False
    assert result["entry_zone"] is None
    assert result["confidence_score"] <= 3.0


def test_weak_prior_level_is_classified_as_noise() -> None:
    rows = [
        _row(0, 108.5, 109.3, 107.8, 108.9),
        _row(1, 108.9, 110.1, 108.7, 109.1),
    ]
    levels = [_level("MICRO_HIGH", "micro_swing_high", "buy_side", 110.0, quality=2.0)]

    result = _detect(rows, levels)

    assert result["stop_hunt_detected"] is False
    assert result["classification"] == "weak_prior_level_noise"
    assert result["confidence_score"] <= 3.0
    assert "weak_prior_level_noise" in result["warnings"]
