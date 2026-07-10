import sqlite3
import json
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

    def _conn(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, key: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data FROM cache WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            return json.loads(row["data"])

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

    def delete(self, key: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))

    def delete_prefix(self, prefix: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM cache WHERE key LIKE ?", (f"{prefix}%",))

    def clear(self):
        with self._conn() as conn:
            conn.execute("DELETE FROM cache")

    def keys_by_prefix(self, prefix: str) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT key FROM cache WHERE key LIKE ?", (prefix + "%",)
            ).fetchall()
            return [row["key"] for row in rows]
