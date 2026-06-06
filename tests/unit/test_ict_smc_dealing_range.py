from datetime import datetime, timedelta

from src.analytics.ict_smc.dealing_range import identify_dealing_range


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


def _swing(index, price, swing_type, strength=8.0, confirmed=True, timeframe="15m"):
    return {
        "index": index,
        "timestamp": datetime(2026, 6, 5) + timedelta(minutes=index),
        "price": price,
        "type": swing_type,
        "strength_score": strength,
        "timeframe": timeframe,
        "confirmed_status": confirmed,
        "structural_importance": "major",
        "source": "test",
    }


def _structure(direction, event_type, index):
    return {
        "direction": direction,
        "event_type": event_type,
        "confirmation_candle_index": index,
    }


def _liquidity(price, liquidity_type, index=0):
    return {"price": price, "liquidity_type": liquidity_type, "index": index, "source": "test_pool"}


def _candles(count=50, base=2350.0):
    return [
        _candle(i, base + i * 0.1, base + i * 0.1 + 1.0, base + i * 0.1 - 1.0, base + i * 0.1 + 0.2)
        for i in range(count)
    ]


def test_valid_bullish_mss_dealing_range_selects_sweep_low_to_new_high() -> None:
    result = identify_dealing_range(
        _candles(),
        [_swing(10, 2340.0, "swing_low"), _swing(25, 2380.0, "swing_high")],
        "15m",
        structure_events=[_structure("bullish", "MSS", 24)],
        liquidity_pools=[
            _liquidity(2339.0, "sell_side_liquidity"),
            _liquidity(2368.0, "internal_liquidity"),
            _liquidity(2382.0, "buy_side_liquidity"),
        ],
        current_price=2350.0,
        atr=5.0,
        symbol="XAUUSD",
    )

    assert result["range_type"] == "bullish_MSS_dealing_range"
    assert result["range_direction"] == "bullish"
    assert result["range_low"] == 2340.0
    assert result["range_high"] == 2380.0
    assert 8.0 <= result["quality_score"] <= 10.0
    assert result["discount_zone"] == {"zone_low": 2340.0, "zone_mid": 2350.0, "zone_high": 2360.0}
    assert any(item["price"] == 2368.0 for item in result["internal_liquidity"])
    assert any(item["price"] == 2380.0 for item in result["external_liquidity"]["buy_side"])
    assert result["entry_allowed_from_dealing_range_alone"] is False


def test_valid_bearish_mss_dealing_range_selects_sweep_high_to_new_low() -> None:
    result = identify_dealing_range(
        _candles(),
        [_swing(10, 2380.0, "swing_high"), _swing(25, 2340.0, "swing_low")],
        "15m",
        structure_events=[_structure("bearish", "MSS", 24)],
        liquidity_pools=[
            _liquidity(2382.0, "buy_side_liquidity"),
            _liquidity(2368.0, "internal_liquidity"),
            _liquidity(2338.0, "sell_side_liquidity"),
        ],
        current_price=2370.0,
        atr=5.0,
        symbol="XAUUSD",
    )

    assert result["range_type"] == "bearish_MSS_dealing_range"
    assert result["range_direction"] == "bearish"
    assert 8.0 <= result["quality_score"] <= 10.0
    assert result["premium_zone"] == {"zone_low": 2360.0, "zone_mid": 2370.0, "zone_high": 2380.0}
    assert any(item["price"] == 2340.0 for item in result["external_liquidity"]["sell_side"])
    assert result["current_price_location"] in {"premium", "deep_premium"}


def test_bullish_bos_continuation_range_scores_strong_but_below_mss() -> None:
    result = identify_dealing_range(
        _candles(),
        [_swing(20, 2350.0, "swing_low"), _swing(35, 2390.0, "swing_high")],
        "15m",
        structure_events=[_structure("bullish", "BOS", 35)],
        current_price=2365.0,
        atr=6.0,
        symbol="XAUUSD",
    )

    assert result["range_type"] == "bullish_BOS_dealing_range"
    assert result["range_direction"] == "bullish"
    assert 7.0 <= result["quality_score"] <= 8.5
    assert result["current_price_location"] in {"discount", "deep_discount"}


def test_tiny_weak_noisy_range_is_rejected() -> None:
    result = identify_dealing_range(
        _candles(),
        [_swing(100, 2340.0, "swing_low", strength=2.0), _swing(103, 2341.0, "swing_high", strength=2.5)],
        "1m",
        current_price=2340.5,
        atr=5.0,
        minimum_candle_distance=5,
        symbol="XAUUSD",
    )

    assert result["range_valid"] is False
    assert result["range_type"] == "noisy_local_range"
    assert result["quality_score"] <= 4.0
    assert "avoid_tiny_noisy_range" in result["warnings"]


def test_htf_discount_context_strengthens_ltf_bullish_dealing_range() -> None:
    htf = identify_dealing_range(
        _candles(base=2320.0),
        [_swing(20, 2320.0, "swing_low", timeframe="4h"), _swing(80, 2400.0, "swing_high", timeframe="4h")],
        "4h",
        current_price=2348.0,
        atr=10.0,
        symbol="XAUUSD",
    )

    ltf = identify_dealing_range(
        _candles(),
        [_swing(120, 2340.0, "swing_low", timeframe="5m"), _swing(156, 2380.0, "swing_high", timeframe="5m")],
        "5m",
        structure_events=[_structure("bullish", "MSS", 155)],
        current_price=2348.0,
        atr=3.0,
        symbol="XAUUSD",
        htf_dealing_range=htf,
    )

    assert htf["current_price_location"] in {"discount", "deep_discount"}
    assert ltf["htf_alignment"]["alignment"] == "strong"
    assert ltf["htf_alignment"]["bullish_poi_quality_adjustment"] == "increase"
    assert ltf["htf_alignment"]["bearish_ltf_setups_reduced"] is True
