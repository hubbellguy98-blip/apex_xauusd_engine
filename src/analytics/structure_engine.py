"""
Apex Engine - Market Structure Analysis System
Responsibility: Identifies swing pivots, maps structural breaks (BOS), and tracks transitions (MSS).
Latency Profile: Highly cohesive tracking arrays minimizing linear lookup search windows.
"""

from datetime import datetime
from typing import List, Optional, Tuple
import structlog
from src.core.domain.market_data import CandleNode
from src.core.domain.structure_models import SwingPoint, StructuralPointType, StructureBreakType

logger = structlog.get_logger()

class DeterministicStructureEngine:
    """Processes historical structures to map trend continuations and shifts mechanical rules."""

    def __init__(self, timeframe: str, pipeline_lookback_len: int = 50) -> None:
        self._timeframe = timeframe
        self._lookback = pipeline_lookback_len
        
        # Core internal tracking matrices
        self._candles: List[CandleNode] = []
        self._high_pivots: List[SwingPoint] = []
        self._low_pivots: List[SwingPoint] = []
        
        self._current_structural_high: Optional[SwingPoint] = None
        self._current_structural_low: Optional[SwingPoint] = None
        self._counter = 0

    def ingest_candle_close(self, candle: CandleNode) -> Tuple[List[SwingPoint], List[Tuple[StructureBreakType, float, datetime]]]:
        """Updates internal structure tracks when a new candle closes, extracting structural transitions."""
        self._candles.append(candle)
        if len(self._candles) > self._lookback:
            self._candles.pop(0)

        new_pivots: List[SwingPoint] = []
        detected_breaks: List[Tuple[StructureBreakType, float, datetime]] = []

        # 1. Execute Classic 3-Candle Pivot Point Tracking Logic
        if len(self._candles) >= 3:
            pivot_node = self._evaluate_local_pivots(self._candles[-3], self._candles[-2], self._candles[-1])
            if pivot_node:
                new_pivots.append(pivot_node)

        # 2. Sequential Break of Structure Verification Loops
        if self._current_structural_high and candle.close_p > self._current_structural_high.price:
            # Bullish Structural Invalidation Leg identified via body validation parameters
            break_type = StructureBreakType.BOS if self._current_structural_low else StructureBreakType.MSS
            detected_breaks.append((break_type, self._current_structural_high.price, candle.end_time))
            logger.info("market_structure.bullish_breakout", type=break_type.value, level=self._current_structural_high.price)
            self._current_structural_high = None  # Flush active boundary reference for expansion tracking

        if self._current_structural_low and candle.close_p < self._current_structural_low.price:
            break_type = StructureBreakType.BOS if self._current_structural_high else StructureBreakType.MSS
            detected_breaks.append((break_type, self._current_structural_low.price, candle.end_time))
            logger.info("market_structure.bearish_breakout", type=break_type.value, level=self._current_structural_low.price)
            self._current_structural_low = None

        return new_pivots, detected_breaks

    def _evaluate_local_pivots(self, c1: CandleNode, c2: CandleNode, c3: CandleNode) -> Optional[SwingPoint]:
        """Applies algorithmic criteria to locate structural pivot zones."""
        self._counter += 1
        mid_time = c2.end_time

        # Swing High Condition (Candle 2 is peak vertex)
        if c2.high_p > c1.high_p and c2.high_p > c3.high_p:
            p = SwingPoint(
                id=f"SW_H_{self._timeframe}_{self._counter}", symbol=c2.symbol,
                timeframe=self._timeframe, point_type=StructuralPointType.SWING_HIGH,
                timestamp=mid_time, price=c2.high_p, confidence=85.0
            )
            self._high_pivots.append(p)
            self._current_structural_high = p
            return p

        # Swing Low Condition (Candle 2 is floor vertex)
        if c2.low_p < c1.low_p and c2.low_p < c3.low_p:
            p = SwingPoint(
                id=f"SW_L_{self._timeframe}_{self._counter}", symbol=c2.symbol,
                timeframe=self._timeframe, point_type=StructuralPointType.SWING_LOW,
                timestamp=mid_time, price=c2.low_p, confidence=85.0
            )
            self._low_pivots.append(p)
            self._current_structural_low = p
            return p

        return None