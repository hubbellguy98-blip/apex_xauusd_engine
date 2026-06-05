from src.analytics.ict_smc.draw_on_liquidity import (
    DrawDirection,
    TradeDirectionBias,
    determine_draw_on_liquidity,
)
from src.analytics.ict_smc.liquidity import LiquidityDirection, LiquidityStatus, LiquidityType


def _context(**overrides):
    base = {
        "symbol": "XAUUSD",
        "current_price": 2354.20,
        "current_timeframe": "15m",
        "htf_trend_state": "neutral",
        "itf_trend_state": "neutral",
        "ltf_trend_state": "neutral",
        "latest_structure_event": "none",
        "latest_mss": "none",
        "latest_bos": "none",
        "latest_choch": "none",
        "recent_liquidity_sweep": "none",
        "premium_discount_position": "equilibrium",
        "session_name": "London",
        "volatility_state": "normal",
        "atr": 5.0,
        "current_structure_state": "balanced",
    }
    base.update(overrides)
    return base


def _liquidity(
    liquidity_id: str,
    direction: LiquidityDirection,
    liquidity_type: LiquidityType,
    zone_mid: float,
    quality_score: float = 8.0,
    timeframe: str = "15m",
    swept_status: LiquidityStatus = LiquidityStatus.UNSWEPT,
    touched_count: int = 3,
):
    return {
        "liquidity_id": liquidity_id,
        "liquidity_type": liquidity_type.value,
        "direction": direction.value,
        "timeframe": timeframe,
        "price_zone": {
            "zone_low": zone_mid - 0.8,
            "zone_mid": zone_mid,
            "zone_high": zone_mid + 0.8,
        },
        "swept_status": swept_status.value,
        "quality_score": quality_score,
        "touched_count": touched_count,
        "source": "unit_test",
        "confluence_sources": (),
    }


def _poi(
    poi_id: str,
    direction: str,
    zone_low: float,
    zone_high: float,
    quality_score: float = 8.5,
    timeframe: str = "1h",
    status: str = "fresh",
    poi_type: str = "order_block",
):
    return {
        "poi_id": poi_id,
        "poi_type": poi_type,
        "direction": direction,
        "timeframe": timeframe,
        "zone_low": zone_low,
        "zone_mid": (zone_low + zone_high) / 2,
        "zone_high": zone_high,
        "quality_score": quality_score,
        "status": status,
        "caused_bos": True,
    }


def test_valid_buy_side_draw_after_sell_side_sweep_selects_equal_highs() -> None:
    context = _context(
        htf_trend_state="bullish",
        itf_trend_state="bullish",
        latest_structure_event="bullish_MSS",
        latest_mss="bullish_MSS",
        recent_liquidity_sweep={"direction": "bullish", "sweep_type": "sell_side_sweep"},
        premium_discount_position="discount_to_equilibrium_reclaim",
        session_name="New_York",
    )
    liquidity = [
        _liquidity("LQ_15M_EQH_021", LiquidityDirection.BUY_SIDE, LiquidityType.EQUAL_HIGHS, 2369.0, 8.3),
        _liquidity("LQ_5M_EQL_010", LiquidityDirection.SELL_SIDE, LiquidityType.EQUAL_LOWS, 2348.0, 5.0),
    ]

    decision = determine_draw_on_liquidity(context, liquidity, [])

    assert decision["expected_draw"] == DrawDirection.BUY_SIDE.value
    assert decision["trade_direction_bias"] == TradeDirectionBias.LONG_FAVORED.value
    assert decision["selected_liquidity"]["liquidity_id"] == "LQ_15M_EQH_021"
    assert decision["blocked_by_poi"] is False
    assert decision["confidence_score"] >= 8.0
    assert decision["entry_allowed"] is False


def test_valid_sell_side_draw_after_buy_side_sweep_selects_equal_lows() -> None:
    context = _context(
        current_price=2372.80,
        htf_trend_state="bearish",
        itf_trend_state="bearish",
        latest_structure_event="bearish_MSS",
        latest_mss="bearish_MSS",
        recent_liquidity_sweep={"direction": "bearish", "sweep_type": "buy_side_sweep"},
        premium_discount_position="premium",
        session_name="London_New_York_Overlap",
    )
    liquidity = [
        _liquidity("LQ_15M_EQL_018", LiquidityDirection.SELL_SIDE, LiquidityType.EQUAL_LOWS, 2351.10, 8.0),
        _liquidity("LQ_1M_EQH_006", LiquidityDirection.BUY_SIDE, LiquidityType.EQUAL_HIGHS, 2378.50, 5.5),
    ]

    decision = determine_draw_on_liquidity(context, liquidity, [])

    assert decision["expected_draw"] == DrawDirection.SELL_SIDE.value
    assert decision["trade_direction_bias"] == TradeDirectionBias.SHORT_FAVORED.value
    assert decision["selected_liquidity"]["liquidity_id"] == "LQ_15M_EQL_018"
    assert decision["blocked_by_poi"] is False
    assert decision["confidence_score"] >= 8.0
    assert decision["entry_allowed"] is False


