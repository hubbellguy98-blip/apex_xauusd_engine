# ===== Source: src/infrastructure/database/base.py =====
"""
Apex Engine - Database Declarative Base Mapping Anchor
Responsibility: Provides a unified declarative mapping base for SQLAlchemy schemas.
Latency Profile: Static mapping layer with zero calculation footprint.
"""

from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    """Declarative base class for all relational database models."""
    pass

# ===== Source: src/infrastructure/database/enums.py =====
"""
Apex Engine - Database Enumerations
Responsibility: Enums specifying transaction, structural alignment, and order lifecycle states.
Latency Profile: Static compilation mapping; zero runtime penalty.
"""

from enum import Enum, unique

@unique
class DbOrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"

@unique
class DbOrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

@unique
class DbOrderStatus(str, Enum):
    PENDING_SUBMIT = "PENDING_SUBMIT"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"

@unique
class DbMitigationState(str, Enum):
    UNMITIGATED = "UNMITIGATED"
    PARTIAL = "PARTIALLY_MITIGATED"
    FULLY_MITIGATED = "FULLY_MITIGATED"
    INVALIDATED = "INVALIDATED"

@unique
class DbStructureBreakType(str, Enum):
    BOS = "BREAK_OF_STRUCTURE"
    MSS = "MARKET_STRUCTURE_SHIFT"
    CHOCH = "CHANGE_OF_CHARACTER"

@unique
class DbStructuralPointType(str, Enum):
    SWING_HIGH = "SWING_HIGH"
    SWING_LOW = "SWING_LOW"

# ===== Source: src/infrastructure/database/mixins.py =====
"""
Apex Engine - Database Audit and Utility Mixins
Responsibility: Provides fields for execution times and transaction tracking metrics.
Latency Profile: Adds zero processing overhead during standard data flows.
"""

import uuid
from datetime import datetime
from sqlalchemy import DateTime, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from src.shared.time_utils import TimeProvider

