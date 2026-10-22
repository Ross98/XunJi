from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import httpx

from config import XunjiConfig


def _nonnegative_seconds(value) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        seconds = float(value)
        return seconds if math.isfinite(seconds) and seconds >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def _rate_limit_delay(response: httpx.Response, data=None) -> Optional[float]:
    containers = [data] if isinstance(data, dict) and data.get("success") is False else []
    if containers and isinstance(data.get("res"), dict):
        containers.append(data["res"])
    limited = response.status_code == 429 or any(
        "retry_after_ms" in container or any(
            "too frequent" in str(container.get(key, "")).lower()
            for key in ("res", "error", "message")
        ) for container in containers
    )
    if not limited:
        return None
    retry_after = response.headers.get("Retry-After")
    seconds = _nonnegative_seconds(retry_after)
    if seconds is not None:
        return seconds
    if retry_after:
        try:
            deadline = parsedate_to_datetime(retry_after)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            return max(0, (deadline - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            pass
    for container in containers:
        milliseconds = _nonnegative_seconds(container.get("retry_after_ms"))
        if milliseconds is not None:
            return milliseconds / 1000
    return 30.0


def make_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept-Encoding": "gzip",
    }


def build_training_url(base: str, action: str) -> str:
    if action == "read":
        return f"{base}/api_trains_for_llm_v2"
    return f"{base}/api_upsert_trains_for_llm_v2"


def build_diet_url(base: str, action: str) -> str:
    paths = {
        "query": "/open/food/query_gzip",
        "upsert": "/open/food/upsert_gzip",
        "custom_upsert": "/open/food/custom/upsert_gzip",
        "templates_list": "/open/food/templates/list_gzip",
        "templates_apply": "/open/food/templates/apply_gzip",
    }
    return f"{base}{paths[action]}"


def build_diet_search_url(base: str) -> str:
    return f"{base}/open_agent/food/search_gzip"


def build_body_url(base: str, action: str) -> str:
    if action == "query":
        return f"{base}/open/body/query_gzip"
    return f"{base}/open/body/upsert_gzip"


class XunjiAPIClient:
    def __init__(self, config: XunjiConfig, timeout: float = 30.0):
        self._config = config
        self._client = httpx.AsyncClient(timeout=timeout)

    async def _post(self, url: str, headers: dict, payload: dict, retries: int = 2) -> dict:
        import asyncio
        for attempt in range(retries + 1):
            resp = await self._client.post(url, headers=headers, json=payload)
            if resp.status_code == 429:
                try:
                    data = resp.json()
                except ValueError:
                    data = None
            else:
                resp.raise_for_status()
                data = resp.json()
            delay = _rate_limit_delay(resp, data)
            if delay is not None and attempt < retries:
                await asyncio.sleep(delay)
                continue
            resp.raise_for_status()
            return data

    # ── Training ──

    async def fetch_training(self, date: str, full: bool = False) -> dict:
        url = build_training_url(self._config.training_base_url, "read")
        headers = make_headers(self._config.training_api_key)
        payload = {
            "schema_version": "train_open_api_v2",
            "datestr": date,
            "include_full_data": full,
        }
        return await self._post(url, headers, payload)

    async def upsert_training(self, payload: dict) -> dict:
        url = build_training_url(self._config.training_base_url, "write")
        headers = make_headers(self._config.training_api_key)
        return await self._post(url, headers, payload)

    # ── Diet ──

    async def query_diet(
        self, start_date: str, end_date: str, detail: bool = True
    ) -> dict:
        url = build_diet_url(self._config.diet_base_url, "query")
        headers = make_headers(self._config.diet_api_key)
        payload = {
            "start_date": start_date,
            "end_date": end_date,
            "include_detail": detail,
        }
        return await self._post(url, headers, payload)

    async def search_food(self, keyword: str, limit: int = 8) -> dict:
        url = build_diet_search_url(self._config.diet_search_base_url)
        headers = make_headers(self._config.diet_search_api_key)
        payload = {"keyword": keyword, "limit": limit}
        return await self._post(url, headers, payload)

    async def upsert_diet(self, payload: dict) -> dict:
        url = build_diet_url(self._config.diet_base_url, "upsert")
        headers = make_headers(self._config.diet_api_key)
        return await self._post(url, headers, payload)

    async def upsert_custom_food(self, payload: dict) -> dict:
        url = build_diet_url(self._config.diet_base_url, "custom_upsert")
        headers = make_headers(self._config.diet_api_key)
        return await self._post(url, headers, payload)

    # ── Body ──

    async def query_body(
        self,
        start_date: str,
        end_date: str,
        types: Optional[list[str]] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> dict:
        url = build_body_url(self._config.body_base_url, "query")
        headers = make_headers(self._config.body_api_key)
        payload = {
            "start_date": start_date,
            "end_date": end_date,
            "include_latest": True,
            "include_records": True,
            "limit": limit,
            "offset": offset,
        }
        if types:
            payload["types"] = types
        return await self._post(url, headers, payload)

    async def upsert_body(self, payload: dict) -> dict:
        url = build_body_url(self._config.body_base_url, "upsert")
        headers = make_headers(self._config.body_api_key)
        return await self._post(url, headers, payload)

    async def close(self):
        await self._client.aclose()
