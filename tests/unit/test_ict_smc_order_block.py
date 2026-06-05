from datetime import datetime, timedelta

from src.analytics.ict_smc.order_block import (
    OrderBlockFreshStatus,
    OrderBlockReactionStatus,
    detect_order_blocks,
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


def _bos(direction, index, **extra):
    payload = {
        "direction": direction,
        "confirmation_candle_index": index,
        "broken_level": 100.0,
        "broken_swing_index": index - 4,
        "displacement_strength": "strong",
        "fvg_created": True,
        "quality_score": 8.0,
    }
    payload.update(extra)
    return payload


def _mss(direction, index, **extra):
    payload = {
        "direction": direction,
        "confirmation_candle_index": index,
        "broken_level": 100.0,
        "broken_swing_index": index - 4,
        "displacement_strength": "strong",
        "fvg_after_mss": True,
        "confidence_score": 8.5,
    }
    payload.update(extra)
    return payload


def _sweep(sweep_type, index):
    direction = "bullish" if "sell_side" in sweep_type else "bearish"
    return {
        "direction": direction,
        "sweep_type": sweep_type,
        "sweep_candle_index": index,
        "swept_liquidity_id": f"LQ_{index}",
        "quality_score": 8.4,
    }


def test_high_quality_bullish_order_block_after_sell_side_sweep_and_mss() -> None:
    candles = [
        _candle(0, 100.0, 101.0, 99.4, 100.4),
        _candle(1, 100.4, 101.1, 99.8, 100.6),
        _candle(2, 100.6, 101.0, 98.8, 100.1),
        _candle(3, 100.1, 100.8, 99.4, 99.7),
        _candle(4, 102.0, 106.8, 101.8, 106.4),
    ]

    blocks = detect_order_blocks(
        candles,
        mss_events=[_mss("bullish", 4)],
        liquidity_sweeps=[_sweep("sell_side_liquidity_sweep", 2)],
        symbol="XAUUSD",
        timeframe="5m",
        premium_discount_context={"poi_location": "discount"},
        htf_context={"htf_trend_state": "bullish"},
    )
    block = blocks[0]

    assert block["direction"] == "bullish"
    assert block["ob_candle"]["index"] == 3
    assert block["created_by_event"] == "bullish_MSS_after_sell_side_sweep"
    assert block["liquidity_context"]["liquidity_sweep_before_displacement"] is True
    assert block["fvg_context"]["fvg_created_after_displacement"] is True
    assert block["fresh_status"] == OrderBlockFreshStatus.FRESH.value
    assert 9.0 <= block["quality_score"] <= 10.0
    assert block["entry_allowed_from_ob_alone"] is False


def test_high_quality_bearish_order_block_after_buy_side_sweep_and_mss() -> None:
    candles = [
        _candle(0, 110.0, 110.5, 109.3, 109.7),
        _candle(1, 109.7, 110.3, 109.0, 109.5),
        _candle(2, 109.5, 111.4, 109.2, 110.4),
        _candle(3, 110.4, 111.0, 109.8, 110.8),
        _candle(4, 108.2, 108.4, 103.2, 103.6),
    ]

    blocks = detect_order_blocks(
        candles,
        mss_events=[_mss("bearish", 4)],
        liquidity_sweeps=[_sweep("buy_side_liquidity_sweep", 2)],
        symbol="XAUUSD",
        timeframe="5m",
        premium_discount_context={"poi_location": "premium"},
        htf_context={"htf_trend_state": "bearish"},
    )
    block = blocks[0]

    assert block["direction"] == "bearish"
    assert block["ob_candle"]["index"] == 3
    assert block["created_by_event"] == "bearish_MSS_after_buy_side_sweep"
    assert block["liquidity_context"]["liquidity_sweep_before_displacement"] is True
    assert block["fvg_context"]["fvg_created_after_displacement"] is True
    assert block["fresh_status"] == OrderBlockFreshStatus.FRESH.value
    assert 9.0 <= block["quality_score"] <= 10.0


def test_weak_order_block_candidate_without_structure_break_is_penalized() -> None:
    candles = [
        _candle(0, 100.0, 101.0, 99.6, 100.4),
        _candle(1, 100.4, 100.8, 99.7, 100.0),
        _candle(2, 100.0, 100.6, 99.9, 100.3),
    ]

    blocks = detect_order_blocks(candles, symbol="XAUUSD", timeframe="1m")
    block = blocks[0]

    assert block["created_by_event"] == "none_or_minor_reaction"
    assert block["quality_score"] <= 4.0
    assert block["quality_grade"] in {"weak", "moderate", "invalidated"}
    assert "no_structure_break_no_valid_ob" in block["warnings"]
    assert block["entry_allowed_from_ob_alone"] is False


def test_partially_mitigated_order_block_stays_valid_until_boundary_close_fails_it() -> None:
    candles = [
        _candle(0, 100.0, 101.0, 99.4, 100.4),
        _candle(1, 100.4, 101.1, 99.8, 100.6),
        _candle(2, 100.6, 101.0, 98.8, 100.1),
        _candle(3, 100.1, 100.8, 99.4, 99.7),
        _candle(4, 102.0, 106.8, 101.8, 106.4),
        _candle(5, 106.4, 106.7, 100.0, 105.8),
        _candle(6, 105.8, 107.0, 103.0, 106.6),
    ]

    blocks = detect_order_blocks(
        candles,
        bos_events=[_bos("bullish", 4, fvg_created=False)],
        symbol="XAUUSD",
        timeframe="5m",
        premium_discount_context={"poi_location": "discount"},
        htf_context={"htf_trend_state": "bullish"},
    )
    block = blocks[0]

    assert block["fresh_status"] == OrderBlockFreshStatus.PARTIALLY_MITIGATED.value
    assert block["freshness"]["mean_threshold_touched"] is True
    assert block["failed_order_block"] is False
    assert block["reaction_status"] == OrderBlockReactionStatus.REACTING.value
    assert block["quality_score"] >= 5.0


def test_failed_bullish_order_block_becomes_bearish_breaker_candidate() -> None:
    candles = [
        _candle(0, 100.0, 101.0, 99.4, 100.4),
        _candle(1, 100.4, 101.1, 99.8, 100.6),
        _candle(2, 100.6, 101.0, 98.8, 100.1),
        _candle(3, 100.1, 100.8, 99.4, 99.7),
        _candle(4, 102.0, 106.8, 101.8, 106.4),
        _candle(5, 106.4, 106.7, 98.8, 98.9),
    ]

    blocks = detect_order_blocks(
        candles,
        bos_events=[_bos("bullish", 4)],
        symbol="XAUUSD",
        timeframe="5m",
        premium_discount_context={"poi_location": "discount"},
        htf_context={"htf_trend_state": "bullish"},
    )
    block = blocks[0]

    assert block["fresh_status"] == OrderBlockFreshStatus.FAILED.value
    assert block["failed_order_block"] is True
    assert block["possible_breaker_created"] is True
    assert block["failure_status"]["possible_new_poi_type"] == "bearish_breaker_block"
    assert block["quality_score"] < 3.0
    assert "failed_order_block_do_not_use_for_original_direction" in block["warnings"]
