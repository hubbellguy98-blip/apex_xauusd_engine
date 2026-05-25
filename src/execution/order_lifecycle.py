"""Order lifecycle transition validation."""

from src.core.domain.execution_models import OrderStatus
from src.shared.exceptions import StateCorruptionError


class OrderLifecycleStateMachine:
    _ALLOWED = {
        OrderStatus.PENDING_SUBMIT: {OrderStatus.ACKNOWLEDGED, OrderStatus.REJECTED, OrderStatus.CANCELED},
        OrderStatus.ACKNOWLEDGED: {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELED},
        OrderStatus.PARTIALLY_FILLED: {OrderStatus.FILLED, OrderStatus.CANCELED},
        OrderStatus.FILLED: set(),
        OrderStatus.REJECTED: set(),
        OrderStatus.CANCELED: set(),
    }

    def validate_lifecycle_transition(self, order_id: str, current: OrderStatus, incoming: OrderStatus) -> None:
        if incoming == current:
            return
        if incoming not in self._ALLOWED.get(current, set()):
            raise StateCorruptionError(f"Invalid order transition for {order_id}: {current.value} -> {incoming.value}")
