"""Telegram delivery and daily reporting for Apex runtime telemetry."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import html
from pathlib import Path
from typing import Any

from src.infrastructure.broker.mt5_config import read_env_file
from src.infrastructure.telemetry.runtime_event_log import JsonlRuntimeEventLog, RuntimeEvent

MAX_TELEGRAM_MESSAGE_LENGTH = 3900


@dataclass(frozen=True, slots=True)
class TelegramReportingConfig:
    """Runtime configuration for observer-only Telegram reporting."""

    enabled: bool
    bot_token: str | None
    chat_id: str | None
    parse_mode: str
    min_severity: str
    timeout_seconds: float
    event_log_path: Path
    daily_report_enabled: bool
    daily_report_lookback_hours: int
    daily_report_timezone: str
    notify_run_started: bool
    notify_session_summary: bool
    notify_qualified_signal: bool
    notify_order_result: bool
    notify_order_rejection: bool

    @classmethod
    def from_env_file(cls, env_path: Path, repo_root: Path) -> "TelegramReportingConfig":
        values = read_env_file(env_path)
        enabled = _as_bool(values.get("APEX_TELEGRAM_ENABLED"), default=False)
        bot_token = _empty_to_none(values.get("APEX_TELEGRAM_BOT_TOKEN"))
        chat_id = _empty_to_none(values.get("APEX_TELEGRAM_CHAT_ID"))
        if enabled and (bot_token is None or chat_id is None):
            raise RuntimeError("Telegram reporting is enabled but bot token or chat id is missing.")

        log_value = values.get("APEX_REPORTING_EVENT_LOG", ".apex_runtime/notifications/telegram_events.jsonl")
        event_log_path = Path(log_value)
        if not event_log_path.is_absolute():
            event_log_path = repo_root / event_log_path

        return cls(
            enabled=enabled,
            bot_token=bot_token,
            chat_id=chat_id,
            parse_mode=values.get("APEX_TELEGRAM_PARSE_MODE", "HTML").upper(),
            min_severity=values.get("APEX_TELEGRAM_MIN_SEVERITY", "INFO").upper(),
            timeout_seconds=float(values.get("APEX_TELEGRAM_TIMEOUT_SECONDS", "10")),
            event_log_path=event_log_path,
            daily_report_enabled=_as_bool(values.get("APEX_DAILY_REPORT_ENABLED"), default=False),
            daily_report_lookback_hours=int(values.get("APEX_DAILY_REPORT_LOOKBACK_HOURS", "24")),
            daily_report_timezone=values.get("APEX_DAILY_REPORT_TIMEZONE", "Asia/Kolkata"),
            notify_run_started=_as_bool(values.get("APEX_TELEGRAM_NOTIFY_RUN_STARTED"), default=False),
            notify_session_summary=_as_bool(values.get("APEX_TELEGRAM_NOTIFY_SESSION_SUMMARY"), default=False),
            notify_qualified_signal=_as_bool(values.get("APEX_TELEGRAM_NOTIFY_QUALIFIED_SIGNAL"), default=False),
            notify_order_result=_as_bool(values.get("APEX_TELEGRAM_NOTIFY_ORDER_RESULT"), default=True),
            notify_order_rejection=_as_bool(values.get("APEX_TELEGRAM_NOTIFY_ORDER_REJECTION"), default=False),
        )


class TelegramClient:
    """Small timeout-bound Telegram client that never participates in trading decisions."""

    def __init__(self, config: TelegramReportingConfig) -> None:
        self.config = config

    async def send_message(self, text: str) -> None:
        if not self.config.enabled or not self.config.bot_token or not self.config.chat_id:
            return
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)
        url = f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage"
        for chunk in split_telegram_message(text):
            payload: dict[str, Any] = {
                "chat_id": self.config.chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            }
            if self.config.parse_mode in {"HTML", "MARKDOWN", "MARKDOWNV2"}:
                payload["parse_mode"] = self.config.parse_mode
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as response:
                    response.raise_for_status()


class TelegramReportingService:
    """Observer layer for local evidence logging and optional Telegram delivery."""

    def __init__(self, config: TelegramReportingConfig) -> None:
        self.config = config
        self.event_log = JsonlRuntimeEventLog(config.event_log_path)
        self.client = TelegramClient(config)

    @classmethod
    def from_env_file(cls, env_path: Path, repo_root: Path) -> "TelegramReportingService":
        return cls(TelegramReportingConfig.from_env_file(env_path, repo_root))

    @classmethod
    def disabled(cls, repo_root: Path) -> "TelegramReportingService":
        return cls(
            TelegramReportingConfig(
                enabled=False,
                bot_token=None,
                chat_id=None,
                parse_mode="HTML",
                min_severity="INFO",
                timeout_seconds=10.0,
                event_log_path=repo_root / ".apex_runtime" / "notifications" / "telegram_events.jsonl",
                daily_report_enabled=False,
                daily_report_lookback_hours=24,
                daily_report_timezone="Asia/Kolkata",
                notify_run_started=False,
                notify_session_summary=False,
                notify_qualified_signal=False,
                notify_order_result=True,
                notify_order_rejection=False,
            )
        )

    def record(self, event_type: str, severity: str = "INFO", **payload: Any) -> RuntimeEvent:
        return self.event_log.append(event_type, severity, **payload)

    async def notify(self, title: str, fields: dict[str, Any], severity: str = "INFO") -> None:
        if not self._passes_severity(severity):
            return
        text = format_compact_message(title, fields, severity)
        try:
            await self.client.send_message(text)
        except Exception as exc:  # pragma: no cover - network failures depend on Telegram.
            self.record("TELEGRAM_DELIVERY_FAILED", "WARNING", error=str(exc), title=title)

    async def record_and_notify(
        self,
        event_type: str,
        severity: str = "INFO",
        notify: bool = False,
        **payload: Any,
    ) -> RuntimeEvent:
        event = self.record(event_type, severity, **payload)
        if notify and self.should_notify_event(event_type, severity, payload):
            await self.notify(event_type.replace("_", " ").title(), payload, severity)
        return event

    async def send_session_summary(self, summary: dict[str, Any]) -> None:
        if not self.config.enabled or not self.config.notify_session_summary:
            return
        await self.notify("Apex Session Summary", summary, "INFO")

    def should_notify_event(self, event_type: str, severity: str, payload: dict[str, Any]) -> bool:
        """Apply the operator-facing Telegram policy while still recording every event locally."""
        severity_upper = severity.upper()
        if severity_upper in {"ERROR", "CRITICAL"}:
            return True
        if event_type == "RUN_STARTED":
            return self.config.notify_run_started
        if event_type == "RISK_APPROVED":
            return self.config.notify_qualified_signal
        if event_type == "ORDER_RESULT":
            status = str(payload.get("order_status", "")).upper()
            if status == "FILLED":
                return self.config.notify_order_result
            return self.config.notify_order_rejection
        return False

    async def send_daily_report(self, lookback_hours: int | None = None) -> str:
        lookback = lookback_hours or self.config.daily_report_lookback_hours
        since = datetime.now(timezone.utc) - timedelta(hours=lookback)
        events = self.event_log.read_since(since)
        report = DailyReportBuilder(events, lookback).build_text_report()
        if self.config.enabled:
            try:
                await self.client.send_message(report)
            except Exception as exc:  # pragma: no cover - network failures depend on Telegram.
                self.record("TELEGRAM_DELIVERY_FAILED", "WARNING", error=str(exc), title="Daily report")
        return report

    def _passes_severity(self, severity: str) -> bool:
        order = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
        return order.get(severity.upper(), 20) >= order.get(self.config.min_severity, 20)


class DailyReportBuilder:
    """Builds an operator-focused report from JSONL runtime events."""

    def __init__(self, events: list[RuntimeEvent], lookback_hours: int = 24) -> None:
        self.events = events
        self.lookback_hours = lookback_hours

    def build_text_report(self) -> str:
        summary_events = [event for event in self.events if event.event_type == "RUN_SUMMARY"]
        latest_summary = summary_events[-1].payload if summary_events else {}
        event_counts = Counter(event.event_type for event in self.events)
        severity_counts = Counter(event.severity for event in self.events)
        session_metrics = _summarize_by_session(summary_events)
        rejection_counts = Counter(
            str(event.payload.get("rejection_reason") or event.payload.get("latest_confirmation_rejection"))
            for event in self.events
            if event.payload.get("rejection_reason") or event.payload.get("latest_confirmation_rejection")
        )
        blocked_counts = Counter(
            reason
            for event in self.events
            for reason in _as_list(event.payload.get("rejection_reasons") or event.payload.get("reasons"))
        )
        recommendations = self._recommendations(latest_summary, severity_counts, rejection_counts, blocked_counts)
        lines = [
            "<b>Apex XAUUSD Daily Intelligence Report</b>",
            f"Window: last {self.lookback_hours} hours",
            "",
            "<b>Operating State</b>",
            f"Mode: {html_escape(str(latest_summary.get('mode', 'unknown')))}",
            f"Symbol: {html_escape(str(latest_summary.get('symbol', 'unknown')))}",
            f"Status: {html_escape(str(latest_summary.get('status', 'NO_COMPLETED_RUN_SUMMARY')))}",
            f"Dry run: {latest_summary.get('dry_run', 'unknown')}",
            f"Max lot: {latest_summary.get('max_lot', 'unknown')}",
            "",
            "<b>Market Data Health</b>",
            f"Live quotes processed: {latest_summary.get('live_quotes_processed', 0)}",
            f"Closed candles ingested: {latest_summary.get('live_closed_candles_ingested', 0)}",
            f"Quote updates confirming live feed: {latest_summary.get('quote_updates_confirming_live_feed', 0)}",
            f"Inactive quote reads discarded: {latest_summary.get('inactive_quote_reads_discarded', 0)}",
            f"Temporary tick gaps: {latest_summary.get('temporary_tick_gaps', 0)}",
            "",
            "<b>Strategy Detection</b>",
            f"Qualified candidates: {latest_summary.get('qualified_candidates', 0)}",
            f"Live sweeps detected: {latest_summary.get('live_sweeps_detected', 0)}",
            f"Reversal candidates detected: {latest_summary.get('reversal_candidates_detected', 0)}",
            f"Confirmation blocks: {latest_summary.get('confirmation_blocks', 0)}",
            f"Quality blocks: {latest_summary.get('quality_blocks', 0)}",
            f"Cooldown blocks: {latest_summary.get('cooldown_blocks', 0)}",
            f"Latest confirmation rejection: {html_escape(str(latest_summary.get('latest_confirmation_rejection', 'none')))}",
            "",
            "<b>Risk And Execution</b>",
            f"Risk approved events: {event_counts['RISK_APPROVED']}",
            f"Risk/scoring blocked events: {event_counts['CANDIDATE_BLOCKED']}",
            f"Pre-submission checks: {event_counts['PRE_SUBMISSION_CHECK']}",
            f"Orders submitted/finalized: {event_counts['ORDER_RESULT']}",
            f"Stop updates applied: {latest_summary.get('stop_updates_applied', 0)}",
            "",
            "<b>Runtime Events</b>",
            f"Total events recorded: {len(self.events)}",
            f"Warnings: {severity_counts['WARNING']}",
            f"Errors: {severity_counts['ERROR'] + severity_counts['CRITICAL']}",
            f"Telegram delivery failures: {event_counts['TELEGRAM_DELIVERY_FAILED']}",
        ]
        if session_metrics:
            lines.extend(["", "<b>Session Breakdown</b>"])
            for session_name in ("ASIAN_ACCUMULATION", "LONDON_KILLZONE", "NEWYORK_KILLZONE", "POST_NY_RESET"):
                metrics = session_metrics.get(session_name)
                if not metrics:
                    continue
                lines.append(
                    f"{session_name}: runs={metrics['runs']} quotes={metrics['live_quotes']} "
                    f"sweeps={metrics['sweeps']} reversals={metrics['reversals']} "
                    f"qualified={metrics['qualified']} blocks={metrics['blocks']}"
                )
        if rejection_counts:
            lines.extend(["", "<b>Top Rejections</b>"])
            lines.extend(f"{html_escape(reason)}: {count}" for reason, count in rejection_counts.most_common(5))
        if blocked_counts:
            lines.extend(["", "<b>Top Blocks</b>"])
            lines.extend(f"{html_escape(reason)}: {count}" for reason, count in blocked_counts.most_common(5))
        lines.extend(["", "<b>What To Fix Or Watch Next</b>"])
        lines.extend(f"- {html_escape(item)}" for item in recommendations)
        return "\n".join(lines)

    def _recommendations(
        self,
        latest_summary: dict[str, Any],
        severity_counts: Counter[str],
        rejection_counts: Counter[str],
        blocked_counts: Counter[str],
    ) -> list[str]:
        recommendations: list[str] = []
        if not latest_summary:
            recommendations.append("No completed run summary was found. Run the strategy long enough to finish cleanly.")
            return recommendations
        if int(latest_summary.get("live_quotes_processed", 0) or 0) == 0:
            recommendations.append("Live quote flow is weak or missing. Verify MT5 is open, logged in, and the symbol is active.")
        if severity_counts["ERROR"] or severity_counts["CRITICAL"]:
            recommendations.append("Runtime errors occurred. Inspect the JSONL event log before enabling any execution mode.")
        if "SETUP_OUTSIDE_KILLZONE_BOUNDARIES" in rejection_counts:
            recommendations.append("Signals appeared outside the configured killzone. Gather more samples during the target sessions.")
        if blocked_counts:
            recommendations.append("Review repeated block reasons before loosening filters; repeated blocks are useful edge-validation evidence.")
        if int(latest_summary.get("qualified_candidates", 0) or 0) == 0:
            recommendations.append("No qualified candidate completed the full pipeline. Keep collecting shadow data before judging accuracy.")
        if not recommendations:
            recommendations.append("No critical fix is obvious from this window. Continue shadow/live-demo sampling and compare reports.")
        return recommendations


def format_compact_message(title: str, fields: dict[str, Any], severity: str = "INFO") -> str:
    lines = [f"<b>{html_escape(title)}</b>", f"Severity: {html_escape(severity.upper())}"]
    for key, value in fields.items():
        if _looks_sensitive(key):
            continue
        if value is None:
            continue
        lines.append(f"{html_escape(str(key))}: {html_escape(str(value))}")
    return "\n".join(lines)


def split_telegram_message(text: str) -> list[str]:
    if len(text) <= MAX_TELEGRAM_MESSAGE_LENGTH:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in text.splitlines():
        line_length = len(line) + 1
        if current and current_length + line_length > MAX_TELEGRAM_MESSAGE_LENGTH:
            chunks.append("\n".join(current))
            current = []
            current_length = 0
        current.append(line)
        current_length += line_length
    if current:
        chunks.append("\n".join(current))
    return chunks


def html_escape(value: str) -> str:
    return html.escape(value, quote=False)


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _empty_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped or stripped.startswith("replace_with_"):
        return None
    return stripped


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return [str(value)]


def _looks_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in ("password", "token", "secret", "key"))


def _summarize_by_session(summary_events: list[RuntimeEvent]) -> dict[str, dict[str, int]]:
    metrics: dict[str, dict[str, int]] = {}
    for event in summary_events:
        session_name = _session_bucket(event.timestamp_utc)
        bucket = metrics.setdefault(
            session_name,
            {"runs": 0, "live_quotes": 0, "sweeps": 0, "reversals": 0, "qualified": 0, "blocks": 0},
        )
        payload = event.payload
        bucket["runs"] += 1
        bucket["live_quotes"] += int(payload.get("live_quotes_processed", 0) or 0)
        bucket["sweeps"] += int(payload.get("live_sweeps_detected", 0) or 0)
        bucket["reversals"] += int(payload.get("reversal_candidates_detected", 0) or 0)
        bucket["qualified"] += int(payload.get("qualified_candidates", 0) or 0)
        bucket["blocks"] += int(payload.get("confirmation_blocks", 0) or 0)
        bucket["blocks"] += int(payload.get("quality_blocks", 0) or 0)
        bucket["blocks"] += int(payload.get("cooldown_blocks", 0) or 0)
    return metrics


def _session_bucket(timestamp_utc: datetime) -> str:
    hour = timestamp_utc.astimezone(timezone.utc).hour
    if hour >= 22 or hour < 6:
        return "ASIAN_ACCUMULATION"
    if 6 <= hour < 12:
        return "LONDON_KILLZONE"
    if 12 <= hour < 20:
        return "NEWYORK_KILLZONE"
    return "POST_NY_RESET"


async def maybe_send_daily_report(env_path: Path, repo_root: Path, lookback_hours: int | None = None) -> str:
    service = TelegramReportingService.from_env_file(env_path, repo_root)
    return await service.send_daily_report(lookback_hours)
