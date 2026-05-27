"""Candle-confirmed staged stop protection for a single open trade."""

from __future__ import annotations

from dataclasses import dataclass
import json
from math import floor
from pathlib import Path
from typing import Sequence

from src.core.domain.constants import OrderDirection
from src.core.domain.execution_models import PositionSnapshot
from src.core.domain.market_data import CandleNode


@dataclass(frozen=True, slots=True)
class TrailingStopDecision:
    """A proposed stop update; the broker layer decides whether to apply it."""

    should_modify: bool
    stop_loss: float | None
    confirmed_milestone: int
    protected_milestone: int
    reason: str


@dataclass(frozen=True, slots=True)
class ManagedTradePlan:
    """Original risk frame retained locally after the broker SL advances."""

    symbol: str
    ticket: int
    direction: OrderDirection
    entry: float
    initial_stop_loss: float
    final_take_profit: float
    last_confirmed_milestone: int = 0


@dataclass(frozen=True, slots=True)
class ManagedTradeReconciliation:
    """Startup result deciding whether an existing broker position can be managed safely."""

    status: str
    active_plan: ManagedTradePlan | None
    clear_stale_plan: bool
    blocks_new_entries: bool
    allows_automatic_management: bool


class ManagedTradePlanStore:
    """Persist the single managed-trade risk frame outside source control."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def save(self, plan: ManagedTradePlan) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "symbol": plan.symbol,
            "ticket": plan.ticket,
            "direction": plan.direction.value,
            "entry": plan.entry,
            "initial_stop_loss": plan.initial_stop_loss,
            "final_take_profit": plan.final_take_profit,
            "last_confirmed_milestone": plan.last_confirmed_milestone,
        }
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def load(self) -> ManagedTradePlan | None:
        if not self._path.exists():
            return None
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        return ManagedTradePlan(
            symbol=str(payload["symbol"]),
            ticket=int(payload["ticket"]),
            direction=OrderDirection(payload["direction"]),
            entry=float(payload["entry"]),
            initial_stop_loss=float(payload["initial_stop_loss"]),
            final_take_profit=float(payload["final_take_profit"]),
            last_confirmed_milestone=int(payload.get("last_confirmed_milestone", 0)),
        )

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()


class ManagedTradePlanReconciler:
    """Match local protection state to broker truth without reconstructing missing risk."""

    def __init__(self, price_tolerance: float = 1e-6) -> None:
        if price_tolerance < 0.0:
            raise ValueError("price_tolerance cannot be negative.")
        self._price_tolerance = price_tolerance

    def reconcile(
        self,
        plan: ManagedTradePlan | None,
        broker_positions: Sequence[PositionSnapshot],
    ) -> ManagedTradeReconciliation:
        if not broker_positions:
            return ManagedTradeReconciliation(
                status="STALE_LOCAL_PLAN_CLEARED_NO_OPEN_POSITION" if plan is not None else "NO_OPEN_POSITION",
                active_plan=None,
                clear_stale_plan=plan is not None,
                blocks_new_entries=False,
                allows_automatic_management=False,
            )
        if len(broker_positions) != 1:
            return ManagedTradeReconciliation(
                status="UNSAFE_MULTIPLE_OPEN_GOLD_POSITIONS_NO_AUTO_MANAGEMENT",
                active_plan=None,
                clear_stale_plan=False,
                blocks_new_entries=True,
                allows_automatic_management=False,
            )
        position = broker_positions[0]
        if plan is None:
            return self._unmanaged("OPEN_POSITION_HAS_NO_LOCAL_RISK_PLAN")
        if position.ticket != plan.ticket:
            return self._unmanaged("LOCAL_PLAN_TICKET_DOES_NOT_MATCH_OPEN_POSITION")
        if position.symbol != plan.symbol or position.direction != plan.direction:
            return self._unmanaged("LOCAL_PLAN_SYMBOL_OR_DIRECTION_MISMATCH")
        if not self._price_matches(position.average_entry_price, plan.entry):
            return self._unmanaged("LOCAL_PLAN_ENTRY_PRICE_MISMATCH")
        if not self._price_matches(position.take_profit, plan.final_take_profit):
            return self._unmanaged("LOCAL_PLAN_TAKE_PROFIT_MISMATCH")
        if not self._valid_risk_geometry(plan, position.stop_loss):
            return self._unmanaged("LOCAL_PLAN_OR_BROKER_STOP_GEOMETRY_INVALID")
        return ManagedTradeReconciliation(
            status="MATCHED_OPEN_POSITION_AUTOMATIC_MANAGEMENT_AVAILABLE",
            active_plan=plan,
            clear_stale_plan=False,
            blocks_new_entries=True,
            allows_automatic_management=True,
        )

    def _unmanaged(self, status: str) -> ManagedTradeReconciliation:
        return ManagedTradeReconciliation(
            status=status,
            active_plan=None,
            clear_stale_plan=False,
            blocks_new_entries=True,
            allows_automatic_management=False,
        )

    def _price_matches(self, first: float, second: float) -> bool:
        return abs(first - second) <= self._price_tolerance

    @staticmethod
    def _valid_risk_geometry(plan: ManagedTradePlan, broker_stop: float) -> bool:
        if broker_stop <= 0.0:
            return False
        if plan.direction == OrderDirection.BUY:
            return plan.initial_stop_loss < plan.entry < plan.final_take_profit and broker_stop < plan.final_take_profit
        return plan.final_take_profit < plan.entry < plan.initial_stop_loss and broker_stop > plan.final_take_profit


class InstitutionalTradeLifecycleManager:
    """Protect profit in R milestones while allowing ordinary candle retracements."""

    def __init__(
        self,
        confirmation_buffer_r: float = 0.20,
        trailing_buffer_r: float = 0.15,
        maximum_milestones: int = 6,
    ) -> None:
        if not 0.0 <= confirmation_buffer_r < 1.0:
            raise ValueError("confirmation_buffer_r must be between 0 and 1.")
        if not 0.0 <= trailing_buffer_r < 1.0:
            raise ValueError("trailing_buffer_r must be between 0 and 1.")
        if maximum_milestones < 1:
            raise ValueError("maximum_milestones must be positive.")
        self._confirmation_buffer_r = confirmation_buffer_r
        self._trailing_buffer_r = trailing_buffer_r
        self._maximum_milestones = maximum_milestones

    def evaluate_candle_confirmed_trail(
        self,
        direction: OrderDirection,
        entry: float,
        initial_stop_loss: float,
        current_stop_loss: float,
        final_take_profit: float,
        closed_candle: CandleNode,
        last_confirmed_milestone: int = 0,
    ) -> TrailingStopDecision:
        """Suggest a one-way SL advance only after a candle closes beyond a milestone buffer."""
        risk = abs(entry - initial_stop_loss)
        if risk <= 0.0:
            return TrailingStopDecision(False, None, last_confirmed_milestone, 0, "INVALID_INITIAL_RISK")

        direction_sign = 1.0 if direction == OrderDirection.BUY else -1.0
        planned_rr = abs(final_take_profit - entry) / risk
        maximum_milestone = min(self._maximum_milestones, max(1, floor(planned_rr + 1e-9)))
        favorable_close_r = direction_sign * (closed_candle.close_p - entry) / risk
        confirmed = min(maximum_milestone, floor(favorable_close_r - self._confirmation_buffer_r))
        if confirmed <= last_confirmed_milestone or confirmed < 1:
            return TrailingStopDecision(False, None, last_confirmed_milestone, 0, "NO_NEW_CONFIRMED_MILESTONE")

        # First confirmation cuts initial exposure but retains a small pullback allowance.
        protected_milestone = max(0, confirmed - 1)
        protected_r = protected_milestone - self._trailing_buffer_r
        proposed_stop = entry + (direction_sign * protected_r * risk)

        improves_stop = proposed_stop > current_stop_loss if direction == OrderDirection.BUY else proposed_stop < current_stop_loss
        if not improves_stop:
            return TrailingStopDecision(False, None, confirmed, protected_milestone, "STOP_ALREADY_MORE_PROTECTIVE")

        return TrailingStopDecision(
            True,
            float(proposed_stop),
            confirmed,
            protected_milestone,
            "CANDLE_CONFIRMED_BUFFERED_MILESTONE_TRAIL",
        )
