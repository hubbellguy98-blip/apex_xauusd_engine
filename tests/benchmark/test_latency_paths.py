"""
Apex Engine - High-Resolution Execution Latency Micro-Benchmarks
Responsibility: Measures tick parsing speeds and tracking delays down to microsecond intervals.
Latency Profile: High-resolution hardware performance checks.
"""

import pytest
import time
from src.infrastructure.telemetry.monitoring.metrics_registry import GlobalMetricsRegistry
from src.infrastructure.telemetry.monitoring.alert_manager import OperationsAlertManager
from src.infrastructure.telemetry.monitoring.latency_monitor import RealTimeLatencyMonitor

@pytest.mark.performance
def test_tick_to_decision_ingest_path_latency() -> None:
    """Benchmarks processing cycles to verify execution speeds satisfy high-frequency constraints."""
    registry = GlobalMetricsRegistry()
    alert_manager = OperationsAlertManager()
    monitor = RealTimeLatencyMonitor(registry, alert_manager)

    execution_profile_runs = 5000
    start_timeline_ns = time.perf_counter_ns()

    for _ in range(execution_profile_runs):
        checkpoint = time.perf_counter_ns()
        # Trigger explicit latency measurements across critical code blocks
        monitor.record_tick_processed(checkpoint)

    total_duration_ns = time.perf_counter_ns() - start_timeline_ns
    average_latency_us = (total_duration_ns / execution_profile_runs) / 1000.0

    sys_logger_info(f"BENCHMARK_RESULT: Average loop latency: {average_latency_us:.3f} microseconds per packet execution run.")
    
    # Enforce strict trading platform execution speed ceilings (< 15 microseconds per data point)
    assert average_latency_us < 15.0

def sys_logger_info(msg: str) -> None:
    print(f"\n[PERFORMANCE_CORE] {msg}")