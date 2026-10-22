import sqlite3

import pytest


@pytest.fixture(params=["cache", "health"])
def connection_factory(request, tmp_cache, isolated_health_db):
    return tmp_cache._conn if request.param == "cache" else isolated_health_db._conn


def test_database_connection_closes_after_normal_exit(connection_factory):
    with connection_factory() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")


def test_database_connection_closes_after_error(connection_factory):
    with pytest.raises(RuntimeError, match="abort"):
        with connection_factory() as conn:
            raise RuntimeError("abort")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")


def test_connection_preserves_commit_and_rollback(connection_factory):
    with connection_factory() as conn:
        conn.execute("CREATE TABLE lifecycle_test (value INTEGER)")
        conn.execute("INSERT INTO lifecycle_test VALUES (1)")
    with pytest.raises(RuntimeError):
        with connection_factory() as conn:
            conn.execute("INSERT INTO lifecycle_test VALUES (2)")
            raise RuntimeError("abort")
    with connection_factory() as conn:
        assert [row[0] for row in conn.execute("SELECT value FROM lifecycle_test")] == [1]
