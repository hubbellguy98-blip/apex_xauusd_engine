from datetime import datetime, timedelta

from src.analytics.ict_smc.mitigation_block import detect_mitigation_blocks


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


def _structure(direction, event_type, index, displacement="strong"):
    return {
        "direction": direction,
        "event_type": event_type,
        "confirmation_candle_index": index,
        "broken_level": 111.0 if direction == "bullish" else 99.0,
        "displacement_strength": displacement,
        "close_confirmed": True,
    }


def _sweep(side, index):
    return {
        "sweep_type": f"{side}_liquidity_sweep",
        "sweep_candle_index": index,
    }


def _fvg(direction, index, low, high):
    return {
        "direction": direction,
        "index": index,
        "zone_low": low,
        "zone_high": high,
    }


def _zone(direction, low, high):
    return {
        "direction": direction,
        "zone_low": low,
        "zone_high": high,
    }


def test_valid_bullish_mitigation_block_after_sell_side_sweep_and_mss() -> None:
    candles = [
        _candle(0, 110.0, 111.0, 108.0, 109.0),
        _candle(1, 109.0, 109.4, 105.8, 106.2),
        _candle(2, 106.2, 108.4, 105.6, 107.8),
        _candle(3, 108.0, 109.0, 106.0, 106.5),
        _candle(4, 106.5, 112.9, 106.4, 112.4),
        _candle(5, 112.4, 113.0, 111.2, 112.0),
        _candle(6, 108.1, 113.2, 106.3, 112.8),
    ]

    blocks = detect_mitigation_blocks(
        candles,
        structure_events=[_structure("bullish", "MSS", 4)],
        liquidity_sweeps=[_sweep("sell_side", 1)],
        fvg_events=[_fvg("bullish", 4, 109.0, 111.0)],
        order_blocks=[_zone("bullish", 106.0, 109.0)],
        context={
            "premium_discount_location": "discount",
            "htf_bias": "bullish",
            "target_liquidity": "buy_side_liquidity_above",
        },
        symbol="XAUUSD",
        timeframe="15m",
    )
    block = blocks[0]

    assert block["mitigation_type"] == "bullish_mitigation_block"
    assert block["valid_mitigation_block"] is True
    assert block["created_after_sweep"] is True
    assert block["created_by_event"] == "bullish_MSS"
    assert block["retest_status"] == "confirmed_reaction"
    assert block["reaction_confirmed"] is True
    assert block["entry_allowed_after_confirmation"] is True
    assert 7.5 <= block["quality_score"] <= 10.0


def test_valid_bearish_mitigation_block_after_buy_side_sweep_and_mss() -> None:
    candles = [
        _candle(0, 101.0, 104.0, 100.5, 103.4),
        _candle(1, 103.4, 105.4, 102.9, 104.8),
        _candle(2, 104.8, 105.8, 103.0, 103.6),
        _candle(3, 103.2, 105.0, 102.8, 104.6),
        _candle(4, 104.6, 104.8, 97.8, 98.2),
        _candle(5, 98.2, 99.0, 97.9, 98.5),
        _candle(6, 103.9, 104.9, 97.4, 98.0),
    ]

    blocks = detect_mitigation_blocks(
        candles,
        structure_events=[_structure("bearish", "MSS", 4)],
        liquidity_sweeps=[_sweep("buy_side", 1)],
        fvg_events=[_fvg("bearish", 4, 99.0, 102.0)],
        order_blocks=[_zone("bearish", 102.8, 105.0)],
        context={
            "premium_discount_location": "premium",
            "htf_bias": "bearish",
            "target_liquidity": "sell_side_liquidity_below",
        },
        symbol="XAUUSD",
        timeframe="15m",
    )
    block = blocks[0]

    assert block["mitigation_type"] == "bearish_mitigation_block"
    assert block["valid_mitigation_block"] is True
    assert block["created_after_sweep"] is True
    assert block["created_by_event"] == "bearish_MSS"
    assert block["retest_status"] == "confirmed_reaction"
    assert block["reaction_confirmed"] is True
    assert block["entry_allowed_after_confirmation"] is True
    assert 7.5 <= block["quality_score"] <= 10.0


def test_no_structure_shift_rejects_subjective_old_candle() -> None:
    candles = [
        _candle(0, 100.0, 101.0, 99.6, 100.4),
        _candle(1, 100.4, 101.2, 100.0, 100.8),
        _candle(2, 100.8, 101.0, 99.8, 100.1),
    ]

    block = detect_mitigation_blocks(candles, structure_events=[], symbol="XAUUSD", timeframe="5m")[0]

    assert block["valid_mitigation_block"] is False
    assert block["mitigation_type"] == "invalid_mitigation_block"
    assert block["quality_score"] <= 3.0
    assert "no_structure_shift" in block["warnings"]


def test_bullish_retest_without_reaction_stays_candidate_and_blocks_entry() -> None:
    candles = [
        _candle(0, 110.0, 111.0, 108.0, 109.0),
        _candle(1, 109.0, 109.4, 105.8, 106.2),
        _candle(2, 106.2, 108.4, 105.6, 107.8),
        _candle(3, 108.0, 109.0, 106.0, 106.5),
        _candle(4, 106.5, 112.9, 106.4, 112.4),
        _candle(5, 108.8, 109.0, 106.7, 108.6),
    ]

    block = detect_mitigation_blocks(
        candles,
        structure_events=[_structure("bullish", "MSS", 4)],
        liquidity_sweeps=[_sweep("sell_side", 1)],
        context={
            "premium_discount_location": "discount",
            "htf_bias": "bullish",
            "target_liquidity": "buy_side_liquidity_above",
        },
        symbol="XAUUSD",
        timeframe="15m",
    )[0]

    assert block["mitigation_type"] == "bullish_mitigation_block_candidate"
    assert block["retest_status"] == "retest_no_reaction"
    assert block["reaction_confirmed"] is False
    assert block["entry_allowed_after_confirmation"] is False
    assert 4.0 <= block["quality_score"] <= 6.0


def test_failed_bullish_mitigation_block_caps_score_and_blocks_entry() -> None:
    candles = [
        _candle(0, 110.0, 111.0, 108.0, 109.0),
        _candle(1, 109.0, 109.4, 105.8, 106.2),
        _candle(2, 106.2, 108.4, 105.6, 107.8),
        _candle(3, 108.0, 109.0, 106.0, 106.5),
        _candle(4, 106.5, 112.9, 106.4, 112.4),
        _candle(5, 108.0, 108.4, 105.0, 105.2),
    ]

    block = detect_mitigation_blocks(
        candles,
        structure_events=[_structure("bullish", "MSS", 4)],
        liquidity_sweeps=[_sweep("sell_side", 1)],
        context={
            "premium_discount_location": "discount",
            "htf_bias": "bullish",
            "target_liquidity": "buy_side_liquidity_above",
        },
        symbol="XAUUSD",
        timeframe="15m",
    )[0]

    assert block["mitigation_type"] == "bullish_mitigation_block_candidate"
    assert block["retest_status"] == "failed"
    assert block["reaction_confirmed"] is False
    assert block["entry_allowed_after_confirmation"] is False
    assert block["quality_score"] <= 3.0
    assert "mitigation_block_failed" in block["warnings"]
