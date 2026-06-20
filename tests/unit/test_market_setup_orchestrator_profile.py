import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.core.domain.market_data import CandleNode, TickNode
from src.core.events.event_bus import EventBus
from src.strategy.confirmation_orchestrator import TradeConfirmationOrchestrator
from src.strategy.setup_detector import MarketSetupOrchestrator
from src.strategy.state_manager import CentralRuntimeStateManager


def test_live_orchestrator_passes_selector_config_and_killzone_context() -> None:
    asyncio.run(_exercise_live_orchestrator())


async def _exercise_live_orchestrator() -> None:
    event_bus = EventBus()
    state = CentralRuntimeStateManager()
    await state.bootstrap()
    confirmation = TradeConfirmationOrchestrator(event_bus, state)
    config = {
        "profile_name": "unit_profile",
        "minimum_rr": 3.0,
        "session_filters": {"disabled_killzones": ["London Open"]},
    }
    orchestrator = MarketSetupOrchestrator(event_bus, state, confirmation, selector_config=config, profile_name="unit_profile")
    fake_selector = _FakeSelector()
    orchestrator._strategy_selector = fake_selector

    start = datetime(2026, 6, 1, 6, 40, tzinfo=timezone.utc)
    for index in range(20):
        await orchestrator.on_candle_evacuation(
            CandleNode(
                symbol="GOLD.i#",
                timeframe="1m",
                start_time=start + timedelta(minutes=index),
                end_time=start + timedelta(minutes=index + 1),
                open_p=100.0,
                high_p=101.0,
                low_p=99.0,
                close_p=100.5,
                volume=100,
                ticks_count=100,
                is_closed=True,
                sequence_id=index,
                correlation_id=f"UNIT_{index}",
            )
        )

    await orchestrator.on_tick_received(
        TickNode(
            symbol="GOLD.i#",
            timestamp=datetime(2026, 6, 1, 7, 5, tzinfo=timezone.utc),
            bid=100.4,
            ask=100.6,
            volume=1,
            sequence_id=99,
            correlation_id="UNIT_TICK",
        )
    )

    assert fake_selector.config["minimum_rr"] == 3.0
    assert fake_selector.context["session_context"]["killzone_name"] == "London Open"
    assert orchestrator.diagnostic_snapshot["selector_profile_name"] == "unit_profile"


class _FakeSelector:
    def evaluate(self, context, config):
        self.context = context
        self.config = config
        return SimpleNamespace(
            selected=None,
            evaluations=(),
            diagnostics={
                "ict_selector_evaluated": 1,
                "ict_selector_rejections": [{"reason": "unit"}],
            },
        )
