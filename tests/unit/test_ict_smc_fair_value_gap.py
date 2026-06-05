from datetime import datetime, timedelta

from src.analytics.ict_smc.fair_value_gap import detect_fvg


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


def _structure(direction, event_type, index):
    return {
        "direction": direction,
        "event_type": event_type,
        "confirmation_candle_index": index,
    }


def _sweep(side, index):
    return {
        "sweep_type": f"{side}_liquidity_sweep",
        "sweep_candle_index": index,
    }


def _zone(direction, low, high):
    return {
        "direction": direction,
        "zone_low": low,
        "zone_high": high,
    }


def test_valid_bullish_fvg_after_sell_side_sweep_and_mss() -> None:
    candles = [
        _candle(0, 2346.0, 2348.0, 2344.0, 2345.0),
        _candle(1, 2345.0, 2350.2, 2344.2, 2348.8),
        _candle(2, 2348.8, 2358.0, 2348.4, 2357.2),
        _candle(3, 2356.0, 2360.0, 2354.8, 2358.7),
    ]

    fvg = detect_fvg(
        candles,
        structure_events=[_structure("bullish", "MSS", 3)],
        liquidity_sweeps=[_sweep("sell_side", 0)],
        order_blocks=[_zone("bullish", 2350.2, 2354.8)],
        context={
            "premium_discount_location": "discount",
            "htf_bias": "bullish",
            "target_liquidity": "nearest_buy_side_liquidity_above",
        },
        symbol="XAUUSD",
        timeframe="15m",
    )[0]

    assert fvg["fvg_type"] == "bullish_fvg"
    assert fvg["zone_low"] == 2350.2
    assert fvg["zone_high"] == 2354.8
    assert fvg["creation_index"] == 3
    assert fvg["active_status"] == "untouched"
    assert fvg["displacement_strength"] in {"moderate", "strong"}
    assert 8.0 <= fvg["quality_score"] <= 10.0


def test_valid_bearish_fvg_after_buy_side_sweep_and_mss() -> None:
    candles = [
        _candle(0, 2376.0, 2378.0, 2372.0, 2377.0),
        _candle(1, 2377.0, 2378.4, 2370.1, 2372.0),
        _candle(2, 2372.0, 2372.4, 2361.0, 2362.0),
        _candle(3, 2362.2, 2365.4, 2359.0, 2360.5),
    ]

    fvg = detect_fvg(
        candles,
        structure_events=[_structure("bearish", "MSS", 3)],
        liquidity_sweeps=[_sweep("buy_side", 0)],
        order_blocks=[_zone("bearish", 2365.4, 2370.1)],
        context={
            "premium_discount_location": "premium",
            "htf_bias": "bearish",
            "target_liquidity": "nearest_sell_side_liquidity_below",
        },
        symbol="XAUUSD",
        timeframe="15m",
    )[0]

    assert fvg["fvg_type"] == "bearish_fvg"
    assert fvg["zone_low"] == 2365.4
    assert fvg["zone_high"] == 2370.1
    assert fvg["creation_index"] == 3
    assert fvg["active_status"] == "untouched"
    assert fvg["displacement_strength"] in {"moderate", "strong"}
    assert 8.0 <= fvg["quality_score"] <= 10.0


def test_bullish_fvg_half_fill_without_invalidation() -> None:
    candles = [
        _candle(0, 2346.0, 2348.0, 2344.0, 2345.0),
        _candle(1, 2345.0, 2350.0, 2344.2, 2348.8),
        _candle(2, 2348.8, 2358.0, 2348.4, 2357.2),
        _candle(3, 2356.0, 2360.0, 2354.0, 2358.7),
        _candle(4, 2358.7, 2359.0, 2352.0, 2353.2),
    ]

    fvg = next(gap for gap in detect_fvg(candles, symbol="XAUUSD", timeframe="5m") if gap["creation_index"] == 3)

    assert fvg["fvg_type"] == "bullish_fvg"
    assert fvg["filled_percent"] == 50.0
    assert fvg["active_status"] == "half_filled"
    assert fvg["invalidated"] is False


def test_bearish_fvg_full_fill_is_not_invalidation_without_close_above_zone() -> None:
    candles = [
        _candle(0, 2376.0, 2378.0, 2372.0, 2377.0),
        _candle(1, 2377.0, 2378.4, 2370.0, 2372.0),
        _candle(2, 2372.0, 2372.4, 2361.0, 2362.0),
        _candle(3, 2362.2, 2365.0, 2359.0, 2360.5),
        _candle(4, 2360.5, 2370.5, 2360.0, 2368.0),
    ]

    fvg = next(gap for gap in detect_fvg(candles, symbol="XAUUSD", timeframe="5m") if gap["creation_index"] == 3)

    assert fvg["fvg_type"] == "bearish_fvg"
    assert fvg["filled_percent"] == 100.0
    assert fvg["active_status"] == "fully_filled"
    assert fvg["invalidated"] is False
    assert "fully_filled_but_not_invalidated" in fvg["warnings"]


def test_invalidated_bullish_fvg_caps_quality_score() -> None:
    candles = [
        _candle(0, 2346.0, 2348.0, 2344.0, 2345.0),
        _candle(1, 2345.0, 2350.0, 2344.2, 2348.8),
        _candle(2, 2348.8, 2358.0, 2348.4, 2357.2),
        _candle(3, 2356.0, 2360.0, 2354.0, 2358.7),
        _candle(4, 2358.7, 2359.0, 2348.0, 2348.0),
    ]

    fvg = next(gap for gap in detect_fvg(candles, symbol="XAUUSD", timeframe="5m") if gap["creation_index"] == 3)

    assert fvg["fvg_type"] == "bullish_fvg"
    assert fvg["active_status"] == "invalidated"
    assert fvg["filled_percent"] == 100.0
    assert fvg["invalidated"] is True
    assert fvg["quality_score"] <= 3.0
    assert "bullish_fvg_invalidated" in fvg["warnings"]
