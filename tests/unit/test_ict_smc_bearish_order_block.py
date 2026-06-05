from datetime import datetime, timedelta

from src.analytics.ict_smc.bearish_order_block import (
    BearishOBRetestStatus,
    detect_bearish_order_block,
    validate_bearish_ob_retest,
)


def _candle(index, open_, high, low, close):
    return {
        "index": index,
        "timestamp": datetime(2026, 6, 3) + timedelta(minutes=index),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100,
        "is_closed": True,
    }


def _bos(index):
    return {
        "direction": "bearish",
        "confirmation_candle_index": index,
        "broken_level": 105.0,
        "broken_swing_index": index - 4,
        "displacement_strength": "strong",
        "fvg_created": True,
        "quality_score": 8.0,
    }


def _mss(index):
    return {
        "direction": "bearish",
        "confirmation_candle_index": index,
        "broken_level": 105.0,
        "broken_swing_index": index - 4,
        "displacement_strength": "strong",
        "fvg_after_mss": True,
        "confidence_score": 8.7,
    }


def _buy_side_sweep(index):
    return {
        "direction": "bearish",
        "sweep_type": "buy_side_liquidity_sweep",
        "sweep_candle_index": index,
        "swept_liquidity_id": "LQ_EQH_001",
        "quality_score": 8.8,
    }


def _target(price=100.0):
    return {
        "liquidity_id": "LQ_SELL_SIDE_LOW",
        "liquidity_type": "equal_lows",
        "direction": "sell_side",
        "zone_mid": price,
        "quality_score": 8.0,
    }


def test_high_quality_bearish_ob_after_buy_side_sweep() -> None:
    candles = [
        _candle(0, 110.0, 110.5, 109.3, 109.7),
        _candle(1, 109.7, 110.3, 109.0, 109.5),
        _candle(2, 109.5, 111.4, 109.2, 110.4),
        _candle(3, 110.4, 111.0, 109.8, 110.8),
        _candle(4, 108.2, 108.4, 103.2, 103.6),
    ]

    blocks = detect_bearish_order_block(
        candles,
        mss_events=[_mss(4)],
        liquidity_sweeps=[_buy_side_sweep(2)],
        symbol="XAUUSD",
        timeframe="5m",
        premium_discount_context={"poi_location": "premium"},
        htf_context={"htf_trend_state": "bearish"},
        target_liquidity=_target(),
    )
    block = blocks[0]

    assert block["created_after_sweep"] is True
    assert block["mss_confirmed"] is True
    assert block["bos_confirmed"] is False
    assert block["fvg_created"] is True
    assert block["retest_status"] == "fresh"
    assert 9.0 <= block["quality_score"] <= 10.0


def test_bearish_continuation_ob_after_bos_without_sweep_is_valid_but_lower_quality() -> None:
    candles = [
        _candle(0, 110.0, 110.4, 109.8, 110.1),
        _candle(1, 110.1, 110.3, 109.6, 109.9),
        _candle(2, 109.9, 111.0, 109.7, 110.8),
        _candle(3, 109.0, 109.2, 103.0, 103.4),
    ]

    blocks = detect_bearish_order_block(
        candles,
        bos_events=[_bos(3)],
        symbol="XAUUSD",
        timeframe="5m",
        premium_discount_context={"poi_location": "premium"},
        htf_context={"htf_trend_state": "bearish"},
    )
    block = blocks[0]

    assert block["created_after_sweep"] is False
    assert block["bos_confirmed"] is True
    assert block["mss_confirmed"] is False
    assert block["fvg_created"] is True
    assert 7.0 <= block["quality_score"] <= 8.0
    assert "no_prior_buy_side_sweep" in block["warnings"]


def test_invalid_bearish_ob_candidate_without_displacement_or_structure_is_weak() -> None:
    candles = [
        _candle(0, 100.0, 101.0, 99.6, 100.4),
        _candle(1, 100.4, 100.8, 99.7, 100.0),
        _candle(2, 100.0, 100.5, 99.8, 99.9),
    ]

    blocks = detect_bearish_order_block(candles, symbol="XAUUSD", timeframe="1m")
    block = blocks[0]

    assert block["valid_bearish_ob"] is False
    assert block["quality_score"] <= 4.0
    assert "no_structure_break_no_valid_ob" in block["warnings"]
    assert "invalid_or_weak_bearish_ob_candidate" in block["warnings"]


def test_retest_holds_mean_threshold_and_allows_entry_after_confirmation() -> None:
    candles = [
        _candle(0, 110.0, 110.5, 109.3, 109.7),
        _candle(1, 109.7, 110.3, 109.0, 109.5),
        _candle(2, 109.5, 111.4, 109.2, 110.4),
        _candle(3, 110.4, 111.0, 109.8, 110.8),
        _candle(4, 108.2, 108.4, 103.2, 103.6),
    ]
    block = detect_bearish_order_block(
        candles,
        mss_events=[_mss(4)],
        liquidity_sweeps=[_buy_side_sweep(2)],
        symbol="XAUUSD",
        timeframe="5m",
        premium_discount_context={"poi_location": "premium"},
        htf_context={"htf_trend_state": "bearish"},
        target_liquidity=_target(100.0),
    )[0]
    retest_candles = candles + [
        _candle(5, 103.6, 110.5, 103.1, 109.2),
    ]

    validation = validate_bearish_ob_retest(
        retest_candles,
        block,
        ltf_confirmation_events=["bearish CHoCH after OB retest"],
        target_liquidity=_target(100.0),
        risk_settings={"stop_buffer": 0.1, "minimum_rr": 1.5},
    )

    assert validation["retest_status"] == BearishOBRetestStatus.CONFIRMED_REJECTION.value
    assert validation["mean_threshold_touched"] is True
    assert validation["rejection_confirmed"] is True
    assert validation["entry_allowed"] is True
    assert validation["stop_loss_reference"] == "above_zone_high"
    assert validation["target_liquidity"]["direction"] == "sell_side"


def test_failed_bearish_ob_retest_blocks_entry() -> None:
    candles = [
        _candle(0, 110.0, 110.5, 109.3, 109.7),
        _candle(1, 109.7, 110.3, 109.0, 109.5),
        _candle(2, 109.5, 111.4, 109.2, 110.4),
        _candle(3, 110.4, 111.0, 109.8, 110.8),
        _candle(4, 108.2, 108.4, 103.2, 103.6),
    ]
    block = detect_bearish_order_block(
        candles,
        mss_events=[_mss(4)],
        symbol="XAUUSD",
        timeframe="5m",
        premium_discount_context={"poi_location": "premium"},
        htf_context={"htf_trend_state": "bearish"},
    )[0]
    retest_candles = candles + [_candle(5, 103.6, 112.1, 103.2, 111.3)]

    validation = validate_bearish_ob_retest(retest_candles, block, target_liquidity=_target(100.0))

    assert validation["retest_status"] == BearishOBRetestStatus.FAILED.value
    assert validation["closed_above_zone_high"] is True
    assert validation["entry_allowed"] is False
    assert validation["quality_score"] < 3.0
    assert "bearish_ob_failed_closed_above_zone_high" in validation["warnings"]
