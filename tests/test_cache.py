import json
import pytest


def test_set_and_get(tmp_cache):
    data = {"trains": [{"datestr": "2026-04-02"}]}
    tmp_cache.set("training:2026-04-02", data)
    result = tmp_cache.get("training:2026-04-02")
    assert result == data


def test_get_missing_key(tmp_cache):
    result = tmp_cache.get("training:2026-04-02")
    assert result is None


def test_get_mtime(tmp_cache):
    tmp_cache.set("training:2026-04-02", {"ok": True})
    mtime = tmp_cache.get_mtime("training:2026-04-02")
    assert mtime is not None
    assert "T" in mtime  # ISO format


def test_clear(tmp_cache):
    tmp_cache.set("a", {"v": 1})
    tmp_cache.set("b", {"v": 2})
    tmp_cache.clear()
    assert tmp_cache.get("a") is None
    assert tmp_cache.get("b") is None


def test_overwrite(tmp_cache):
    tmp_cache.set("k", {"v": 1})
    tmp_cache.set("k", {"v": 2})
    assert tmp_cache.get("k") == {"v": 2}


def test_keys_by_prefix(tmp_cache):
    tmp_cache.set("training:2026-04-02", {"ok": True})
    tmp_cache.set("training:2026-04-03", {"ok": True})
    tmp_cache.set("diet:2026-04", {"ok": True})
    keys = tmp_cache.keys_by_prefix("training:")
    assert len(keys) == 2
    assert "training:2026-04-02" in keys
    assert "training:2026-04-03" in keys
    assert "diet:2026-04" not in keys
