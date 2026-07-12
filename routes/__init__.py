from fastapi import FastAPI


def register_routes(app: FastAPI):
    from routes import (
        dashboard,
        training,
        diet,
        body,
        plan,
        health,
    )
    app.include_router(dashboard.router)
    app.include_router(training.router)
    app.include_router(diet.router)
    app.include_router(body.router)
    app.include_router(plan.router)
    app.include_router(health.router)

    @app.post("/admin/clear-cache")
    async def clear_cache():
        try:
            from datetime import datetime, timezone, timedelta
            from cache import Cache
            c = Cache()
            # 保留系统 key 再清
            health_time = c.get("_health_import_time")
            c.clear()
            # 恢复系统 key + 更新训记时间 (北京时间)
            bj = datetime.now(timezone.utc) + timedelta(hours=8)
            now = bj.isoformat()[:19]
            c.set("_xunji_refresh_time", {"time": now})
            if health_time:
                c.set("_health_import_time", health_time)
            return {"status": "ok"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def to_bj(ts):
        """若 ts 含 Z 或 +00:00 则转北京时间, 否则原样返回"""
        if not ts: return ts
        t = ts.replace('Z','').replace('+00:00','')
        if 'T' in t and ts.endswith(('Z','+00:00')):
            from datetime import datetime, timezone, timedelta
            dt = datetime.fromisoformat(t).replace(tzinfo=timezone.utc) + timedelta(hours=8)
            return dt.isoformat()[:19]
        return ts[:19]

    @app.get("/admin/refresh-times")
    async def refresh_times():
        from cache import Cache
        c = Cache()
        xunji = c.get("_xunji_refresh_time")
        health = c.get("_health_import_time")
        return {"xunji": to_bj(xunji.get("time")) if xunji else None, "health": to_bj(health.get("time")) if health else None}
