import asyncio
from pathlib import Path

import httpx
from fastapi import FastAPI

import routes
import shutdown_service


def _request(app, *, headers=None, client=("127.0.0.1", 53000)):
    async def run():
        transport = httpx.ASGITransport(app=app, client=client)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1"
        ) as browser:
            return await browser.post("/admin/shutdown", headers=headers)

    return asyncio.run(run())


def test_shutdown_requires_explicit_confirmation(monkeypatch):
    app = FastAPI()
    routes.register_routes(app)
    monkeypatch.setattr(routes, "schedule_shutdown", lambda **kwargs: None)

    response = _request(app)

    assert response.status_code == 409
    assert response.json()["detail"] == "需要确认退出"


def test_shutdown_only_accepts_local_clients(monkeypatch):
    app = FastAPI()
    routes.register_routes(app)
    monkeypatch.setattr(routes, "schedule_shutdown", lambda **kwargs: None)

    response = _request(
        app,
        headers={"X-Xunji-Shutdown": "confirm"},
        client=("192.168.1.20", 53000),
    )

    assert response.status_code == 403


def test_shutdown_schedules_process_exit_once(monkeypatch):
    calls = []
    app = FastAPI()
    routes.register_routes(app)
    monkeypatch.setattr(routes, "schedule_shutdown", lambda **kwargs: calls.append("shutdown"))
    headers = {"X-Xunji-Shutdown": "confirm"}

    first = _request(app, headers=headers)
    second = _request(app, headers=headers)

    assert first.status_code == 202
    assert first.json()["status"] == "shutting_down"
    assert second.status_code == 202
    assert calls == ["shutdown"]


def test_reload_mode_targets_uvicorn_parent(monkeypatch):
    monkeypatch.setattr(shutdown_service.sys, "argv", ["uvicorn", "app:app", "--reload"])
    monkeypatch.setattr(shutdown_service.os, "getpid", lambda: 222)
    monkeypatch.setattr(shutdown_service.os, "getppid", lambda: 111)

    assert shutdown_service.shutdown_target_pid() == 111


def test_normal_mode_targets_current_process(monkeypatch):
    monkeypatch.setattr(shutdown_service.sys, "argv", ["uvicorn", "app:app"])
    monkeypatch.setattr(shutdown_service.os, "getpid", lambda: 222)
    monkeypatch.setattr(shutdown_service.os, "getppid", lambda: 111)

    assert shutdown_service.shutdown_target_pid() == 222


def test_base_template_contains_confirmed_exit_flow():
    source = Path(routes.__file__).parent.parent / "templates" / "base.html"
    html = source.read_text(encoding="utf-8")

    assert 'id="exitProjectBtn"' in html
    assert 'id="exitConfirmDialog"' in html
    assert "X-Xunji-Shutdown" in html
