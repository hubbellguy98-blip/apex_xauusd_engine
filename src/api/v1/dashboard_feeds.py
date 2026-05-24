"""
Apex Engine - Dashboard Feeds API
Responsibility: Exposes read-optimized snapshots for UI monitoring layers.
Latency Profile: Non-blocking in-memory reads from state containers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from src.api.dependencies import get_runtime


def _safe_attr(obj: Any, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default) if obj is not None else default


def build_dashboard_snapshot() -> Dict[str, Any]:
    """Build a compact, serialization-safe operational snapshot."""
    runtime = get_runtime()
    manager = runtime.state_manager

    if manager is None:
        return {
            "status": "BOOTSTRAPPING",
            "timestamp": datetime.utcnow().isoformat(),
            "market": {},
            "session": {},
            "regime": {},
            "health": {},
        }

    snapshot = _safe_attr(manager, "snapshot")
    market = _safe_attr(snapshot, "market", None)
    session = _safe_attr(snapshot, "session", None)
    regime = _safe_attr(snapshot, "regime", None)
    health = _safe_attr(snapshot, "health", None)

    return {
        "status": "LIVE",
        "timestamp": datetime.utcnow().isoformat(),
        "market": {
            "mid": _safe_attr(market, "current_mid", 0.0),
            "spread": _safe_attr(market, "current_spread", 0.0),
            "tick_count": _safe_attr(market, "accumulated_tick_count", 0),
            "synced": _safe_attr(market, "is_synchronized", False),
        },
        "session": {
            "phase": str(_safe_attr(session, "current_phase", "UNKNOWN")),
            "last_transition": str(_safe_attr(session, "last_phase_transition", "")),
        },
        "regime": {
            "state": str(_safe_attr(regime, "current_regime", "UNKNOWN")),
            "volatility_ratio": _safe_attr(regime, "volatility_ratio", 0.0),
            "volume_z": _safe_attr(regime, "volume_z_score", 0.0),
        },
        "health": {
            "queue_backpressure": _safe_attr(health, "queue_backpressure_count", 0),
            "error_count": _safe_attr(health, "error_count", 0),
            "halted": _safe_attr(health, "is_halted", False),
        },
    }


def register_dashboard_routes(app: Any) -> None:
    """Attach dashboard feed routes to compatible API app objects."""
    if not hasattr(app, "get"):
        return

    @app.get("/api/v1/dashboard/snapshot")
    def get_dashboard_snapshot() -> Dict[str, Any]:
        return build_dashboard_snapshot()

    @app.get("/api/v1/dashboard/health")
    def get_dashboard_health() -> Dict[str, Any]:
        snapshot = build_dashboard_snapshot()
        return {
            "status": snapshot.get("status", "UNKNOWN"),
            "halted": snapshot.get("health", {}).get("halted", False),
            "timestamp": snapshot.get("timestamp"),
        }