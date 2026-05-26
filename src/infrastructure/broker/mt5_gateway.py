"""MetaTrader 5 broker gateway for demo execution."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import AsyncGenerator, List, Optional

from src.core.domain.constants import OrderDirection
from src.core.domain.execution_models import ExecutionReport, OrderRequest, OrderStatus, PositionSnapshot
from src.core.domain.market_data import TickNode
from src.execution.broker_abc import BrokerGatewayABC
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
        self._require_connected()
        mt5 = self._mt5
        symbol = self._resolved_symbol or self.resolve_symbol(request.symbol)
        volume = min(float(request.quantity_lots), self._config.max_lot)
        if volume <= 0:
            return self._reject(request, "ORDER_VOLUME_ZERO_OR_NEGATIVE")

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return self._reject(request, "SYMBOL_TICK_UNAVAILABLE")

        order_type = mt5.ORDER_TYPE_BUY if request.direction == OrderDirection.BUY else mt5.ORDER_TYPE_SELL
        price = float(tick.ask if request.direction == OrderDirection.BUY else tick.bid)
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
            snapshots.append(
                PositionSnapshot(
                    symbol=position.symbol,
                    net_quantity_lots=float(position.volume),
                    average_entry_price=float(position.price_open),
                    floating_pnl_pips=float(getattr(position, "profit", 0.0)),
                )
            )
        return snapshots

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
        volume = min(float(request.quantity_lots), self._config.max_lot)
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
