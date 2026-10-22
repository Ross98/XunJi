"""Exercise real route rendering with isolated stores and an unavailable upstream."""
import asyncio
from datetime import date

import httpx
import pytest

from app import app
from routes import body, dashboard, health, plan, training


@pytest.mark.parametrize("path", [
    "/", "/dashboard/content", "/training", "/training/2026-10-22",
    "/training/calendar/2026/10", "/body", "/health", "/plan",
])
def test_pages_render_with_empty_health_store_and_upstream_errors(
    path, monkeypatch, isolated_health_db, tmp_cache, tmp_path
):
    class UnavailableService:
        async def get_training(self, *args, **kwargs):
            return {"success": False, "res": "too frequent"}

        async def get_body(self, *args, **kwargs):
            return {"success": False, "res": "too frequent"}

    service = UnavailableService()
    for module in (body, dashboard, health, plan, training):
        monkeypatch.setattr(module, "get_cache", lambda: tmp_cache)
        if hasattr(module, "get_data_service"):
            monkeypatch.setattr(module, "get_data_service", lambda: service)
    monkeypatch.setattr(training, "_ensure_training_history_backfill", lambda *args: None)
    monkeypatch.setattr(plan, "PLAN_CONFIG_PATH", tmp_path / "plan.json")
    monkeypatch.setattr(body, "BODY_CONFIG_PATH", str(tmp_path / "body.json"))
    tmp_cache.set(f"training:{date.today().isoformat()}:False", {"res": "too frequent"})

    async def run():
        # Lifespan stays off: no real API client, iCloud sync, or background backfill.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(path)
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    asyncio.run(run())
