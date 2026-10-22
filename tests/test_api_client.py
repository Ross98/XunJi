import asyncio
import httpx
import pytest
from api_client import make_headers, build_training_url
from api_client import XunjiAPIClient
from config import XunjiConfig


async def query_with_mock_transport(respond):
    client = XunjiAPIClient(XunjiConfig('test-training', 'test-body'))
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        return await client.query_body('2026-10-01', '2026-10-23')
    finally:
        await client.close()


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


@pytest.mark.parametrize('response', [
    {'status': 429, 'headers': {'Retry-After': '2'}, 'body': {}},
    {'status': 200, 'headers': {}, 'body': {'success': False, 'res': 'too frequent', 'retry_after_ms': 1500}},
    {'status': 200, 'headers': {}, 'body': {'success': False, 'res': {'error': 'too frequent', 'retry_after_ms': 1500}}},
])
def test_rate_limit_retry_uses_server_delay(monkeypatch, response):
    import asyncio
    import httpx
    import api_client
    from config import XunjiConfig
    delays = []
    calls = []

    async def sleep(seconds):
        delays.append(seconds)

    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(response['status'], headers=response['headers'], json=response['body'])
        return httpx.Response(200, json={'success': True, 'res': {'records': []}})

    monkeypatch.setattr(asyncio, 'sleep', sleep)

    result = asyncio.run(query_with_mock_transport(respond))
    assert result['success'] is True
    assert len(calls) == 2
    assert delays == [2.0 if response['status'] == 429 else 1.5]


@pytest.mark.parametrize('header,body,expected', [
    ('not-a-date', {}, 30), ('NaN', {}, 30), ('-1', {}, 30),
    (None, {'success': False, 'retry_after_ms': float('inf')}, 30),
    (None, {'success': False, 'retry_after_ms': True}, 30),
    ('2', {'success': False, 'retry_after_ms': 9000}, 2),
])
def test_malformed_retry_delays_are_safe_and_header_takes_priority(header, body, expected):
    import httpx
    from api_client import _rate_limit_delay
    response = httpx.Response(429, headers={'Retry-After': header} if header else {})
    assert _rate_limit_delay(response, body) == expected


def test_retry_after_accepts_http_date(monkeypatch):
    import httpx
    import api_client
    from datetime import datetime, timezone

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 23, tzinfo=timezone.utc)

    monkeypatch.setattr(api_client, 'datetime', Clock)
    response = httpx.Response(429, headers={'Retry-After': 'Fri, 23 Oct 2026 00:00:07 GMT'})
    assert api_client._rate_limit_delay(response) == 7


@pytest.mark.parametrize('status,body,expected_calls', [
    (200, {'success': False, 'res': 'too frequent', 'retry_after_ms': 1500}, 3),
    (429, {}, 3),
    (200, {'success': False, 'res': 'apikey invalid'}, 1),
    (401, {}, 1),
])
def test_retries_are_bounded_and_ordinary_errors_are_not_retried(
    monkeypatch, status, body, expected_calls
):
    import asyncio
    import httpx
    from api_client import XunjiAPIClient
    from config import XunjiConfig
    calls = []
    delays = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, json=body)

    async def sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(asyncio, 'sleep', sleep)

    if status >= 400:
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(query_with_mock_transport(respond))
    else:
        assert asyncio.run(query_with_mock_transport(respond)) == body
    assert len(calls) == expected_calls
    assert len(delays) == expected_calls - 1


def test_rate_limit_wait_can_be_cancelled_without_another_request(monkeypatch):
    import asyncio
    import httpx
    from api_client import XunjiAPIClient
    from config import XunjiConfig
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(429, headers={'Retry-After': '30'})

    async def cancelled_sleep(seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, 'sleep', cancelled_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(query_with_mock_transport(respond))
    assert len(calls) == 1


@pytest.mark.parametrize('start', [None, '2020-01-01'])
def test_body_query_transmits_pagination_offset(start):
    import json
    payloads = []

    def respond(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={'records': []})

    async def run():
        client = XunjiAPIClient(XunjiConfig('test-training', 'test-body'))
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            await client.query_body(start, '2026-10-23', ['weight'], 500, offset=1000)
        finally:
            await client.close()

    asyncio.run(run())
    assert payloads[0]['offset'] == 1000
    assert payloads[0]['start_date'] == start
    assert payloads[0]['limit'] == 500
    assert payloads[0]['types'] == ['weight']
