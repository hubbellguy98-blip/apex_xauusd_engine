"""Unit checks for live MT5 quote freshness measurement."""

from datetime import datetime, timedelta, timezone

from scripts.mt5_intelligent_demo_runner import quote_age_seconds


def test_current_utc_quote_is_treated_as_fresh() -> None:
    assert quote_age_seconds(datetime.now(timezone.utc)) < 1.0


def test_old_quote_is_detectable_before_shadow_or_execution_processing() -> None:
    age = quote_age_seconds(datetime.now(timezone.utc) - timedelta(seconds=30))

    assert age >= 29.0
