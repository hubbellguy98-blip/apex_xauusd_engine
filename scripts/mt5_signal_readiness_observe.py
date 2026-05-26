"""Observe real MT5 quotes for analytical signal readiness without order routing."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analytics.liquidity_engine import LiquidityInterceptionEngine
from src.analytics.session_engine import GoldSessionIntelligenceEngine
from src.analytics.structure_engine import DeterministicStructureEngine
from src.core.domain.market_data import TickNode
from src.infrastructure.broker.mt5_config import load_mt5_config
from src.infrastructure.broker.mt5_gateway import MT5BrokerGateway
from src.strategy.reversal_detectors import LiquiditySweepReversalDetector
from src.strategy.state_manager import CentralRuntimeStateManager


async def observe_signal_readiness(
    duration_seconds: float,
    poll_interval_seconds: float,
    warmup_bars: int,
) -> int:
    config = load_mt5_config(ROOT / ".env")
    if not config.dry_run or not config.require_demo:
        raise RuntimeError("Signal readiness observation requires demo and dry-run protections enabled.")

    state_manager = CentralRuntimeStateManager()
    gateway = MT5BrokerGateway(config)
    session_engine = GoldSessionIntelligenceEngine()
    structure_engine = DeterministicStructureEngine("1m")
    liquidity_engine = LiquidityInterceptionEngine("1m")
    reversal_detector = LiquiditySweepReversalDetector()

    polls = 0
    unique_quotes = 0
    pivots = []
    historical_close_sweeps = 0
    sweeps = 0
    potential_candidates = []
    current_session = "UNINITIALIZED"
    killzone_active = False
    previous_signature = None

    await state_manager.bootstrap()
    await gateway.connect()
    try:
        symbol = str(gateway.connection_summary()["symbol"])
        historical_candles = gateway.read_recent_closed_candles(timeframe_minutes=1, count=warmup_bars)
        for candle in historical_candles:
            close_tick = TickNode(
                symbol=candle.symbol,
                timestamp=candle.end_time,
                bid=candle.close_p,
                ask=candle.close_p,
                sequence_id=candle.sequence_id,
                trace_id=f"HISTORY_CLOSE_{candle.sequence_id}",
                correlation_id="MT5_CLOSED_CANDLE_WARMUP",
            )
            historical_close_sweeps += len(liquidity_engine.evaluate_tick_sweeps(close_tick))
            new_pivots, _ = structure_engine.ingest_candle_close(candle)
            for pivot in new_pivots:
                liquidity_engine.register_structural_pivot_pool(pivot)
            pivots.extend(new_pivots)

        started = asyncio.get_running_loop().time()

        while asyncio.get_running_loop().time() - started < duration_seconds:
            tick = gateway.read_current_tick()
            polls += 1
            signature = (tick.timestamp, tick.bid, tick.ask, tick.volume)
            if signature == previous_signature:
                await asyncio.sleep(poll_interval_seconds)
                continue
            previous_signature = signature
            unique_quotes += 1

            await state_manager.commit_market_update(
                {
                    "last_tick_time": tick.timestamp.replace(tzinfo=None),
                    "current_ask": tick.ask,
                    "current_bid": tick.bid,
                    "current_mid": tick.mid,
                    "current_spread": tick.spread,
                    "accumulated_tick_count": unique_quotes,
                    "is_synchronized": True,
                },
                f"MT5_SIGNAL_READINESS_TICK_{unique_quotes}",
            )

            session, killzone_active, _ = session_engine.evaluate_temporal_context(tick.timestamp, tick.mid)
            current_session = session.value
            await state_manager.commit_session_update(
                {
                    "current_phase": session,
                    "last_phase_transition": tick.timestamp.replace(tzinfo=None),
                },
                f"MT5_SIGNAL_READINESS_SESSION_{unique_quotes}",
            )

            swept_pools = liquidity_engine.evaluate_tick_sweeps(tick)
            sweeps += len(swept_pools)
            if swept_pools:
                candidate = reversal_detector.evaluate_sweep_reversal(
                    tick,
                    [pool for pool, _ in swept_pools],
                    pivots,
                    state_manager.snapshot,
                )
                if candidate[0]:
                    _, direction, entry, stop_loss, take_profit = candidate
                    potential_candidates.append(
                        (direction.value, entry, stop_loss, take_profit, killzone_active)
                    )

            await asyncio.sleep(poll_interval_seconds)

        print("MT5 signal readiness - READ ONLY; NO SCORING, RISK, OR ORDER PATH INVOKED")
        print("analysis_timeframe=1m_closed_broker_candles_plus_live_quote_monitor")
        print(f"symbol={symbol}")
        print(f"historical_bars_processed={len(historical_candles)}")
        print(f"polls={polls}")
        print(f"unique_quotes_processed={unique_quotes}")
        print(f"structural_pivots={len(pivots)}")
        print(f"historical_close_sweeps={historical_close_sweeps}")
        print(f"live_liquidity_sweeps={sweeps}")
        print(f"potential_reversal_candidates={len(potential_candidates)}")
        print(f"session={current_session}")
        print(f"killzone_active={killzone_active}")
        if potential_candidates:
            direction, entry, stop_loss, take_profit, candidate_killzone = potential_candidates[-1]
            print(f"latest_candidate_direction={direction}")
            print(f"latest_candidate_entry={entry:.2f}")
            print(f"latest_candidate_stop_loss={stop_loss:.2f}")
            print(f"latest_candidate_take_profit={take_profit:.2f}")
            print(f"latest_candidate_within_killzone={candidate_killzone}")
        return 0
    finally:
        await gateway.disconnect()
        await state_manager.terminate()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Observe analytical signal readiness using MT5 Gold quotes only.")
    parser.add_argument("--duration-seconds", type=float, default=20.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=0.25)
    parser.add_argument("--warmup-bars", type=int, default=50)
    args = parser.parse_args()
    if args.duration_seconds <= 0 or args.poll_interval_seconds <= 0 or args.warmup_bars <= 0:
        parser.error("duration, interval, and warmup bar count must all be positive.")
    return args


if __name__ == "__main__":
    parsed = parse_args()
    raise SystemExit(
        asyncio.run(
            observe_signal_readiness(
                parsed.duration_seconds,
                parsed.poll_interval_seconds,
                parsed.warmup_bars,
            )
        )
    )
