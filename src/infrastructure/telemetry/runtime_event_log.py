"""Append-only runtime evidence log for trading session reporting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """A sanitized event emitted by the engine for reports and notifications."""

    timestamp_utc: datetime
    event_type: str
    severity: str
    payload: dict[str, Any]

    def to_record(self) -> dict[str, Any]:
        return {
            "timestamp_utc": self.timestamp_utc.astimezone(timezone.utc).isoformat(),
            "event_type": self.event_type,
            "severity": self.severity.upper(),
            "payload": _sanitize_payload(self.payload),
        }


class JsonlRuntimeEventLog:
    """Durable JSONL log used by daily reports without requiring a database."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, event_type: str, severity: str = "INFO", **payload: Any) -> RuntimeEvent:
        event = RuntimeEvent(
            timestamp_utc=datetime.now(timezone.utc),
            event_type=event_type,
            severity=severity.upper(),
            payload=_sanitize_payload(payload),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_record(), sort_keys=True, ensure_ascii=True))
            handle.write("\n")
        return event

    def read_since(self, since_utc: datetime) -> list[RuntimeEvent]:
        if not self.path.exists():
            return []
        since = since_utc.astimezone(timezone.utc)
        events: list[RuntimeEvent] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    timestamp = datetime.fromisoformat(record["timestamp_utc"]).astimezone(timezone.utc)
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                if timestamp < since:
                    continue
                payload = record.get("payload")
                events.append(
                    RuntimeEvent(
                        timestamp_utc=timestamp,
                        event_type=str(record.get("event_type", "UNKNOWN")),
                        severity=str(record.get("severity", "INFO")).upper(),
                        payload=payload if isinstance(payload, dict) else {},
                    )
                )
        return events


def _sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in payload.items():
        normalized_key = str(key)
        if _looks_secret(normalized_key):
            sanitized[normalized_key] = "***"
            continue
        sanitized[normalized_key] = _json_safe(value)
    return sanitized


def _looks_secret(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in ("password", "token", "secret", "key"))


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat() if value.tzinfo else value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return _sanitize_payload(value)
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "value"):
        return _json_safe(value.value)
    return str(value)
