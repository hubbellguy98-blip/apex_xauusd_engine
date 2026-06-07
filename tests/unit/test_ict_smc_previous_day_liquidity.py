from datetime import datetime, timedelta, timezone

from src.analytics.ict_smc.previous_day_liquidity import calculate_previous_day_levels, detect_pdh_pdl_raid


def _row(index, day_offset, open_p, high_p, low_p, close_p, timeframe="5m", is_closed=True):
    return {
        "index": index,
        "timestamp": datetime(2026, 6, 3, tzinfo=timezone.utc) + timedelta(days=day_offset, minutes=index),
        "symbol": "XAUUSD",
        "timeframe": timeframe,
        "open": open_p,
        "high": high_p,
        "low": low_p,
        "close": close_p,
        "volume": 100 + index,
        "is_closed": is_closed,
    }


def test_calculate_previous_day_levels_from_intraday_candles() -> None:
    rows = [
        _row(0, 0, 2356.70, 2362.00, 2354.00, 2359.00),
        _row(10, 0, 2359.00, 2381.40, 2358.00, 2372.00),
        _row(20, 0, 2372.00, 2374.00, 2348.20, 2372.10),
        _row(0, 1, 2372.10, 2375.00, 2368.00, 2371.50),
        _row(10, 1, 2371.50, 2378.00, 2369.00, 2377.00),
    ]

    result = calculate_previous_day_levels(rows, session_definition="utc_day", symbol="XAUUSD", tolerance=0.40)

    assert result["previous_day_date"] == "2026-06-03"
    assert result["current_trading_day"] == "2026-06-04"
    assert result["pdh"] == 2381.40
    assert result["pdl"] == 2348.20
    assert result["previous_day_open"] == 2356.70
    assert result["previous_day_close"] == 2372.10
    assert result["previous_day_range"] == 33.20
    assert result["previous_day_midpoint"] == 2364.80
    assert result["liquidity_objects"][0]["liquidity_role"] == "external_buy_side_liquidity"
    assert result["liquidity_objects"][1]["liquidity_role"] == "external_sell_side_liquidity"
    assert result["entry_allowed_from_pdh_pdl_alone"] is False


def test_pdh_raid_rejection_with_bearish_mss_and_fvg_scores_strong_context() -> None:
    rows = [
        _row(0, 1, 2376.0, 2379.0, 2374.0, 2378.0),
        _row(1, 1, 2378.0, 2384.2, 2377.9, 2380.3),
        _row(2, 1, 2380.3, 2380.8, 2375.0, 2375.4),
        _row(3, 1, 2375.4, 2376.0, 2370.5, 2371.0),
        _row(4, 1, 2371.0, 2371.8, 2368.5, 2369.2),
    ]

    result = detect_pdh_pdl_raid(
        rows,
        pdh=2381.40,
        pdl=2348.20,
        sweep_buffer=0.25,
        close_buffer=0.25,
        mss_events=[{"direction": "bearish", "index": 3}],
        fvg_events=[{"fvg_type": "bearish_fvg", "creation_index": 4, "zone_low": 2371.8, "zone_high": 2375.0}],
        session_label="newyork_killzone",
    )

    assert result["raid_type"] == "pdh_raid"
    assert result["raid_direction"] == "buy_side_liquidity_taken"
    assert result["sweep_confirmed"] is True
    assert result["breakout_confirmed"] is False
    assert result["reaction_bias"] == "bearish_possible"
    assert result["mss_after_raid"] is True
    assert result["fvg_after_raid"] is True
    assert result["quality_score"] >= 8.0
    assert result["entry_model"]["entry_allowed_from_raid_alone"] is False


def test_pdl_raid_reclaim_with_bullish_mss_and_fvg_scores_strong_context() -> None:
    rows = [
        _row(0, 1, 2352.0, 2354.0, 2350.0, 2351.5),
        _row(1, 1, 2351.5, 2352.3, 2345.7, 2349.6),
        _row(2, 1, 2349.6, 2354.0, 2349.0, 2353.6),
        _row(3, 1, 2353.6, 2358.0, 2353.2, 2357.2),
        _row(4, 1, 2357.2, 2362.0, 2358.4, 2361.0),
    ]

    result = detect_pdh_pdl_raid(
        rows,
        pdh=2381.40,
        pdl=2348.20,
        sweep_buffer=0.25,
        close_buffer=0.25,
        mss_events=[{"direction": "bullish", "index": 3}],
        fvg_events=[{"fvg_type": "bullish_fvg", "creation_index": 4, "zone_low": 2354.0, "zone_high": 2358.4}],
        session_label="london_killzone",
    )

    assert result["raid_type"] == "pdl_raid"
    assert result["raid_direction"] == "sell_side_liquidity_taken"
    assert result["sweep_confirmed"] is True
    assert result["breakout_confirmed"] is False
    assert result["reaction_bias"] == "bullish_possible"
    assert result["mss_after_raid"] is True
    assert result["fvg_after_raid"] is True
    assert result["quality_score"] >= 8.0


def test_pdh_breakout_is_continuation_not_bearish_sweep() -> None:
    rows = [
        _row(0, 1, 2378.0, 2380.0, 2377.0, 2379.5),
        _row(1, 1, 2379.5, 2386.0, 2379.0, 2385.2),
        _row(2, 1, 2385.2, 2387.0, 2382.5, 2386.4),
    ]

    result = detect_pdh_pdl_raid(rows, pdh=2381.40, pdl=2348.20, sweep_buffer=0.25, close_buffer=0.25)

    assert result["raid_type"] == "pdh_breakout"
    assert result["sweep_confirmed"] is False
    assert result["breakout_confirmed"] is True
    assert result["reaction_bias"] == "bullish_continuation_possible"
    assert "not_a_bearish_pdh_sweep" in result["warnings"]


def test_pdl_breakdown_is_continuation_not_bullish_sweep() -> None:
    rows = [
        _row(0, 1, 2352.0, 2353.0, 2349.0, 2350.0),
        _row(1, 1, 2350.0, 2350.5, 2344.0, 2344.6),
        _row(2, 1, 2344.6, 2345.0, 2340.5, 2341.2),
    ]

    result = detect_pdh_pdl_raid(rows, pdh=2381.40, pdl=2348.20, sweep_buffer=0.25, close_buffer=0.25)

    assert result["raid_type"] == "pdl_breakdown"
    assert result["sweep_confirmed"] is False
    assert result["breakout_confirmed"] is True
    assert result["reaction_bias"] == "bearish_continuation_possible"
    assert "not_a_bullish_pdl_sweep" in result["warnings"]


def test_no_pdh_pdl_interaction_returns_invalid_observer_result() -> None:
    rows = [
        _row(0, 1, 2360.0, 2362.0, 2357.0, 2361.0),
        _row(1, 1, 2361.0, 2364.0, 2359.0, 2363.0),
    ]

    result = detect_pdh_pdl_raid(rows, pdh=2381.40, pdl=2348.20)

    assert result["raid_detected"] is False
    assert result["raid_type"] == "none"
    assert result["quality_score"] == 0.0
    assert result["entry_model"]["entry_allowed_from_raid_alone"] is False
