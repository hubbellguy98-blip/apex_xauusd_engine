"""Bullish ICT/SMC order block formation and retest validation.

This specialist layer sits on top of the general order-block detector and adds
bullish-only formation context, retest classification, target logic, and entry
permission rules. A bullish OB remains a reaction zone, not an automatic buy.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence

from src.analytics.ict_smc.order_block import (
    OrderBlockDetectionConfig,
    OrderBlockDirection,
    OrderBlockFreshStatus,
    detect_order_blocks,
)
from src.core.domain.market_data import CandleNode


class BullishOBRetestStatus(str, Enum):
    FRESH = "fresh"
    TOUCHED = "touched"
    PARTIALLY_MITIGATED = "partially_mitigated"
    DEEP_MITIGATION = "deep_mitigation"
    CONFIRMED_REACTION = "confirmed_reaction"
    FAILED = "failed"


class BullishOBQualityGrade(str, Enum):
    INVALID = "invalid"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    HIGH_QUALITY = "high_quality"


@dataclass(frozen=True, slots=True)
class BullishOBRetestValidation:
    concept_name: str
    symbol: str
    timeframe: str
    ob_id: str
    detected: bool
    retest_status: BullishOBRetestStatus
    retest_candle: dict[str, Any] | None
    entered_ob_zone: bool
    zone_high: float
    zone_low: float
    mean_threshold: float
    mean_threshold_touched: bool
    closed_below_zone_low: bool
    mitigation_depth: str
    reaction_confirmed: bool
    reaction_type: str
    entry_allowed: bool
    entry_reason: str
    invalidation_level: float
    stop_loss_reference: str
    stop_loss_price: float
    target_liquidity: Mapping[str, Any] | None
    reward_to_risk: float | None
    quality_score: float
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["retest_status"] = self.retest_status.value
        return payload


def detect_bullish_order_block(
    df: Sequence[CandleNode | Mapping[str, Any]],
    swings: Sequence[Mapping[str, Any]] | None = None,
    bos_events: Sequence[Mapping[str, Any] | str] | None = None,
    mss_events: Sequence[Mapping[str, Any] | str] | None = None,
    liquidity_sweeps: Sequence[Mapping[str, Any] | str] | None = None,
    *,
    symbol: str = "unknown",
    timeframe: str = "unknown",
    premium_discount_context: Mapping[str, Any] | None = None,
    htf_context: Mapping[str, Any] | None = None,
    target_liquidity: Mapping[str, Any] | None = None,
    config: OrderBlockDetectionConfig | None = None,
) -> list[dict[str, Any]]:
    """Detect bullish order blocks and expose bullish-specific output fields."""
    raw_blocks = detect_order_blocks(
        df,
        swings,
        bos_events,
        mss_events,
        liquidity_sweeps,
        symbol=symbol,
        timeframe=timeframe,
        premium_discount_context=premium_discount_context,
        htf_context=htf_context,
        config=config,
    )
    return [
        _bullish_view(block, target_liquidity)
        for block in raw_blocks
        if block.get("direction") == OrderBlockDirection.BULLISH.value
    ]


def validate_bullish_ob_retest(
    df: Sequence[CandleNode | Mapping[str, Any]],
    bullish_ob: Mapping[str, Any],
    *,
    ltf_confirmation_events: Sequence[Mapping[str, Any] | str] | None = None,
    liquidity_events: Sequence[Mapping[str, Any] | str] | None = None,
    target_liquidity: Mapping[str, Any] | None = None,
    risk_settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a bullish OB retest and decide whether entry context is allowed."""
    del liquidity_events  # Reserved for later sweep-inside-OB expansion.
    candles = _normalize_candles(df)
    symbol = str(bullish_ob.get("symbol", "unknown"))
    timeframe = str(bullish_ob.get("timeframe", "unknown"))
    ob_id = str(bullish_ob.get("ob_id", "unknown"))
    zone_low = float(bullish_ob["zone_low"])
    zone_high = float(bullish_ob["zone_high"])
    mean_threshold = float(bullish_ob.get("mean_threshold", (zone_low + zone_high) / 2.0))
    confirmation_index = _confirmation_index(bullish_ob)
    stop_buffer = float((risk_settings or {}).get("stop_buffer", 0.10))
    minimum_rr = float((risk_settings or {}).get("minimum_rr", 1.5))

    retest_candle = None
    status = BullishOBRetestStatus.FRESH
    mitigation_depth = "untouched"
    mean_touched = False
    closed_below = False
    reaction_confirmed = False
    reaction_type = "none"
    warnings: list[str] = ["bullish_ob_is_reaction_zone_not_automatic_entry"]

    for candle in candles:
        if candle["index"] <= confirmation_index:
            continue
        if candle["close"] < zone_low - stop_buffer:
            retest_candle = candle
            status = BullishOBRetestStatus.FAILED
            closed_below = True
            mitigation_depth = "closed_below_bullish_ob_low"
            warnings.append("bullish_ob_failed_closed_below_zone_low")
            break
        if candle["low"] <= zone_high:
            retest_candle = candle
            if candle["low"] <= zone_low and candle["close"] > zone_low:
                status = BullishOBRetestStatus.DEEP_MITIGATION
                mitigation_depth = "deep_mitigation_reclaimed_zone_low"
                mean_touched = True
            elif candle["low"] <= mean_threshold:
                status = BullishOBRetestStatus.PARTIALLY_MITIGATED
                mitigation_depth = "mean_threshold_retest"
                mean_touched = True
            else:
                status = BullishOBRetestStatus.TOUCHED
                mitigation_depth = "shallow_retest"

            reaction_confirmed, reaction_type = _bullish_reaction_confirmed(
                candle,
                zone_high,
                mean_threshold,
                ltf_confirmation_events or (),
            )
            if reaction_confirmed:
                status = BullishOBRetestStatus.CONFIRMED_REACTION
            break

    stop_loss_price = zone_low - stop_buffer
    target = target_liquidity or _target_from_ob(bullish_ob)
    target_price = _target_price(target)
    entry_price = float(retest_candle["close"]) if retest_candle else zone_high
    reward_to_risk = None
    if target_price is not None and entry_price > stop_loss_price:
        reward_to_risk = round((target_price - entry_price) / (entry_price - stop_loss_price), 2)

    entry_allowed = (
        status is BullishOBRetestStatus.CONFIRMED_REACTION
        and target_price is not None
        and reward_to_risk is not None
        and reward_to_risk >= minimum_rr
    )
    if not reaction_confirmed and status not in {BullishOBRetestStatus.FRESH, BullishOBRetestStatus.FAILED}:
        warnings.append("retest_without_bullish_reaction_confirmation")
    if target_price is None:
        warnings.append("no_buy_side_target_liquidity_provided")
    if reward_to_risk is not None and reward_to_risk < minimum_rr:
        warnings.append("reward_to_risk_below_minimum")

    validation = BullishOBRetestValidation(
        concept_name="Bullish Order Block Retest",
        symbol=symbol,
        timeframe=timeframe,
        ob_id=ob_id,
        detected=retest_candle is not None,
        retest_status=status,
        retest_candle=retest_candle,
        entered_ob_zone=retest_candle is not None and status is not BullishOBRetestStatus.FAILED,
        zone_high=zone_high,
        zone_low=zone_low,
        mean_threshold=mean_threshold,
        mean_threshold_touched=mean_touched,
        closed_below_zone_low=closed_below,
        mitigation_depth=mitigation_depth,
        reaction_confirmed=reaction_confirmed,
        reaction_type=reaction_type,
        entry_allowed=entry_allowed,
        entry_reason="bullish_ob_retest_confirmed_with_valid_target_rr" if entry_allowed else "entry_not_allowed",
        invalidation_level=zone_low,
        stop_loss_reference="below_zone_low",
        stop_loss_price=round(stop_loss_price, 5),
        target_liquidity=target,
        reward_to_risk=reward_to_risk,
        quality_score=_retest_quality_score(float(bullish_ob.get("quality_score", 0.0)), status, entry_allowed),
        warnings=tuple(dict.fromkeys(warnings)),
    )
    return validation.as_dict()


