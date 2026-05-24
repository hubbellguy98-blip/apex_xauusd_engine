"""
Apex Engine - Algorithmic Market Session Tracker
Responsibility: Processes timeline matrices to delineate session transitions, killzones, and ranges.
Latency Profile: Synchronous temporal indexing, O(1) mathematical lookups.
"""

from datetime import datetime, time, timezone
from typing import Dict, Tuple, Optional
import structlog
from src.core.domain.market_data import TickNode
from src.core.domain.constants import SessionState

logger = structlog.get_logger()

class GoldSessionIntelligenceEngine:
    """Tracks specialized XAUUSD market hours, overlaps, and range accumulation zones."""

    def __init__(self) -> None:
        # Define algorithmic boundaries in UTC
        self._asian_start = time(22, 0, 0)
        self._asian_end = time(6, 0, 0)
        self._london_start = time(7, 0, 0)
        self._london_end = time(15, 0, 0)
        self._ny_start = time(12, 0, 0)
        self._ny_end = time(20, 0, 0)

        self._london_open_kz_start = time(7, 0, 0)
        self._london_open_kz_end = time(9, 30, 0)
        self._ny_open_kz_start = time(12, 0, 0)
        self._ny_open_kz_end = time(16, 0, 0)

        # In-memory range buffers
        self._active_asian_high = 0.0
        self._active_asian_low = float('inf')
        self._in_asian_accumulation = False

    def evaluate_temporal_context(self, eval_time: datetime, current_mid: float) -> Tuple[SessionState, bool, bool]:
        """Classifies the current baseline session phase, killzone activation, and overlap states."""
        time_utc = eval_time.astimezone(timezone.utc).time()
        
        # 1. Base Session Determination
        session = SessionState.SYSTEM_SHUTDOWN
        is_london = self._check_time_in_range(time_utc, self._london_start, self._london_end)
        is_ny = self._check_time_in_range(time_utc, self._ny_start, self._ny_end)
        is_asian = self._check_time_in_range(time_utc, self._asian_start, self._asian_end)

        if is_london and is_ny:
            session = SessionState.LONDON_KILLZONE  # Overlap marker proxy state
        elif is_ny:
            session = SessionState.NEWYORK_KILLZONE
        elif is_london:
            session = SessionState.LONDON_KILLZONE
        elif is_asian:
            session = SessionState.ASI_ACCUMULATION

        # 2. Asian Accumulation Range Monitoring
        if session == SessionState.ASI_ACCUMULATION:
            if not self._in_asian_accumulation:
                self._active_asian_high = current_mid
                self._active_asian_low = current_mid
                self._in_asian_accumulation = True
            else:
                self._active_asian_high = max(self._active_asian_high, current_mid)
                self._active_asian_low = min(self._active_asian_low, current_mid)
        else:
            self._in_asian_accumulation = False  # Lock range tracking outside Asian hours

        # 3. Killzone and Overlap Calculation
        is_kz = (self._check_time_in_range(time_utc, self._london_open_kz_start, self._london_open_kz_end) or 
                 self._check_time_in_range(time_utc, self._ny_open_kz_start, self._ny_open_kz_end))
        is_overlap = is_london and is_ny

        return session, is_kz, is_overlap

    @property
    def asian_range(self) -> Tuple[float, float]:
        return self._active_asian_high, self._active_asian_low

    def _check_time_in_range(self, target: time, start: time, end: time) -> bool:
        """Helper that safely evaluates time crossings, including overnight boundaries."""
        if start <= end:
            return start <= target <= end
        else:  # Overnight wrapping interval
            return target >= start or target <= end