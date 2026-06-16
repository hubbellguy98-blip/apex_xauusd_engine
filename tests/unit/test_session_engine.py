from datetime import datetime, timezone

from src.analytics.session_engine import GoldSessionIntelligenceEngine
from src.core.domain.constants import SessionState


def test_post_ny_reset_window_is_tradable_session() -> None:
    engine = GoldSessionIntelligenceEngine()

    session, is_killzone, is_overlap = engine.evaluate_temporal_context(
        datetime(2026, 6, 15, 21, 0, tzinfo=timezone.utc),
        current_mid=4450.0,
    )

    assert session == SessionState.POST_NY_RESET
    assert is_killzone is False
    assert is_overlap is False


def test_asian_session_still_starts_after_reset_window() -> None:
    engine = GoldSessionIntelligenceEngine()

    session, is_killzone, is_overlap = engine.evaluate_temporal_context(
        datetime(2026, 6, 15, 23, 0, tzinfo=timezone.utc),
        current_mid=4450.0,
    )

    assert session == SessionState.ASI_ACCUMULATION
    assert is_killzone is False
    assert is_overlap is False

