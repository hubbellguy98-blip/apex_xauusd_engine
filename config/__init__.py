"""
Apex Engine - Configuration Package
Responsibility: Exposes profile-aware settings factories for runtime bootstrap.
"""

from __future__ import annotations

from typing import Literal

from config.base_settings import EngineSettings
from config.live_profile import LiveEngineSettings
from config.simulation_profile import SimulationEngineSettings

ProfileName = Literal["base", "live", "simulation"]


def load_settings(profile: ProfileName = "base") -> EngineSettings:
    """Load a concrete settings profile for the requested runtime mode."""
    normalized = profile.strip().lower()
    if normalized == "live":
        return LiveEngineSettings()
    if normalized == "simulation":
        return SimulationEngineSettings()
    return EngineSettings()


__all__ = [
    "EngineSettings",
    "LiveEngineSettings",
    "SimulationEngineSettings",
    "ProfileName",
    "load_settings",
]