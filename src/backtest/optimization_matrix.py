"""
Apex Engine - Quantitative Parameter Sweep Optimization Engine
Responsibility: Coordinates parameter evaluations across strategy variations concurrently.
Latency Profile: Highly parallelized processing framework tracking top-performing profiles.
"""

from typing import List, Dict, Any, Callable, Coroutine
import structlog

logger = structlog.get_logger()

class ParameterSweepOptimizationEngine:
    """Orchestrates historical verification passes across an input grid coordinate matrix."""

    __slots__ = ("_backtest_runner_callback",)

    def __init__(self, backtest_runner_callback: Callable[[Dict[str, Any]], Coroutine[Any, Any, Float]]) -> None:
        self._backtest_runner_callback = backtest_runner_callback

    async def execute_grid_search_sweep(self, parameter_grid: Dict[str, List[Any]]) -> List[Dict[str, Any]]:
        """Runs optimization loops across configuration arrays sequentially, identifying top-performing profiles."""
        import itertools
        
        # Unpack grid combinations cleanly
        keys, values = zip(*parameter_grid.items())
        grid_permutations = [dict(zip(keys, v)) for v in itertools.product(*values)]
        
        optimization_profiles_summary: List[Dict[str, Any]] = []
        logger.info("optimization_engine.grid_sweep_start", total_permutations=len(grid_permutations))

        for idx, config_variant in enumerate(grid_permutations):
            try:
                # Target independent runner executors to evaluate metrics profiles
                metric_result = await self._backtest_runner_callback(config_variant)
                optimization_profiles_summary.append({
                    "permutation_id": idx,
                    "parameters": config_variant,
                    "target_metric_outcome": metric_result
                })
            except Exception as ex:
                logger.error("optimization_engine.permutation_crash", permutation=idx, error=str(ex))

        # Sort summary results to prioritize parameter configurations that maximize returns
        optimization_profiles_summary.sort(key=lambda x: x["target_metric_outcome"], reverse=True)
        return optimization_profiles_summary