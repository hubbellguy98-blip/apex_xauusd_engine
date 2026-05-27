"""MetaTrader 5 broker gateway for demo execution."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator, List, Optional

from src.core.domain.constants import OrderDirection
from src.core.domain.execution_models import (
    ExecutionReport,
    OrderRequest,
    OrderStatus,
    PositionProtectionReport,
    PositionSnapshot,
)
from src.core.domain.market_data import CandleNode, TickNode
from src.core.domain.risk_models import BrokerSizingSpecification, PreSubmissionRiskAssessment
from src.execution.broker_abc import BrokerGatewayABC
from src.execution.pre_submission_guard import PreSubmissionRiskGuard
from src.infrastructure.broker.mt5_config import MT5GatewayConfig


class MT5BrokerGateway(BrokerGatewayABC):
    """Routes orders through a locally running MetaTrader 5 terminal."""

    def __init__(self, config: MT5GatewayConfig) -> None:
        self._config = config
        self._mt5 = None
        self._connected = False
        self._resolved_symbol: Optional[str] = None
        self._tick_sequence = 0

    async def connect(self) -> None:
        try:
            import MetaTrader5 as mt5  # type: ignore
        except ImportError as exc:
            raise RuntimeError("MetaTrader5 package is not installed. Run: python -m pip install MetaTrader5") from exc

        self._mt5 = mt5
        init_kwargs = {
            "login": self._config.login,
            "password": self._config.password,
            "server": self._config.server,
        }
        if self._config.terminal_path:
            init_kwargs["path"] = self._config.terminal_path

        if not mt5.initialize(**init_kwargs):
            code, message = mt5.last_error()
            raise RuntimeError(f"MT5 initialize failed: {code} {message}")

        account = mt5.account_info()
        if account is None:
            mt5.shutdown()
            code, message = mt5.last_error()
            raise RuntimeError(f"MT5 account_info failed: {code} {message}")

        demo_mode = getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)
        if self._config.require_demo and getattr(account, "trade_mode", None) != demo_mode:
            mt5.shutdown()
            raise RuntimeError("MT5 account is not detected as demo. Refusing to connect while demo guard is enabled.")

        self._resolved_symbol = self.resolve_symbol(self._config.symbol)
        self._connected = True

    async def disconnect(self) -> None:
        if self._mt5 is not None:
            self._mt5.shutdown()
        self._connected = False

    def resolve_symbol(self, preferred_symbol: str) -> str:
        self._require_mt5()
        mt5 = self._mt5
        candidates = [
            preferred_symbol,
            preferred_symbol.upper(),
            "XAUUSD",
            "GOLD",
            "GOLD#",
            "XAUUSD#",
        ]
        for symbol in dict.fromkeys(candidates):
            info = mt5.symbol_info(symbol)
            if info is not None:
                if not info.visible and not mt5.symbol_select(symbol, True):
                    continue
                return symbol
        all_symbols = mt5.symbols_get()
        gold_matches = [
            item.name
            for item in all_symbols or []
            if self._is_xauusd_instrument(item.name)
        ]
        if gold_matches:
            symbol = gold_matches[0]
            if mt5.symbol_select(symbol, True):
                return symbol
        raise RuntimeError(
            f"Could not resolve an XAUUSD/Gold spot instrument for {preferred_symbol}. "
            "Open the Gold CFD symbol in MT5 Market Watch and set APEX_SYMBOL to its exact name."
        )

    async def route_order_submission(self, request: OrderRequest) -> ExecutionReport:
        try:
            tick = self.read_current_tick()
        except RuntimeError as exc:
            if "No tick available" in str(exc):
                return self._reject(request, "SYMBOL_TICK_UNAVAILABLE")
            raise
        live_entry_price = tick.ask if request.direction == OrderDirection.BUY else tick.bid
        routed_request = replace(request, entry_price=live_entry_price)
        return self._submit_at_validated_quote(routed_request, live_entry_price)

    async def route_revalidated_order_submission(
        self,
        request: OrderRequest,
        maximum_currency_risk: float,
        maximum_spread_price: float,
        maximum_quote_age_seconds: float = 5.0,
        observed_quote_age_seconds: float | None = None,
    ) -> tuple[ExecutionReport, PreSubmissionRiskAssessment]:
        """Submit only if a recently active quote stream still respects approved risk."""
        self._require_connected()
        volume = self.normalize_order_volume(float(request.quantity_lots))
        try:
            tick = self.read_current_tick()
        except RuntimeError as exc:
            if "No tick available" not in str(exc):
                raise
            assessment = PreSubmissionRiskAssessment(
                is_approved=False,
                live_entry_price=request.entry_price,
                normalized_lots=volume,
                currency_risk=0.0,
                maximum_currency_risk=maximum_currency_risk,
                spread_price=0.0,
                quote_age_seconds=float("inf"),
                rejection_reasons=["BROKER_EXECUTABLE_QUOTE_UNAVAILABLE"],
            )
            return self._reject(request, "PRE_SUBMISSION_REVALIDATION_FAILED: BROKER_EXECUTABLE_QUOTE_UNAVAILABLE"), assessment
        live_entry_price = tick.ask if request.direction == OrderDirection.BUY else tick.bid
        try:
            currency_risk = (
                self.calculate_stop_loss_currency_per_lot(request.direction, live_entry_price, request.stop_loss)
                * volume
                if volume > 0.0
                else 0.0
            )
        except (RuntimeError, ValueError):
            currency_risk = 0.0
        assessment = PreSubmissionRiskGuard(
            maximum_spread_price=maximum_spread_price,
            maximum_quote_age_seconds=maximum_quote_age_seconds,
        ).evaluate(
            direction=request.direction,
            live_entry_price=live_entry_price,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            normalized_lots=volume,
            currency_risk=currency_risk,
            maximum_currency_risk=maximum_currency_risk,
            spread_price=tick.spread,
            observed_quote_age_seconds=(
                float("inf") if observed_quote_age_seconds is None else observed_quote_age_seconds
            ),
        )
        if not assessment.is_approved:
            reason = "PRE_SUBMISSION_REVALIDATION_FAILED: " + ",".join(assessment.rejection_reasons)
            return self._reject(request, reason), assessment
        routed_request = replace(request, entry_price=live_entry_price, quantity_lots=volume)
        return self._submit_at_validated_quote(routed_request, live_entry_price), assessment

    def _submit_at_validated_quote(self, request: OrderRequest, price: float) -> ExecutionReport:
        self._require_connected()
        mt5 = self._mt5
        symbol = self._resolved_symbol or self.resolve_symbol(request.symbol)
        if mt5.positions_get(symbol=symbol):
            return self._reject(request, "ACTIVE_GOLD_POSITION_EXISTS_SINGLE_TRADE_LIMIT")
        volume = self.normalize_order_volume(float(request.quantity_lots))
        if volume <= 0:
            return self._reject(request, "ORDER_VOLUME_BELOW_BROKER_MINIMUM_OR_ZERO")
        order_type = mt5.ORDER_TYPE_BUY if request.direction == OrderDirection.BUY else mt5.ORDER_TYPE_SELL
        payload = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": float(request.stop_loss),
            "tp": float(request.take_profit),
            "deviation": self._config.deviation_points,
            "magic": self._config.magic_number,
            "comment": "apex_xauusd_engine",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        check = mt5.order_check(payload)
        if check is None:
            return self._reject(request, "ORDER_CHECK_RETURNED_NONE")
        if getattr(check, "retcode", None) not in {0, getattr(mt5, "TRADE_RETCODE_DONE", 10009)}:
            return self._reject(
                request,
                f"ORDER_CHECK_FAILED: {getattr(check, 'comment', getattr(check, 'retcode', 'UNKNOWN'))}",
            )

        if self._config.dry_run:
            return self._from_mt5_result(request, check, OrderStatus.ACKNOWLEDGED, dry_run=True)

        result = mt5.order_send(payload)
        if result is None:
            return self._reject(request, "ORDER_SEND_RETURNED_NONE")
        success_codes = {getattr(mt5, "TRADE_RETCODE_DONE", 10009), getattr(mt5, "TRADE_RETCODE_PLACED", 10008)}
        status = OrderStatus.FILLED if getattr(result, "retcode", None) in success_codes else OrderStatus.REJECTED
        return self._from_mt5_result(request, result, status, dry_run=False)

    def read_sizing_specification(self) -> BrokerSizingSpecification:
        """Return account and broker volume constraints used for stop-risk sizing."""
        self._require_connected()
        symbol = self._resolved_symbol or self.resolve_symbol(self._config.symbol)
        account = self._mt5.account_info()
        info = self._mt5.symbol_info(symbol)
        if account is None or info is None:
            raise RuntimeError("MT5 account or symbol sizing information is unavailable.")

        equity = float(getattr(account, "equity", 0.0))
        volume_min = float(getattr(info, "volume_min", 0.0))
        volume_step = float(getattr(info, "volume_step", 0.0))
        volume_max = float(getattr(info, "volume_max", 0.0))
        if equity <= 0.0 or volume_min <= 0.0 or volume_step <= 0.0 or volume_max <= 0.0:
            raise RuntimeError("MT5 returned invalid equity or symbol volume constraints.")
        return BrokerSizingSpecification(
            symbol=symbol,
            account_equity=equity,
            account_currency=str(getattr(account, "currency", "")),
            volume_min=volume_min,
            volume_step=volume_step,
            volume_max=volume_max,
        )

    def calculate_stop_loss_currency_per_lot(
        self,
        direction: OrderDirection,
        entry_price: float,
        stop_loss: float,
    ) -> float:
        """Calculate one-lot stop loss in account currency using the active MT5 account."""
        self._require_connected()
        if direction == OrderDirection.BUY and stop_loss >= entry_price:
            raise ValueError("BUY protective stop must be below entry.")
        if direction == OrderDirection.SELL and stop_loss <= entry_price:
            raise ValueError("SELL protective stop must be above entry.")

        symbol = self._resolved_symbol or self.resolve_symbol(self._config.symbol)
        order_type = self._mt5.ORDER_TYPE_BUY if direction == OrderDirection.BUY else self._mt5.ORDER_TYPE_SELL
        estimated_profit = self._mt5.order_calc_profit(order_type, symbol, 1.0, float(entry_price), float(stop_loss))
        if estimated_profit is None:
            raise RuntimeError("MT5 could not calculate stop-loss currency exposure.")
        loss = -float(estimated_profit)
        if loss <= 0.0:
            raise RuntimeError("MT5 stop-loss calculation did not produce an adverse currency loss.")
        return loss

    def normalize_order_volume(self, requested_lots: float) -> float:
        """Floor order volume to broker step while respecting configured and broker caps."""
        specification = self.read_sizing_specification()
        capped = min(float(requested_lots), self._config.max_lot, specification.volume_max)
        if capped < specification.volume_min:
            return 0.0
        from decimal import Decimal, ROUND_FLOOR

        step = Decimal(str(specification.volume_step))
        normalized_steps = (Decimal(str(capped)) / step).to_integral_value(rounding=ROUND_FLOOR)
        normalized = float(normalized_steps * step)
        return normalized if normalized >= specification.volume_min else 0.0

    async def stream_execution_lifecycle_events(self) -> AsyncGenerator[ExecutionReport, None]:
        while self._connected:
            await asyncio.sleep(1.0)
            if False:
                yield  # keeps this as an async generator without emitting synthetic reports

    async def query_live_positions(self) -> List[PositionSnapshot]:
        self._require_connected()
        positions = self._mt5.positions_get(symbol=self._resolved_symbol)
        snapshots: List[PositionSnapshot] = []
        for position in positions or []:
            direction = (
                OrderDirection.BUY
                if getattr(position, "type", None) == getattr(self._mt5, "POSITION_TYPE_BUY", 0)
                else OrderDirection.SELL
            )
            snapshots.append(
                PositionSnapshot(
                    symbol=position.symbol,
                    net_quantity_lots=float(position.volume),
                    average_entry_price=float(position.price_open),
                    floating_pnl_pips=float(getattr(position, "profit", 0.0)),
                    ticket=int(getattr(position, "ticket", 0)),
                    direction=direction,
                    stop_loss=float(getattr(position, "sl", 0.0)),
                    take_profit=float(getattr(position, "tp", 0.0)),
                    current_price=float(getattr(position, "price_current", 0.0)),
                )
            )
        return snapshots

    async def route_position_stop_update(
        self,
        position: PositionSnapshot,
        proposed_stop_loss: float,
    ) -> PositionProtectionReport:
        """Update only SL for one open MT5 position while retaining its final TP."""
        self._require_connected()
        if position.ticket <= 0:
            return self._protection_reject(position, proposed_stop_loss, "POSITION_TICKET_UNAVAILABLE")
        if position.direction is None:
            return self._protection_reject(position, proposed_stop_loss, "POSITION_DIRECTION_UNAVAILABLE")
        improves_stop = (
            proposed_stop_loss > position.stop_loss
            if position.direction == OrderDirection.BUY
            else proposed_stop_loss < position.stop_loss
        )
        if not improves_stop:
            return self._protection_reject(position, proposed_stop_loss, "STOP_UPDATE_NOT_MORE_PROTECTIVE")

        payload = {
            "action": self._mt5.TRADE_ACTION_SLTP,
            "position": position.ticket,
            "symbol": position.symbol,
            "sl": float(proposed_stop_loss),
            "tp": float(position.take_profit),
            "magic": self._config.magic_number,
            "comment": "apex_staged_trail",
        }
        if self._config.dry_run:
            return PositionProtectionReport(
                position_ticket=position.ticket,
                timestamp=datetime.now(timezone.utc),
                requested_stop_loss=float(proposed_stop_loss),
                retained_take_profit=float(position.take_profit),
                applied=False,
                dry_run=True,
            )

        result = self._mt5.order_send(payload)
        success_codes = {
            getattr(self._mt5, "TRADE_RETCODE_DONE", 10009),
            getattr(self._mt5, "TRADE_RETCODE_PLACED", 10008),
        }
        if result is None or getattr(result, "retcode", None) not in success_codes:
            reason = (
                "STOP_UPDATE_RETURNED_NONE"
                if result is None
                else f"STOP_UPDATE_REJECTED: {getattr(result, 'comment', getattr(result, 'retcode', 'UNKNOWN'))}"
            )
            return self._protection_reject(position, proposed_stop_loss, reason)
        return PositionProtectionReport(
            position_ticket=position.ticket,
            timestamp=datetime.now(timezone.utc),
            requested_stop_loss=float(proposed_stop_loss),
            retained_take_profit=float(position.take_profit),
            applied=True,
            dry_run=False,
        )

    def read_current_tick(self) -> TickNode:
        """Read the currently quoted broker tick without creating an order request."""
        self._require_connected()
        symbol = self._resolved_symbol or self.resolve_symbol(self._config.symbol)
        raw_tick = self._mt5.symbol_info_tick(symbol)
        if raw_tick is None:
            raise RuntimeError(f"No tick available for {symbol}")

        timestamp_msc = getattr(raw_tick, "time_msc", None)
        timestamp = (
            datetime.fromtimestamp(float(timestamp_msc) / 1000.0, tz=timezone.utc)
            if timestamp_msc
            else datetime.fromtimestamp(float(raw_tick.time), tz=timezone.utc)
        )
        self._tick_sequence += 1
        return TickNode(
            symbol=symbol,
            timestamp=timestamp,
            bid=float(raw_tick.bid),
            ask=float(raw_tick.ask),
            volume=int(getattr(raw_tick, "volume", 0)),
            sequence_id=self._tick_sequence,
            trace_id=f"MT5_TICK_{self._tick_sequence}",
            correlation_id="MT5_READ_ONLY_STREAM",
        )

    def read_recent_closed_candles(self, timeframe_minutes: int = 1, count: int = 50) -> List[CandleNode]:
        """Read recent completed broker candles for analytical warmup only."""
        self._require_connected()
        if count <= 0:
            raise ValueError("Candle history count must be positive.")

        mt5 = self._mt5
        timeframe_map = {
            1: mt5.TIMEFRAME_M1,
            5: mt5.TIMEFRAME_M5,
            15: mt5.TIMEFRAME_M15,
            60: mt5.TIMEFRAME_H1,
            240: mt5.TIMEFRAME_H4,
        }
        if timeframe_minutes not in timeframe_map:
            raise ValueError("Supported MT5 candle timeframes are 1, 5, 15, 60, and 240 minutes.")

        symbol = self._resolved_symbol or self.resolve_symbol(self._config.symbol)
        raw_rates = mt5.copy_rates_from_pos(symbol, timeframe_map[timeframe_minutes], 1, count)
        if raw_rates is None:
            raise RuntimeError(f"MT5 returned no closed candle history for {symbol}.")

        timeframe_label = f"{timeframe_minutes}m" if timeframe_minutes < 60 else f"{timeframe_minutes // 60}h"
        candles: List[CandleNode] = []
        ordered_rates = sorted(raw_rates, key=lambda rate: float(rate["time"]))
        for sequence, rate in enumerate(ordered_rates, start=1):
            start_time = datetime.fromtimestamp(float(rate["time"]), tz=timezone.utc)
            candles.append(
                CandleNode(
                    symbol=symbol,
                    timeframe=timeframe_label,
                    start_time=start_time,
                    end_time=start_time + timedelta(minutes=timeframe_minutes),
                    open_p=float(rate["open"]),
                    high_p=float(rate["high"]),
                    low_p=float(rate["low"]),
                    close_p=float(rate["close"]),
                    volume=int(rate["tick_volume"]),
                    ticks_count=int(rate["tick_volume"]),
                    is_closed=True,
                    sequence_id=sequence,
                    trace_id=f"MT5_CANDLE_{timeframe_label}_{sequence}",
                    correlation_id="MT5_READ_ONLY_HISTORY",
                )
            )
        return candles

    def connection_summary(self) -> dict[str, object]:
        self._require_connected()
        account = self._mt5.account_info()
        return {
            "login": getattr(account, "login", None),
            "server": getattr(account, "server", None),
            "trade_mode": getattr(account, "trade_mode", None),
            "currency": getattr(account, "currency", None),
            "balance": getattr(account, "balance", None),
            "symbol": self._resolved_symbol,
            "dry_run": self._config.dry_run,
            "max_lot": self._config.max_lot,
        }

    def _from_mt5_result(self, request: OrderRequest, result, status: OrderStatus, dry_run: bool) -> ExecutionReport:
        retcode = getattr(result, "retcode", 0)
        order_id = str(getattr(result, "order", "CHECK_ONLY" if dry_run else "UNKNOWN"))
        price = float(getattr(result, "price", request.entry_price) or request.entry_price)
        volume = self.normalize_order_volume(float(request.quantity_lots))
        comment = getattr(result, "comment", "")
        return ExecutionReport(
            execution_id=f"MT5_{retcode}_{request.client_order_id}",
            client_order_id=request.client_order_id,
            broker_order_id=order_id,
            timestamp=datetime.now(timezone.utc),
            status=status,
            filled_quantity=0.0 if dry_run else volume,
            remaining_quantity=volume if dry_run else 0.0,
            average_fill_price=0.0 if dry_run else price,
            last_fill_price=price,
            slippage_pips=0.0,
            rejection_reason=None if status != OrderStatus.REJECTED else str(comment or retcode),
        )

    def _reject(self, request: OrderRequest, reason: str) -> ExecutionReport:
        volume = min(float(request.quantity_lots), self._config.max_lot)
        return ExecutionReport(
            execution_id=f"MT5_REJECT_{request.client_order_id}",
            client_order_id=request.client_order_id,
            broker_order_id="NONE",
            timestamp=datetime.now(timezone.utc),
            status=OrderStatus.REJECTED,
            filled_quantity=0.0,
            remaining_quantity=volume,
            average_fill_price=0.0,
            last_fill_price=0.0,
            slippage_pips=0.0,
            rejection_reason=reason,
        )

    @staticmethod
    def _protection_reject(
        position: PositionSnapshot,
        proposed_stop_loss: float,
        reason: str,
    ) -> PositionProtectionReport:
        return PositionProtectionReport(
            position_ticket=position.ticket,
            timestamp=datetime.now(timezone.utc),
            requested_stop_loss=float(proposed_stop_loss),
            retained_take_profit=float(position.take_profit),
            applied=False,
            dry_run=False,
            rejection_reason=reason,
        )

    def _require_connected(self) -> None:
        self._require_mt5()
        if not self._connected:
            raise RuntimeError("MT5 gateway is not connected.")

    def _require_mt5(self) -> None:
        if self._mt5 is None:
            raise RuntimeError("MT5 module is not initialized.")

    @staticmethod
    def _is_xauusd_instrument(symbol: str) -> bool:
        normalized = symbol.upper().replace("_", "").replace("-", "")
        return normalized.startswith("XAUUSD") or normalized == "GOLD" or normalized.startswith("GOLD.")
