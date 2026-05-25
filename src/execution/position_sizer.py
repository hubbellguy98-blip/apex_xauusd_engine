"""Position sizing engine."""

from src.core.domain.risk_models import PositionSizingPayload


class InstitutionalPositionSizer:
    def __init__(self, account_equity: float = 10000.0, contract_size: float = 100.0) -> None:
        self._account_equity = account_equity
        self._contract_size = contract_size

    def calculate_lot_size(self, setup, state_snapshot, score_multiplier: float = 1.0) -> PositionSizingPayload:
        risk_pct = min(max(score_multiplier, 0.0), 1.0)
        stop_distance = max(abs(setup.entry_price - setup.stop_loss), 1e-6)
        currency_risk = self._account_equity * (risk_pct / 100.0)
        lots = currency_risk / (stop_distance * self._contract_size)
        return PositionSizingPayload(calculated_lots=max(lots, 0.0), risk_percentage_applied=risk_pct, currency_risk=currency_risk)
