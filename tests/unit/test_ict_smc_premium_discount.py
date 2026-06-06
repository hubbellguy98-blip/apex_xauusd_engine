from datetime import datetime

from src.analytics.ict_smc.premium_discount import calculate_premium_discount, evaluate_poi_premium_discount


def _swing(index, price, swing_type, strength=8.0, confirmed=True, timeframe="15m", source="test"):
    return {
        "index": index,
        "timestamp": datetime(2026, 6, 5, 10, 0),
        "price": price,
        "type": swing_type,
        "strength_score": strength,
        "timeframe": timeframe,
        "confirmed_status": confirmed,
        "structural_importance": "major",
        "source": source,
    }


def _zone(low, high):
    return {"zone_low": low, "zone_high": high}


def test_valid_bullish_discount_range_prefers_bullish_pois() -> None:
    result = calculate_premium_discount(
        _swing(120, 2340.0, "swing_low"),
        _swing(156, 2380.0, "swing_high"),
        current_price=2350.0,
        atr=5.0,
        context_direction="bullish",
        poi_zone=_zone(2346.0, 2354.0),
        dealing_range_type="bullish_MSS_dealing_range",
        symbol="XAUUSD",
        timeframe="15m",
    )

    assert result["equilibrium"] == 2360.0
    assert result["discount_zone"]["zone_low"] == 2340.0
    assert result["discount_zone"]["zone_high"] == 2360.0
    assert result["premium_zone"]["zone_low"] == 2360.0
    assert result["premium_zone"]["zone_high"] == 2380.0
    assert result["current_price_location"] in {"deep_discount", "discount"}
    assert result["trade_filter"]["bullish_setups_preferred"] is True
    assert result["poi_quality_filter"]["premium_discount_alignment"] is True
    assert result["poi_quality_filter"]["quality_adjustment"] > 0
    assert result["entry_allowed_from_premium_discount_alone"] is False


def test_valid_bearish_premium_range_prefers_bearish_pois() -> None:
    result = calculate_premium_discount(
        _swing(120, 2340.0, "swing_low"),
        _swing(156, 2380.0, "swing_high"),
        current_price=2372.0,
        atr=5.0,
        context_direction="bearish",
        poi_zone=_zone(2368.0, 2378.0),
        dealing_range_type="bearish_MSS_dealing_range",
        symbol="XAUUSD",
        timeframe="15m",
    )

    assert result["equilibrium"] == 2360.0
    assert result["current_price_location"] in {"premium", "deep_premium"}
    assert result["trade_filter"]["bearish_setups_preferred"] is True
    assert result["poi_quality_filter"]["premium_discount_alignment"] is True
    assert result["poi_quality_filter"]["quality_adjustment"] > 0


def test_price_near_equilibrium_is_neutral_with_warning() -> None:
    result = calculate_premium_discount(
        _swing(120, 2340.0, "swing_low"),
        _swing(156, 2380.0, "swing_high"),
        current_price=2360.2,
        atr=5.0,
        equilibrium_buffer=0.5,
        context_direction="bullish",
        poi_zone=_zone(2357.0, 2361.0),
        symbol="XAUUSD",
        timeframe="15m",
    )

    assert result["current_price_location"] == "equilibrium_zone"
    assert result["trade_filter"]["bullish_location_score"] == 0.0
    assert result["trade_filter"]["bearish_location_score"] == 0.0
    assert result["poi_quality_filter"]["alignment_label"] == "neutral_equilibrium"
    assert "price_near_equilibrium" in result["warnings"]


def test_bad_dealing_range_is_invalidated_when_tiny_and_weak() -> None:
    result = calculate_premium_discount(
        _swing(10, 2340.0, "swing_low", strength=2.0),
        _swing(12, 2341.0, "swing_high", strength=2.5),
        current_price=2340.5,
        atr=2.0,
        minimum_range_atr_multiplier=2.0,
        symbol="XAUUSD",
        timeframe="1m",
    )

    assert result["range_valid"] is False
    assert result["range_quality_grade"] == "invalid"
    assert result["range_quality_score"] < 4.0
    assert "weak_or_invalid_dealing_range" in result["warnings"]


def test_htf_discount_improves_ltf_bullish_setup_alignment() -> None:
    htf = calculate_premium_discount(
        _swing(20, 2320.0, "swing_low", timeframe="4h"),
        _swing(80, 2400.0, "swing_high", timeframe="4h"),
        current_price=2348.0,
        atr=10.0,
        dealing_range_type="HTF_structural_range",
        symbol="XAUUSD",
        timeframe="4h",
    )
    ltf = calculate_premium_discount(
        _swing(120, 2340.0, "swing_low", timeframe="5m"),
        _swing(156, 2380.0, "swing_high", timeframe="5m"),
        current_price=2348.0,
        atr=3.0,
        context_direction="bullish",
        poi_zone=_zone(2344.0, 2352.0),
        htf_result=htf,
        symbol="XAUUSD",
        timeframe="5m",
    )
    poi_filter = evaluate_poi_premium_discount("bullish", _zone(2344.0, 2352.0), ltf)

    assert htf["current_price_location"] in {"deep_discount", "discount"}
    assert ltf["poi_quality_filter"]["premium_discount_alignment"] is True
    assert ltf["poi_quality_filter"]["quality_adjustment"] > 1.25
    assert poi_filter["premium_discount_alignment"] is True
    assert "HTF discount supports the bullish setup" in ltf["poi_quality_filter"]["reason"]