def test_sell_side_draw_is_reduced_when_strong_bullish_poi_blocks_path() -> None:
    context = _context(
        current_price=2372.80,
        htf_trend_state="bearish",
        latest_structure_event="bearish_MSS",
        latest_mss="bearish_MSS",
        recent_liquidity_sweep={"direction": "bearish", "sweep_type": "buy_side_sweep"},
        premium_discount_position="premium",
        session_name="London_New_York_Overlap",
    )
    liquidity = [
        _liquidity("LQ_15M_EQL_018", LiquidityDirection.SELL_SIDE, LiquidityType.EQUAL_LOWS, 2351.10, 8.0),
        _liquidity("LQ_5M_SWING_LOW_011", LiquidityDirection.SELL_SIDE, LiquidityType.SWING_LOW, 2365.20, 5.6, "5m"),
    ]
    poi_zones = [_poi("POI_1H_BULLISH_OB_007", "bullish", 2360.20, 2364.90, 8.7)]

    decision = determine_draw_on_liquidity(context, liquidity, poi_zones)

    assert decision["expected_draw"] == DrawDirection.SELL_SIDE.value
    assert decision["trade_direction_bias"] == TradeDirectionBias.SHORT_FAVORED_BUT_BLOCKED.value
    assert decision["blocked_by_poi"] is True
    assert decision["blocking_poi_reference"]["poi_id"] == "POI_1H_BULLISH_OB_007"
    assert decision["confidence_score"] < 7.0
    assert "do_not_assume_full_target_until_blocking_poi_fails" in decision["warnings"]


def test_unclear_draw_in_range_middle_keeps_neutral_bias() -> None:
    context = _context(
        current_price=100.0,
        htf_trend_state="neutral",
        itf_trend_state="neutral",
        ltf_trend_state="neutral",
        latest_structure_event="none",
        recent_liquidity_sweep="none",
        premium_discount_position="range_middle",
        current_structure_state="choppy_range_middle",
        atr=2.0,
    )
    liquidity = [
        _liquidity("LQ_EQH", LiquidityDirection.BUY_SIDE, LiquidityType.EQUAL_HIGHS, 103.0, 7.0),
        _liquidity("LQ_EQL", LiquidityDirection.SELL_SIDE, LiquidityType.EQUAL_LOWS, 97.0, 7.0),
    ]

    decision = determine_draw_on_liquidity(context, liquidity, [])

    assert decision["expected_draw"] == DrawDirection.UNCLEAR.value
    assert decision["trade_direction_bias"] == TradeDirectionBias.NEUTRAL.value
    assert decision["target_price_zone"] is None
    assert 2.0 <= decision["confidence_score"] <= 4.0
    assert "range_middle_no_clear_draw" in decision["warnings"]


def test_htf_buy_side_draw_overrides_minor_ltf_sell_side_target() -> None:
    context = _context(
        current_price=2342.0,
        htf_trend_state="strong_bullish",
        itf_trend_state="bullish",
        ltf_trend_state="minor_bearish_pullback",
        latest_structure_event="bullish_BOS",
        latest_bos="bullish_BOS",
        recent_liquidity_sweep="sell_side_sweep",
        premium_discount_position="htf_discount",
        session_name="London",
        atr=6.0,
    )
    liquidity = [
        _liquidity("LQ_5M_MINOR_LOW", LiquidityDirection.SELL_SIDE, LiquidityType.SWING_LOW, 2338.0, 5.8, "5m"),
        _liquidity("LQ_1H_EQH_022", LiquidityDirection.BUY_SIDE, LiquidityType.EQUAL_HIGHS, 2365.0, 8.8, "1h"),
        _liquidity("LQ_DAILY_HIGH", LiquidityDirection.BUY_SIDE, LiquidityType.PREVIOUS_DAY_HIGH, 2380.0, 9.0, "daily"),
    ]
    poi_zones = [_poi("POI_15M_DEMAND", "bullish", 2337.0, 2341.0, 8.2, "15m", poi_type="demand")]

    decision = determine_draw_on_liquidity(context, liquidity, poi_zones)

    assert decision["expected_draw"] == DrawDirection.BUY_SIDE.value
    assert decision["trade_direction_bias"] == TradeDirectionBias.LONG_FAVORED.value
    assert decision["selected_liquidity"]["direction"] == LiquidityDirection.BUY_SIDE.value
    assert decision["selected_liquidity"]["liquidity_id"] in {"LQ_1H_EQH_022", "LQ_DAILY_HIGH"}
    assert 7.0 <= decision["confidence_score"] <= 8.5
    assert "target_direction_is_against_htf_bias" in decision["best_sell_side_target"]["warnings"]
