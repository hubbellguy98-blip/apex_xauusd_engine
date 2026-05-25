"""Realtime latency monitor."""

import time


class RealTimeLatencyMonitor:
    def __init__(self, registry, alert_manager, warn_threshold_us: float = 250.0) -> None:
        self._registry = registry
        self._alert_manager = alert_manager
        self._warn_threshold_us = warn_threshold_us

    def record_tick_processed(self, checkpoint_ns: int) -> float:
        latency_us = (time.perf_counter_ns() - checkpoint_ns) / 1000.0
        self._registry.record("tick_processed_us", latency_us)
        if latency_us > self._warn_threshold_us:
            self._alert_manager.emit(f"tick latency {latency_us:.2f}us")
        return latency_us
