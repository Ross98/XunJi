import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app_state import STATIC_DIR, TEMPLATES_DIR
from routes import register_routes


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    os.makedirs(STATIC_DIR / "css", exist_ok=True)
    yield


app = FastAPI(title="Xunji Analysis", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
register_routes(app)