def _bullish_view(block: Mapping[str, Any], target_liquidity: Mapping[str, Any] | None) -> dict[str, Any]:
    structure = block.get("structure_event_reference", {})
    liquidity = block.get("liquidity_context", {})
    fvg = block.get("fvg_context", {})
    freshness = block.get("freshness", {})
    created_after_sweep = bool(liquidity.get("liquidity_sweep_before_displacement", False))
    event_type = str(structure.get("event_type", "none"))
    fvg_created = bool(fvg.get("fvg_created_after_displacement", False))
    payload = dict(block)
    payload["concept_name"] = "Bullish Order Block"
    payload["ob_id"] = str(block.get("ob_id", "")).replace("OB_", "BULL_OB_", 1)
    payload["valid_bullish_ob"] = event_type in {"BOS", "MSS"} and float(block.get("quality_score", 0.0)) > 4.0
    payload["created_after_sweep"] = created_after_sweep
    payload["bos_confirmed"] = event_type == "BOS"
    payload["mss_confirmed"] = event_type == "MSS"
    payload["fvg_created"] = fvg_created
    payload["fvg_overlap"] = bool(fvg.get("ob_fvg_overlap", False))
    payload["retest_status"] = block.get("fresh_status", "fresh")
    payload["target_liquidity"] = target_liquidity
    payload["formation_context"] = {
        "created_after_sweep": created_after_sweep,
        "sweep_type": liquidity.get("sweep_type", "none"),
        "sweep_candle_index": liquidity.get("sweep_candle_index"),
        "swept_liquidity_id": liquidity.get("swept_liquidity_id"),
        "bullish_displacement": block.get("displacement", {}).get("displacement_present", False),
        "displacement_strength": block.get("displacement", {}).get("displacement_strength", "unknown"),
        "bos_confirmed": event_type == "BOS",
        "mss_confirmed": event_type == "MSS",
        "created_by_event": block.get("created_by_event"),
        "structure_confirmation_candle_index": structure.get("confirmation_candle_index"),
        "broken_level": structure.get("broken_level"),
    }
    payload["retest"] = {
        "retest_status": block.get("fresh_status", "fresh"),
        "mean_threshold_touched": freshness.get("mean_threshold_touched", False),
        "times_tapped": freshness.get("times_tapped", 0),
        "reaction_confirmed": False,
    }
    payload["risk_and_target_logic"] = {
        "entry_allowed_from_ob_alone": False,
        "invalidation_level": block.get("invalidation_level"),
        "preferred_stop_loss": "below_zone_low_or_below_sweep_low",
        "target_liquidity": target_liquidity or "nearest_buy_side_liquidity_above",
        "requires_retest_confirmation": True,
    }
    warnings = list(payload.get("warnings", ()))
    if not created_after_sweep:
        warnings.append("no_prior_sell_side_sweep")
    if not payload["valid_bullish_ob"]:
        warnings.append("invalid_or_weak_bullish_ob_candidate")
    payload["warnings"] = tuple(dict.fromkeys(warnings))
    return payload


