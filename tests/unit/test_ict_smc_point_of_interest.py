from datetime import datetime, timedelta

from src.analytics.ict_smc.point_of_interest import (
    POIDetectionConfig,
    POIFreshStatus,
    POIReactionStatus,
    POIType,
    confirm_poi_reaction,
    detect_poi_zones,
)


def _candle(index, open_, high, low, close):
    return {
        "index": index,
        "timestamp": datetime(2026, 1, 1) + timedelta(minutes=index),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100,
        "is_closed": True,
    }


def _context(**overrides):
    base = {
        "htf_trend_state": "bullish",
        "premium_discount_position": "discount",
    }
    base.update(overrides)
    return base


def _structure(event, index):
    return {"event": event, "index": index}


def _liquidity(event, index):
    return {"event": event, "index": index}


def _poi_by_type(zones, poi_type):
    return next(zone for zone in zones if zone["poi_type"] == poi_type)


def test_high_quality_bullish_poi_after_sell_side_sweep_and_mss() -> None:
    candles = [
        _candle(0, 100.0, 101.0, 99.5, 100.6),
        _candle(1, 100.6, 101.2, 100.1, 100.8),
        _candle(2, 100.8, 101.1, 99.2, 100.4),
        _candle(3, 100.4, 101.0, 99.8, 100.0),
        _candle(4, 102.2, 106.2, 102.0, 106.0),
    ]

    zones = detect_poi_zones(
        candles,
        "5m",
        symbol="XAUUSD",
        structure_events=[_structure("bullish_MSS", 4)],
        liquidity_events=[_liquidity("sell_side_liquidity_sweep", 2)],
        htf_context=_context(),
        config=POIDetectionConfig(include_weak_candidates=False, include_demand_supply=False),
    )
    ob = _poi_by_type(zones, POIType.ORDER_BLOCK.value)

    assert ob["direction"] == "bullish"
    assert ob["created_by_event"] == "bullish_MSS_after_sell_side_liquidity_sweep"
    assert ob["fresh_status"] == POIFreshStatus.FRESH.value
    assert ob["quality_score"] >= 8.5
    assert ob["entry_allowed_from_poi_alone"] is False


def test_high_quality_bearish_poi_after_buy_side_sweep_and_mss() -> None:
    candles = [
        _candle(0, 110.0, 110.5, 109.5, 109.8),
        _candle(1, 109.8, 110.1, 109.0, 109.6),
        _candle(2, 109.6, 111.0, 109.4, 110.2),
        _candle(3, 110.2, 110.8, 109.8, 110.7),
        _candle(4, 108.0, 108.2, 103.8, 104.0),
    ]

    zones = detect_poi_zones(
        candles,
        "5m",
        symbol="XAUUSD",
        structure_events=[_structure("bearish_MSS", 4)],
        liquidity_events=[_liquidity("buy_side_liquidity_sweep", 2)],
        htf_context=_context(htf_trend_state="bearish", premium_discount_position="premium"),
        config=POIDetectionConfig(include_weak_candidates=False, include_demand_supply=False),
    )
    ob = _poi_by_type(zones, POIType.ORDER_BLOCK.value)

    assert ob["direction"] == "bearish"
    assert ob["created_by_event"] == "bearish_MSS_after_buy_side_liquidity_sweep"
    assert ob["fresh_status"] == POIFreshStatus.FRESH.value
    assert ob["quality_score"] >= 8.5


def test_fvg_poi_after_bullish_bos_scores_as_trade_context_not_entry() -> None:
    candles = [
        _candle(0, 100.0, 100.5, 99.5, 100.2),
        _candle(1, 100.2, 100.8, 99.9, 100.1),
        _candle(2, 101.5, 105.0, 101.2, 104.8),
    ]

    zones = detect_poi_zones(
        candles,
        "1m",
        symbol="XAUUSD",
        structure_events=[_structure("bullish_BOS", 2)],
        htf_context=_context(),
        config=POIDetectionConfig(include_order_blocks=False, include_weak_candidates=False),
    )
    fvg = _poi_by_type(zones, POIType.FAIR_VALUE_GAP.value)

    assert fvg["direction"] == "bullish"
    assert fvg["created_by_event"] == "bullish_BOS_displacement"
    assert fvg["fresh_status"] == POIFreshStatus.FRESH.value
    assert 6.5 <= fvg["quality_score"] <= 8.5
    assert fvg["entry_allowed_from_poi_alone"] is False


def test_weak_random_ob_candidate_is_penalized_and_not_tradeable() -> None:
    candles = [
        _candle(0, 100.0, 101.0, 99.5, 100.5),
        _candle(1, 100.5, 101.0, 99.9, 100.2),
        _candle(2, 100.2, 100.9, 99.8, 100.6),
        _candle(3, 100.6, 101.0, 99.7, 100.4),
        _candle(4, 100.4, 100.8, 99.7, 99.9),
        _candle(5, 99.9, 100.5, 99.8, 100.2),
        _candle(6, 100.2, 100.4, 99.75, 100.0),
        _candle(7, 100.0, 100.3, 99.72, 99.95),
    ]

    zones = detect_poi_zones(
        candles,
        "1m",
        symbol="XAUUSD",
        htf_context=_context(htf_trend_state="neutral", premium_discount_position="equilibrium"),
        config=POIDetectionConfig(include_order_blocks=False, include_fair_value_gaps=False),
    )
    candidate = _poi_by_type(zones, POIType.ORDER_BLOCK_CANDIDATE.value)

    assert candidate["direction"] == "bullish"
    assert candidate["created_by_event"] == "none_or_minor_reaction"
    assert candidate["fresh_status"] in {
        POIFreshStatus.TOUCHED.value,
        POIFreshStatus.PARTIALLY_MITIGATED.value,
        POIFreshStatus.STALE.value,
    }
    assert 1.0 <= candidate["quality_score"] <= 4.0
    assert candidate["reaction_status"] == POIReactionStatus.NOT_TRADEABLE.value
    assert "weak_random_ob_not_tradeable" in candidate["warnings"]


def test_htf_poi_requires_ltf_confirmation_before_entry_permission() -> None:
    poi = {
        "poi_id": "POI_1H_order_block_bullish_40",
        "poi_type": POIType.ORDER_BLOCK.value,
        "direction": "bullish",
        "timeframe": "1h",
        "zone_low": 4450.0,
        "zone_mid": 4453.0,
        "zone_high": 4456.0,
        "quality_score": 8.0,
        "quality_grade": "strong",
        "fresh_status": POIFreshStatus.TOUCHED.value,
        "reaction_status": POIReactionStatus.WAITING_FOR_RETEST.value,
        "reasons": (),
    }
    confirmed = confirm_poi_reaction(
        poi,
        [
            "5m sell_side_liquidity_sweep inside POI",
            "5m bullish_CHoCH",
            "5m bullish_MSS with displacement",
            "5m bullish_FVG created",
        ],
        target_liquidity={"liquidity_id": "LQ_15M_EQUAL_HIGHS"},
    )

    assert confirmed["reaction_status"] == POIReactionStatus.CONFIRMED_REACTION.value
    assert confirmed["ltf_confirmation"] == "bullish_MSS"
    assert confirmed["entry_allowed_after_confirmation"] is True
    assert confirmed["entry_allowed_from_poi_alone"] is False
    assert confirmed["target_reference"] == "LQ_15M_EQUAL_HIGHS"
    assert confirmed["quality_score"] >= 9.0
