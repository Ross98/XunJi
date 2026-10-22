"""Schedule a graceful shutdown after the HTTP response has been flushed."""

import asyncio
import os
import signal
import sys
from typing import Awaitable, Callable, Optional


def shutdown_target_pid() -> int:
    """Target the reload supervisor when uvicorn is running with --reload."""
    if "--reload" in sys.argv and os.getppid() > 1:
        return os.getppid()
    return os.getpid()


async def _shutdown_after_response(
    delay_seconds: float,
    before_signal: Optional[Callable[[], Awaitable[None]]] = None,
) -> None:
    await asyncio.sleep(delay_seconds)
    try:
        if before_signal is not None:
            await before_signal()
    finally:
        os.kill(shutdown_target_pid(), signal.SIGTERM)


def schedule_shutdown(
    delay_seconds: float = 0.35,
    *,
    before_signal: Optional[Callable[[], Awaitable[None]]] = None,
) -> asyncio.Task:
    """Return a tracked task so the current request can return before SIGTERM."""
    return asyncio.create_task(_shutdown_after_response(delay_seconds, before_signal))
