"""
Apex Engine - Time Standardization Utilities
Responsibility: Provides deterministic time handling across live trading and simulation backtests.
Latency Profile: Highly optimized system datetime retrievals.
"""

from datetime import datetime, timezone
from typing import Optional

class TimeProvider:
    """Manages system time calculations across active live pipelines and backtest engines."""
    
    _simulated_time: Optional[datetime] = None
    _is_simulated: bool = False

    @classmethod
    def set_simulation_mode(cls, initial_time: datetime) -> None:
        """Locks the execution timeline to match the historical backtest sequence."""
        cls._is_simulated = True
        cls._simulated_time = initial_time

    @classmethod
    def update_simulation_time(cls, new_time: datetime) -> None:
        """Advances the internal simulation clock."""
        if cls._is_simulated:
            cls._simulated_time = new_time

    @classmethod
    def get_utc_now(cls) -> datetime:
        """Returns the current structural time context based on system mode."""
        if cls._is_simulated and cls._simulated_time:
            return cls._simulated_time
        return datetime.now(timezone.utc)