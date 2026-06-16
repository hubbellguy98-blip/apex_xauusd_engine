from datetime import datetime, timedelta

import pytest

from src.core.domain.constants import OrderDirection
from src.core.domain.setup_models import SetupType
from src.strategy.ict_smc_strategy_selector import ICTSMCStrategySelector, StrategyDefinition


def _context() -> dict:
    candles = [
        {"open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i, "close": 100.5 + i, "timestamp": i}
        for i in range(30)
    ]
    return {
        "candles": candles,
        "candles_by_timeframe": {"1m": candles, "15m": candles},
        "session_context": {"session": "LONDON_KILLZONE"},
        "latest_sweep_event": {"id": "sweep_1"},
        "htf_bias": {"bias_direction": "bullish"},
    }


def test_selector_picks_highest_scored_tradeable_strategy() -> None:
    def weaker(context, config):
        return {
            "trade_allowed": True,
            "direction": "bullish",
            "entry": {"entry_price": 100.0},
            "risk": {"stop_loss": 98.0, "target": 106.0, "rr": 3.0},
            "score": {"total_score": 7.2, "trade_allowed": True},
        }

    def stronger(context, config):
        return {
            "trade_allowed": True,
            "direction": "bearish",
            "entry": {"entry_price": 110.0},
            "risk": {"stop_loss": 113.0, "target": 101.0, "rr": 3.0},
            "score": {"total_score": 8.8, "trade_allowed": True},
        }

    selector = ICTSMCStrategySelector(
        (
            StrategyDefinition("weak", "Weak", weaker, SetupType.LIQUIDITY_SWEEP_REVERSAL),
            StrategyDefinition("strong", "Strong", stronger, SetupType.FVG_CONTINUATION),
        )
    )

    result = selector.evaluate(_context())

    assert result.selected is not None
    assert result.selected.definition.key == "strong"
    assert result.selected.direction is OrderDirection.SELL
    assert result.selected.normalized_score == 88.0


def test_selector_rejects_tradeable_signal_missing_required_prices() -> None:
    def missing_prices(context, config):
        return {
            "trade_allowed": True,
            "direction": "bullish",
            "score": {"total_score": 9.0, "trade_allowed": True},
        }

    selector = ICTSMCStrategySelector(
        (StrategyDefinition("missing", "Missing", missing_prices, SetupType.LIQUIDITY_SWEEP_REVERSAL),)
    )

    result = selector.evaluate(_context())

    assert result.selected is None
    assert result.evaluations[0].status == "REJECTED"
    assert "entry" in result.evaluations[0].reason


def test_selector_isolates_strategy_exceptions() -> None:
    def broken(context, config):
        raise RuntimeError("strategy exploded")

    selector = ICTSMCStrategySelector(
        (StrategyDefinition("broken", "Broken", broken, SetupType.LIQUIDITY_SWEEP_REVERSAL),)
    )

    result = selector.evaluate(_context())

    assert result.selected is None
    assert result.evaluations[0].status == "ERROR"
    assert "strategy exploded" in result.evaluations[0].reason


def test_selector_builds_live_setup_and_confirmation_contracts() -> None:
    def valid(context, config):
        return {
            "signal_id": "unit_signal_1",
            "trade_allowed": True,
            "direction": "bullish",
            "entry": {"entry_price": 100.0},
            "risk": {"stop_loss": 98.0, "target": 106.0, "rr": 3.0},
            "score": {
                "total_score": 8.9,
                "trade_allowed": True,
                "component_scores": {"displacement": 8.0, "htf_alignment": 9.0, "session_timing": 8.5},
            },
        }

    selector = ICTSMCStrategySelector(
        (StrategyDefinition("valid", "Valid", valid, SetupType.ORDER_BLOCK_CONTINUATION),)
    )
    selected = selector.evaluate(_context()).selected
    assert selected is not None

    now = datetime(2026, 6, 14, 12, 0, 0)
    setup = selector.build_setup_node(
        selected,
        setup_id="STP_TEST",
        now=now,
        correlation_id="UNIT",
        timeframe="1m",
    )
    confirmation = selector.build_confirmation_snapshot(selected, now=now)

    assert setup.direction is OrderDirection.BUY
    assert setup.setup_type is SetupType.ORDER_BLOCK_CONTINUATION
    assert setup.estimated_rr == pytest.approx(3.0)
    assert setup.confidence_score == pytest.approx(89.0)
    assert setup.expiration_time == now + timedelta(minutes=20)
    assert confirmation.is_validated is True
    assert "valid" in confirmation.validated_components


def test_selector_default_gate_skips_disabled_report_loser_strategy() -> None:
    def valid_disabled_strategy(context, config):
        return {
            "trade_allowed": True,
            "direction": "bullish",
            "entry": {"entry_price": 100.0},
            "risk": {"stop_loss": 98.0, "target": 106.0, "rr": 3.0},
            "score": {"total_score": 9.2, "trade_allowed": True},
        }

    selector = ICTSMCStrategySelector(
        (
            StrategyDefinition(
                "sweep_mss_fvg",
                "Sweep MSS FVG",
                valid_disabled_strategy,
                SetupType.LIQUIDITY_SWEEP_REVERSAL,
            ),
        )
    )

    result = selector.evaluate(_context())

    assert result.selected is None
    assert result.evaluations[0].status == "SKIPPED"
    assert result.evaluations[0].reason == "strategy_disabled_by_risk_gate"


def test_selector_default_gate_requires_elite_score_unless_overridden() -> None:
    def high_probability_only(context, config):
        return {
            "trade_allowed": True,
            "direction": "bullish",
            "entry": {"entry_price": 100.0},
            "risk": {"stop_loss": 98.0, "target": 106.0, "rr": 3.0},
            "score": {"total_score": 8.7, "trade_allowed": True},
        }

    selector = ICTSMCStrategySelector(
        (StrategyDefinition("candidate", "Candidate", high_probability_only, SetupType.FVG_CONTINUATION),)
    )

    blocked = selector.evaluate(_context())
    allowed = selector.evaluate(_context(), {"minimum_score": 75.0})

    assert blocked.selected is None
    assert "score_below_minimum:88" in blocked.evaluations[0].reason
    assert allowed.selected is not None
