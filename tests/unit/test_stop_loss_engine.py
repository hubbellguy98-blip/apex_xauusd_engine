"""Unit coverage for execution-layer demo stop hardening."""

from dataclasses import replace

from src.core.domain.constants import OrderDirection
from src.execution.stop_loss_engine import DynamicStructuralStopEngine, StopHardeningConfig
from tests.factories.candle_factory import CandlePrimitiveFactory
from tests.factories.setup_factory import SetupOpportunityFactory


def _recent_gold_candles():
    return [
        CandlePrimitiveFactory.create_candle(open_p=4460.0, high_p=4462.2, low_p=4459.4, close_p=4461.5),
        CandlePrimitiveFactory.create_candle(open_p=4461.5, high_p=4463.0, low_p=4460.2, close_p=4462.4),
        CandlePrimitiveFactory.create_candle(open_p=4462.4, high_p=4464.1, low_p=4461.1, close_p=4463.2),
        CandlePrimitiveFactory.create_candle(open_p=4463.2, high_p=4465.0, low_p=4462.0, close_p=4464.0),
    ]


def test_tight_buy_stop_is_widened_and_rr_is_preserved() -> None:
    setup = SetupOpportunityFactory.create_setup(entry=4470.0, sl=4468.0, tp=4476.0)
    engine = DynamicStructuralStopEngine()

    result = engine.harden_for_demo_execution(setup, _recent_gold_candles(), current_spread_price=0.26)

    assert result.adjusted is True
    assert result.original_stop_distance == 2.0
    assert result.hardened_stop_distance == 4.0
    assert result.setup.stop_loss == 4466.0
    assert result.setup.take_profit == 4482.0
    assert result.setup.estimated_rr == 3.0


def test_tight_sell_stop_is_widened_above_entry() -> None:
    setup = replace(
        SetupOpportunityFactory.create_setup(entry=4470.0, sl=4472.0, tp=4464.0),
        direction=OrderDirection.SELL,
    )
    engine = DynamicStructuralStopEngine()

    result = engine.harden_for_demo_execution(setup, _recent_gold_candles(), current_spread_price=0.26)

    assert result.adjusted is True
    assert result.setup.stop_loss == 4474.0
    assert result.setup.take_profit == 4458.0
    assert result.setup.estimated_rr == 3.0


def test_already_safe_stop_is_left_unchanged() -> None:
    setup = SetupOpportunityFactory.create_setup(entry=4470.0, sl=4464.0, tp=4488.0)
    engine = DynamicStructuralStopEngine()

    result = engine.harden_for_demo_execution(setup, _recent_gold_candles(), current_spread_price=0.26)

    assert result.adjusted is False
    assert result.setup == setup
    assert result.hardened_stop_distance == 6.0


def test_high_spread_can_expand_required_stop_but_stays_capped() -> None:
    setup = SetupOpportunityFactory.create_setup(entry=4470.0, sl=4468.0, tp=4476.0)
    engine = DynamicStructuralStopEngine(StopHardeningConfig(maximum_stop_distance_price=5.0))

    result = engine.harden_for_demo_execution(setup, _recent_gold_candles(), current_spread_price=0.80)

    assert result.adjusted is True
    assert result.hardened_stop_distance == 5.0
    assert "MAXIMUM_STOP_DISTANCE_CAP" in result.reasons
