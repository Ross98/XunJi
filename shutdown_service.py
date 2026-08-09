"""Schedule a graceful shutdown after the HTTP response has been flushed."""

import asyncio
import os
import signal
import sys


def shutdown_target_pid() -> int:
    """Target the reload supervisor when uvicorn is running with --reload."""
    if "--reload" in sys.argv and os.getppid() > 1:
        return os.getppid()
    return os.getpid()


async def _shutdown_after_response(delay_seconds: float) -> None:
    await asyncio.sleep(delay_seconds)
    os.kill(shutdown_target_pid(), signal.SIGTERM)


def schedule_shutdown(delay_seconds: float = 0.35) -> asyncio.Task:
    """Return a tracked task so the current request can return before SIGTERM."""
    return asyncio.create_task(_shutdown_after_response(delay_seconds))
