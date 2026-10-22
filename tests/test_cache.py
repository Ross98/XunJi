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


@pytest.mark.parametrize('raw', ['{broken', '[]', 'null', '42', '"text"'])
def test_invalid_cached_payload_is_a_miss_and_can_be_replaced(tmp_cache, raw):
    with tmp_cache._conn() as conn:
        conn.execute('INSERT INTO cache VALUES (?,?,?)', ('training:test', raw, '2026-10-22'))
    assert tmp_cache.get('training:test') is None
    tmp_cache.set('training:test', {'trains': []})
    assert tmp_cache.get('training:test') == {'trains': []}


@pytest.mark.parametrize('prefix,matching,unrelated', [
    ('data_', 'data_value', 'dataXvalue'),
    ('data%', 'data%value', 'dataAnything'),
    ('Data:', 'Data:value', 'data:value'),
    ('数据_', '数据_一', '数据二一'),
    ('path\\', 'path\\value', 'path/value'),
])
def test_prefix_operations_use_literal_case_sensitive_prefixes(tmp_cache, prefix, matching, unrelated):
    tmp_cache.set(matching, {'ok': True})
    tmp_cache.set(unrelated, {'ok': True})
    assert tmp_cache.keys_by_prefix(prefix) == [matching]
    tmp_cache.delete_prefix(prefix)
    assert tmp_cache.get(matching) is None
    assert tmp_cache.get(unrelated) == {'ok': True}


def test_empty_prefix_matches_all_keys(tmp_cache):
    tmp_cache.set('a', {})
    tmp_cache.set('b', {})
    assert set(tmp_cache.keys_by_prefix('')) == {'a', 'b'}
    tmp_cache.delete_prefix('')
    assert tmp_cache.keys_by_prefix('') == []
