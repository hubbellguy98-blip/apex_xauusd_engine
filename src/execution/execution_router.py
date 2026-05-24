"""
Apex Engine - Low Latency Execution Coordination Orchestrator
Responsibility: Houses sequential scheduling queues, manages symbol locks, handles error recoveries, and reconciles positions.
Latency Profile: Asynchronous message queue distribution running on single-threaded loops.
"""

import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Any
import structlog

from src.core.base_engine import BaseSubsystem
from src.core.events.event_bus import EventBus
from src.core.events.event_types import EngineEventType
from src.strategy.state_manager import CentralRuntimeStateManager

# Core Domain & Logic Layer Imports
from src.core.domain.execution_models import OrderRequest, ExecutionReport, PositionSnapshot, OrderStatus
from src.execution.broker_abc import BrokerGatewayABC
from src.execution.slippage_protection import ExecutionSlippageProtectionEngine
from src.execution.order_lifecycle import OrderLifecycleStateMachine
from src.shared.exceptions import InfrastructureError, StateCorruptionError

logger = structlog.get_logger()

class HighSpeedExecutionOrchestrator(BaseSubsystem):
    """Orchestrates internal queues, synchronizes open trades, handles idempotency, and runs broker reconciliation loops."""

    def __init__(self, event_bus: EventBus, state_manager: CentralRuntimeStateManager, broker_gateway: BrokerGatewayABC) -> None:
        super().__init__("HighSpeedExecutionOrchestrator")
        self._event_bus = event_bus
        self._state_manager = state_manager
        self._broker = broker_gateway

        # Instantiate composite verification utilities
        self._protection_gate = ExecutionSlippageProtectionEngine()
        self._lifecycle_machine = OrderLifecycleStateMachine()

        # Operational queues and bounded allocation caches
        self._execution_queue: asyncio.Queue[OrderRequest] = asyncio.Queue(maxsize=1000)
        self._active_orders_ledger: Dict[str, ExecutionReport] = {}
        self._idempotency_registry: Dict[str, datetime] = {}
        
        # Concurrency safety parameters
        self._symbol_execution_locks: Dict[str, asyncio.Lock] = {}
        self._orchestrator_loop_task: Optional[asyncio.Task] = None
        self._reconciliation_task: Optional[asyncio.Task] = None
        
        self._emergency_halt_active = False

    async def bootstrap(self) -> None:
        """Establishes network links and spins up background worker loops."""
        await self._broker.connect()
        
        # Initialize scheduling worker tasks
        self._orchestrator_loop_task = asyncio.create_task(self._process_order_queue_worker())
        self._reconciliation_task = asyncio.create_task(self._run_periodic_broker_reconciliation())
        
        # Attach downstream updates listener loops
        asyncio.create_task(self._handle_broker_websocket_stream())
        
        logger.info("execution_orchestrator.bootstrap_complete", pipeline_status="OPERATIONAL")

    async def terminate(self) -> None:
        """Safely flatlines active tasks and tears down broker connection links."""
        self._emergency_halt_active = True
        
        if self._orchestrator_loop_task:
            self._orchestrator_loop_task.cancel()
        if self._reconciliation_task:
            self._reconciliation_task.cancel()

        await self._broker.disconnect()
        self._active_orders_ledger.clear()
        self._idempotency_registry.clear()
        logger.info("execution_orchestrator.terminated")

    def trigger_emergency_shutdown(self) -> None:
        """Activates defensive firewalls, revoking active order placement privileges completely."""
        self._emergency_halt_active = True
        logger.critical("execution_orchestrator.emergency_shutdown_activated_system_locked")

    async def submit_execution_intent(self, request: OrderRequest) -> bool:
        """Pushes an authenticated trading instruction payload into the low-latency queue."""
        if self._emergency_halt_active:
            logger.error("execution_orchestrator.submission_blocked_emergency_halt_active", order=request.client_order_id)
            return False

        # 1. Enforce Strict Idempotency Verification Rules
        if request.idempotency_key in self._idempotency_registry:
            logger.warn("execution_orchestrator.duplicate_idempotency_key_blocked", key=request.idempotency_key)
            return False
        
        self._idempotency_registry[request.idempotency_key] = datetime.utcnow()

        try:
            self._execution_queue.put_nowait(request)
            logger.debug("execution_orchestrator.order_queued", order=request.client_order_id)
            return True
        except asyncio.QueueFull:
            logger.critical("execution_orchestrator.queue_overflow_failure_drop_frame")
            return False

    async def _process_order_queue_worker(self) -> None:
        """Background pipeline consumer that extracts items from the queue and routes them to the exchange."""
        while not self._emergency_halt_active:
            try:
                request = await self._execution_queue.pop() # Wait for incoming execution packets
                
                # Fetch asset-specific symbol execution locks to eliminate simultaneous duplicate entries
                if request.symbol not in self._symbol_execution_locks:
                    self._symbol_execution_locks[request.symbol] = asyncio.Lock()
                
                async with self._symbol_execution_locks[request.symbol]:
                    await self._execute_order_routing_sequence(request)
                    
                self._execution_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as ex:
                logger.error("execution_orchestrator.queue_worker_exception", error=str(ex))

    async def _execute_order_routing_sequence(self, request: OrderRequest) -> None:
        """Validates slippage and passes transaction frames to the lower provider interfaces."""
        state_snap = self._state_manager.snapshot
        
        # 1. Perform Real-Time Pre-Routing Slippage & Spread Validation Filters
        is_safe, safety_msg = self._protection_gate.verify_execution_parameters(request, state_snap)
        if not is_safe:
            logger.warn("execution_orchestrator.pre_routing_safety_gate_rejected", order=request.client_order_id, cause=safety_msg)
            return

        # Initialize base entry configuration records inside local status registers
        initial_report = ExecutionReport(
            execution_id=f"EX_PRE_{request.client_order_id}", client_order_id=request.client_order_id,
            broker_order_id="UNASSIGNED", timestamp=datetime.utcnow(), status=OrderStatus.PENDING_SUBMIT,
            filled_quantity=0.0, remaining_quantity=request.quantity_lots, average_fill_price=0.0,
            last_fill_price=0.0, slippage_pips=0.0
        )
        self._active_orders_ledger[request.client_order_id] = initial_report

        # 2. Route transactional requests to the lower broker connectivity engines
        try:
            logger.info("execution_orchestrator.dispatching_payload_to_broker", order=request.client_order_id)
            report = await self._broker.route_order_submission(request)
            self._update_local_ledger_state(report)
        except Exception as ex:
            logger.error("execution_orchestrator.broker_routing_transport_failed", order=request.client_order_id, error=str(ex))
            # Trigger emergency fallback recovery transformations
            fail_report = ExecutionReport(
                execution_id=f"EX_ERR_{request.client_order_id}", client_order_id=request.client_order_id,
                broker_order_id="FAILED_NODE", timestamp=datetime.utcnow(), status=OrderStatus.REJECTED,
                filled_quantity=0.0, remaining_quantity=request.quantity_lots, average_fill_price=0.0,
                last_fill_price=0.0, slippage_pips=0.0, rejection_reason=f"GATEWAY_TRANSPORT_EXCEPTION: {str(ex)}"
            )
            self._update_local_ledger_state(fail_report)

    def _update_local_ledger_state(self, report: ExecutionReport) -> None:
        """Processes transaction records through state verification frameworks to maintain synchronization."""
        current_record = self._active_orders_ledger.get(report.client_order_id)
        if current_record:
            # Validate transition safety before applying mutations to in-memory ledgers
            self._lifecycle_machine.validate_lifecycle_transition(report.client_order_id, current_record.status, report.status)
        
        self._active_orders_ledger[report.client_order_id] = report
        
        # Publish structural transaction alerts out to the central communications bus
        # self._event_bus.publish(EngineEventType.ORDER_FILLED, report)

    async def _handle_broker_websocket_stream(self) -> None:
        """Listens for streaming execution metrics generated via remote exchange links."""
        try:
            async for execution_report in self._broker.stream_execution_lifecycle_events():
                logger.info("execution_orchestrator.websocket_event_received", order=execution_report.client_order_id, update_status=execution_report.status.value)
                self._update_local_ledger_state(execution_report)
                
                # Dynamically update positions matrix configurations inside state repositories
                if execution_report.status == OrderStatus.FILLED:
                    pos_mutations = {"active_position_count": 1, "net_exposure_lots": execution_report.filled_quantity}
                    await self._state_manager.commit_position_update(pos_mutations, f"FILL_SYNC_{execution_report.execution_id}")
        except asyncio.CancelledError:
            pass
        except Exception as ex:
            logger.error("execution_orchestrator.websocket_stream_consumer_failed", error=str(ex))

    async def _run_periodic_broker_reconciliation(self) -> None:
        """Runs a background loop to verify matching data sets and reconcile local logs against broker records."""
        while not self._emergency_halt_active:
            try:
                await asyncio.sleep(30.0)  # Reconciliation evaluation interval pass limits
                logger.debug("execution_orchestrator.initiating_reconciliation_cycle")
                
                broker_positions = await self._broker.query_live_positions()
                
                # Identify ghost orders or orphaned data configurations
                state_snap = self._state_manager.snapshot
                if state_snap.positions.active_position_count > 0 and not broker_positions:
                    logger.critical("execution_orchestrator.reconciliation_divergence_detected_orphaned_positions_active")
                    # Enforce protection corrections
                    correction = {"active_position_count": 0, "net_exposure_lots": 0.0}
                    await self._state_manager.commit_position_update(correction, "RECONCILIATION_EMERGENCY_MUTATION")
                    
            except asyncio.CancelledError:
                break
            except Exception as ex:
                logger.error("execution_orchestrator.reconciliation_cycle_failed", error=str(ex))

    @property
    def tracking_ledger(self) -> Dict[str, ExecutionReport]:
        return self._active_orders_ledger
