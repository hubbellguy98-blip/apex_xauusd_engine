"""Backtest row-to-candle conversion."""

from src.core.domain.market_data import CandleNode


class BacktestCandleReplayer:
    def __init__(self, symbol: str) -> None:
        self._symbol = symbol

    def convert_row_to_candle(self, row, timeframe: str) -> CandleNode:
        timestamp = row["timestamp"].to_pydatetime() if hasattr(row["timestamp"], "to_pydatetime") else row["timestamp"]
        return CandleNode(
            symbol=self._symbol,
            timeframe=timeframe,
            start_time=timestamp,
            end_time=timestamp,
            open_p=float(row["open"]),
            high_p=float(row["high"]),
            low_p=float(row["low"]),
            close_p=float(row["close"]),
            volume=int(row.get("volume", 0)),
            is_closed=True,
        )
