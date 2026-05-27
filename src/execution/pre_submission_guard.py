"""Final quote-time risk checks before an approved order can be routed."""

from __future__ import annotations

from datetime import datetime, timezone

from src.core.domain.constants import OrderDirection
from src.core.domain.risk_models import PreSubmissionRiskAssessment


class PreSubmissionRiskGuard:
    """Fail closed if executable broker conditions invalidate approved risk."""

    def __init__(
        self,
        maximum_spread_price: float,
        maximum_quote_age_seconds: float = 5.0,
        currency_risk_tolerance: float = 0.01,
    ) -> None:
        if maximum_spread_price <= 0.0 or maximum_quote_age_seconds <= 0.0:
            raise ValueError("Spread and quote-age limits must be positive.")
        if currency_risk_tolerance < 0.0:
            raise ValueError("Currency risk tolerance cannot be negative.")
        self._maximum_spread_price = maximum_spread_price
        self._maximum_quote_age_seconds = maximum_quote_age_seconds
        self._currency_risk_tolerance = currency_risk_tolerance

    def evaluate(
        self,
        direction: OrderDirection,
        live_entry_price: float,
        stop_loss: float,
        take_profit: float,
        normalized_lots: float,
        currency_risk: float,
        maximum_currency_risk: float,
        spread_price: float,
        quote_timestamp: datetime,
        now: datetime | None = None,
    ) -> PreSubmissionRiskAssessment:
        reasons: list[str] = []
        current_time = now or datetime.now(timezone.utc)
        normalized_quote_time = (
            quote_timestamp.replace(tzinfo=timezone.utc)
            if quote_timestamp.tzinfo is None
            else quote_timestamp.astimezone(timezone.utc)
        )
        quote_age = abs((current_time - normalized_quote_time).total_seconds())

        if normalized_lots <= 0.0:
            reasons.append("BROKER_NORMALIZED_VOLUME_IS_ZERO")
        if quote_age > self._maximum_quote_age_seconds:
            reasons.append("BROKER_QUOTE_IS_STALE")
        if spread_price > self._maximum_spread_price:
            reasons.append("LIVE_SPREAD_EXCEEDS_PRE_SUBMISSION_LIMIT")
        if maximum_currency_risk <= 0.0 or currency_risk <= 0.0:
            reasons.append("LIVE_STOP_CURRENCY_RISK_IS_INVALID")
        elif currency_risk > maximum_currency_risk + self._currency_risk_tolerance:
            reasons.append("LIVE_STOP_CURRENCY_RISK_EXCEEDS_APPROVED_BUDGET")

        valid_geometry = (
            stop_loss < live_entry_price < take_profit
            if direction == OrderDirection.BUY
            else take_profit < live_entry_price < stop_loss
        )
        if not valid_geometry:
            reasons.append("LIVE_ENTRY_INVALIDATES_STOP_TARGET_GEOMETRY")

        return PreSubmissionRiskAssessment(
            is_approved=not reasons,
            live_entry_price=live_entry_price,
            normalized_lots=normalized_lots,
            currency_risk=currency_risk,
            maximum_currency_risk=maximum_currency_risk,
            spread_price=spread_price,
            quote_age_seconds=quote_age,
            rejection_reasons=reasons,
        )
