from src.analytics.ict_smc.risk_management import (
    RiskDecisionStatus,
    calculate_position_size,
    validate_trade_risk,
)


def _signal(**overrides):
    base = {
        "signal_id": "SMC_SETUP_001",
        "symbol": "GOLD.i#",
        "direction": "bullish",
        "entry": 2360.70,
        "stop": 2353.30,
        "target": 2376.80,
        "score": 86.0,
        "correlation_group": "xauusd",
        "stop_valid": True,
    }
    base.update(overrides)
    return base


def _account(**overrides):
    base = {
        "account_balance": 10000.0,
        "equity": 10000.0,
        "daily_realized_pnl": 0.0,
        "weekly_realized_pnl": 0.0,
        "current_open_risk": 0.0,
        "current_spread": 0.20,
        "average_spread": 0.18,
        "open_positions": [],
        "news_restricted": False,
        "trading_locked": False,
    }
    base.update(overrides)
    return base


def _risk_config(**overrides):
    base = {
        "risk_percent": 1.0,
        "pip_value": 10.0,
        "min_rr": 1.5,
        "min_position_size": 0.01,
        "max_position_size": 2.0,
        "lot_step": 0.01,
        "max_daily_loss_percent": 3.0,
        "max_weekly_loss_percent": 6.0,
        "max_open_risk_percent": 5.0,
        "max_correlated_risk_percent": 3.0,
        "max_spread": 0.80,
        "abnormal_spread_multiplier": 4.0,
        "slippage_buffer": 0.10,
        "min_stop_distance": 0.50,
    }
    base.update(overrides)
    return base


def test_position_size_shrinks_when_stop_distance_widens() -> None:
    tight = calculate_position_size(10000, 1.0, 2360.0, 2350.0, 100.0)
    wide = calculate_position_size(10000, 1.0, 2360.0, 2340.0, 100.0)

    assert tight["position_size"] == 0.1
    assert wide["position_size"] == 0.05
    assert wide["position_size"] < tight["position_size"]
    assert tight["max_loss"] == 100.0
    assert tight["rejection_reason"] is None


def test_valid_ict_smc_trade_is_approved_with_sized_risk() -> None:
    result = validate_trade_risk(_signal(), _account(), _risk_config())

    assert result["approved"] is True
    assert result["decision"]["status"] == RiskDecisionStatus.APPROVED.value
    assert result["position_size"] > 0
    assert result["max_loss"] <= 100.0
    assert result["rr"] >= 1.5
    assert result["rejection_reason"] is None


def test_invalid_bullish_stop_is_rejected_before_sizing() -> None:
    result = validate_trade_risk(
        _signal(stop=2362.0, target=2372.0),
        _account(),
        _risk_config(),
    )

    assert result["approved"] is False
    assert result["position_size"] == 0.0
    assert result["rejection_reason"] == "invalid_long_stop"


def test_max_daily_loss_halts_new_trade() -> None:
    result = validate_trade_risk(
        _signal(),
        _account(daily_realized_pnl=-310.0),
        _risk_config(max_daily_loss_percent=3.0),
    )

    assert result["approved"] is False
    assert result["rejection_reason"] == "max_daily_loss_reached"


def test_correlated_exposure_blocks_additional_gold_risk() -> None:
    result = validate_trade_risk(
        _signal(correlation_group="metals_usd"),
        _account(
            open_positions=[
                {
                    "symbol": "GOLD.i#",
                    "correlation_group": "metals_usd",
                    "open_risk": 180.0,
                }
            ]
        ),
        _risk_config(max_correlated_risk_percent=2.0),
    )

    assert result["approved"] is False
    assert result["rejection_reason"] == "correlated_exposure_too_high"
    assert result["correlation_details"]["current_correlated_risk"] == 180.0


def test_news_window_blocks_even_when_spread_is_high() -> None:
    result = validate_trade_risk(
        _signal(),
        _account(news_restricted=True, current_spread=1.20),
        _risk_config(max_spread=0.80),
    )

    assert result["approved"] is False
    assert result["rejection_reason"] == "news_restricted"


def test_spread_filter_blocks_non_news_volatility() -> None:
    result = validate_trade_risk(
        _signal(),
        _account(current_spread=1.20, average_spread=0.20),
        _risk_config(max_spread=0.80),
    )

    assert result["approved"] is False
    assert result["rejection_reason"] == "spread_too_high"
