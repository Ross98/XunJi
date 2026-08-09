import asyncio

import app_state


def test_close_state_closes_http_client_and_resets_singletons(monkeypatch):
    class Service:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    service = Service()
    monkeypatch.setattr(app_state, "_config", object())
    monkeypatch.setattr(app_state, "_cache", object())
    monkeypatch.setattr(app_state, "_api_client", object())
    monkeypatch.setattr(app_state, "_data_service", service)

    asyncio.run(app_state.close_state())

    assert service.closed is True
    assert app_state._config is None
    assert app_state._cache is None
    assert app_state._api_client is None
    assert app_state._data_service is None
