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

    assert session == SessionState.ASIAN_SESSION
    assert is_killzone is False
    assert is_overlap is False


def test_broad_london_session_is_not_all_day_killzone() -> None:
    engine = GoldSessionIntelligenceEngine()

    context = engine.evaluate_session_context(
        datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc),
        current_mid=4450.0,
    )

    assert context.session_name == SessionState.LONDON_SESSION.value
    assert context.killzone_active is False
    assert context.killzone_name is None


def test_exact_killzone_is_preserved_separately_from_session() -> None:
    engine = GoldSessionIntelligenceEngine()

    context = engine.evaluate_session_context(
        datetime(2026, 6, 15, 7, 30, tzinfo=timezone.utc),
        current_mid=4450.0,
    )

    assert context.session_name == SessionState.LONDON_SESSION.value
    assert context.killzone_active is True
    assert context.killzone_name == "London Open"
