"""
Apex Engine - Pre-Execution Risk Firewall Unit Verification Suite
Responsibility: Verifies position sizing rules, drawdown limits, and safety controls.
Latency Profile: Evaluates firewall validation branches.
"""

import pytest
from datetime import datetime, timezone
from src.core.domain.constants import OrderDirection
from src.execution.position_sizer import InstitutionalPositionSizer
from src.execution.drawdown_protection import CapitalProtectionDrawdownEngine
from src.core.domain.risk_models import RiskHaltState
from tests.factories.setup_factory import SetupOpportunityFactory
from tests.factories.candle_factory import CandlePrimitiveFactory

@pytest.mark.unit
def test_position_sizing_leverage_caps(state_manager: CentralRuntimeStateManager) -> None:
    """Verifies that lot-sizing engines calculate fractional allocations correctly."""
    sizer = InstitutionalPositionSizer(contract_size=100.0)
    setup = SetupOpportunityFactory.create_setup(entry=2400.0, sl=2395.0, tp=2415.0)
    state = state_manager.snapshot

    sizing_payload = sizer.calculate_lot_size(setup, state, score_multiplier=1.0)
    assert sizing_payload.calculated_lots > 0.0
    assert sizing_payload.risk_percentage_applied <= 1.0

@pytest.mark.unit
def test_drawdown_firewall_systemic_halt(state_manager: CentralRuntimeStateManager) -> None:
    """Verifies that drawdown protection engines trigger platform halts when capital risk thresholds are crossed."""
    firewall = CapitalProtectionDrawdownEngine(max_daily_loss_pct=3.0, max_consecutive_losses=3)
    state = state_manager.snapshot
    
    # Simulate an active daily capital depletion breach inside a mocked state snapshot configuration
    from dataclasses import replace
    mutated_positions = replace(state.positions, daily_realized_loss_pct=3.5)
    mocked_state = replace(state, positions=mutated_positions)

    halt_state = firewall.evaluate_systemic_restrictions(mocked_state, datetime.now(timezone.utc))
    assert halt_state == RiskHaltState.DAILY_LOSS_BREACHED