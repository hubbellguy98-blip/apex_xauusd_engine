"""
Apex Engine - API Server Bootstrap
Responsibility: Build and wire runtime API surface for dashboard and control endpoints.
Latency Profile: Startup-only wiring; negligible impact on core execution loops.
"""

from __future__ import annotations

from typing import Any, Optional

from src.api.dependencies import ApiRuntimeContainer, bind_runtime
from src.api.v1.dashboard_feeds import register_dashboard_routes
from src.api.v1.operational_control import register_operational_routes


class MinimalApiApp:
    """Fallback API object used when FastAPI is not installed."""

    def __init__(self) -> None:
        self.routes: dict[str, dict[str, Any]] = {"GET": {}, "POST": {}}

    def get(self, path: str):
        def decorator(func):
            self.routes["GET"][path] = func
            return func
        return decorator

    def post(self, path: str):
        def decorator(func):
            self.routes["POST"][path] = func
            return func
        return decorator


def _build_framework_app() -> Any:
    """Create a FastAPI app when available, otherwise use fallback app."""
    try:
        from fastapi import FastAPI  # type: ignore
        return FastAPI(title="Apex XAUUSD Engine API", version="1.0.0")
    except Exception:
        return MinimalApiApp()


def create_api_server(runtime: Optional[ApiRuntimeContainer] = None) -> Any:
    """Create and wire the API server with runtime dependencies and routes."""
    app = _build_framework_app()
    if runtime is not None:
        bind_runtime(runtime)

    register_dashboard_routes(app)
    register_operational_routes(app)
    return app