def _bullish_reaction_confirmed(
    candle: Mapping[str, Any],
    zone_high: float,
    mean_threshold: float,
    ltf_events: Sequence[Mapping[str, Any] | str],
) -> tuple[bool, str]:
    event_text = " ".join(_event_text(event).lower() for event in ltf_events)
    if "bullish" in event_text and ("mss" in event_text or "choch" in event_text):
        return True, "ltf_bullish_mss_or_choch"
    if "sell_side" in event_text and "sweep" in event_text and "displacement" in event_text:
        return True, "sell_side_sweep_inside_ob_then_bullish_displacement"
    if candle["close"] > zone_high and candle["close"] > candle["open"]:
        return True, "bullish_close_back_above_zone_high"
    if candle["close"] > mean_threshold and candle["close"] > candle["open"]:
        return True, "bullish_rejection_close_above_mean_threshold"
    return False, "none"


def _retest_quality_score(base_score: float, status: BullishOBRetestStatus, entry_allowed: bool) -> float:
    score = base_score
    if status is BullishOBRetestStatus.CONFIRMED_REACTION:
        score += 1.0
    elif status is BullishOBRetestStatus.FAILED:
        return min(score, 2.9)
    elif status is BullishOBRetestStatus.PARTIALLY_MITIGATED:
        score -= 0.25
    elif status is BullishOBRetestStatus.DEEP_MITIGATION:
        score -= 0.5
    if entry_allowed:
        score += 0.5
    return max(0.0, min(10.0, round(score, 2)))


def _normalize_candles(candles: Sequence[CandleNode | Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for position, raw in enumerate(candles):
        if isinstance(raw, CandleNode):
            if not raw.is_closed:
                continue
            timestamp = raw.start_time
            values = {
                "index": raw.sequence_id or position,
                "open": raw.open_p,
                "high": raw.high_p,
                "low": raw.low_p,
                "close": raw.close_p,
                "volume": raw.volume,
            }
        else:
            if not bool(raw.get("is_closed", raw.get("closed", True))):
                continue
            timestamp = raw.get("timestamp", raw.get("time", datetime.fromtimestamp(position)))
            values = raw
        if not isinstance(timestamp, datetime):
            timestamp = datetime.fromisoformat(str(timestamp)) if isinstance(timestamp, str) else datetime.fromtimestamp(float(timestamp))
        normalized.append(
            {
                "index": int(values.get("index", position)),
                "timestamp": timestamp,
                "open": float(values["open"]),
                "high": float(values["high"]),
                "low": float(values["low"]),
                "close": float(values["close"]),
                "volume": float(values.get("volume", 0.0)),
            }
        )
    return normalized


def _confirmation_index(ob: Mapping[str, Any]) -> int:
    structure = ob.get("structure_event_reference", {})
    if isinstance(structure, Mapping) and structure.get("confirmation_candle_index") is not None:
        return int(structure["confirmation_candle_index"])
    formation = ob.get("formation_context", {})
    if isinstance(formation, Mapping) and formation.get("structure_confirmation_candle_index") is not None:
        return int(formation["structure_confirmation_candle_index"])
    return int(ob.get("ob_candle", {}).get("index", ob.get("created_index", 0)))


def _target_from_ob(ob: Mapping[str, Any]) -> Mapping[str, Any] | None:
    target = ob.get("target_liquidity")
    return target if isinstance(target, Mapping) else None


def _target_price(target: Mapping[str, Any] | None) -> float | None:
    if not target:
        return None
    for key in ("zone_mid", "price", "target_price", "zone_high"):
        if key in target and target[key] is not None:
            return float(target[key])
    zone = target.get("price_zone")
    if isinstance(zone, Mapping):
        return float(zone.get("zone_mid", zone.get("zone_high")))
    return None


def _event_text(event: Mapping[str, Any] | str) -> str:
    if isinstance(event, str):
        return event
    return " ".join(str(value) for value in event.values() if isinstance(value, str))
