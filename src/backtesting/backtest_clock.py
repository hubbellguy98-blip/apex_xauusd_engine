"""Deterministic clock for replay runs."""

from datetime import datetime


class BacktestDeterministicClock:
    def __init__(self) -> None:
        self.current_time: datetime | None = None

    def initialize_timeline(self, start_time: datetime) -> None:
        self.current_time = start_time

    def set_time(self, timestamp: datetime) -> None:
        self.current_time = timestamp
