"""Capital protection rules."""

from src.core.domain.risk_models import RiskHaltState


class CapitalProtectionDrawdownEngine:
    def __init__(self, max_daily_loss_pct: float = 3.0, max_consecutive_losses: int = 3) -> None:
        self._max_daily_loss_pct = max_daily_loss_pct
        self._max_consecutive_losses = max_consecutive_losses
        self._consecutive_losses = 0

    def evaluate_systemic_restrictions(self, state_snapshot, current_time) -> RiskHaltState:
        positions = state_snapshot.positions
        if getattr(positions, "is_trading_halted", False):
            return RiskHaltState.TRADING_HALTED
        if getattr(positions, "daily_realized_loss_pct", 0.0) >= self._max_daily_loss_pct:
            return RiskHaltState.DAILY_LOSS_BREACHED
        if self._consecutive_losses >= self._max_consecutive_losses:
            return RiskHaltState.CONSECUTIVE_LOSS_BREACHED
        return RiskHaltState.NOMINAL

    def register_execution_win_event(self) -> None:
        self._consecutive_losses = 0

    def register_execution_loss_event(self) -> None:
        self._consecutive_losses += 1
