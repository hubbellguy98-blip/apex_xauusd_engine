"""
Apex Engine - Configuration Matrix Architecture
Responsibility: Type-safe system parameter modeling using Pydantic Settings management.
Latency Profile: In-memory configuration variables; zero tracking evaluation overhead.
"""

from pathlib import Path
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from src.core.domain.constants import Environment

class EngineSettings(BaseSettings):
    """System configuration parameters parsed from environment layers or secure configurations."""
    
    ENV: Environment = Field(default=Environment.DEVELOPMENT, alias="APEX_ENV")
    TWELVEDATA_API_KEY: SecretStr = Field(..., alias="APEX_TWELVEDATA_KEY")
    DATABASE_URL: SecretStr = Field(default=SecretStr("postgresql+asyncpg://postgres:postgres@localhost:5432/apex_db"))
    
    # Asset Specific Validation Constants
    TARGET_SYMBOL: str = "XAUUSD"
    MAX_ALLOWABLE_SPREAD_PIPS: float = 3.5
    RISK_ALLOCATION_PRIORITY_1: float = 1.0  # Percentage limits
    RISK_ALLOCATION_PRIORITY_2: float = 0.5
    MAX_DAILY_LOSS_PCT: float = 3.0
    
    # Base Storage Path Definitions
    PROJECT_ROOT_DIR: Path = Field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True  # Guarantees runtime structural immutability
    )