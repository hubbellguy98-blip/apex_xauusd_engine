"""
Apex Engine - Testing Assertion Macro Assertions
Responsibility: Provides type-safe validation templates to verify state metrics.
Latency Profile: Direct attribute checks with minimal performance overhead.
"""

import math
from datetime import datetime, timedelta
from src.core.domain.market_data import TickNode

class StructuralAssertionMatrix:
    """Enforces absolute verification boundaries across tracking variables."""

    @staticmethod
    def assert_floating_numerical_equality(actual: float, expected: float, tolerance: float = 1e-6) -> None:
        """Validates floating point values within predefined precision bounds."""
        if not math.isclose(actual, expected, abs_tol=tolerance):
            raise AssertionError(f"Numerical Variance Breach: Found {actual}, expected {expected} within tolerance {tolerance}")

    @staticmethod
    def assert_chronological_sequence_bounds(actual_time: datetime, base_time: datetime, window: timedelta) -> None:
        """Verifies that processing timestamps fall within expected time window horizons."""
        lower_bound = base_time - window
        upper_bound = base_time + window
        if not (lower_bound <= actual_time <= upper_bound):
            raise AssertionError(f"Temporal Drift Encountered: Timestamp {actual_time} fell outside range {lower_bound} - {upper_bound}")