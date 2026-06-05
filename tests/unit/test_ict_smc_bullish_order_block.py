from datetime import datetime, timedelta

from src.analytics.ict_smc.bullish_order_block import (
    BullishOBRetestStatus,
    detect_bullish_order_block,
    validate_bullish_ob_retest,
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
        "direction": "bullish",
        "confirmation_candle_index": index,
        "broken_level": 105.0,
        "broken_swing_index": index - 4,
        "displacement_strength": "strong",
        "fvg_created": True,
        "quality_score": 8.0,
    }


def _mss(index):
    return {
        "direction": "bullish",
        "confirmation_candle_index": index,
        "broken_level": 105.0,
        "broken_swing_index": index - 4,
        "displacement_strength": "strong",
        "fvg_after_mss": True,
        "confidence_score": 8.7,
    }


def _sell_side_sweep(index):
    return {
        "direction": "bullish",
        "sweep_type": "sell_side_liquidity_sweep",
        "sweep_candle_index": index,
        "swept_liquidity_id": "LQ_EQL_001",
        "quality_score": 8.8,
    }


def _target(price=112.0):
    return {
        "liquidity_id": "LQ_BUY_SIDE_HIGH",
        "liquidity_type": "equal_highs",
        "direction": "buy_side",
        "zone_mid": price,
        "quality_score": 8.0,
    }


def test_high_quality_bullish_ob_after_sell_side_sweep() -> None:
    candles = [
        _candle(0, 100.0, 101.0, 99.4, 100.4),
        _candle(1, 100.4, 101.1, 99.8, 100.6),
        _candle(2, 100.6, 101.0, 98.8, 100.1),
        _candle(3, 100.1, 100.8, 99.4, 99.7),
        _candle(4, 102.0, 106.8, 101.8, 106.4),
    ]

    blocks = detect_bullish_order_block(
        candles,
        mss_events=[_mss(4)],
        liquidity_sweeps=[_sell_side_sweep(2)],
        symbol="XAUUSD",
        timeframe="5m",
        premium_discount_context={"poi_location": "discount"},
        htf_context={"htf_trend_state": "bullish"},
        target_liquidity=_target(),
    )
    block = blocks[0]

    assert block["created_after_sweep"] is True
    assert block["mss_confirmed"] is True
    assert block["bos_confirmed"] is False
    assert block["fvg_created"] is True
    assert block["retest_status"] == "fresh"
    assert 9.0 <= block["quality_score"] <= 10.0


def test_bullish_continuation_ob_after_bos_without_sweep_is_valid_but_lower_quality() -> None:
    candles = [
        _candle(0, 100.0, 101.0, 99.5, 100.6),
        _candle(1, 100.6, 102.0, 100.2, 101.4),
        _candle(2, 101.4, 102.2, 100.8, 100.9),
        _candle(3, 103.0, 107.4, 102.8, 107.1),
    ]

    blocks = detect_bullish_order_block(
        candles,
        bos_events=[_bos(3)],
        symbol="XAUUSD",
        timeframe="5m",
        premium_discount_context={"poi_location": "discount"},
        htf_context={"htf_trend_state": "bullish"},
    )
    block = blocks[0]

    assert block["created_after_sweep"] is False
    assert block["bos_confirmed"] is True
    assert block["mss_confirmed"] is False
    assert block["fvg_created"] is True
    assert 7.0 <= block["quality_score"] <= 8.0
    assert "no_prior_sell_side_sweep" in block["warnings"]


def test_invalid_bullish_ob_candidate_without_displacement_or_structure_is_weak() -> None:
    candles = [
        _candle(0, 100.4, 100.8, 99.7, 100.0),
        _candle(1, 100.0, 100.6, 99.9, 100.3),
        _candle(2, 100.3, 100.7, 100.0, 100.4),
    ]

    blocks = detect_bullish_order_block(candles, symbol="XAUUSD", timeframe="1m")
    block = blocks[0]

    assert block["valid_bullish_ob"] is False
    assert block["quality_score"] <= 4.0
    assert "no_structure_break_no_valid_ob" in block["warnings"]
    assert "invalid_or_weak_bullish_ob_candidate" in block["warnings"]


def test_retest_holds_mean_threshold_and_allows_entry_after_confirmation() -> None:
    candles = [
        _candle(0, 100.0, 101.0, 99.4, 100.4),
        _candle(1, 100.4, 101.1, 99.8, 100.6),
        _candle(2, 100.6, 101.0, 98.8, 100.1),
        _candle(3, 100.1, 100.8, 99.4, 99.7),
        _candle(4, 102.0, 106.8, 101.8, 106.4),
    ]
    block = detect_bullish_order_block(
        candles,
        mss_events=[_mss(4)],
        liquidity_sweeps=[_sell_side_sweep(2)],
        symbol="XAUUSD",
        timeframe="5m",
        premium_discount_context={"poi_location": "discount"},
        htf_context={"htf_trend_state": "bullish"},
        target_liquidity=_target(118.0),
    )[0]
    retest_candles = candles + [
        _candle(5, 106.4, 106.8, 100.0, 106.1),
    ]

    validation = validate_bullish_ob_retest(
        retest_candles,
        block,
        ltf_confirmation_events=["bullish CHoCH after OB retest"],
        target_liquidity=_target(118.0),
        risk_settings={"stop_buffer": 0.1, "minimum_rr": 1.5},
    )

    assert validation["retest_status"] == BullishOBRetestStatus.CONFIRMED_REACTION.value
    assert validation["mean_threshold_touched"] is True
    assert validation["reaction_confirmed"] is True
    assert validation["entry_allowed"] is True
    assert validation["stop_loss_reference"] == "below_zone_low"
    assert validation["target_liquidity"]["direction"] == "buy_side"


def test_failed_bullish_ob_retest_blocks_entry() -> None:
    candles = [
        _candle(0, 100.0, 101.0, 99.4, 100.4),
        _candle(1, 100.4, 101.1, 99.8, 100.6),
        _candle(2, 100.6, 101.0, 98.8, 100.1),
        _candle(3, 100.1, 100.8, 99.4, 99.7),
        _candle(4, 102.0, 106.8, 101.8, 106.4),
    ]
    block = detect_bullish_order_block(
        candles,
        mss_events=[_mss(4)],
        symbol="XAUUSD",
        timeframe="5m",
        premium_discount_context={"poi_location": "discount"},
        htf_context={"htf_trend_state": "bullish"},
    )[0]
    retest_candles = candles + [_candle(5, 106.4, 106.8, 98.4, 98.6)]

    validation = validate_bullish_ob_retest(retest_candles, block, target_liquidity=_target(112.0))

    assert validation["retest_status"] == BullishOBRetestStatus.FAILED.value
    assert validation["closed_below_zone_low"] is True
    assert validation["entry_allowed"] is False
    assert validation["quality_score"] < 3.0
    assert "bullish_ob_failed_closed_below_zone_low" in validation["warnings"]
