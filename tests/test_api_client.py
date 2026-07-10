import pytest
from api_client import make_headers, build_training_url


def test_make_headers():
    h = make_headers("test-key-123")
    assert h["Authorization"] == "Bearer test-key-123"
    assert h["Content-Type"] == "application/json"
    assert "Accept-Encoding" in h


def test_build_training_url():
    url = build_training_url("https://trains.xunjiapp.cn", "read")
    assert url == "https://trains.xunjiapp.cn/api_trains_for_llm_v2"
    url = build_training_url("https://trains.xunjiapp.cn", "write")
    assert url == "https://trains.xunjiapp.cn/api_upsert_trains_for_llm_v2"
