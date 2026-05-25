"""Backtest row-to-tick conversion."""

from src.core.domain.market_data import TickNode


class BacktestTickReplayer:
    def __init__(self, symbol: str) -> None:
        self._symbol = symbol

    def convert_row_to_tick(self, row) -> TickNode:
        timestamp = row["timestamp"].to_pydatetime() if hasattr(row["timestamp"], "to_pydatetime") else row["timestamp"]
        return TickNode(
            symbol=self._symbol,
            timestamp=timestamp,
            bid=float(row["bid"]),
            ask=float(row.get("ask", row["bid"])),
            volume=int(row.get("volume", 0)),
        )
