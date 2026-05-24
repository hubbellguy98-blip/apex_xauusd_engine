"""
Apex Engine - Telemetry Tracking Framework
Responsibility: Provides low-overhead trackers for processing latency and queue depth metrics.
Latency Profile: Uses integer-based microsecond performance metrics.
"""

import time
from typing import Dict, Any
import structlog

logger = structlog.get_logger()

class PerformanceMetricsTracker:
    """Tracks latency metrics across critical transaction paths."""
    
    def __init__(self) -> None:
        self._latencies: Dict[str, list[int]] = {}

    def record_latency(self, metric_key: str, start_time_ns: int) -> int:
        """Calculates and logs processing latency in microseconds."""
        end_time_ns = time.perf_counter_ns()
        latency_us = (end_time_ns - start_time_ns) // 1000
        
        if metric_key not in self._latencies:
            self._latencies[metric_key] = []
        self._latencies[metric_key].append(latency_us)
        
        # Trim historical records to manage memory footprint
        if len(self._latencies[metric_key]) > 10000:
            self._latencies[metric_key].pop(0)
            
        return latency_us

    def get_summary(self, metric_key: str) -> Dict[str, Any]:
        """Returns structural analysis metrics for a targeted benchmark cluster."""
        records = self._latencies.get(metric_key, [])
        if not records:
            return {"count": 0, "avg_us": 0, "max_us": 0}
        return {
            "count": len(records),
            "avg_us": sum(records) // len(records),
            "max_us": max(records)
        }