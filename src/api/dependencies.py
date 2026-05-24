"""
Apex Engine - API Dependency Registry
Responsibility: Runtime dependency container for API-facing services and query helpers.
Latency Profile: O(1) in-memory lookups guarded by lock-free access patterns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(slots=True)
class ApiRuntimeContainer:
    """Container for runtime services consumed by API route handlers."""

    event_bus: Optional[Any] = None
    state_manager: Optional[Any] = None
    lifecycle_manager: Optional[Any] = None


_RUNTIME = ApiRuntimeContainer()


def bind_runtime(runtime: ApiRuntimeContainer) -> None:
    """Bind runtime service references for API handlers."""
    _RUNTIME.event_bus = runtime.event_bus
    _RUNTIME.state_manager = runtime.state_manager
    _RUNTIME.lifecycle_manager = runtime.lifecycle_manager


def get_runtime() -> ApiRuntimeContainer:
    """Return currently bound runtime container."""
    return _RUNTIME


def clear_runtime() -> None:
    """Clear runtime references during shutdown or test teardown."""
    _RUNTIME.event_bus = None
    _RUNTIME.state_manager = None
    _RUNTIME.lifecycle_manager = None