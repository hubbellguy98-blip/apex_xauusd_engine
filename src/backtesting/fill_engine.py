"""Backtest order fill simulation."""

from datetime import datetime

from src.core.domain.execution_models import ExecutionReport, OrderRequest, OrderStatus


class BacktestFillEngine:
    def fill_market_order(self, request: OrderRequest, current_mid: float, vol_ratio: float, timestamp: datetime) -> ExecutionReport:
        slippage = max(vol_ratio - 1.0, 0.0) * 0.1
        return ExecutionReport(
            execution_id=f"SIM_FILL_{request.client_order_id}",
            client_order_id=request.client_order_id,
            broker_order_id=f"SIM_{request.client_order_id}",
            timestamp=timestamp,
            status=OrderStatus.FILLED,
            filled_quantity=request.quantity_lots,
            remaining_quantity=0.0,
            average_fill_price=current_mid,
            last_fill_price=current_mid,
            slippage_pips=slippage,
        )

    def evaluate_pending_triggers(self, request: OrderRequest, high_p: float, low_p: float, timestamp: datetime):
        entry = request.entry_price
        if low_p <= entry <= high_p:
            return True, self.fill_market_order(request, entry, 1.0, timestamp)
        return False, None
