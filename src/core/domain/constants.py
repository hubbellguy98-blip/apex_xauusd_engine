"""
Apex Engine - Core Domain Constants
Responsibility: Centralized type-safe definitions and enums.
Latency Profile: Static compilation allocation; zero runtime penalty.
"""

from enum import Enum, unique

@unique
class Environment(str, Enum):
    DEVELOPMENT = "DEVELOPMENT"
    STAGING = "STAGING"
    PRODUCTION = "PRODUCTION"
    BACKTEST = "BACKTEST"
    REPLAY = "REPLAY"

@unique
class MarketRegime(str, Enum):
    HIGH_VOLATILITY_EXPANSION = "HIGH_VOLATILITY_EXPANSION"
    HIGH_VOLATILITY_REVERSAL = "HIGH_VOLATILITY_REVERSAL"
    LOW_VOLATILITY_COMPRESSION = "LOW_VOLATILITY_COMPRESSION"
    UNKNOWN = "UNKNOWN"

@unique
class SessionState(str, Enum):
    ASI_ACCUMULATION = "ASI_ACCUMULATION"
    LONDON_KILLZONE = "LONDON_KILLZONE"
    NEWYORK_KILLZONE = "NEWYORK_KILLZONE"
    POST_NY_RESET = "POST_NY_RESET"
    SYSTEM_SHUTDOWN = "SYSTEM_SHUTDOWN"

@unique
class OrderDirection(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

@unique
class EventPriority(int, Enum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
