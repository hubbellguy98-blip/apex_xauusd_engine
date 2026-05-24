"""
Apex Engine - Master Application Initialization Script
Responsibility: Main entry point configuration that bootstraps the trading architecture under uvloop.
Latency Profile: High-performance initialization backed by uvloop event management.
"""

import asyncio
import sys
import signal
from config.base_settings import EngineSettings
from src.core.domain.constants import Environment
from src.infrastructure.telemetry.structured_logger import configure_logger
from src.core.events.event_bus import EventBus
from src.core.lifecycle_manager import ApplicationLifecycleManager
import structlog

# Attempt to load uvloop for optimized asynchronous event loop performance
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

logger = structlog.get_logger()

async def runtime_main() -> None:
    """Bootstraps components and initializes the production application loop."""
    
    # 1. Load System Settings Configuration Matrix
    settings = EngineSettings()
    
    # 2. Configure Structured Log Format Output Routing
    configure_logger(settings.ENV)
    logger.info("engine.bootstrapped", env=settings.ENV.value, symbol=settings.TARGET_SYMBOL)

    # 3. Instantiate Core Communication Channels
    event_bus = EventBus()
    lifecycle_manager = ApplicationLifecycleManager(event_bus)

    # 4. Set Up OS Signal Handlers for Graceful Shutdowns
    loop = asyncio.get_running_loop()
    def trigger_shutdown_signal() -> None:
        logger.warn("engine.signal_interrupt_received")
        asyncio.create_task(lifecycle_manager.initiate_shutdown())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, trigger_shutdown_signal)

    # 5. Start the Global Architecture Cascade
    try:
        await lifecycle_manager.initiate_bootstrap()
        
        # Keep the primary orchestrator thread running indefinitely
        while not lifecycle_manager._is_terminating:
            await asyncio.sleep(1.0)
            
    except Exception as ex:
        logger.critical("engine.fatal_crash", error=str(ex))
        sys.exit(1)
    finally:
        logger.info("engine.process_exited")

if __name__ == "__main__":
    if sys.version_info < (3, 12):
        print("Fatal Failure: Apex System Engine requires minimum Python 3.12+ execution layers.")
        sys.exit(1)
    asyncio.run(runtime_main())
