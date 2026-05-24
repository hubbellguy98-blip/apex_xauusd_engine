"""Versioned API namespace for v1 endpoints."""

from src.api.v1.dashboard_feeds import register_dashboard_routes
from src.api.v1.operational_control import register_operational_routes

__all__ = ["register_dashboard_routes", "register_operational_routes"]