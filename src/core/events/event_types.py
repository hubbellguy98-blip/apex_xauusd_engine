"""
Apex Engine - Event Routing Definitions
Responsibility: Mappings and payload structures for internal event dispatching.
Latency Profile: Fast integer-based enumeration evaluations.
"""

from enum import Enum, unique

@unique
class EngineEventType(str, Enum):
    MARKET_TICK = "engine.market_tick"
    CANDLE_CLOSED = "engine.candle_closed"
    STRUCTURE_SHIFT = "engine.structure_shift"
    LIQUIDITY_SWEPT = "engine.liquidity_swept"
    SETUP_FOUND = "engine.setup_found"
    RISK_APPROVED = "engine.risk_approved"
    ORDER_SUBMITTED = "engine.order_submitted"
    ORDER_FILLED = "engine.order_filled"
    TELEMETRY_HEARTBEAT = "engine.telemetry_heartbeat"
    SYSTEM_CRITICAL_HALT = "engine.system_critical_halt"