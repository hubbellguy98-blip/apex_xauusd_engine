"""
Apex Engine - Broker Operations & Execution Simulator
Responsibility: Implements the low-level communication adapters needed for backtesting, managing queues and acknowledge lag.
Latency Profile: Simulates network latency metrics inside independent task tracks.
"""

from typing import List, Dict, AsyncGenerator
import structlog
from src.core.domain.execution_models import OrderRequest, ExecutionReport, PositionSnapshot, OrderStatus
from src.backtesting.backtest_config import BacktestConfig
from src.backtesting.fill_engine import BacktestFillEngine

logger = structlog.get_logger()

class BacktestExecutionSimulator:
    """Replaces network broker dependencies, mapping transaction requests to fill functions."""

    __slots__ = ("_config", "_fill_engine", "_pending_queue", "_order_history")

    def __init__(self, config: BacktestConfig, fill_engine: BacktestFillEngine) -> None:
        self._config = config
        self._fill_engine = fill_engine
        self._pending_queue: List[OrderRequest] = []
        self._order_history: List[ExecutionReport] = []

    def submit_simulated_order(self, request: OrderRequest, current_mid: float, vol_ratio: float, timestamp: datetime) -> ExecutionReport:
        """Processes an order through fill rules, logging the outcome to the simulation ledger."""
        if request.order_type.value == "MARKET":
            report = self._fill_engine.fill_market_order(request, current_mid, vol_ratio, timestamp)
            self._order_history.append(report)
            return report
        else:
            # Enqueue pending limits/stops within internal ledger profiles
            self._pending_queue.append(request)
            ack_report = ExecutionReport(
                execution_id=f"ACK_{request.client_order_id}", client_order_id=request.client_order_id,
                broker_order_id="PENDING", timestamp=timestamp, status=OrderStatus.ACKNOWLEDGED,
                filled_quantity=0.0, remaining_quantity=request.quantity_lots, average_fill_price=0.0,
                last_fill_price=0.0, slippage_pips=0.0
            )
            return ack_report

    def evaluate_working_orders(self, high_p: float, low_p: float, timestamp: datetime) -> List[ExecutionReport]:
        """Scans the pending registry against market thresholds to return triggered fills."""
        triggered_fills: List[ExecutionReport] = []
        still_pending: List[OrderRequest] = []

        for req in self._pending_queue:
            triggered, report = self._fill_engine.evaluate_pending_triggers(req, high_p, low_p, timestamp)
            if triggered and report:
                triggered_fills.append(report)
                self._order_history.append(report)
            else:
                still_pending.append(req)

        self._pending_queue = still_pending
        return triggered_fills