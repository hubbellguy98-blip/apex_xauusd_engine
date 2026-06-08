"""ICT/SMC kill-zone timing filters.

Kill zones are modeled as deterministic session-time context, not entry
signals. This module can label, filter, or score existing setups, but it never
turns a weak price-action idea into a valid trade by time alone.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone as dt_timezone, tzinfo
from enum import Enum
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class KillzoneFilterMode(str, Enum):
    STRICT = "strict"
    SCORE_MODIFIER = "score_modifier"
    LABEL_ONLY = "label_only"


class KillzoneTimezoneStatus(str, Enum):
    VALID = "valid"
    FALLBACK_FIXED_OFFSET = "fallback_fixed_offset"
    UNKNOWN_ASSUMED_UTC = "unknown_assumed_utc"


@dataclass(frozen=True, slots=True)
class _KillzoneWindow:
    name: str
    session: str
    start_time: time
    end_time: time
    priority_weight: float
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class _TimezoneResolution:
    tz: tzinfo
    name: str
    status: KillzoneTimezoneStatus
    warning: str | None = None


_FIXED_ZONE_FALLBACKS: dict[str, int] = {
    "America/New_York": -4,
    "Europe/London": 1,
}

_TIMESTAMP_FIELD_MAP: dict[str, tuple[str, ...]] = {
    "silver_bullet": ("fvg_creation_timestamp", "sweep_timestamp", "timestamp"),
    "judas_swing": (
        "manipulation_timestamp",
        "sweep_timestamp",
        "confirmation_timestamp",
        "timestamp",
    ),
    "liquidity_sweep": ("sweep_timestamp", "confirmation_timestamp", "timestamp"),
    "mss": ("mss_confirmation_timestamp", "confirmation_timestamp", "timestamp"),
    "market_structure_shift": (
        "mss_confirmation_timestamp",
        "confirmation_timestamp",
        "timestamp",
    ),
    "fvg": ("fvg_creation_timestamp", "confirmation_timestamp", "timestamp"),
    "fvg_retest": ("fvg_creation_timestamp", "confirmation_timestamp", "timestamp"),
}


def is_in_killzone(timestamp: Any, killzone_config: Mapping[str, Any]) -> dict[str, Any]:
    """Return whether ``timestamp`` falls inside configured ICT kill zones."""
    warnings: list[str] = []
    timestamp_value = _coerce_datetime(timestamp)
    if timestamp_value is None:
        return _empty_killzone_result(
            timestamp,
            killzone_config,
            ["invalid_or_missing_timestamp"],
        )

    timestamp_tz = _resolve_timezone(
        killzone_config.get("timestamp_timezone"),
        killzone_config.get("broker_utc_offset"),
        "timestamp_timezone",
    )
    strategy_tz_name = (
        killzone_config.get("strategy_timezone")
        or killzone_config.get("killzone_timezone")
        or killzone_config.get("timezone")
        or "UTC"
    )
    strategy_tz = _resolve_timezone(strategy_tz_name, None, "strategy_timezone")
    for resolution in (timestamp_tz, strategy_tz):
        if resolution.warning:
            warnings.append(resolution.warning)

    if timestamp_value.tzinfo is None:
        if "timestamp_timezone" not in killzone_config:
            warnings.append("timestamp_timezone_unknown_assumed_UTC")
        timestamp_value = timestamp_value.replace(tzinfo=timestamp_tz.tz)

    converted = timestamp_value.astimezone(strategy_tz.tz)
    allowed_days = set(killzone_config.get("allowed_days") or [])
    allowed_day = not allowed_days or converted.strftime("%A") in allowed_days
    windows = _parse_killzones(killzone_config.get("killzones", []), warnings)

    matched = []
    if allowed_day:
        for window in windows:
            if not window.enabled:
                continue
            if _time_inside_window(converted.time(), window.start_time, window.end_time):
                matched.append(_window_to_dict(window))
    else:
        warnings.append(f"day_not_allowed:{converted.strftime('%A')}")

    primary = max(matched, key=lambda item: item["priority_weight"]) if matched else None
    if not matched and allowed_day:
        warnings.append("timestamp_outside_all_configured_killzones")

    return {
        "function": "is_in_killzone",
        "input_timestamp": _format_timestamp(timestamp_value),
        "converted_timestamp": _format_timestamp(converted),
        "timezone_used": strategy_tz.name,
        "timezone_status": strategy_tz.status.value,
        "in_killzone": bool(matched),
        "matched_killzones": matched,
        "primary_killzone": primary["name"] if primary else None,
        "killzone_name": primary["name"] if primary else None,
        "session_name": primary["session"] if primary else None,
        "killzone_start": primary["start_time"] if primary else None,
        "killzone_end": primary["end_time"] if primary else None,
        "allowed_day": allowed_day,
        "time_filter_passed": bool(matched),
        "killzone_score_adjustment": 0.0,
        "warnings": _dedupe(warnings),
    }


def filter_setups_by_killzone(
    setups: Sequence[Mapping[str, Any]],
    killzone_config: Mapping[str, Any],
) -> dict[str, Any]:
    """Filter, label, or score existing setup dictionaries by kill-zone timing."""
    warnings: list[str] = []
    mode = _parse_filter_mode(killzone_config.get("filter_mode", "score_modifier"))
    bonus = float(killzone_config.get("inside_killzone_bonus", 1.0))
    penalty = float(killzone_config.get("outside_killzone_penalty", 1.0))
    strict_models = {
        str(model).lower() for model in killzone_config.get("strict_for_models", [])
    }

    filtered_setups: list[dict[str, Any]] = []
    rejected_setups: list[dict[str, Any]] = []
    labeled_setups: list[dict[str, Any]] = []
    inside_count = 0
    outside_count = 0

    for raw_setup in setups or []:
        setup = deepcopy(dict(raw_setup))
        timestamp_value, timestamp_field = _select_setup_timestamp(setup, killzone_config)
        result = is_in_killzone(timestamp_value, killzone_config)
        setup_type = str(setup.get("setup_type", "")).lower()
        effective_mode = (
            KillzoneFilterMode.STRICT if setup_type in strict_models else mode
        )
        primary_weight = _primary_weight(result)
        base_score = float(setup.get("base_score", setup.get("score", 0.0)) or 0.0)

        if result["in_killzone"]:
            inside_count += 1
        else:
            outside_count += 1

        enriched = _enrich_setup(
            setup,
            result,
            timestamp_value,
            timestamp_field,
            base_score,
        )
        enriched["filter_mode_applied"] = effective_mode.value

        if effective_mode is KillzoneFilterMode.STRICT:
            if result["in_killzone"]:
                enriched["time_filter_passed"] = True
                enriched["killzone_score_adjustment"] = 0.0
                enriched["final_score"] = _clamp_score(base_score)
                enriched["reason"] = _inside_reason(result)
                filtered_setups.append(enriched)
            else:
                enriched["time_filter_passed"] = False
                enriched["killzone_score_adjustment"] = 0.0
                enriched["final_score"] = _clamp_score(base_score)
                enriched["rejection_reason"] = "outside_configured_killzone"
                enriched["note"] = (
                    "Price action may be valid, but strict kill-zone rules "
                    "reject setups outside configured timing windows."
                )
                rejected_setups.append(enriched)
        elif effective_mode is KillzoneFilterMode.SCORE_MODIFIER:
            if result["in_killzone"]:
                adjustment = bonus * primary_weight
                enriched["time_filter_passed"] = True
                enriched["reason"] = _inside_reason(result)
            else:
                adjustment = -penalty
                enriched["time_filter_passed"] = False
                enriched["reason"] = "Setup timestamp is outside configured kill zones"
            enriched["killzone_score_adjustment"] = round(adjustment, 4)
            enriched["final_score"] = _clamp_score(base_score + adjustment)
            filtered_setups.append(enriched)
        else:
            enriched["time_filter_passed"] = result["in_killzone"]
            enriched["killzone_score_adjustment"] = 0.0
            enriched["final_score"] = _clamp_score(base_score)
            enriched["reason"] = (
                _inside_reason(result)
                if result["in_killzone"]
                else "Setup labeled outside configured kill zones"
            )
            labeled_setups.append(enriched)

        if not _has_price_action_confirmation(enriched):
            enriched["warnings"].append("killzone_alone_not_enough")
            enriched["valid_trade"] = False
            enriched["entry_allowed_from_killzone_alone"] = False

        warnings.extend(result["warnings"])

    return {
        "function": "filter_setups_by_killzone",
        "filter_mode": mode.value,
        "summary": {
            "total_setups": len(setups or []),
            "inside_killzone": inside_count,
            "outside_killzone": outside_count,
            "accepted": len(filtered_setups),
            "rejected": len(rejected_setups),
            "labeled": len(labeled_setups),
        },
        "filtered_setups": filtered_setups,
        "rejected_setups": rejected_setups,
        "labeled_setups": labeled_setups,
        "warnings": _dedupe(warnings),
    }


def _empty_killzone_result(
    timestamp: Any,
    killzone_config: Mapping[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    timezone_used = (
        killzone_config.get("strategy_timezone")
        or killzone_config.get("killzone_timezone")
        or killzone_config.get("timezone")
        or "UTC"
    )
    return {
        "function": "is_in_killzone",
        "input_timestamp": str(timestamp),
        "converted_timestamp": None,
        "timezone_used": timezone_used,
        "timezone_status": KillzoneTimezoneStatus.UNKNOWN_ASSUMED_UTC.value,
        "in_killzone": False,
        "matched_killzones": [],
        "primary_killzone": None,
        "killzone_name": None,
        "session_name": None,
        "killzone_start": None,
        "killzone_end": None,
        "allowed_day": False,
        "time_filter_passed": False,
        "killzone_score_adjustment": 0.0,
        "warnings": _dedupe(warnings),
    }


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None
    return None


def _resolve_timezone(
    name: Any,
    broker_utc_offset: Any,
    field_name: str,
) -> _TimezoneResolution:
    tz_name = str(name or "UTC")
    if tz_name.lower() == "broker":
        if broker_utc_offset is not None:
            return _offset_timezone(str(broker_utc_offset), "broker")
        return _TimezoneResolution(
            dt_timezone.utc,
            "UTC",
            KillzoneTimezoneStatus.UNKNOWN_ASSUMED_UTC,
            f"{field_name}_broker_offset_missing_assumed_UTC",
        )

    offset_tz = _offset_timezone(tz_name, tz_name)
    if offset_tz is not None:
        return offset_tz
    if tz_name.upper() == "UTC":
        return _TimezoneResolution(dt_timezone.utc, "UTC", KillzoneTimezoneStatus.VALID)

    try:
        return _TimezoneResolution(
            ZoneInfo(tz_name),
            tz_name,
            KillzoneTimezoneStatus.VALID,
        )
    except ZoneInfoNotFoundError:
        fallback_hours = _FIXED_ZONE_FALLBACKS.get(tz_name)
        if fallback_hours is not None:
            fallback = dt_timezone(timedelta(hours=fallback_hours), tz_name)
            return _TimezoneResolution(
                fallback,
                tz_name,
                KillzoneTimezoneStatus.FALLBACK_FIXED_OFFSET,
                f"{field_name}_fixed_offset_fallback_used:{tz_name}",
            )
        return _TimezoneResolution(
            dt_timezone.utc,
            "UTC",
            KillzoneTimezoneStatus.UNKNOWN_ASSUMED_UTC,
            f"{field_name}_unknown_assumed_UTC:{tz_name}",
        )


def _offset_timezone(value: str, name: str) -> _TimezoneResolution | None:
    value = value.strip()
    if len(value) != 6 or value[0] not in "+-" or value[3] != ":":
        return None
    try:
        hours = int(value[1:3])
        minutes = int(value[4:6])
    except ValueError:
        return None
    sign = 1 if value[0] == "+" else -1
    offset = timedelta(hours=hours, minutes=minutes) * sign
    return _TimezoneResolution(
        dt_timezone(offset, name),
        name,
        KillzoneTimezoneStatus.VALID,
    )


def _parse_killzones(raw_windows: Any, warnings: list[str]) -> list[_KillzoneWindow]:
    windows: list[_KillzoneWindow] = []
    for index, raw in enumerate(raw_windows or []):
        if not isinstance(raw, Mapping):
            warnings.append(f"invalid_killzone_config:{index}")
            continue
        start = _parse_time(raw.get("start_time"))
        end = _parse_time(raw.get("end_time"))
        if start is None or end is None or start == end:
            warnings.append(f"invalid_killzone_time:{raw.get('name', index)}")
            continue
        windows.append(
            _KillzoneWindow(
                name=str(raw.get("name") or f"killzone_{index}"),
                session=str(raw.get("session") or raw.get("name") or "unknown"),
                start_time=start,
                end_time=end,
                priority_weight=float(raw.get("priority_weight", 1.0)),
                enabled=bool(raw.get("enabled", True)),
            )
        )
    return windows


def _parse_time(value: Any) -> time | None:
    if isinstance(value, time):
        return value
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) < 2:
        return None
    try:
        return time(int(parts[0]), int(parts[1]))
    except ValueError:
        return None


def _time_inside_window(value: time, start: time, end: time) -> bool:
    if start < end:
        return start <= value <= end
    return value >= start or value <= end


def _window_to_dict(window: _KillzoneWindow) -> dict[str, Any]:
    return {
        "name": window.name,
        "session": window.session,
        "start_time": window.start_time.strftime("%H:%M"),
        "end_time": window.end_time.strftime("%H:%M"),
        "priority_weight": window.priority_weight,
    }


def _parse_filter_mode(value: Any) -> KillzoneFilterMode:
    try:
        return KillzoneFilterMode(str(value))
    except ValueError:
        return KillzoneFilterMode.SCORE_MODIFIER


def _select_setup_timestamp(
    setup: Mapping[str, Any],
    killzone_config: Mapping[str, Any],
) -> tuple[Any, str | None]:
    configured_field = killzone_config.get("timestamp_field")
    if configured_field:
        value = _field_value(setup, str(configured_field))
        if value is not None:
            return value, str(configured_field)

    setup_type = str(setup.get("setup_type", "")).lower()
    field_candidates = _TIMESTAMP_FIELD_MAP.get(
        setup_type,
        ("confirmation_timestamp", "entry_timestamp", "timestamp"),
    )
    for field_name in field_candidates:
        value = _field_value(setup, field_name)
        if value is not None:
            return value, field_name
    return None, None


def _field_value(setup: Mapping[str, Any], field_name: str) -> Any:
    if field_name in setup:
        return setup[field_name]
    nested_fields = {
        "sweep_timestamp": (("sweep", "timestamp"), ("sweep", "sweep_timestamp")),
        "fvg_creation_timestamp": (
            ("fvg_zone", "creation_timestamp"),
            ("fvg", "creation_timestamp"),
        ),
        "mss_confirmation_timestamp": (
            ("mss", "confirmation_timestamp"),
            ("structure_shift", "confirmation_timestamp"),
        ),
    }
    for path in nested_fields.get(field_name, ()):
        current: Any = setup
        for key in path:
            if not isinstance(current, Mapping) or key not in current:
                current = None
                break
            current = current[key]
        if current is not None:
            return current
    return None


def _enrich_setup(
    setup: dict[str, Any],
    result: Mapping[str, Any],
    timestamp_value: Any,
    timestamp_field: str | None,
    base_score: float,
) -> dict[str, Any]:
    setup["timestamp_checked"] = result.get("converted_timestamp")
    setup["raw_timestamp_checked"] = _format_timestamp(_coerce_datetime(timestamp_value))
    setup["timestamp_field_used"] = timestamp_field
    setup["in_killzone"] = result["in_killzone"]
    setup["killzone_name"] = result["killzone_name"]
    setup["session_name"] = result["session_name"]
    setup["matched_killzones"] = result["matched_killzones"]
    setup["timezone_used"] = result["timezone_used"]
    setup["timezone_status"] = result["timezone_status"]
    setup["base_score"] = _clamp_score(base_score)
    setup["valid_trade"] = bool(setup.get("valid_trade", setup.get("valid_setup", False)))
    setup["entry_allowed_from_killzone_alone"] = False
    setup["killzone_is_signal"] = False
    setup["warnings"] = _dedupe(list(setup.get("warnings", [])) + result["warnings"])
    return setup


def _primary_weight(result: Mapping[str, Any]) -> float:
    for window in result.get("matched_killzones", []):
        if window.get("name") == result.get("primary_killzone"):
            return float(window.get("priority_weight", 1.0))
    return 1.0


def _inside_reason(result: Mapping[str, Any]) -> str:
    return (
        f"Setup occurred inside {result['killzone_name']} "
        f"({result['session_name']} session)"
    )


def _has_price_action_confirmation(setup: Mapping[str, Any]) -> bool:
    if setup.get("valid_setup") or setup.get("valid_trade"):
        return True
    keys = (
        "liquidity_sweep",
        "has_liquidity_sweep",
        "mss_confirmed",
        "bos_confirmed",
        "choch_confirmed",
        "displacement_confirmed",
        "fvg_created_by_displacement",
    )
    return any(bool(setup.get(key)) for key in keys)


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(10.0, value)), 4)


def _format_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
