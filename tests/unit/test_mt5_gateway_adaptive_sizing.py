from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from src.core.domain.constants import OrderDirection
from src.core.domain.execution_models import ExecutionReport, OrderRequest, OrderStatus
from src.core.domain.market_data import TickNode
from src.core.domain.risk_models import BrokerSizingSpecification
from src.infrastructure.broker.mt5_config import MT5GatewayConfig
from src.infrastructure.broker.mt5_gateway import MT5BrokerGateway


def test_revalidated_submission_reduces_lots_to_fit_live_stop_risk() -> None:
    gateway = _FakeGateway(loss_per_lot=300.0)
    request = _request(quantity_lots=0.03)

    report, assessment = asyncio.run(
        gateway.route_revalidated_order_submission(
            request,
            maximum_currency_risk=5.0,
            maximum_spread_price=0.35,
            observed_quote_age_seconds=0.0,
            adaptive_lot_sizing=True,
        )
    )

    assert report.status == OrderStatus.FILLED
    assert assessment.is_approved is True
    assert assessment.requested_lots == 0.03
    assert assessment.normalized_lots == 0.01
    assert assessment.adapted_to_fit_risk is True
    assert assessment.demo_minimum_lot_override is False
    assert gateway.submitted_request is not None
    assert gateway.submitted_request.quantity_lots == 0.01


def test_demo_observation_can_use_minimum_lot_when_budget_is_too_small() -> None:
    gateway = _FakeGateway(loss_per_lot=700.0)
    request = _request(quantity_lots=0.03)

    report, assessment = asyncio.run(
        gateway.route_revalidated_order_submission(
            request,
            maximum_currency_risk=5.0,
            maximum_spread_price=0.35,
            observed_quote_age_seconds=0.0,
            adaptive_lot_sizing=True,
            demo_observation_minimum_lot=True,
        )
    )

    assert report.status == OrderStatus.FILLED
    assert assessment.is_approved is True
    assert assessment.normalized_lots == 0.01
    assert assessment.demo_minimum_lot_override is True


class _FakeGateway(MT5BrokerGateway):
    def __init__(self, loss_per_lot: float) -> None:
        super().__init__(
            MT5GatewayConfig(
                login=1,
                password="demo",
                server="demo",
                terminal_path=None,
                symbol="GOLD.i#",
                dry_run=False,
                require_demo=True,
                max_lot=0.05,
            )
        )
        self.loss_per_lot = loss_per_lot
        self.submitted_request: OrderRequest | None = None

    def _require_connected(self) -> None:
        return None

    def read_current_tick(self) -> TickNode:
        return TickNode(
            symbol="GOLD.i#",
            timestamp=datetime.now(timezone.utc),
            bid=4499.90,
            ask=4500.00,
            volume=1,
            sequence_id=1,
            trace_id="TEST",
            correlation_id="TEST",
        )

    def read_sizing_specification(self) -> BrokerSizingSpecification:
        return BrokerSizingSpecification(
            symbol="GOLD.i#",
            account_equity=1000.0,
            account_currency="USD",
            volume_min=0.01,
            volume_step=0.01,
            volume_max=100.0,
        )

    def calculate_stop_loss_currency_per_lot(
        self,
        direction: OrderDirection,
        entry_price: float,
        stop_loss: float,
    ) -> float:
        return self.loss_per_lot

    def _submit_at_validated_quote(self, request: OrderRequest, price: float) -> ExecutionReport:
        self.submitted_request = request
        return ExecutionReport(
            execution_id="TEST_FILL",
            client_order_id=request.client_order_id,
            broker_order_id="1",
            timestamp=datetime.now(timezone.utc),
            status=OrderStatus.FILLED,
            filled_quantity=request.quantity_lots,
            remaining_quantity=0.0,
            average_fill_price=price,
            last_fill_price=price,
            slippage_pips=0.0,
        )


def _request(quantity_lots: float) -> OrderRequest:
    return OrderRequest(
        client_order_id="TEST",
        symbol="GOLD.i#",
        direction=OrderDirection.BUY,
        quantity_lots=quantity_lots,
        entry_price=4500.0,
        stop_loss=4490.0,
        take_profit=4560.0,
        idempotency_key="TEST",
        timestamp=datetime.now(timezone.utc),
    )
