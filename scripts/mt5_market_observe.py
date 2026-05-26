"""Observe live MT5 Gold quotes through analytics without generating orders."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analytics.candle_builder import IncrementalCandleBuilder
from src.analytics.liquidity_engine import LiquidityInterceptionEngine
from src.analytics.regime_detection import QuantitativeRegimeClassificationEngine
from src.analytics.session_engine import GoldSessionIntelligenceEngine
from src.analytics.structure_engine import DeterministicStructureEngine
from src.infrastructure.broker.mt5_config import load_mt5_config
from src.infrastructure.broker.mt5_gateway import MT5BrokerGateway
from src.strategy.state_manager import CentralRuntimeStateManager


async def observe(duration_seconds: float, poll_interval_seconds: float, candle_seconds: int) -> int:
    config = load_mt5_config(ROOT / ".env")
    if not config.dry_run or not config.require_demo:
        raise RuntimeError("Read-only observation requires both MT5 dry-run and demo protections enabled.")

    state_manager = CentralRuntimeStateManager()
    gateway = MT5BrokerGateway(config)
    session_engine = GoldSessionIntelligenceEngine()
    structure_engine = DeterministicStructureEngine("observe")
    liquidity_engine = LiquidityInterceptionEngine("observe")
    regime_engine = QuantitativeRegimeClassificationEngine(baseline_period=3)

    polls = 0
    quotes_read = 0
    closed_candles = 0
    pivots_found = 0
    sweeps_found = 0
    last_regime = "INSUFFICIENT_CLOSED_CANDLES"
    latest_session = "UNINITIALIZED"
    latest_tick = None
    last_tick_signature = None

    await state_manager.bootstrap()
    await gateway.connect()
    try:
        summary = gateway.connection_summary()
        symbol = str(summary["symbol"])
        candle_builder = IncrementalCandleBuilder(symbol, "observe", candle_seconds)
        started = asyncio.get_running_loop().time()
        while asyncio.get_running_loop().time() - started < duration_seconds:
            tick = gateway.read_current_tick()
            polls += 1
            tick_signature = (tick.timestamp, tick.bid, tick.ask, tick.volume)
            if tick_signature == last_tick_signature:
                await asyncio.sleep(poll_interval_seconds)
                continue
            last_tick_signature = tick_signature
            latest_tick = tick
            quotes_read += 1

            await state_manager.commit_market_update(
                {
                    "last_tick_time": tick.timestamp.replace(tzinfo=None),
                    "current_ask": tick.ask,
                    "current_bid": tick.bid,
                    "current_mid": tick.mid,
                    "current_spread": tick.spread,
                    "accumulated_tick_count": quotes_read,
                    "is_synchronized": True,
                },
                f"MT5_OBSERVE_TICK_{quotes_read}",
            )

            session, is_killzone, _ = session_engine.evaluate_temporal_context(tick.timestamp, tick.mid)
            latest_session = session.value
            await state_manager.commit_session_update(
                {
                    "current_phase": session,
                    "last_phase_transition": tick.timestamp.replace(tzinfo=None),
                },
                f"MT5_OBSERVE_SESSION_{quotes_read}",
            )

            sweeps_found += len(liquidity_engine.evaluate_tick_sweeps(tick))

            closed, _ = candle_builder.process_tick(tick)
            if closed is not None:
                closed_candles += 1
                regime_engine.append_candle_metrics(closed)
                metrics = regime_engine.extract_environment_metrics(1.0 / poll_interval_seconds)
                last_regime = regime_engine.classify_regime(metrics, is_killzone).value
                pivots, _ = structure_engine.ingest_candle_close(closed)
                for pivot in pivots:
                    liquidity_engine.register_structural_pivot_pool(pivot)
                pivots_found += len(pivots)

            await asyncio.sleep(poll_interval_seconds)

        if latest_tick is None:
            raise RuntimeError("No MT5 quotes were observed.")

        print("MT5 market observation - READ ONLY; NO ORDER PATH INVOKED")
        print(f"symbol={latest_tick.symbol}")
        print(f"polls={polls}")
        print(f"unique_quotes_processed={quotes_read}")
        print(f"latest_bid={latest_tick.bid:.2f}")
        print(f"latest_ask={latest_tick.ask:.2f}")
        print(f"latest_spread={latest_tick.spread:.5f}")
        print(f"session={latest_session}")
        print(f"closed_candles={closed_candles}")
        print(f"pivots_found={pivots_found}")
        print(f"liquidity_sweeps_observed={sweeps_found}")
        print(f"regime={last_regime}")
        return 0
    finally:
        await gateway.disconnect()
        await state_manager.terminate()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Observe MT5 Gold quotes without evaluating or sending orders.")
    parser.add_argument("--duration-seconds", type=float, default=8.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=0.25)
    parser.add_argument("--candle-seconds", type=int, default=2)
    args = parser.parse_args()
    if args.duration_seconds <= 0 or args.poll_interval_seconds <= 0 or args.candle_seconds <= 0:
        parser.error("duration, interval, and candle window must all be positive.")
    return args


if __name__ == "__main__":
    parsed = parse_args()
    raise SystemExit(
        asyncio.run(observe(parsed.duration_seconds, parsed.poll_interval_seconds, parsed.candle_seconds))
    )