class AuditMixin:
    """Appends created and modified timestamps across persistent entities."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=TimeProvider.get_utc_now,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=TimeProvider.get_utc_now,
        onupdate=TimeProvider.get_utc_now,
        nullable=False
    )

class UUIDPrimaryKeyMixin:
    """Enforces UUID-based primary identifiers to streamline multi-node tracking alignment."""
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), 
        primary_key=True, 
        default=uuid.uuid4, 
        nullable=False
    )

# ===== Source: src/infrastructure/database/models/backtest_run.py =====
"""
Apex Engine - Backtest Run Execution Metadata Schema
Responsibility: Logs historical simulation parameters and aggregates statistical results.
Latency Profile: Research processing configuration storage layer.
"""

from datetime import datetime
from sqlalchemy import String, Float, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import UUIDPrimaryKeyMixin, AuditMixin
from typing import List

class BacktestRunModel(Base, UUIDPrimaryKeyMixin, AuditMixin):
    """Logs historical simulation configurations and compiled strategy metrics."""
    __tablename__ = "backtest_runs"

    strategy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    initial_capital: Mapped[float] = mapped_column(Float, nullable=False)
    final_equity: Mapped[float] = mapped_column(Float, nullable=False)
    compiled_statistics: Mapped[dict] = mapped_column(JSON, nullable=False) # Stores Sharpe, Sortino data objects

    trades = relationship("TradeModel", back_populates="backtest_run", cascade="all, delete-orphan")
    backtest_trades = relationship("BacktestTradeModel", back_populates="backtest_run", cascade="all, delete-orphan")

# ===== Source: src/infrastructure/database/models/backtest_trade.py =====
"""
Apex Engine - Backtest Trade Metric Ingestion Schema
Responsibility: Logs simulated position outcomes generated inside testing modules.
Latency Profile: High throughput processing data recording.
"""

import uuid
from datetime import datetime
from sqlalchemy import String, Float, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import UUIDPrimaryKeyMixin, AuditMixin

class BacktestTradeModel(Base, UUIDPrimaryKeyMixin, AuditMixin):
    """Logs simulated positions generated during historical research runs."""
    __tablename__ = "backtest_trades"

    backtest_run_id_fk: Mapped[uuid.UUID] = mapped_column(ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False)
    simulated_position_id: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float] = mapped_column(Float, nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    realized_pnl_currency: Mapped[float] = mapped_column(Float, nullable=False)
    realized_slippage_pips: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    backtest_run = relationship("BacktestRunModel", back_populates="backtest_trades")

    __table_args__ = (
        Index("idx_backtest_trade_search", "backtest_run_id_fk", "entry_time"),
    )

# ===== Source: src/infrastructure/database/models/candle.py =====
"""
Apex Engine - Downsampled Candle Metrics Entity Schema
Responsibility: Defines columns and composite indexes to store multi-timeframe candle data.
Latency Profile: High-speed range filtering using explicit index bounds.
"""

from datetime import datetime
from sqlalchemy import String, Float, BigInteger, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column
from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import UUIDPrimaryKeyMixin, AuditMixin

class CandleModel(Base, UUIDPrimaryKeyMixin, AuditMixin):
    """Stores historical and real-time downsampled price candles."""
    __tablename__ = "market_candles"

    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open_price: Mapped[float] = mapped_column(Float, nullable=False)
    high_price: Mapped[float] = mapped_column(Float, nullable=False)
    low_price: Mapped[float] = mapped_column(Float, nullable=False)
    close_price: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    vwap: Mapped[float] = mapped_column(Float, nullable=False)
    ticks_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_closed: Mapped[bool] = mapped_column(nullable=False)

    __table_args__ = (
        Index("idx_candle_lookup", "symbol", "timeframe", "start_time", unique=True),
    )

# ===== Source: src/infrastructure/database/models/confirmation.py =====
"""
Apex Engine - Trade Setup Confirmation Link Schema
Responsibility: Logs sub-validation checks and ties confirmation details to setups.
Latency Profile: Sequential transaction logging path.
"""

from datetime import datetime
import uuid
from sqlalchemy import String, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import UUIDPrimaryKeyMixin, AuditMixin

class ConfirmationModel(Base, UUIDPrimaryKeyMixin, AuditMixin):
    """Logs individual validation checks passed during setup generation."""
    __tablename__ = "setup_confirmations"

    setup_id_fk: Mapped[uuid.UUID] = mapped_column(ForeignKey("setup_opportunities.id", ondelete="CASCADE"), nullable=False)
    component_name: Mapped[str] = mapped_column(String(64), nullable=False)
    is_triggered: Mapped[bool] = mapped_column(Boolean, nullable=False)
    confidence_delta: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    evaluation_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    setup = relationship("SetupOpportunityModel", back_populates="confirmations")

# ===== Source: src/infrastructure/database/models/displacement.py =====
"""
Apex Engine - Institutional Displacement Verification Schema
Responsibility: Logs volume expansions and body-to-wick calculation ratios.
Latency Profile: Pure calculation recording metrics layers.
"""

from datetime import datetime
from sqlalchemy import String, Float, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import UUIDPrimaryKeyMixin, AuditMixin

class DisplacementModel(Base, UUIDPrimaryKeyMixin, AuditMixin):
    """Logs calculated volume indicators to track structural execution intent."""
    __tablename__ = "displacement_records"

    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    candle_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    body_density_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    intensity_index: Mapped[float] = mapped_column(Float, nullable=False)

# ===== Source: src/infrastructure/database/models/execution.py =====
"""
Apex Engine - Order Execution Tracking Schema
Responsibility: Logs order submissions and updates fill and slippage metrics.
Latency Profile: Highly optimized logging on execution loops.
"""

import uuid
from datetime import datetime
from sqlalchemy import String, Float, DateTime, Enum, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import UUIDPrimaryKeyMixin, AuditMixin
from src.infrastructure.database.enums import DbOrderStatus, DbOrderSide, DbOrderType

class ExecutionModel(Base, UUIDPrimaryKeyMixin, AuditMixin):
    """Logs individual order routing requests and broker responses for transaction tracing."""
    __tablename__ = "executions"

    trade_id_fk: Mapped[uuid.UUID] = mapped_column(ForeignKey("trades.id", ondelete="CASCADE"), nullable=False)
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    broker_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[DbOrderStatus] = mapped_column(Enum(DbOrderStatus), nullable=False)
    order_side: Mapped[DbOrderSide] = mapped_column(Enum(DbOrderSide), nullable=False)
    order_type: Mapped[DbOrderType] = mapped_column(Enum(DbOrderType), nullable=False)
    filled_quantity: Mapped[float] = mapped_column(Float, nullable=False)
    average_fill_price: Mapped[float] = mapped_column(Float, nullable=False)
    slippage_pips: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    trade = relationship("TradeModel", back_populates="executions")

    __table_args__ = (
        Index("idx_execution_audit", "client_order_id", "timestamp"),
    )

# ===== Source: src/infrastructure/database/models/fvg.py =====
"""
Apex Engine - Fair Value Gap Mitigation Tracking Schema
Responsibility: Stores pricing imbalances with real-time tracking of partial fills.
Latency Profile: High efficiency lookup updates processing array boundaries.
"""

from datetime import datetime
from sqlalchemy import String, Float, DateTime, Enum, Index
from sqlalchemy.orm import Mapped, mapped_column
from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import UUIDPrimaryKeyMixin, AuditMixin
from src.infrastructure.database.enums import DbMitigationState

class FvgModel(Base, UUIDPrimaryKeyMixin, AuditMixin):
    """Logs pricing imbalances and mitigation parameters across downsampled intervals."""
    __tablename__ = "market_fvgs"

    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    is_bullish: Mapped[bool] = mapped_column(nullable=False)
    creation_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    top_boundary: Mapped[float] = mapped_column(Float, nullable=False)
    bottom_boundary: Mapped[float] = mapped_column(Float, nullable=False)
    initial_gap_size: Mapped[float] = mapped_column(Float, nullable=False)
    mitigation_state: Mapped[DbMitigationState] = mapped_column(Enum(DbMitigationState), nullable=False)
    remaining_unmitigated_size: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        Index("idx_fvg_active_query", "timeframe", "mitigation_state", "is_bullish"),
    )

# ===== Source: src/infrastructure/database/models/liquidity_event.py =====
"""
Apex Engine - Liquidity Sweep Event Ingestion Schema
Responsibility: Logs swept external order pools and equal structure data.
Latency Profile: High efficiency target lookup processing loops.
"""

from datetime import datetime
from sqlalchemy import String, Float, DateTime, Boolean, Index
from sqlalchemy.orm import Mapped, mapped_column
from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import UUIDPrimaryKeyMixin, AuditMixin

class LiquidityEventModel(Base, UUIDPrimaryKeyMixin, AuditMixin):
    """Logs intercepted order pools to support post-trade manipulation tracking."""
    __tablename__ = "liquidity_events"

    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    is_buy_side: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_equal_structure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ceiling_price: Mapped[float] = mapped_column(Float, nullable=False)
    floor_price: Mapped[float] = mapped_column(Float, nullable=False)
    sweep_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sweep_depth_pips: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        Index("idx_liquidity_timeline", "timeframe", "is_buy_side", "sweep_timestamp"),
    )

# ===== Source: src/infrastructure/database/models/market_structure.py =====
"""
Apex Engine - Structural Shift & Break Identification Schema
Responsibility: Stores trend line transitions, breaks of structure (BOS), and swing point pivots.
Latency Profile: Indexes key levels to optimize chart mapping passes.
"""

from datetime import datetime
import uuid
from sqlalchemy import String, Float, DateTime, Enum, Index, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import UUIDPrimaryKeyMixin, AuditMixin
from src.infrastructure.database.enums import DbStructureBreakType

class MarketStructureModel(Base, UUIDPrimaryKeyMixin, AuditMixin):
    """Logs identified breaks of structure and trend shifts across monitored windows."""
    __tablename__ = "market_structures"

    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    break_type: Mapped[DbStructureBreakType] = mapped_column(Enum(DbStructureBreakType), nullable=False)
    price_level: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        Index("idx_structure_search", "timeframe", "break_type", "timestamp"),
    )

# ===== Source: src/infrastructure/database/models/order_block.py =====
"""
Apex Engine - Order Block & Breaker Invalidation Schema
Responsibility: Stores unmitigated blocks and records structural breaker conversions.
Latency Profile: Evaluates block boundary intersections.
"""

from datetime import datetime
from sqlalchemy import String, Float, DateTime, Enum, Boolean, Index
from sqlalchemy.orm import Mapped, mapped_column
from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import UUIDPrimaryKeyMixin, AuditMixin
from src.infrastructure.database.enums import DbMitigationState

class OrderBlockModel(Base, UUIDPrimaryKeyMixin, AuditMixin):
    """Logs institutional order blocks and tracks dynamic breaker conversions."""
    __tablename__ = "order_blocks"

    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    is_bullish: Mapped[bool] = mapped_column(nullable=False)
    is_breaker: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    high_boundary: Mapped[float] = mapped_column(Float, nullable=False)
    low_boundary: Mapped[float] = mapped_column(Float, nullable=False)
    mean_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    mitigation_state: Mapped[DbMitigationState] = mapped_column(Enum(DbMitigationState), nullable=False)

    __table_args__ = (
        Index("idx_ob_active_query", "timeframe", "mitigation_state", "is_breaker"),
    )

# ===== Source: src/infrastructure/database/models/order_book.py =====
"""
Apex Engine - Order Book Depth Telemetry Schema
Responsibility: Stores top-of-book depth metric arrays.
Latency Profile: Stores snapshot profiles.
"""

from datetime import datetime
from sqlalchemy import String, Float, BigInteger, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column
from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import UUIDPrimaryKeyMixin, AuditMixin

class OrderBookSnapshotModel(Base, UUIDPrimaryKeyMixin, AuditMixin):
    """Logs historical depth parameters matching top-of-book metrics."""
    __tablename__ = "order_book_snapshots"

    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bid_depth_1: Mapped[float] = mapped_column(Float, nullable=False)
    bid_volume_1: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ask_depth_1: Mapped[float] = mapped_column(Float, nullable=False)
    ask_volume_1: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        Index("idx_order_book_timeline", "symbol", "timestamp"),
    )

# ===== Source: src/infrastructure/database/models/performance_metric.py =====
"""
Apex Engine - Account Equity & Performance Metrics Schema
Responsibility: Stores rolling metrics data to monitor risk limits and performance.
Latency Profile: Periodic metrics logging path.
"""

from datetime import datetime
from sqlalchemy import Float, BigInteger, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import UUIDPrimaryKeyMixin, AuditMixin

class PerformanceMetricModel(Base, UUIDPrimaryKeyMixin, AuditMixin):
    """Logs balance metrics and rolling equity drawdowns for automated risk reviews."""
    __tablename__ = "performance_metrics"

    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    account_balance: Mapped[float] = mapped_column(Float, nullable=False)
    account_equity: Mapped[float] = mapped_column(Float, nullable=False)
    daily_realized_loss_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    active_position_count: Mapped[int] = mapped_column(nullable=False, default=0)
    event_bus_backpressure_depth: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

# ===== Source: src/infrastructure/database/models/regime_state.py =====
"""
Apex Engine - Market Regime Classification Schema
Responsibility: Stores rolling volatility parameters and statistical environment profiles.
Latency Profile: Tracks environment state updates.
"""

from datetime import datetime
from sqlalchemy import String, Float, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import UUIDPrimaryKeyMixin, AuditMixin

class RegimeStateModel(Base, UUIDPrimaryKeyMixin, AuditMixin):
    """Logs calculated structural volatility states and market delivery classifications."""
    __tablename__ = "regime_states"

    market_regime: Mapped[str] = mapped_column(String(64), nullable=False)
    volatility_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    volume_z_score: Mapped[float] = mapped_column(Float, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

# ===== Source: src/infrastructure/database/models/replay_session.py =====
"""
Apex Engine - Historical Replay Verification Session Schema
Responsibility: Logs deterministic data playback tracking milestones.
Latency Profile: Research orchestration logging module.
"""

from datetime import datetime
from sqlalchemy import String, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column
from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import UUIDPrimaryKeyMixin, AuditMixin

class ReplaySessionModel(Base, UUIDPrimaryKeyMixin, AuditMixin):
    """Logs playback tracking sessions to assist with compliance reviews."""
    __tablename__ = "replay_sessions"

    session_identifier: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    execution_start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    execution_end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_ticks_count: Mapped[int] = mapped_column(nullable=False, default=0)
    consistency_metrics_log: Mapped[dict] = mapped_column(JSON, nullable=False)

# ===== Source: src/infrastructure/database/models/risk_assessment.py =====
"""
Apex Engine - Pre-Execution Risk Validation Schema
Responsibility: Logs sizing allocations and safety checks before order submission.
Latency Profile: Logs data frames before trading entries.
"""

import uuid
from sqlalchemy import String, Float, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import UUIDPrimaryKeyMixin, AuditMixin

class RiskAssessmentModel(Base, UUIDPrimaryKeyMixin, AuditMixin):
    """Logs pre-trade risk validations and lot size allocations."""
    __tablename__ = "risk_assessments"

    setup_id_fk: Mapped[uuid.UUID] = mapped_column(ForeignKey("setup_opportunities.id", ondelete="CASCADE"), unique=True, nullable=False)
    is_approved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    calculated_lots: Mapped[float] = mapped_column(Float, nullable=False)
    max_risk_currency: Mapped[float] = mapped_column(Float, nullable=False)
    risk_percentage_applied: Mapped[float] = mapped_column(Float, nullable=False)
    applied_spread_pips: Mapped[float] = mapped_column(Float, nullable=False)
    halt_state_snapshot: Mapped[str] = mapped_column(String(64), nullable=False)

    setup = relationship("SetupOpportunityModel", back_populates="risk_assessment")

# ===== Source: src/infrastructure/database/models/session_state.py =====
"""
Apex Engine - Trading Session Operational State Schema
Responsibility: Stores active timezone horizons, ranges, and temporal transitions.
Latency Profile: High efficiency state lookups.
"""

from datetime import datetime
from sqlalchemy import String, Float, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import UUIDPrimaryKeyMixin, AuditMixin

class SessionStateModel(Base, UUIDPrimaryKeyMixin, AuditMixin):
    """Logs algorithmic timezone phases and verified boundary coordinates."""
    __tablename__ = "session_states"

    session_phase: Mapped[str] = mapped_column(String(32), nullable=False)
    asian_high: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    asian_low: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_killzone_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    transition_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

# ===== Source: src/infrastructure/database/models/setup.py =====
"""
Apex Engine - Strategy Setup Discovery Schema
Responsibility: Maps validated setup opportunities and manages relational integrity links.
Latency Profile: Binds setup entries to downstream trade records.
"""

from datetime import datetime
from sqlalchemy import String, Float, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import UUIDPrimaryKeyMixin, AuditMixin
from typing import Optional, List

class SetupOpportunityModel(Base, UUIDPrimaryKeyMixin, AuditMixin):
    """Logs algorithmically validated trading opportunities discovered inside killzones."""
    __tablename__ = "setup_opportunities"

    setup_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    setup_type: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    estimated_rr: Mapped[float] = mapped_column(Float, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    creation_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expiration_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # Database Relational Architecture Maps
    confirmations = relationship("ConfirmationModel", back_populates="setup", cascade="all, delete-orphan")
    score = relationship("SetupScoreModel", back_populates="setup", uselist=False, cascade="all, delete-orphan")
    risk_assessment = relationship("RiskAssessmentModel", back_populates="setup", uselist=False, cascade="all, delete-orphan")
    trades = relationship("TradeModel", back_populates="setup")

    __table_args__ = (
        Index("idx_setup_search", "setup_type", "direction", "creation_time"),
    )

# ===== Source: src/infrastructure/database/models/setup_score.py =====
"""
Apex Engine - Matrix Quality Scoring Schema
Responsibility: Stores composite scoring values and penalty metrics for setups.
Latency Profile: Simple transactional logging layer.
"""

import uuid
from sqlalchemy import Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import UUIDPrimaryKeyMixin, AuditMixin

class SetupScoreModel(Base, UUIDPrimaryKeyMixin, AuditMixin):
    """Logs weighted matrix evaluations and penalty deductions for discovery audits."""
    __tablename__ = "setup_scores"

    setup_id_fk: Mapped[uuid.UUID] = mapped_column(ForeignKey("setup_opportunities.id", ondelete="CASCADE"), unique=True, nullable=False)
    structure_score: Mapped[float] = mapped_column(Float, nullable=False)
    liquidity_score: Mapped[float] = mapped_column(Float, nullable=False)
    momentum_score: Mapped[float] = mapped_column(Float, nullable=False)
    volatility_score: Mapped[float] = mapped_column(Float, nullable=False)
    rr_score: Mapped[float] = mapped_column(Float, nullable=False)
    execution_score: Mapped[float] = mapped_column(Float, nullable=False)
    applied_penalties: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    normalized_final_score: Mapped[float] = mapped_column(Float, nullable=False)

    setup = relationship("SetupOpportunityModel", back_populates="score")

# ===== Source: src/infrastructure/database/models/system_alert.py =====
"""
Apex Engine - Operational System Alert Schema
Responsibility: Logs infrastructure alerts and tracks escalation milestones.
Latency Profile: Triggered logging channel.
"""

from datetime import datetime
from sqlalchemy import String, Text, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column
from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import UUIDPrimaryKeyMixin, AuditMixin

class SystemAlertModel(Base, UUIDPrimaryKeyMixin, AuditMixin):
    """Logs triggered system alerts and track lifecycle status changes."""
    __tablename__ = "system_alerts"

    alert_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    component_origin: Mapped[str] = mapped_column(String(64), nullable=False)
    severity_level: Mapped[str] = mapped_column(String(16), nullable=False)
    message_description: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        Index("idx_alert_severity_timeline", "severity_level", "timestamp"),
    )

# ===== Source: src/infrastructure/database/models/system_log.py =====
"""
Apex Engine - Structured Internal System Log Schema
Responsibility: Logs runtime trace events and processing operations.
Latency Profile: High capacity, decoupled async logging writer.
"""

from datetime import datetime
from sqlalchemy import String, Text, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column
from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import UUIDPrimaryKeyMixin, AuditMixin

class SystemLogModel(Base, UUIDPrimaryKeyMixin, AuditMixin):
    """Logs runtime tracing information generated across active core loops."""
    __tablename__ = "system_logs"

    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    severity_level: Mapped[str] = mapped_column(String(16), nullable=False)
    component_context: Mapped[str] = mapped_column(String(64), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    message_payload: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("idx_system_log_search", "severity_level", "timestamp"),
    )

# ===== Source: src/infrastructure/database/models/tick.py =====
"""
Apex Engine - High-Frequency Tick Data Ingestion Schema
Responsibility: Stores raw pricing ticks with microsecond-level precision.
Latency Profile: High-speed processing arrays optimizing continuous query inserts.
"""

from datetime import datetime
from sqlalchemy import String, Float, BigInteger, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column
from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import UUIDPrimaryKeyMixin, AuditMixin

class TickModel(Base, UUIDPrimaryKeyMixin, AuditMixin):
    """Logs raw transactional pricing data points received from data feeds."""
    __tablename__ = "market_ticks"

    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bid_price: Mapped[float] = mapped_column(Float, nullable=False)
    ask_price: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sequence_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    flags: Mapped[int] = mapped_column(nullable=False, default=0)

    __table_args__ = (
        Index("idx_tick_timeline", "symbol", "timestamp", "sequence_id"),
    )

# ===== Source: src/infrastructure/database/models/trade.py =====
"""
Apex Engine - Central Position Tracking Schema
Responsibility: Maps core positions, manages performance tracking metrics, and enforces relationships.
Latency Profile: High-speed lookups matching working asset allocations.
"""

import uuid
from datetime import datetime
from sqlalchemy import String, Float, DateTime, Index, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import UUIDPrimaryKeyMixin, AuditMixin
from typing import List, Optional

class TradeModel(Base, UUIDPrimaryKeyMixin, AuditMixin):
    """Maintains an active ledger of open positions and finalized PnL metrics."""
    __tablename__ = "trades"

    position_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    setup_id_fk: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("setup_opportunities.id", ondelete="SET NULL"), nullable=True)
    backtest_id_fk: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    volume_lots: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    close_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # Relational Architecture Links
    setup = relationship("SetupOpportunityModel", back_populates="trades")
    backtest_run = relationship("BacktestRunModel", back_populates="trades")
    executions = relationship("ExecutionModel", back_populates="trade", cascade="all, delete-orphan")
    events = relationship("TradeEventModel", back_populates="trade", cascade="all, delete-orphan")
    journal_entries = relationship("TradeJournalModel", back_populates="trade", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_active_positions", "symbol", "position_id", "open_time"),
    )

# ===== Source: src/infrastructure/database/models/trade_event.py =====
"""
Apex Engine - Trade Lifecycle Modifications Schema
Responsibility: Logs post-execution trailing adjustments and breakeven stop changes.
Latency Profile: Standard sequential event tracking log.
"""

import uuid
from datetime import datetime
from sqlalchemy import String, Float, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import UUIDPrimaryKeyMixin, AuditMixin

class TradeEventModel(Base, UUIDPrimaryKeyMixin, AuditMixin):
    """Logs post-execution stop modifications and partial profit take scaling events."""
    __tablename__ = "trade_lifecycle_events"

    trade_id_fk: Mapped[uuid.UUID] = mapped_column(ForeignKey("trades.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False) # e.g., 'BREAKEVEN_TRIGGER', 'PARTIAL_TP'
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    price_level: Mapped[float] = mapped_column(Float, nullable=False)
    lots_affected: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    log_summary: Mapped[str] = mapped_column(String(256), nullable=False)

    trade = relationship("TradeModel", back_populates="events")

# ===== Source: src/infrastructure/database/models/trade_journal.py =====
"""
Apex Engine - Post-Trade Compliance Journal Schema
Responsibility: Logs strategy choices and performance details for audit reviews.
Latency Profile: Asynchronous data logging layer.
"""

import uuid
from sqlalchemy import String, Float, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import UUIDPrimaryKeyMixin, AuditMixin

class TradeJournalModel(Base, UUIDPrimaryKeyMixin, AuditMixin):
    """Maintains type-safe narrative logs tracking contextual analytics for position closes."""
    __tablename__ = "trade_journals"

    trade_id_fk: Mapped[uuid.UUID] = mapped_column(ForeignKey("trades.id", ondelete="CASCADE"), nullable=False)
    max_adverse_excursion_pips: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_favorable_excursion_pips: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    session_context: Mapped[str] = mapped_column(String(32), nullable=False)
    regime_context: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_efficiency_score: Mapped[float] = mapped_column(Float, nullable=False)
    narrative_summary: Mapped[str] = mapped_column(Text, nullable=False)

    trade = relationship("TradeModel", back_populates="journal_entries")