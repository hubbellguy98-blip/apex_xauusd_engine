"""
Apex Engine - Operational Control API
Responsibility: Exposes safe control-plane actions for runtime orchestration.
Latency Profile: Executes low-frequency commands off critical market data paths.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from src.api.dependencies import get_runtime


def halt_engine(reason: str = "manual_operator_request") -> Dict[str, Any]:
    """Set an operational halt flag in runtime state when available."""
    runtime = get_runtime()
    manager = runtime.state_manager

    if manager is None:
        return {
            "ok": False,
            "message": "State manager is unavailable.",
            "timestamp": datetime.utcnow().isoformat(),
        }

    setattr(manager, "_emergency_halt_reason", reason)
    setattr(manager, "_emergency_halt_at", datetime.utcnow())
    return {
        "ok": True,
        "action": "HALT",
        "reason": reason,
        "timestamp": datetime.utcnow().isoformat(),
    }


def resume_engine() -> Dict[str, Any]:
    """Clear previously applied manual halt markers."""
    runtime = get_runtime()
    manager = runtime.state_manager

    if manager is None:
        return {
            "ok": False,
            "message": "State manager is unavailable.",
            "timestamp": datetime.utcnow().isoformat(),
        }

    setattr(manager, "_emergency_halt_reason", None)
    setattr(manager, "_emergency_halt_at", None)
    return {
        "ok": True,
        "action": "RESUME",
        "timestamp": datetime.utcnow().isoformat(),
    }


def register_operational_routes(app: Any) -> None:
    """Attach operational endpoints to compatible API app objects."""
    if not hasattr(app, "post"):
        return

    @app.post("/api/v1/control/halt")
    def post_halt_engine(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        reason = (payload or {}).get("reason", "manual_operator_request")
        return halt_engine(reason=reason)

    @app.post("/api/v1/control/resume")
    def post_resume_engine() -> Dict[str, Any]:
        return resume_engine()