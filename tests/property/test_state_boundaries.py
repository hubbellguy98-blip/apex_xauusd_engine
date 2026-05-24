"""
Apex Engine - Central State Single Source of Truth Unit Verification
Responsibility: Verifies that state engines enforce invariant constraints and track sequences accurately.
Latency Profile: Atomic verification loops.
"""

import pytest
from typing import Dict, Any
from src.strategy.state_manager import CentralRuntimeStateManager
from src.shared.exceptions import ValidationError

@pytest.mark.asyncio
@pytest.mark.unit
async def test_state_invariants_violation_rejection(state_manager: CentralRuntimeStateManager) -> None:
    """Verifies that state mutators reject updates that violate structural invariants."""
    # Attempt to inject an inverted pricing configuration to trigger an active error response
    invalid_mutation = {
        "current_ask": 2400.0,
        "current_bid": 2405.0, # Bid dropped above ask
        "current_mid": 2402.5,
        "current_spread": -5.0
    }
    
    with pytest.raises(Exception) as error_context:
        await state_manager.commit_market_update(invalid_mutation, correlation_id="MALFORMED_TRANSACTION_TEST")