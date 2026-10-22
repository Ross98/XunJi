import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app_state import STATIC_DIR, TEMPLATES_DIR, close_state, get_data_service
from routes import close_route_tasks, register_routes


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    os.makedirs(STATIC_DIR / "css", exist_ok=True)
    from routes.health import start_health_background_tasks
    try:
        get_data_service()
        await start_health_background_tasks()
        yield
    finally:
        try:
            await close_route_tasks()
        finally:
            await close_state()


app = FastAPI(title="Xunji Analysis", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(STATIC_DIR / "icons" / "favicon.ico", media_type="image/x-icon")


register_routes(app)
