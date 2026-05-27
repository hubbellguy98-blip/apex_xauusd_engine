"""Position sizing engine."""

from decimal import Decimal, ROUND_FLOOR
from typing import Callable, Optional

from src.core.domain.risk_models import PositionSizingPayload


class InstitutionalPositionSizer:
    """Size positions from account-currency loss at the proposed protective stop."""

    def __init__(
        self,
        account_equity: float = 10000.0,
        contract_size: float = 100.0,
        maximum_lots: Optional[float] = None,
        minimum_lots: float = 0.0,
        volume_step: float = 0.0,
        loss_per_lot_calculator: Optional[Callable[[object], float]] = None,
    ) -> None:
        if account_equity <= 0.0:
            raise ValueError("Account equity must be positive for position sizing.")
        if contract_size <= 0.0:
            raise ValueError("Fallback contract size must be positive.")
        if minimum_lots < 0.0 or volume_step < 0.0:
            raise ValueError("Broker volume limits cannot be negative.")
        self._account_equity = account_equity
        self._contract_size = contract_size
        self._maximum_lots = maximum_lots
        self._minimum_lots = minimum_lots
        self._volume_step = volume_step
        self._loss_per_lot_calculator = loss_per_lot_calculator

    def calculate_lot_size(self, setup, state_snapshot, score_multiplier: float = 1.0) -> PositionSizingPayload:
        target_risk_pct = min(max(score_multiplier, 0.0), 1.0)
        stop_distance = max(abs(setup.entry_price - setup.stop_loss), 1e-6)
        target_currency_risk = self._account_equity * (target_risk_pct / 100.0)
        currency_loss_per_lot = (
            float(self._loss_per_lot_calculator(setup))
            if self._loss_per_lot_calculator is not None
            else stop_distance * self._contract_size
        )
        if currency_loss_per_lot <= 0.0:
            raise ValueError("Stop-loss currency risk per lot must be positive.")

        calculated_lots = target_currency_risk / currency_loss_per_lot
        lots = max(calculated_lots, 0.0)
        if self._maximum_lots is not None:
            lots = min(lots, max(self._maximum_lots, 0.0))
        lots = self._round_down_to_broker_step(lots)
        if lots < self._minimum_lots:
            lots = 0.0

        applied_currency_risk = lots * currency_loss_per_lot
        applied_risk_pct = (applied_currency_risk / self._account_equity) * 100.0
        return PositionSizingPayload(
            calculated_lots=lots,
            risk_percentage_applied=applied_risk_pct,
            currency_risk=applied_currency_risk,
        )

    def _round_down_to_broker_step(self, lots: float) -> float:
        if self._volume_step <= 0.0:
            return lots
        step = Decimal(str(self._volume_step))
        steps = (Decimal(str(lots)) / step).to_integral_value(rounding=ROUND_FLOOR)
        return float(steps * step)
