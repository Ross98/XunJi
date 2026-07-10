import pytest
from cache import Cache


@pytest.fixture
def tmp_cache(tmp_path):
    db_path = tmp_path / "test_cache.sqlite"
    return Cache(str(db_path))
