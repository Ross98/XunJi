import asyncio

import httpx

import app as application
import apple_health as ah
import cache as cache_module
from routes import health


def test_fresh_install_has_queryable_health_tables_before_watcher_starts(tmp_path, tmp_cache, monkeypatch):
    health_path = tmp_path / 'new-health.sqlite'
    monkeypatch.setattr(ah, 'HEALTH_DB_PATH', health_path)
    monkeypatch.setattr(ah, 'EXPORT_XML', tmp_path / 'missing-export.xml')
    monkeypatch.setattr(cache_module, 'Cache', lambda: tmp_cache)
    monkeypatch.setattr(application, 'get_data_service', lambda: object())
    monkeypatch.setattr(health, 'get_cache', lambda: tmp_cache)
    calls = []

    async def start():
        assert health_path.exists(), 'Health store must exist before requests are accepted'
        with ah._conn() as conn:
            assert conn.execute('SELECT COUNT(*) FROM health_records').fetchone()[0] == 0
            assert conn.execute('SELECT COUNT(*) FROM health_workouts').fetchone()[0] == 0
            assert conn.execute('SELECT COUNT(*) FROM health_sleep').fetchone()[0] == 0
        calls.append('watcher ready')

    async def stop():
        calls.append('stopped')

    async def close():
        calls.append('closed')

    monkeypatch.setattr(health, 'start_health_background_tasks', start)
    monkeypatch.setattr(application, 'close_route_tasks', stop)
    monkeypatch.setattr(application, 'close_state', close)

    async def run():
        async with application.lifespan(application.app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application.app), base_url='http://test'
            ) as client:
                response = await client.get('/health')
                assert response.status_code == 200
                assert '尚未导入 Apple Health' in response.text
    asyncio.run(run())
    assert calls == ['watcher ready', 'stopped', 'closed']
