from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


def _bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(value: object, default: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _float(value: object, default: float) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class ReportingConfig:
    reporting_enabled: bool = True
    output_dir: Path = Path("reports/output")
    display_timezone: str = "Asia/Kolkata"
    broker_timezone: str = "UTC"
    symbol_filter: str = ""
    trade_log_path: Path = Path("trade_log.csv")
    execution_log_path: Path | None = None
    broker_history_path: Path | None = None
    signal_log_path: Path | None = None
    risk_log_path: Path | None = None
    equity_csv_path: Path | None = None
    ai_enabled: bool = False
    ai_provider: str = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-1.5-flash"
    ai_timeout_seconds: int = 30
    ai_max_retries: int = 2
    telegram_enabled: bool = False
    telegram_send_summary: bool = True
    telegram_send_files: bool = True
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_max_file_size_mb: float = 45.0
    minimum_planned_rr: float = 2.0
    pnl_tolerance: float = 0.01
    extra_metadata: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(
        cls,
        env_path: Path | None = Path(".env"),
        overrides: Mapping[str, object] | None = None,
    ) -> "ReportingConfig":
        file_values = read_env_file(env_path) if env_path else {}
        env = {**file_values, **os.environ}
        if overrides:
            env.update({k: str(v) for k, v in overrides.items() if v is not None})

        def opt_path(key: str) -> Path | None:
            value = env.get(key, "").strip()
            return Path(value) if value else None

        return cls(
            reporting_enabled=_bool(env.get("REPORTING_ENABLED"), True),
            output_dir=Path(env.get("REPORT_OUTPUT_DIR", "reports/output")),
            display_timezone=env.get("REPORT_DISPLAY_TIMEZONE", "Asia/Kolkata"),
            broker_timezone=env.get("REPORT_BROKER_TIMEZONE", "UTC"),
            symbol_filter=env.get("REPORT_SYMBOL_FILTER", ""),
            trade_log_path=Path(env.get("REPORT_TRADE_LOG", env.get("TRADE_LOG_PATH", "trade_log.csv"))),
            execution_log_path=opt_path("REPORT_EXECUTION_LOG"),
            broker_history_path=opt_path("REPORT_BROKER_HISTORY_EXPORT"),
            signal_log_path=opt_path("REPORT_SIGNAL_LOG"),
            risk_log_path=opt_path("REPORT_RISK_LOG"),
            equity_csv_path=opt_path("REPORT_EQUITY_CSV"),
            ai_enabled=_bool(env.get("REPORT_AI_ENABLED"), False),
            ai_provider=env.get("AI_PROVIDER", "gemini"),
            gemini_api_key=env.get("GEMINI_API_KEY", ""),
            gemini_model=env.get("GEMINI_MODEL", "gemini-1.5-flash"),
            ai_timeout_seconds=_int(env.get("AI_REPORT_TIMEOUT_SECONDS"), 30),
            ai_max_retries=_int(env.get("AI_REPORT_MAX_RETRIES"), 2),
            telegram_enabled=_bool(env.get("REPORT_TELEGRAM_ENABLED"), False),
            telegram_send_summary=_bool(env.get("REPORT_TELEGRAM_SEND_SUMMARY"), True),
            telegram_send_files=_bool(env.get("REPORT_TELEGRAM_SEND_FILES"), True),
            telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN", env.get("APEX_TELEGRAM_BOT_TOKEN", "")),
            telegram_chat_id=env.get("TELEGRAM_CHAT_ID", env.get("APEX_TELEGRAM_CHAT_ID", "")),
            telegram_max_file_size_mb=_float(env.get("REPORT_TELEGRAM_MAX_FILE_SIZE_MB"), 45.0),
            minimum_planned_rr=_float(env.get("REPORT_MINIMUM_PLANNED_RR"), 2.0),
            pnl_tolerance=_float(env.get("REPORT_PNL_TOLERANCE"), 0.01),
        )

