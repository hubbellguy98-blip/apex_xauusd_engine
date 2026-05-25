"""MT5 configuration loading from local environment files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass(frozen=True, slots=True)
class MT5GatewayConfig:
    login: int
    password: str
    server: str
    terminal_path: Optional[str]
    symbol: str
    dry_run: bool = True
    require_demo: bool = True
    max_lot: float = 0.01
    deviation_points: int = 20
    magic_number: int = 260525


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def read_env_file(env_path: Optional[Path] = None) -> Dict[str, str]:
    path = env_path or repo_root() / ".env"
    values: Dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _as_bool(value: str, default: bool) -> bool:
    if value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_mt5_config(env_path: Optional[Path] = None) -> MT5GatewayConfig:
    values = read_env_file(env_path)
    missing = [key for key in ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER") if not values.get(key)]
    if missing:
        target = env_path or repo_root() / ".env"
        raise ValueError(f"Missing MT5 settings in {target}: {', '.join(missing)}")

    return MT5GatewayConfig(
        login=int(values["MT5_LOGIN"]),
        password=values["MT5_PASSWORD"],
        server=values["MT5_SERVER"],
        terminal_path=values.get("MT5_PATH") or None,
        symbol=values.get("APEX_SYMBOL", "XAUUSD"),
        dry_run=_as_bool(values.get("APEX_MT5_DRY_RUN", "true"), True),
        require_demo=_as_bool(values.get("APEX_MT5_REQUIRE_DEMO", "true"), True),
        max_lot=float(values.get("APEX_MAX_LOT", "0.01")),
        deviation_points=int(values.get("APEX_MT5_DEVIATION_POINTS", "20")),
        magic_number=int(values.get("APEX_MT5_MAGIC", "260525")),
    )
