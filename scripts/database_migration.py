"""
Apex Engine - Alembic Database Migrations Ingestion Controller
Responsibility: Runs schema synchronization scripts across target engines.
Latency Profile: Pre-operational configuration routine.
"""

import asyncio
from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context

from src.infrastructure.database.base import Base
from src.infrastructure.database.models.candle import CandleModel
from src.infrastructure.database.models.tick import TickModel
from src.infrastructure.database.models.order_book import OrderBookSnapshotModel
from src.infrastructure.database.models.session_state import SessionStateModel
from src.infrastructure.database.models.regime_state import RegimeStateModel
from src.infrastructure.database.models.market_structure import MarketStructureModel
from src.infrastructure.database.models.liquidity_event import LiquidityEventModel
from src.infrastructure.database.models.displacement import DisplacementModel
from src.infrastructure.database.models.fvg import FvgModel
from src.infrastructure.database.models.order_block import OrderBlockModel
from src.infrastructure.database.models.setup import SetupOpportunityModel
from src.infrastructure.database.models.confirmation import ConfirmationModel
from src.infrastructure.database.models.setup_score import SetupScoreModel
from src.infrastructure.database.models.risk_assessment import RiskAssessmentModel
from src.infrastructure.database.models.trade import TradeModel
from src.infrastructure.database.models.execution import ExecutionModel
from src.infrastructure.database.models.trade_event import TradeEventModel
from src.infrastructure.database.models.trade_journal import TradeJournalModel
from src.infrastructure.database.models.performance_metric import PerformanceMetricModel
from src.infrastructure.database.models.backtest_run import BacktestRunModel
from src.infrastructure.database.models.backtest_trade import BacktestTradeModel
from src.infrastructure.database.models.replay_session import ReplaySessionModel
from src.infrastructure.database.models.system_log import SystemLogModel
from src.infrastructure.database.models.system_alert import SystemAlertModel

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def run_migrations_offline() -> None:
    """Executes database schema upgrades using raw script generation paths."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()

async def run_migrations_online() -> None:
    """Connects to async pools and coordinates structural system updates."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()

if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())