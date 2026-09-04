import pytest
import cache as cache_module
from cache import Cache


@pytest.fixture
def tmp_cache(tmp_path):
    db_path = tmp_path / "test_cache.sqlite"
    return Cache(str(db_path))


@pytest.fixture
def isolated_health_db(tmp_path, monkeypatch):
    import apple_health as ah

    monkeypatch.setattr(ah, "HEALTH_DB_PATH", tmp_path / "apple_health.sqlite")
    runtime_cache = Cache(str(tmp_path / "xunji_cache.sqlite"))
    monkeypatch.setattr(cache_module, "Cache", lambda: runtime_cache)
    ah._init_db()
    return ah
