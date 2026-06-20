from __future__ import annotations

from datetime import datetime


def floor_to_candle(dt: datetime, minutes: int) -> datetime:
    minute = dt.minute - (dt.minute % minutes)
    return dt.replace(minute=minute, second=0, microsecond=0)


def map_time_to_candles(dt: datetime | None) -> dict[str, str]:
    if not dt:
        return {f"M{minutes}": "" for minutes in (1, 3, 5, 15)}
    return {f"M{minutes}": floor_to_candle(dt, minutes).isoformat() for minutes in (1, 3, 5, 15)}

