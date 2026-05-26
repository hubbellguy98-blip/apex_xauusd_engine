"""Position sizing engine."""

from typing import Optional

from src.core.domain.risk_models import PositionSizingPayload


class InstitutionalPositionSizer:
    def __init__(
        self,
        account_equity: float = 10000.0,
        contract_size: float = 100.0,
        maximum_lots: Optional[float] = None,
    ) -> None:
        self._account_equity = account_equity
        self._contract_size = contract_size
        self._maximum_lots = maximum_lots

    def calculate_lot_size(self, setup, state_snapshot, score_multiplier: float = 1.0) -> PositionSizingPayload:
        target_risk_pct = min(max(score_multiplier, 0.0), 1.0)
        stop_distance = max(abs(setup.entry_price - setup.stop_loss), 1e-6)
        target_currency_risk = self._account_equity * (target_risk_pct / 100.0)
        calculated_lots = target_currency_risk / (stop_distance * self._contract_size)
        lots = max(calculated_lots, 0.0)
        if self._maximum_lots is not None:
            lots = min(lots, max(self._maximum_lots, 0.0))

        applied_currency_risk = lots * stop_distance * self._contract_size
        applied_risk_pct = (applied_currency_risk / self._account_equity) * 100.0
        return PositionSizingPayload(
            calculated_lots=lots,
            risk_percentage_applied=applied_risk_pct,
            currency_risk=applied_currency_risk,
        )
