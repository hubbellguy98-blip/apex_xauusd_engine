"""
Apex Engine - Historical Replication Replay Engine Subsystem
Responsibility: Master class orchestrating streams, updating system clocks, and calling handlers.
Latency Profile: Main historical processing pipeline driver.
"""

import asyncio
from datetime import datetime
from typing import Optional, Any
import structlog

from src.core.base_engine import BaseSubsystem
from src.core.events.event_bus import EventBus
from src.core.events.event_types import EngineEventType
from src.backtesting.backtest_config import BacktestConfig
from src.backtesting.backtest_clock import BacktestDeterministicClock
from src.backtesting.replay_scheduler import BacktestReplayScheduler
from src.backtesting.replay_controller import BacktestReplayController
from src.backtesting.tick_replayer import BacktestTickReplayer
from src.backtesting.candle_replayer import BacktestCandleReplayer

logger = structlog.get_logger()

class HistoricalReplayEngine(BaseSubsystem):
    """Coordinates chronological event dispatching loop workers across simulation frames."""

    def __init__(self, event_bus: EventBus, config: BacktestConfig) -> None:
        super().__init__("HistoricalReplayEngine")
        self._event_bus = event_bus
        self._config = config
        
        self._clock = BacktestDeterministicClock()
        self._scheduler = BacktestReplayScheduler()
        self._controller = BacktestReplayController()
        
        self._tick_mapper = BacktestTickReplayer(config.symbol)
        self._candle_mapper = BacktestCandleReplayer(config.symbol)
        self._execution_task: Optional[asyncio.Task[None]] = None
        self._is_running = False

    async def bootstrap(self) -> None:
        self._clock.initialize_timeline(self._config.start_time)
        self._is_running = True
        logger.info("replay_engine.bootstrap_complete", target_asset=self._config.symbol)

    async def terminate(self) -> None:
        self._is_running = False
        if self._execution_task:
            self._execution_task.cancel()
        logger.info("replay_engine.terminated")

    def attach_data_buffer(self, key: str, buffer_pool: Any) -> None:
        """Binds a structural data cache loop to the internal timeline manager."""
        self._scheduler.register_stream_buffer(key, buffer_pool)

    async def launch_replay_loop(self) -> None:
        """Spins up the centralized loop processing data inputs step-by-step."""
        logger.info("replay_engine.loop_start_sequence_initiated")
        
        while self._is_running:
            # 1. Enforce Interactive Pause States
            while self._controller.is_paused and not self._controller.is_step_mode:
                await asyncio.sleep(0.1)
                if not self._is_running:
                    return

            # 2. Query Scheduler to Extract the Nearest Chronological Track
            next_track_key = self._scheduler.locate_next_chronological_track()
            if not next_track_key:
                logger.info("replay_engine.data_stream_exhausted_halting_simulation")
                break

            target_buf = self._scheduler._buffers_registry[next_track_key]
            row_node = target_buf.fetch_next_node()
            
            # Sync timeline clock updates
            current_ts = row_node["timestamp"].to_pydatetime()
            self._clock.set_time(current_ts)

            # 3. Transform and Dispatch Payload Packages Downstream
            if "bid" in row_node:
                tick_node = self._tick_mapper.convert_row_to_tick(row_node)
                await self._event_bus.publish(EngineEventType.MARKET_TICK, tick_node)
            else:
                tf = next_track_key.split("_")[-1] # Extract pattern keys (e.g., 'candle_1m')
                candle_node = self._candle_mapper.convert_row_to_candle(row_node, tf)
                await self._event_bus.publish(EngineEventType.CANDLE_CLOSED, candle_node)

            # Clear single step triggers immediately after data processing completes
            if self._controller.is_step_mode:
                self._controller.pause()

            await asyncio.sleep(0) # Yield loop control to avoid core locking bottlenecks