"""
Common subsystem contract used by orchestration components.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class BaseSubsystem:
    """Small lifecycle base class shared by runtime services."""

    name: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    is_bootstrapped: bool = False

    async def bootstrap(self) -> None:
        self.is_bootstrapped = True

    async def terminate(self) -> None:
        self.is_bootstrapped = False
