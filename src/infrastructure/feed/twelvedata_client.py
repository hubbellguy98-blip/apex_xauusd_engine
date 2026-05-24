"""
Apex Engine - REST Historical Retrieval Agent
Responsibility: Pulls multi-timeframe backfill profiles via aiohttp endpoints.
Latency Profile: Execution bounded by remote server response latency.
"""

import asyncio
import aiohttp
from pydantic import SecretStr
import pandas as pd
import structlog
from typing import Optional

logger = structlog.get_logger()

class TwelveDataHistoricalFetcher:
    """Fetches cold historical market data to initialize system arrays on startup."""

    def __init__(self, api_key: SecretStr, base_url: str = "https://api.twelvedata.com") -> None:
        self._api_key = api_key
        self._base_url = base_url

    async def fetch_bars(self, symbol: str, timeframe: str, output_size: int = 500) -> Optional[pd.DataFrame]:
        """Queries historical candles to populate strategy cache layers on startup."""
        url = f"{self._base_url}/time_series"
        params = {
            "symbol": symbol,
            "interval": timeframe,
            "outputsize": str(output_size),
            "apikey": self._api_key.get_secret_value(),
            "format": "JSON"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=15) as response:
                    if response.status != 200:
                        logger.error("historical_fetcher.http_error", status=response.status)
                        return None
                    
                    payload = await response.json()
                    if "values" not in payload:
                        logger.error("historical_fetcher.invalid_payload", response=str(payload))
                        return None
                    
                    df = pd.DataFrame(payload["values"])
                    df["datetime"] = pd.to_datetime(df["datetime"])
                    for col in ["open", "high", "low", "close", "volume"]:
                        df[col] = pd.to_numeric(df[col])
                        
                    df.sort_values("datetime", ascending=True, inplace=True)
                    df.reset_index(drop=True, inplace=True)
                    logger.info("historical_fetcher.success", symbol=symbol, bars_count=len(df))
                    return df
        except Exception as ex:
            logger.error("historical_fetcher.exception", error=str(ex))
            return None