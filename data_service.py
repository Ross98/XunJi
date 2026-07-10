from datetime import datetime, timezone
from typing import Optional

from api_client import XunjiAPIClient
from cache import Cache


class DataService:
    def __init__(self, cache: Cache, api_client: XunjiAPIClient):
        self._cache = cache
        self._client = api_client

    # ── Training ──

    async def get_training(self, date: str, full: bool = False) -> dict:
        cache_key = f"training:{date}:{full}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        data = await self._client.fetch_training(date, full)
        self._cache.set(cache_key, data)
        return data

    # ── Diet ──

    async def get_diet(self, start_date: str, end_date: str, detail: bool = True) -> dict:
        cache_key = f"diet:{start_date}:{end_date}:{detail}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        data = await self._client.query_diet(start_date, end_date, detail)
        self._cache.set(cache_key, data)
        return data

    # ── Body ──

    async def get_body(
        self,
        start_date: str,
        end_date: str,
        types: Optional[list[str]] = None,
        limit: int = 500,
    ) -> dict:
        type_str = ",".join(types) if types else "all"
        cache_key = f"body:{type_str}:{start_date}:{end_date}:{limit}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        data = await self._client.query_body(start_date, end_date, types, limit)
        self._cache.set(cache_key, data)
        return data

    # ── Diet Search / Upsert ──

    async def search_food(self, keyword: str, limit: int = 8) -> dict:
        return await self._client.search_food(keyword, limit)

    async def upsert_diet(self, payload: dict) -> dict:
        return await self._client.upsert_diet(payload)

    async def upsert_custom_food(self, payload: dict) -> dict:
        return await self._client.upsert_custom_food(payload)

    async def close(self):
        await self._client.close()
