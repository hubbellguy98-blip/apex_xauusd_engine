"""Deterministic ICT/SMC premium and discount range logic.

Premium/discount is a location filter, not an entry trigger. It helps the
strategy score whether a bullish POI is being offered at a discount, or whether
a bearish POI is being offered at a premium, inside a selected dealing range.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping


class PremiumDiscountLocation(str, Enum):
    OUTSIDE_RANGE_BELOW = "outside_range_below"
    DEEP_DISCOUNT = "deep_discount"
    DISCOUNT = "discount"
    EQUILIBRIUM_ZONE = "equilibrium_zone"
    PREMIUM = "premium"
    DEEP_PREMIUM = "deep_premium"
    OUTSIDE_RANGE_ABOVE = "outside_range_above"
    UNKNOWN = "unknown"


class PremiumDiscountSetupDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class DealingRangeType(str, Enum):
    STRUCTURAL = "structural_dealing_range"
    HTF_STRUCTURAL = "HTF_structural_range"
    BULLISH_MSS = "bullish_MSS_dealing_range"
    BEARISH_MSS = "bearish_MSS_dealing_range"
    BULLISH_BOS = "bullish_BOS_dealing_range"
    BEARISH_BOS = "bearish_BOS_dealing_range"
    SESSION = "session_dealing_range"
    LOCAL = "local_dealing_range"


class DealingRangeQualityGrade(str, Enum):
    INVALID = "invalid"
    WEAK = "weak"
    USABLE = "usable"
    STRONG = "strong"
    HIGH_QUALITY = "high_quality"


@dataclass(frozen=True, slots=True)
class PriceZone:
    zone_low: float
    zone_high: float

    @property
    def zone_mid(self) -> float:
        return (self.zone_low + self.zone_high) / 2.0

    def as_dict(self) -> dict[str, float]:
        return {
            "zone_low": round(self.zone_low, 5),
            "zone_mid": round(self.zone_mid, 5),
            "zone_high": round(self.zone_high, 5),
        }


@dataclass(frozen=True, slots=True)
class DealingRangeAnchor:
    index: int
    timestamp: datetime | None
    price: float
    type: str
    strength_score: float
    timeframe: str
    confirmed_status: bool
    structural_importance: str = "unknown"
    source: str = "unknown"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.timestamp is not None:
            payload["timestamp"] = self.timestamp.isoformat()
        return payload


@dataclass(frozen=True, slots=True)
class TradeLocationFilter:
    bullish_setups_preferred: bool
    bearish_setups_preferred: bool
    bullish_location_score: float
    bearish_location_score: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class POIQualityFilter:
    setup_direction: PremiumDiscountSetupDirection
    poi_zone_mid: float | None
    premium_discount_alignment: bool | None
    quality_adjustment: float
    alignment_label: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["setup_direction"] = self.setup_direction.value
        return payload


@dataclass(frozen=True, slots=True)
class PremiumDiscountResult:
    concept_name: str
    symbol: str
    timeframe: str
    dealing_range: dict[str, Any]
    equilibrium: float
    discount_zone: PriceZone
    premium_zone: PriceZone
    deep_discount_zone: PriceZone
    normal_discount_zone: PriceZone
    normal_premium_zone: PriceZone
    deep_premium_zone: PriceZone
    current_price: float | None
    current_price_location: PremiumDiscountLocation
    position_percent: float | None
    range_valid: bool
    range_quality_score: float
    range_quality_grade: DealingRangeQualityGrade
    trade_filter: TradeLocationFilter
    poi_quality_filter: POIQualityFilter | None
    warnings: tuple[str, ...]
    entry_allowed_from_premium_discount_alone: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "concept_name": self.concept_name,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "dealing_range": self.dealing_range,
            "equilibrium": round(self.equilibrium, 5),
            "discount_zone": self.discount_zone.as_dict(),
            "premium_zone": self.premium_zone.as_dict(),
            "deep_discount_zone": self.deep_discount_zone.as_dict(),
            "normal_discount_zone": self.normal_discount_zone.as_dict(),
            "normal_premium_zone": self.normal_premium_zone.as_dict(),
            "deep_premium_zone": self.deep_premium_zone.as_dict(),
            "current_price": None if self.current_price is None else round(self.current_price, 5),
            "current_price_location": self.current_price_location.value,
            "position_percent": None if self.position_percent is None else round(self.position_percent, 3),
            "range_valid": self.range_valid,
            "range_quality_score": round(self.range_quality_score, 2),
            "range_quality_grade": self.range_quality_grade.value,
            "trade_filter": self.trade_filter.as_dict(),
            "poi_quality_filter": None if self.poi_quality_filter is None else self.poi_quality_filter.as_dict(),
            "warnings": list(self.warnings),
            "entry_allowed_from_premium_discount_alone": self.entry_allowed_from_premium_discount_alone,
        }


def calculate_premium_discount(
    swing_low: Mapping[str, Any] | DealingRangeAnchor | float,
    swing_high: Mapping[str, Any] | DealingRangeAnchor | float,
    *,
    current_price: float | None = None,
    atr: float | None = None,
    equilibrium_buffer: float | None = None,
    minimum_range_atr_multiplier: float = 2.0,
    minimum_strength_score: float = 5.0,
    symbol: str = "unknown",
    timeframe: str = "unknown",
    dealing_range_type: str | DealingRangeType = DealingRangeType.STRUCTURAL,
    context_direction: str | PremiumDiscountSetupDirection = PremiumDiscountSetupDirection.NEUTRAL,
    poi_zone: Mapping[str, Any] | PriceZone | None = None,
    htf_result: Mapping[str, Any] | PremiumDiscountResult | None = None,
) -> dict[str, Any]:
    """Calculate premium, discount, equilibrium, and setup-location filters."""
    low_anchor = _anchor(swing_low, "swing_low", timeframe)
    high_anchor = _anchor(swing_high, "swing_high", timeframe)
    if low_anchor.price > high_anchor.price:
        low_anchor, high_anchor = high_anchor, low_anchor

    range_low = low_anchor.price
    range_high = high_anchor.price
    range_size = range_high - range_low
    if range_size <= 0:
        raise ValueError("swing_high price must be greater than swing_low price.")

    range_type_value = dealing_range_type.value if isinstance(dealing_range_type, DealingRangeType) else str(dealing_range_type)
    equilibrium = (range_low + range_high) / 2.0
    twenty_five = range_low + range_size * 0.25
    seventy_five = range_low + range_size * 0.75
    buffer_value = _equilibrium_buffer(range_size, atr, equilibrium_buffer)

    location, position_percent = _price_location(current_price, range_low, range_high, equilibrium, buffer_value)
    warnings = _range_warnings(low_anchor, high_anchor, range_size, atr, minimum_range_atr_multiplier, minimum_strength_score, location)
    quality_score = _range_quality_score(
        low_anchor,
        high_anchor,
        range_size,
        atr,
        minimum_range_atr_multiplier,
        range_type_value,
        location,
        warnings,
    )
    range_valid = quality_score >= 4.0 and "weak_or_invalid_dealing_range" not in warnings
    trade_filter = _trade_filter(location)
    direction = _setup_direction(context_direction)
    poi_filter = _poi_filter(direction, poi_zone, equilibrium, location, htf_result)

    result = PremiumDiscountResult(
        concept_name="ict_smc_premium_discount",
        symbol=symbol,
        timeframe=timeframe,
        dealing_range={
            "range_low": round(range_low, 5),
            "range_high": round(range_high, 5),
            "range_size": round(range_size, 5),
            "range_type": range_type_value,
            "range_quality_score": round(quality_score, 2),
            "selected_from": {"swing_low": low_anchor.as_dict(), "swing_high": high_anchor.as_dict()},
        },
        equilibrium=equilibrium,
        discount_zone=PriceZone(range_low, equilibrium),
        premium_zone=PriceZone(equilibrium, range_high),
        deep_discount_zone=PriceZone(range_low, twenty_five),
        normal_discount_zone=PriceZone(twenty_five, equilibrium),
        normal_premium_zone=PriceZone(equilibrium, seventy_five),
        deep_premium_zone=PriceZone(seventy_five, range_high),
        current_price=current_price,
        current_price_location=location,
        position_percent=position_percent,
        range_valid=range_valid,
        range_quality_score=quality_score,
        range_quality_grade=_quality_grade(quality_score, range_valid),
        trade_filter=trade_filter,
        poi_quality_filter=poi_filter,
        warnings=tuple(warnings),
    )
    return result.as_dict()


def evaluate_poi_premium_discount(
    setup_direction: str | PremiumDiscountSetupDirection,
    poi_zone: Mapping[str, Any] | PriceZone,
    premium_discount_result: Mapping[str, Any] | PremiumDiscountResult,
) -> dict[str, Any]:
    """Score a POI location against an existing premium/discount result."""
    payload = premium_discount_result.as_dict() if isinstance(premium_discount_result, PremiumDiscountResult) else premium_discount_result
    direction = _setup_direction(setup_direction)
    equilibrium = float(payload["equilibrium"])
    location = PremiumDiscountLocation(str(payload.get("current_price_location", "unknown")))
    return _poi_filter(direction, poi_zone, equilibrium, location, payload).as_dict()


def _anchor(source: Mapping[str, Any] | DealingRangeAnchor | float, expected_type: str, timeframe: str) -> DealingRangeAnchor:
    if isinstance(source, DealingRangeAnchor):
        return source
    if isinstance(source, int | float):
        return DealingRangeAnchor(
            index=-1,
            timestamp=None,
            price=float(source),
            type=expected_type,
            strength_score=10.0,
            timeframe=timeframe,
            confirmed_status=True,
            structural_importance="manual_price_anchor",
            source="numeric_input",
        )
    timestamp = source.get("timestamp")
    if timestamp is not None and not isinstance(timestamp, datetime):
        timestamp = datetime.fromisoformat(str(timestamp))
    return DealingRangeAnchor(
        index=int(source.get("index", source.get("confirmation_index", -1))),
        timestamp=timestamp,
        price=float(source["price"]),
        type=str(source.get("type", expected_type)),
        strength_score=float(source.get("strength_score", 0.0)),
        timeframe=str(source.get("timeframe", timeframe)),
        confirmed_status=_confirmed(source),
        structural_importance=str(source.get("structural_importance", "unknown")),
        source=str(source.get("source", "provided_swing")),
    )


def _confirmed(source: Mapping[str, Any]) -> bool:
    if "confirmed_status" in source:
        value = source["confirmed_status"]
    elif "is_confirmed" in source:
        value = source["is_confirmed"]
    elif "confirmed" in source:
        value = source["confirmed"]
    else:
        value = True
    if isinstance(value, str):
        return value.lower() in {"true", "confirmed", "yes", "1"}
    return bool(value)


def _equilibrium_buffer(range_size: float, atr: float | None, explicit_buffer: float | None) -> float:
    if explicit_buffer is not None:
        return max(0.0, explicit_buffer)
    if atr is not None and atr > 0:
        return atr * 0.10
    return range_size * 0.01


def _price_location(
    current_price: float | None,
    range_low: float,
    range_high: float,
    equilibrium: float,
    buffer_value: float,
) -> tuple[PremiumDiscountLocation, float | None]:
    if current_price is None:
        return PremiumDiscountLocation.UNKNOWN, None
    range_size = range_high - range_low
    position_percent = ((current_price - range_low) / range_size) * 100.0
    if current_price < range_low:
        return PremiumDiscountLocation.OUTSIDE_RANGE_BELOW, position_percent
    if current_price > range_high:
        return PremiumDiscountLocation.OUTSIDE_RANGE_ABOVE, position_percent
    if abs(current_price - equilibrium) <= buffer_value:
        return PremiumDiscountLocation.EQUILIBRIUM_ZONE, position_percent
    if position_percent < 25.0:
        return PremiumDiscountLocation.DEEP_DISCOUNT, position_percent
    if position_percent < 50.0:
        return PremiumDiscountLocation.DISCOUNT, position_percent
    if position_percent <= 75.0:
        return PremiumDiscountLocation.PREMIUM, position_percent
    return PremiumDiscountLocation.DEEP_PREMIUM, position_percent


def _range_warnings(
    low_anchor: DealingRangeAnchor,
    high_anchor: DealingRangeAnchor,
    range_size: float,
    atr: float | None,
    minimum_range_atr_multiplier: float,
    minimum_strength_score: float,
    location: PremiumDiscountLocation,
) -> list[str]:
    warnings = [
        "premium_discount_is_location_filter_not_entry_signal",
        "entry_still_requires_liquidity_sweep_structure_displacement_poi_retest_and_risk_reward",
    ]
    if not low_anchor.confirmed_status or not high_anchor.confirmed_status:
        warnings.append("unconfirmed_swing_anchor")
    if low_anchor.strength_score < minimum_strength_score or high_anchor.strength_score < minimum_strength_score:
        warnings.append("weak_swing_anchor")
    if atr is not None and atr > 0 and range_size < atr * minimum_range_atr_multiplier:
        warnings.append("dealing_range_too_small_vs_atr")
    if location == PremiumDiscountLocation.EQUILIBRIUM_ZONE:
        warnings.append("price_near_equilibrium")
    if any(item in warnings for item in ("unconfirmed_swing_anchor", "weak_swing_anchor", "dealing_range_too_small_vs_atr")):
        warnings.append("weak_or_invalid_dealing_range")
    return warnings


def _range_quality_score(
    low_anchor: DealingRangeAnchor,
    high_anchor: DealingRangeAnchor,
    range_size: float,
    atr: float | None,
    minimum_range_atr_multiplier: float,
    range_type: str,
    location: PremiumDiscountLocation,
    warnings: list[str],
) -> float:
    score = 0.0
    if low_anchor.confirmed_status and high_anchor.confirmed_status:
        score += 1.0
        if low_anchor.strength_score >= 6.0 and high_anchor.strength_score >= 6.0:
            score += 1.0

    if atr is None or atr <= 0:
        score += 0.75
    else:
        ratio = range_size / atr
        if ratio >= minimum_range_atr_multiplier * 2:
            score += 1.5
        elif ratio >= minimum_range_atr_multiplier:
            score += 0.75

    lowered = range_type.lower()
    if "mss" in lowered:
        score += 2.0
    elif "bos" in lowered:
        score += 1.0
    elif "structural" in lowered:
        score += 1.25

    if "htf" in lowered or "4h" in lowered or "1h" in lowered:
        score += 1.0
    elif "session" in lowered or "intraday" in lowered:
        score += 0.5
    else:
        score += 0.25

    score += 1.0 if "stale" not in warnings else 0.0
    score += 0.75 if "weak_swing_anchor" not in warnings else 0.25
    score += 0.75 if "unconfirmed_swing_anchor" not in warnings else 0.0
    if location not in {PremiumDiscountLocation.OUTSIDE_RANGE_ABOVE, PremiumDiscountLocation.OUTSIDE_RANGE_BELOW, PremiumDiscountLocation.UNKNOWN}:
        score += 0.5
    if "weak_or_invalid_dealing_range" in warnings:
        score = min(score, 3.0)
    return max(0.0, min(10.0, score))


def _quality_grade(score: float, range_valid: bool) -> DealingRangeQualityGrade:
    if not range_valid:
        return DealingRangeQualityGrade.INVALID
    if score >= 9:
        return DealingRangeQualityGrade.HIGH_QUALITY
    if score >= 7:
        return DealingRangeQualityGrade.STRONG
    if score >= 4:
        return DealingRangeQualityGrade.USABLE
    return DealingRangeQualityGrade.WEAK


def _trade_filter(location: PremiumDiscountLocation) -> TradeLocationFilter:
    if location in {PremiumDiscountLocation.DEEP_DISCOUNT, PremiumDiscountLocation.DISCOUNT}:
        return TradeLocationFilter(True, False, 1.0, -0.75, "Current price is below equilibrium, so bullish setups have better location.")
    if location in {PremiumDiscountLocation.DEEP_PREMIUM, PremiumDiscountLocation.PREMIUM}:
        return TradeLocationFilter(False, True, -0.75, 1.0, "Current price is above equilibrium, so bearish setups have better location.")
    if location == PremiumDiscountLocation.EQUILIBRIUM_ZONE:
        return TradeLocationFilter(False, False, 0.0, 0.0, "Current price is near equilibrium, so premium/discount edge is neutral.")
    return TradeLocationFilter(False, False, 0.0, 0.0, "Current price is outside or unavailable for the selected dealing range.")


def _poi_filter(
    direction: PremiumDiscountSetupDirection,
    poi_zone: Mapping[str, Any] | PriceZone | None,
    equilibrium: float,
    location: PremiumDiscountLocation,
    htf_result: Mapping[str, Any] | PremiumDiscountResult | None,
) -> POIQualityFilter | None:
    if direction == PremiumDiscountSetupDirection.NEUTRAL or poi_zone is None:
        return None
    zone_mid = _zone_mid(poi_zone)
    if location == PremiumDiscountLocation.EQUILIBRIUM_ZONE:
        return POIQualityFilter(direction, zone_mid, None, 0.0, "neutral_equilibrium", "Price is near equilibrium; premium/discount edge is weak.")

    aligned = zone_mid <= equilibrium if direction == PremiumDiscountSetupDirection.BULLISH else zone_mid >= equilibrium
    adjustment = 1.25 if aligned else -1.0
    label = "aligned_discount_for_bullish_poi" if direction == PremiumDiscountSetupDirection.BULLISH and aligned else "aligned_premium_for_bearish_poi" if aligned else "misaligned_premium_discount_location"
    reason = "Bullish POI is in discount." if direction == PremiumDiscountSetupDirection.BULLISH and aligned else "Bearish POI is in premium." if aligned else "POI is on the weaker side of the dealing range for this direction."

    htf_payload = htf_result.as_dict() if isinstance(htf_result, PremiumDiscountResult) else htf_result
    htf_location = str(htf_payload.get("current_price_location", "")) if htf_payload else ""
    if direction == PremiumDiscountSetupDirection.BULLISH and htf_location in {"discount", "deep_discount"}:
        adjustment += 0.5
        reason += " HTF discount supports the bullish setup."
    if direction == PremiumDiscountSetupDirection.BEARISH and htf_location in {"premium", "deep_premium"}:
        adjustment += 0.5
        reason += " HTF premium supports the bearish setup."
    if direction == PremiumDiscountSetupDirection.BULLISH and htf_location in {"premium", "deep_premium"}:
        adjustment -= 0.5
        reason += " HTF premium conflicts with the bullish setup."
    if direction == PremiumDiscountSetupDirection.BEARISH and htf_location in {"discount", "deep_discount"}:
        adjustment -= 0.5
        reason += " HTF discount conflicts with the bearish setup."

    return POIQualityFilter(direction, zone_mid, aligned, round(adjustment, 2), label, reason)


def _zone_mid(zone: Mapping[str, Any] | PriceZone) -> float:
    if isinstance(zone, PriceZone):
        return zone.zone_mid
    if "zone_mid" in zone and zone["zone_mid"] is not None:
        return float(zone["zone_mid"])
    if "mid" in zone and zone["mid"] is not None:
        return float(zone["mid"])
    low = float(zone.get("zone_low", zone.get("low")))
    high = float(zone.get("zone_high", zone.get("high")))
    return (low + high) / 2.0


def _setup_direction(value: str | PremiumDiscountSetupDirection) -> PremiumDiscountSetupDirection:
    if isinstance(value, PremiumDiscountSetupDirection):
        return value
    lowered = str(value).lower()
    if "bull" in lowered or lowered == "buy":
        return PremiumDiscountSetupDirection.BULLISH
    if "bear" in lowered or lowered == "sell":
        return PremiumDiscountSetupDirection.BEARISH
    return PremiumDiscountSetupDirection.NEUTRAL
