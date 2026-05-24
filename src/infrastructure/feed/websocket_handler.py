"""
Apex Engine - Twelve Data WebSocket Transport Client Layer
Responsibility: Robust async transport interface executing frame parse loops.
Latency Profile: Driven by non-blocking network socket polling patterns.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import AsyncGenerator, List, Optional
import websockets
from pydantic import SecretStr
import structlog
from src.core.domain.constants import EventPriority
from src.core.domain.market_data import TickNode
from src.infrastructure.feed.base_provider import MarketDataProviderABC

logger = structlog.get_logger()

class TwelveDataWebSocketClient(MarketDataProviderABC):
    """Manages secure session connectivity, subscriptions, and frame serialization loops."""

    def __init__(self, api_key: SecretStr, ws_url: str = "wss://ws.twelvedata.com/v1/quotes/price") -> None:
        self._api_key = api_key
        self._ws_url = ws_url
        self._socket: Optional[websockets.WebSocketClientConnector] = None
        self._outbound_queue: asyncio.Queue[TickNode] = asyncio.Queue()
        self._sequence_counter = 0
        self._is_connected = False

    async def connect(self) -> None:
        """Constructs safe network handshakes and initiates persistent listening contexts."""
        if self._is_connected:
            return
        
        url_with_auth = f"{self._ws_url}?apikey={self._api_key.get_secret_value()}"
        try:
            self._socket = await websockets.connect(url_with_auth, ping_interval=20, ping_timeout=10)
            self._is_connected = True
            logger.info("twelvedata_ws.connected", url=self._ws_url)
        except Exception as ex:
            logger.error("twelvedata_ws.connection_failed", error=str(ex))
            raise ex

    async def disconnect(self) -> None:
        """Disconnects transport connections cleanly."""
        self._is_connected = False
        if self._socket:
            await self._socket.close()
        logger.info("twelvedata_ws.disconnected")

    async def subscribe(self, symbols: List[str]) -> None:
        """Transmits structural JSON subscriptions over the active channel."""
        if not self._is_connected or not self._socket:
            raise RuntimeError("WebSocket transport unavailable for asset subscription routing.")
        
        payload = {
            "action": "subscribe",
            "params": {
                "symbols": ",".join(symbols)
            }
        }
        await self._socket.send(json.dumps(payload))
        logger.info("twelvedata_ws.subscription_sent", symbols=symbols)

    async def start_ingest_loop(self) -> None:
        """Spins up background workers to read frame inputs sequentially."""
        if not self._socket:
            return
        
        try:
            async for raw_frame in self._socket:
                parsed_frame = json.loads(raw_frame)
                
                # Filter heartbeats and subscription confirmations
                if parsed_frame.get("event") == "heartbeat":
                    continue
                if parsed_frame.get("status") == "ok":
                    continue
                
                if "price" in parsed_frame:
                    self._sequence_counter += 1
                    # Parse timestamp format from endpoint (Expected Epoch integer/string values)
                    ts_raw = parsed_frame.get("timestamp", int(datetime.now(timezone.utc).timestamp()))
                    ts_obj = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
                    
                    tick = TickNode(
                        timestamp=ts_obj,
                        sequence_id=self._sequence_counter,
                        trace_id=f"TD_TICK_{self._sequence_counter}_{ts_raw}",
                        correlation_id="LIVE_STREAM",
                        priority=EventPriority.CRITICAL,
                        symbol=parsed_frame["symbol"],
                        bid=float(parsed_frame["price"]), # Note: Twelve data price format adjustments mapped
                        ask=float(parsed_frame["price"]), # Map spread primitives explicitly inside system buffers
                        volume=int(parsed_frame.get("day_volume", 0))
                    )
                    await self._outbound_queue.put(tick)
        except asyncio.CancelledError:
            pass
        except Exception as ex:
            logger.error("twelvedata_ws.ingest_loop_error", error=str(ex))
            await self.disconnect()

    async def stream_ticks(self) -> AsyncGenerator[TickNode, None]:
        """Exposes serialized data streams out to internal analytical queues."""
        while self._is_connected or not self._outbound_queue.empty():
            try:
                yield await asyncio.wait_for(self._outbound_queue.get(), timeout=0.1)
                self._outbound_queue.task_done()
            except asyncio.TimeoutError:
                continue