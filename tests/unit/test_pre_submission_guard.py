"""Unit tests for final quote-time execution risk validation."""

from src.core.domain.constants import OrderDirection
from src.execution.pre_submission_guard import PreSubmissionRiskGuard


def test_latest_quote_passes_when_spread_and_stop_risk_remain_inside_budget() -> None:
    guard = PreSubmissionRiskGuard(maximum_spread_price=0.35)
    result = guard.evaluate(
        direction=OrderDirection.BUY,
        live_entry_price=2400.10,
        stop_loss=2395.0,
        take_profit=2410.0,
        normalized_lots=0.01,
        currency_risk=5.10,
        maximum_currency_risk=5.10,
        spread_price=0.20,
        observed_quote_age_seconds=0.0,
    )

    assert result.is_approved is True
    assert result.rejection_reasons == []


def test_latest_quote_rejects_increased_loss_and_expanded_spread() -> None:
    guard = PreSubmissionRiskGuard(maximum_spread_price=0.35)
    result = guard.evaluate(
        direction=OrderDirection.BUY,
        live_entry_price=2400.50,
        stop_loss=2395.0,
        take_profit=2410.0,
        normalized_lots=0.01,
        currency_risk=5.50,
        maximum_currency_risk=5.00,
        spread_price=0.50,
        observed_quote_age_seconds=0.0,
    )

    assert result.is_approved is False
    assert "LIVE_STOP_CURRENCY_RISK_EXCEEDS_APPROVED_BUDGET" in result.rejection_reasons
    assert "LIVE_SPREAD_EXCEEDS_PRE_SUBMISSION_LIMIT" in result.rejection_reasons


def test_latest_quote_rejects_stale_quote_or_invalid_trade_geometry() -> None:
    guard = PreSubmissionRiskGuard(maximum_spread_price=0.35, maximum_quote_age_seconds=5.0)
    result = guard.evaluate(
        direction=OrderDirection.SELL,
        live_entry_price=2394.0,
        stop_loss=2405.0,
        take_profit=2395.0,
        normalized_lots=0.01,
        currency_risk=5.0,
        maximum_currency_risk=5.0,
        spread_price=0.10,
        observed_quote_age_seconds=6.0,
    )

    assert result.is_approved is False
    assert "BROKER_QUOTE_IS_STALE" in result.rejection_reasons
    assert "LIVE_ENTRY_INVALIDATES_STOP_TARGET_GEOMETRY" in result.rejection_reasons
