from datetime import datetime, timedelta

from src.analytics.ict_smc.breaker_block import detect_breaker_blocks


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


def _bearish_ob(created_index=2, quality=8.8):
    return {
        "ob_id": f"OB_BEAR_{created_index}",
        "direction": "bearish",
        "zone_high": 110.0,
        "zone_low": 108.0,
        "mean_threshold": 109.0,
        "created_index": created_index,
        "quality_score": quality,
        "created_by_event": "bearish_mss_after_buy_side_sweep",
        "fvg_context": {"fvg_created_after_displacement": True},
        "premium_discount_context": {"poi_location": "discount"},
    }


def _bullish_ob(created_index=2, quality=8.8):
    return {
        "ob_id": f"OB_BULL_{created_index}",
        "direction": "bullish",
        "zone_high": 102.0,
        "zone_low": 100.0,
        "mean_threshold": 101.0,
        "created_index": created_index,
        "quality_score": quality,
        "created_by_event": "bullish_mss_after_sell_side_sweep",
        "fvg_context": {"fvg_created_after_displacement": True},
        "premium_discount_context": {"poi_location": "premium"},
    }


def _structure(direction, event_type, index, quality=8.5):
    return {
        "direction": direction,
        "event_type": event_type,
        "confirmation_candle_index": index,
        "quality_score": quality,
    }


def test_valid_bullish_breaker_from_failed_bearish_ob() -> None:
    candles = [
        _candle(0, 106.0, 107.0, 105.5, 106.5),
        _candle(1, 106.5, 108.8, 106.2, 108.2),
        _candle(2, 108.2, 110.0, 107.8, 108.6),
        _candle(3, 109.2, 112.6, 108.8, 111.9),
        _candle(4, 111.9, 112.4, 110.8, 111.6),
        _candle(5, 109.2, 112.1, 108.7, 111.4),
    ]

    breakers = detect_breaker_blocks(
        candles,
        [_bearish_ob()],
        [_structure("bullish", "MSS", 3)],
        symbol="XAUUSD",
        timeframe="5m",
    )
    breaker = breakers[0]

    assert breaker["breaker_type"] == "bullish_breaker"
    assert breaker["original_ob_id"] == "OB_BEAR_2"
    assert breaker["failed_at_index"] == 3
    assert breaker["retest_status"] == "confirmed_reaction"
    assert breaker["confirmed_breaker"] is True
    assert breaker["trapped_side"] == "sellers"
    assert breaker["entry_allowed_after_reaction"] is True
    assert 8.0 <= breaker["confidence_score"] <= 10.0


def test_valid_bearish_breaker_from_failed_bullish_ob() -> None:
    candles = [
        _candle(0, 105.0, 105.5, 103.8, 104.8),
        _candle(1, 104.8, 105.1, 101.0, 102.2),
        _candle(2, 102.2, 102.0, 100.0, 101.6),
        _candle(3, 101.2, 101.4, 97.7, 98.2),
        _candle(4, 98.2, 99.0, 97.9, 98.5),
        _candle(5, 100.8, 101.4, 98.0, 98.7),
    ]

    breakers = detect_breaker_blocks(
        candles,
        [_bullish_ob()],
        [_structure("bearish", "BOS", 3)],
        symbol="XAUUSD",
        timeframe="5m",
    )
    breaker = breakers[0]

    assert breaker["breaker_type"] == "bearish_breaker"
    assert breaker["original_ob_id"] == "OB_BULL_2"
    assert breaker["failed_at_index"] == 3
    assert breaker["retest_status"] == "confirmed_reaction"
    assert breaker["confirmed_breaker"] is True
    assert breaker["trapped_side"] == "buyers"
    assert breaker["entry_allowed_after_reaction"] is True
    assert 8.0 <= breaker["confidence_score"] <= 10.0


def test_wick_only_failure_attempt_is_not_confirmed_breaker() -> None:
    candles = [
        _candle(0, 106.0, 107.0, 105.5, 106.5),
        _candle(1, 106.5, 108.8, 106.2, 108.2),
        _candle(2, 108.2, 110.0, 107.8, 108.6),
        _candle(3, 109.0, 111.0, 108.7, 109.4),
    ]

    breaker = detect_breaker_blocks(candles, [_bearish_ob()], [], symbol="XAUUSD", timeframe="5m")[0]

    assert breaker["breaker_type"] == "bullish_breaker_attempt"
    assert breaker["confirmed_breaker"] is False
    assert breaker["failed_at_index"] is None
    assert breaker["retest_status"] == "wick_only_failure_attempt"
    assert breaker["confidence_score"] <= 3.0
    assert "no_acceptance_close_beyond_ob" in breaker["warnings"]


def test_breaker_candidate_without_retest_is_moderate_and_not_entry_ready() -> None:
    candles = [
        _candle(0, 105.0, 105.5, 103.8, 104.8),
        _candle(1, 104.8, 105.1, 101.0, 102.2),
        _candle(2, 102.2, 102.0, 100.0, 101.6),
        _candle(3, 101.2, 101.4, 97.7, 98.2),
        _candle(4, 98.2, 99.0, 97.6, 98.0),
        _candle(5, 98.0, 99.4, 97.2, 97.8),
    ]

    breaker = detect_breaker_blocks(
        candles,
        [_bullish_ob()],
        [_structure("bearish", "BOS", 3)],
        symbol="XAUUSD",
        timeframe="5m",
    )[0]

    assert breaker["breaker_type"] == "bearish_breaker"
    assert breaker["retest_status"] == "not_retested"
    assert 5.0 <= breaker["confidence_score"] <= 6.5
    assert breaker["entry_allowed_after_reaction"] is False


def test_failed_bearish_breaker_after_retest_blocks_entry() -> None:
    candles = [
        _candle(0, 105.0, 105.5, 103.8, 104.8),
        _candle(1, 104.8, 105.1, 101.0, 102.2),
        _candle(2, 102.2, 102.0, 100.0, 101.6),
        _candle(3, 101.2, 101.4, 97.7, 98.2),
        _candle(4, 98.2, 103.0, 98.0, 102.4),
    ]

    breaker = detect_breaker_blocks(
        candles,
        [_bullish_ob()],
        [_structure("bearish", "BOS", 3)],
        symbol="XAUUSD",
        timeframe="5m",
    )[0]

    assert breaker["breaker_type"] == "bearish_breaker"
    assert breaker["retest_status"] == "failed"
    assert breaker["confidence_score"] <= 3.0
    assert breaker["entry_allowed_after_reaction"] is False
    assert "breaker_failed_after_retest" in breaker["warnings"]
