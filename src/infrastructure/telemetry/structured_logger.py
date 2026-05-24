"""
Apex Engine - Structured Logging Factory
Responsibility: Implements low-overhead async JSON processing for structlog.
Latency Profile: Highly optimized thread-safe logging injection context configuration.
"""

import sys
import logging
from typing import Any, Dict
import structlog
from src.core.domain.constants import Environment

def inject_runtime_context(logger: Any, method_name: str, event_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Dynamically appends tracking variables into log messages."""
    from src.shared.time_utils import TimeProvider
    event_dict["sys_timestamp"] = TimeProvider.get_utc_now().isoformat()
    return event_dict

def configure_logger(environment: Environment) -> None:
    """Configures the unified structured logging framework for the application layer."""
    
    # Baseline processing filters
    processors: list[Any] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        inject_runtime_context,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if environment == Environment.PRODUCTION:
        # Emit raw JSON records for production monitoring aggregators
        processors.append(structlog.processors.JSONRenderer())
    else:
        # Use human-readable colorized formatting for development environments
        processors.append(structlog.processors.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG if environment != Environment.PRODUCTION else logging.INFO),
        cache_logger_on_first_use=True,
    )
    
    # Route standard Python logs into the structlog processor
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.INFO)