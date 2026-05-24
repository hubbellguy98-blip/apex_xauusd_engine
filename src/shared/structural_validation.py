"""
Apex Engine - Structural Validation Interface
Responsibility: Performs type verification checks on critical trading inputs.
Latency Profile: Fast lookups using Python slots.
"""

import math
from typing import Any

class MarketDataValidator:
    """Validates structural correctness of tick data packets."""
    
    @staticmethod
    def validate_tick_bounds(ask_price: float, bid_price: float) -> bool:
        """Returns true if numeric pricing data is structured correctly."""
        if math.isnan(ask_price) or math.isnan(bid_price):
            return False
        if math.isinf(ask_price) or math.isinf(bid_price):
            return False
        if ask_price <= 0.0 or bid_price <= 0.0:
            return False
        if ask_price < bid_price:
            return False  # Structural inversion check
        return True