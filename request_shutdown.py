"""Drain HTTP work before uvicorn waits for connections during local quit."""

import asyncio

from starlette.responses import JSONResponse


class ShutdownRequests:
    def __init__(self):
        self.closing = False
        self.tasks = set()

    async def stop(self):
        self.closing = True
        tasks = [task for task in self.tasks if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class RequestShutdownMiddleware:
    def __init__(self, app, requests: ShutdownRequests):
        self.app = app
        self.requests = requests

    async def __call__(self, scope, receive, send):
        # Repeated local quit requests still pass through their authorization.
        if scope["type"] != "http" or scope.get("path") == "/admin/shutdown":
            await self.app(scope, receive, send)
            return
        stopped = JSONResponse({"detail": "项目正在关闭"}, status_code=503)
        if self.requests.closing:
            await stopped(scope, receive, send)
            return
        task = asyncio.current_task()
        self.requests.tasks.add(task)
        started = False
        completed = False

        async def track_send(message):
            nonlocal started, completed
            if message["type"] == "http.response.start":
                started = True
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                completed = True
            await send(message)

        try:
            await self.app(scope, receive, track_send)
        except asyncio.CancelledError:
            if not self.requests.closing:
                raise
            if not started:
                await stopped(scope, receive, send)
            elif not completed:
                # A partially streamed response cannot be replaced with JSON.
                raise
        finally:
            self.requests.tasks.discard(task)
