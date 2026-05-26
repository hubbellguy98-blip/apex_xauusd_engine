"""
Apex Engine - High Fidelity Liquidity Mapping Engine
Responsibility: Maps resting order clusters, identifies retail equal structures, and flags sweeps.
Latency Profile: High frequency calculation matrices evaluating incoming microsecond ticks.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import structlog
from src.core.domain.market_data import TickNode
from src.core.domain.structure_models import SwingPoint, LiquidityPool, StructuralPointType

logger = structlog.get_logger()

class LiquidityInterceptionEngine:
    """Tracks historical liquidity zones, identifying engineering phases and structural sweeps."""

    def __init__(self, timeframe: str, structural_pip_tolerance: float = 5.0) -> None:
        self._timeframe = timeframe
        self._tolerance = structural_pip_tolerance / 10.0  # Scale index mapping parameter
        self._pools: List[LiquidityPool] = []
        self._counter = 0

    def register_structural_pivot_pool(self, pivot: SwingPoint) -> Optional[LiquidityPool]:
        """Transforms newly closed structural swing points into active resting liquidity models."""
        is_buy_side = pivot.point_type == StructuralPointType.SWING_HIGH
        
        # Check for matching equal structures within the tolerance band
        is_equal = False
        target_ceiling = pivot.price + self._tolerance
        target_floor = pivot.price - self._tolerance

        for idx, pool in enumerate(self._pools):
            if pool.is_swept:
                continue
            if pool.is_buy_side == is_buy_side:
                if target_floor <= pool.ceiling_price <= target_ceiling:
                    is_equal = True
                    # Re-map limits to account for equal structure touches
                    from dataclasses import replace
                    updated_pool = replace(pool, accumulated_touches=pool.accumulated_touches + 1, is_equal_structure=True)
                    self._pools[idx] = updated_pool
                    return updated_pool

        self._counter += 1
        new_pool = LiquidityPool(
            id=f"LIQ_{self._timeframe}_{self._counter}", timeframe=self._timeframe,
            is_buy_side=is_buy_side, is_equal_structure=is_equal,
            ceiling_price=pivot.price + (0.1 if is_buy_side else 0.0),
            floor_price=pivot.price - (0.0 if is_buy_side else 0.1),
            accumulated_touches=1
        )
        self._pools.append(new_pool)
        return new_pool

    def evaluate_tick_sweeps(self, tick: TickNode) -> List[Tuple[LiquidityPool, float]]:
        """Monitors resting order pools against incoming ticks to capture microsecond-level liquidity sweeps."""
        detected_sweeps: List[Tuple[LiquidityPool, float]] = []
        price = tick.mid

        for idx, pool in enumerate(self._pools):
            if pool.is_swept:
                continue

            if pool.is_buy_side and price > pool.ceiling_price:
                # Buy-side liquidity intercepted by a high-velocity tick spike
                sweep_depth = price - pool.ceiling_price
                from dataclasses import replace
                updated_pool = replace(pool, is_swept=True, sweep_timestamp=tick.timestamp)
                self._pools[idx] = updated_pool
                detected_sweeps.append((updated_pool, sweep_depth))
                logger.info("liquidity_engine.bsl_swept", id=pool.id, depth=sweep_depth)
                
            elif not pool.is_buy_side and price < pool.floor_price:
                # Sell-side liquidity intercepted
                sweep_depth = pool.floor_price - price
                from dataclasses import replace
                updated_pool = replace(pool, is_swept=True, sweep_timestamp=tick.timestamp)
                self._pools[idx] = updated_pool
                detected_sweeps.append((updated_pool, sweep_depth))
                logger.info("liquidity_engine.ssl_swept", id=pool.id, depth=sweep_depth)

        # Retain unmitigated parameters within active memory structures
        self._pools = [p for p in self._pools if not p.is_swept]
        return detected_sweeps

    def nearest_active_pool(self, price: float) -> Optional[Dict[str, Any]]:
        """Return observation-only proximity data for the closest unswept pool."""
        active_pools = [pool for pool in self._pools if not pool.is_swept]
        if not active_pools:
            return None

        def pool_level(pool: LiquidityPool) -> float:
            return pool.ceiling_price if pool.is_buy_side else pool.floor_price

        nearest = min(active_pools, key=lambda pool: abs(pool_level(pool) - price))
        level = pool_level(nearest)
        return {
            "active_pool_count": len(active_pools),
            "pool_id": nearest.id,
            "side": "BUY_SIDE_HIGH" if nearest.is_buy_side else "SELL_SIDE_LOW",
            "level_price": level,
            "distance": abs(level - price),
            "is_equal_structure": nearest.is_equal_structure,
            "touches": nearest.accumulated_touches,
        }
