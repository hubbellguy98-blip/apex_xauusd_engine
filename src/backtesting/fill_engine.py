"""Backtest order fill simulation."""

from datetime import datetime

from src.core.domain.constants import OrderDirection
from src.core.domain.execution_models import ExecutionReport, OrderRequest, OrderStatus
from src.backtesting.backtest_config import BacktestConfig


class BacktestFillEngine:
    """Apply explicit adverse transaction-cost assumptions to simulated fills."""

    def __init__(
        self,
        spread_price: float,
        price_unit_per_pip: float,
        base_slippage_price: float = 0.0,
        volatility_slippage_price: float = 0.0,
    ) -> None:
        if spread_price <= 0.0:
            raise ValueError("Simulated execution requires a positive spread.")
        if price_unit_per_pip <= 0.0:
            raise ValueError("Simulated execution requires a positive price-unit-per-pip conversion.")
        if base_slippage_price < 0.0 or volatility_slippage_price < 0.0:
            raise ValueError("Simulated slippage cannot improve an order.")
        self._spread_price = spread_price
        self._price_unit_per_pip = price_unit_per_pip
        self._base_slippage_price = base_slippage_price
        self._volatility_slippage_price = volatility_slippage_price

    @classmethod
    def from_config(cls, config: BacktestConfig) -> "BacktestFillEngine":
        return cls(
            spread_price=config.spread_price,
            price_unit_per_pip=config.price_unit_per_pip,
            base_slippage_price=config.base_slippage_price,
            volatility_slippage_price=config.volatility_slippage_price,
        )

    def fill_market_order(self, request: OrderRequest, current_mid: float, vol_ratio: float, timestamp: datetime) -> ExecutionReport:
        adverse_slippage_price = self._base_slippage_price + (
            max(vol_ratio - 1.0, 0.0) * self._volatility_slippage_price
        )
        transaction_cost_price = (self._spread_price / 2.0) + adverse_slippage_price
        fill_price = (
            current_mid + transaction_cost_price
            if request.direction == OrderDirection.BUY
            else current_mid - transaction_cost_price
        )
        return ExecutionReport(
            execution_id=f"SIM_FILL_{request.client_order_id}",
            client_order_id=request.client_order_id,
            broker_order_id=f"SIM_{request.client_order_id}",
            timestamp=timestamp,
            status=OrderStatus.FILLED,
            filled_quantity=request.quantity_lots,
            remaining_quantity=0.0,
            average_fill_price=fill_price,
            last_fill_price=fill_price,
            slippage_pips=adverse_slippage_price / self._price_unit_per_pip,
        )

    def evaluate_pending_triggers(self, request: OrderRequest, high_p: float, low_p: float, timestamp: datetime):
        entry = request.entry_price
        if low_p <= entry <= high_p:
            return True, self.fill_market_order(request, entry, 1.0, timestamp)
        return False, None
