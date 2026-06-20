from __future__ import annotations

from datetime import datetime

from reports.candle_mapper import map_time_to_candles


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def build_manual_chart_review(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    review_rows: list[dict[str, str]] = []
    for row in rows:
        entry = _parse_iso(row.get("entry_time_display", ""))
        exit_dt = _parse_iso(row.get("exit_time_display", ""))
        entry_candles = map_time_to_candles(entry)
        exit_candles = map_time_to_candles(exit_dt)
        review_rows.append(
            {
                "trade_id": row.get("trade_id", ""),
                "symbol": row.get("symbol", ""),
                "direction": row.get("direction", ""),
                "entry_time_utc": row.get("entry_time_utc_normalized", ""),
                "entry_time_broker": row.get("entry_time_broker_normalized", ""),
                "entry_time_display": row.get("entry_time_display", ""),
                "exit_time_utc": row.get("exit_time_utc_normalized", ""),
                "exit_time_broker": row.get("exit_time_broker_normalized", ""),
                "exit_time_display": row.get("exit_time_display", ""),
                "entry_m1_candle": entry_candles["M1"],
                "entry_m3_candle": entry_candles["M3"],
                "entry_m5_candle": entry_candles["M5"],
                "entry_m15_candle": entry_candles["M15"],
                "exit_m1_candle": exit_candles["M1"],
                "exit_m3_candle": exit_candles["M3"],
                "exit_m5_candle": exit_candles["M5"],
                "exit_m15_candle": exit_candles["M15"],
                "entry_price": row.get("entry_price", row.get("entry", "")),
                "exit_price": row.get("exit_price", row.get("exit", "")),
                "stop_loss": row.get("stop_loss", row.get("stop", "")),
                "take_profit": row.get("take_profit", row.get("target_1", row.get("tp1", ""))),
                "strategy_setup": row.get("strategy_setup", ""),
                "session": row.get("session", ""),
                "timeframe": row.get("timeframe", ""),
                "result": row.get("result", ""),
                "pnl": row.get("pnl", ""),
                "rr_actual": row.get("rr_actual", ""),
                "review_status": "pending_manual_review",
                "review_notes": "",
            }
        )
    return review_rows

