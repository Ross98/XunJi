import asyncio
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, HTTPException, Request, status

from shutdown_service import schedule_shutdown
from request_shutdown import RequestShutdownMiddleware, ShutdownRequests


_LOCAL_CLIENTS = {"127.0.0.1", "::1"}


async def close_route_tasks() -> None:
    """Stop process-local jobs before shared clients are closed."""
    from routes import health, training

    results = await asyncio.gather(
        training.stop_training_background_tasks(),
        health.stop_health_background_tasks(),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result


def register_routes(app: FastAPI):
    app.state.shutdown_requests = ShutdownRequests()
    app.add_middleware(RequestShutdownMiddleware, requests=app.state.shutdown_requests)
    from routes import (
        dashboard,
        training,
        # diet,
        body,
        plan,
        health,
    )
    app.include_router(dashboard.router)
    app.include_router(training.router)
    # app.include_router(diet.router)
    app.include_router(body.router)
    app.include_router(plan.router)
    app.include_router(health.router)

    @app.post("/admin/clear-cache")
    async def clear_cache():
        try:
            from app_state import get_cache
            c = get_cache()
            # A cache clear is not a successful upstream refresh. Preserve all
            # system markers atomically, including concurrent health updates.
            c.clear(preserve_system=True)
            return {"status": "ok"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def to_bj(ts):
        """Convert aware timestamps to Beijing time; legacy naive values are local."""
        if not isinstance(ts, str) or not ts:
            return None
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone(timedelta(hours=8)))
            return dt.isoformat()[:19]
        return ts[:19]

    @app.get("/admin/refresh-times")
    async def refresh_times():
        from app_state import get_cache
        c = get_cache()
        xunji = c.get("_xunji_refresh_time")
        health = c.get("_health_import_time")
        return {"xunji": to_bj(xunji.get("time")) if xunji else None, "health": to_bj(health.get("time")) if health else None}

    @app.post("/admin/shutdown", status_code=status.HTTP_202_ACCEPTED)
    async def shutdown_project(request: Request):
        if request.headers.get("X-Xunji-Shutdown") != "confirm":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="需要确认退出",
            )
        client_host = request.client.host if request.client else ""
        if client_host not in _LOCAL_CLIENTS:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="仅允许从本机关闭项目",
            )
        if not getattr(app.state, "shutdown_requested", False):
            app.state.shutdown_requested = True
            app.state.shutdown_requests.closing = True
            app.state.shutdown_task = schedule_shutdown(before_signal=app.state.shutdown_requests.stop)
        return {
            "status": "shutting_down",
            "message": "项目正在关闭并释放资源",
        }
