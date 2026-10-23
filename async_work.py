"""Offload synchronous work without abandoning its thread on request cancellation."""

import asyncio
from fastapi.concurrency import run_in_threadpool


async def run_joined_threadpool(func, **kwargs):
    """Keep the thread joined even when its owning asyncio task is cancelled."""
    worker = asyncio.create_task(run_in_threadpool(func, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # Cancelling an asyncio wrapper cannot stop its native thread. Keep it
        # joined before the owner releases shared resources. Import jobs also
        # signal their own cooperative cancellation event.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()  # Consume failures; shutdown retains cancellation.
        raise
