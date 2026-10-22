import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional


class Cache:
    def __init__(self, db_path: str = "xunji_cache.sqlite"):
        self._db_path = db_path
        self._init_db()

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    mtime TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache_generation (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    generation INTEGER NOT NULL
                )
            """)
            conn.execute("INSERT OR IGNORE INTO cache_generation VALUES (1, 0)")

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def get(self, key: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data FROM cache WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            try:
                data = json.loads(row["data"])
            except (json.JSONDecodeError, UnicodeDecodeError):
                return None
            return data if isinstance(data, dict) else None

    def set(self, key: str, data: dict):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, data, mtime) VALUES (?, ?, ?)",
                (key, json.dumps(data), datetime.now(timezone.utc).isoformat()),
            )

    def get_mtime(self, key: str) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT mtime FROM cache WHERE key = ?", (key,)
            ).fetchone()
            return row["mtime"] if row else None

    def get_generation(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT generation FROM cache_generation WHERE singleton=1").fetchone()[0]

    def set_many_if_generation(self, values: dict[str, dict], generation: int) -> bool:
        """Commit a read and its provenance only if no cache clear intervened."""
        timestamp = datetime.now(timezone.utc).isoformat()
        rows = [(key, json.dumps(value), timestamp) for key, value in values.items()]
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT generation FROM cache_generation WHERE singleton=1").fetchone()[0]
            if current != generation:
                return False
            conn.executemany("INSERT OR REPLACE INTO cache (key, data, mtime) VALUES (?, ?, ?)", rows)
        return True

    def delete(self, key: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))

    def delete_prefix(self, prefix: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM cache WHERE substr(key, 1, ?) = ?", (len(prefix), prefix))

    def clear(self, *, preserve_system: bool = False):
        with self._conn() as conn:
            conn.execute("UPDATE cache_generation SET generation=generation+1 WHERE singleton=1")
            if preserve_system:
                conn.execute("DELETE FROM cache WHERE substr(key, 1, 1) != '_'")
            else:
                conn.execute("DELETE FROM cache")

    def keys_by_prefix(self, prefix: str) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT key FROM cache WHERE substr(key, 1, ?) = ?", (len(prefix), prefix)
            ).fetchall()
            return [row["key"] for row in rows]
