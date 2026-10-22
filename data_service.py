from datetime import date, datetime, timezone
import asyncio
from asyncio import sleep as _body_page_sleep
from dataclasses import dataclass
import hashlib
import json
import math
from time import monotonic
from typing import Optional

from api_client import XunjiAPIClient
from cache import Cache


@dataclass
class _BodyRequest:
    task: asyncio.Task
    waiters: int = 0


def _response_records(data: object, key: str, *, decode_json: bool = False) -> Optional[list[dict]]:
    """Validate both API envelopes without confusing errors with empty results."""
    if not isinstance(data, dict) or data.get("success") is False:
        return None
    res = data.get("res")
    containers = [res, data] if isinstance(res, dict) else [data]
    for container in containers:
        if key not in container:
            continue
        records = container[key]
        if decode_json and isinstance(records, str):
            try:
                records = json.loads(records)
            except ValueError:
                continue
        if isinstance(records, list) and all(isinstance(row, dict) for row in records):
            return records
    return None


def extract_trains(data: object) -> list[dict]:
    return _response_records(data, "trains") or []


def extract_body_records(data: object) -> list[dict]:
    return filter_body_records(_response_records(data, "records", decode_json=True) or [])


def filter_body_records(records: list[dict]) -> list[dict]:
    """Keep usable measurements without converting missing or invalid values to zero."""
    valid = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if not isinstance(record.get("type"), str) or not record["type"]:
            continue
        if isinstance(record.get("value"), bool):
            continue
        try:
            date.fromisoformat(record.get("datestr", ""))
            if not math.isfinite(float(record.get("value"))):
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        valid.append(record)
    return valid


def resolve_training_cache(cache, day: str) -> tuple[Optional[dict], Optional[bool]]:
    """Choose one valid snapshot for every reader; mode is metadata, not a version."""
    candidates = []
    get_mtime = getattr(cache, "get_mtime", None)
    for mode in (True, False):
        key = f"training:{day}:{mode}"
        payload = cache.get(key)
        records = _response_records(payload, "trains")
        if records is None:
            continue
        timestamp = None
        if callable(get_mtime):
            try:
                timestamp = datetime.fromisoformat(get_mtime(key).replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
            except (AttributeError, TypeError, ValueError):
                pass
        candidates.append((payload, mode, timestamp, bool(records)))
    if not candidates:
        return None, None
    if all(candidate[2] is not None for candidate in candidates):
        chosen = max(candidates, key=lambda candidate: (candidate[2], candidate[3], candidate[1]))
    else:
        # Without comparable versions, preserve observed records; prefer richer
        # full data only when emptiness does not supply stronger evidence.
        chosen = max(candidates, key=lambda candidate: (candidate[3], candidate[1]))
    return chosen[0], chosen[1]


def get_cached_training(cache, day: str, full: bool = False) -> Optional[dict]:
    """Return the canonical snapshot; full remains accepted for existing callers."""
    return resolve_training_cache(cache, day)[0]


class DataService:
    def __init__(self, cache: Cache, api_client: XunjiAPIClient):
        self._cache = cache
        self._client = api_client
        self._training_attempts = {}
        self._body_requests: dict[tuple[str, Optional[int]], _BodyRequest] = {}
        self._closing = False

    # ── Training ──

    def _mark_xunji_refresh(self) -> None:
        self._cache.set(
            "_xunji_refresh_time",
            {"time": datetime.now(timezone.utc).isoformat()},
        )

    def _cache_generation(self) -> Optional[int]:
        get_generation = getattr(self._cache, "get_generation", None)
        return get_generation() if callable(get_generation) else None

    def _save_read_result(self, key: str, data: dict, generation: Optional[int]) -> None:
        commit = getattr(self._cache, "set_many_if_generation", None)
        if generation is not None and callable(commit):
            commit({key: data, "_xunji_refresh_time": {
                "time": datetime.now(timezone.utc).isoformat(),
            }}, generation)
        else:
            self._cache.set(key, data)
            self._mark_xunji_refresh()

    @staticmethod
    def _is_valid_training_response(data: object) -> bool:
        return _response_records(data, "trains") is not None

    async def get_training(self, date: str, full: bool = False, *, force_refresh: bool = False) -> dict:
        cache_key = f"training:{date}:{full}"
        cached, cached_mode = resolve_training_cache(self._cache, date)
        needs_full = full and cached_mode is False and bool(extract_trains(cached))
        if cached is not None and not force_refresh and not needs_full:
            return cached
        generation = self._cache_generation()
        now = monotonic()
        self._training_attempts = {key: started for key, started in self._training_attempts.items()
                                   if now - started < 30}
        attempt = (date, full)
        previous = self._training_attempts.get(attempt)
        cooldown = 30 if full else 15
        if cached is not None and previous is not None and now - previous < cooldown:
            return cached
        exact = self._cache.get(cache_key)
        if exact is not None and not self._is_valid_training_response(exact):
            self._cache.delete(cache_key)
        self._training_attempts[attempt] = now
        try:
            data = await self._client.fetch_training(date, full)
        except Exception:
            if cached is not None:
                return cached
            raise
        if self._is_valid_training_response(data):
            self._save_read_result(cache_key, data, generation)
            return data
        return cached if cached is not None else data

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

    @staticmethod
    def _is_valid_body_response(data: object) -> bool:
        return _response_records(data, "records", decode_json=True) is not None

    @staticmethod
    def body_history_complete(data: object, limit: int = 500) -> bool:
        records = _response_records(data, "records", decode_json=True)
        return records is not None and (len(records) < limit or data.get("_history_complete") is True)

    async def _fetch_body_history(self, start_date, end_date, types, limit):
        first = await self._client.query_body(start_date, end_date, types, limit)
        records = _response_records(first, "records", decode_json=True)
        if records is None or len(records) < limit:
            return first
        records = list(records)

        def fingerprint(rows):
            return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).digest()

        signatures = {fingerprint(records)}
        identities = {(row["datestr"], row["type"]) for row in records
                      if isinstance(row.get("datestr"), str) and isinstance(row.get("type"), str)}
        while True:
            # The body endpoint permits one query per key every 15 seconds.
            await _body_page_sleep(15)
            page = await self._client.query_body(start_date, end_date, types, limit, offset=len(records))
            batch = _response_records(page, "records", decode_json=True)
            if batch is None:
                raise ValueError("Body pagination returned an invalid response")
            signature = fingerprint(batch)
            batch_ids = {(row["datestr"], row["type"]) for row in batch
                         if isinstance(row.get("datestr"), str) and isinstance(row.get("type"), str)}
            if batch and (signature in signatures or identities.intersection(batch_ids)):
                raise ValueError("Body pagination repeated or overlapped records")
            signatures.add(signature)
            identities.update(batch_ids)
            records.extend(batch)
            if len(batch) < limit:
                break
        result = dict(first)
        container = result
        if isinstance(first.get("res"), dict) and _response_records(first["res"], "records", decode_json=True) is not None:
            container = result["res"] = dict(first["res"])
        container["records"] = records
        if "by_type" in container:
            by_type = {}
            for record in records:
                if isinstance(record.get("type"), str):
                    by_type.setdefault(record["type"], []).append(record)
            container["by_type"] = by_type
        result["_history_complete"] = True
        return result

    async def get_body(
        self,
        start_date: Optional[str],
        end_date: str,
        types: Optional[list[str]] = None,
        limit: int = 500,
        force_refresh: bool = False,
    ) -> dict:
        if self._closing:
            raise RuntimeError("Data service is closed")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("Body page limit must be a positive integer")
        type_str = ",".join(types) if types else "all"
        cache_key = f"body:{type_str}:{start_date}:{end_date}:{limit}"
        cached = self._cache.get(cache_key)
        complete_cache = self.body_history_complete(cached, limit)
        if complete_cache and not force_refresh:
            return cached
        generation = self._cache_generation()
        request_key = (cache_key, generation)
        pending = self._body_requests.get(request_key)
        if pending is None:
            pending = _BodyRequest(asyncio.create_task(self._load_body_history(
                cache_key, start_date, end_date, types, limit, cached, complete_cache, generation)))
            self._body_requests[request_key] = pending
        pending.waiters += 1
        try:
            return await asyncio.shield(pending.task)
        finally:
            pending.waiters -= 1
            if pending.waiters == 0:
                if self._body_requests.get(request_key) is pending:
                    del self._body_requests[request_key]
                if not pending.task.done():
                    pending.task.cancel()
                    await asyncio.gather(pending.task, return_exceptions=True)

    async def _load_body_history(self, cache_key, start_date, end_date, types, limit, cached, complete_cache, generation):
        if cached is not None and not self._is_valid_body_response(cached):
            self._cache.delete(cache_key)
        try:
            data = await self._fetch_body_history(start_date, end_date, types, limit)
        except Exception:
            if complete_cache:
                return cached
            raise
        if self._is_valid_body_response(data):
            self._save_read_result(cache_key, data, generation)
            return data
        return cached if complete_cache else data

    # ── Diet Search / Upsert ──

    async def search_food(self, keyword: str, limit: int = 8) -> dict:
        return await self._client.search_food(keyword, limit)

    async def upsert_diet(self, payload: dict) -> dict:
        return await self._client.upsert_diet(payload)

    async def upsert_custom_food(self, payload: dict) -> dict:
        return await self._client.upsert_custom_food(payload)

    async def close(self):
        self._closing = True
        tasks = [pending.task for pending in self._body_requests.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._body_requests.clear()
        await self._client.close()


KEY_AH_METRICS = [
    ("steps", "HKQuantityTypeIdentifierStepCount", "步数"),
    ("exercise_minutes", "HKQuantityTypeIdentifierAppleExerciseTime", "活动分钟"),
    ("stand_hours", "HKQuantityTypeIdentifierAppleStandTime", "站立"),
    ("active_energy", "HKQuantityTypeIdentifierActiveEnergyBurned", "活动热量"),
    ("hrv", "HKQuantityTypeIdentifierHeartRateVariabilitySDNN", "HRV"),
    ("resting_hr", "HKQuantityTypeIdentifierRestingHeartRate", "静息心率"),
    ("vo2max", "HKQuantityTypeIdentifierVO2Max", "VO2Max"),
    ("weight", "HKQuantityTypeIdentifierBodyMass", "体重"),
    ("bodyfat", "HKQuantityTypeIdentifierBodyFatPercentage", "体脂"),
]


class DataFreshnessService:
    """Compute data freshness context for templates."""

    def __init__(self, ah_module=None, xunji_cache=None):
        import apple_health as _ah
        self.ah = ah_module or _ah
        self.xunji_cache = xunji_cache

    def get_freshness_context(self) -> dict:
        from datetime import date
        all_dates = self.ah.get_latest_dates()
        today = date.today().isoformat()

        # Per-metric freshness
        metrics = {}
        for key, ah_type, label in KEY_AH_METRICS:
            latest = all_dates.get(ah_type)
            if latest:
                days_ago = (date.fromisoformat(today) - date.fromisoformat(latest)).days
                if days_ago == 0:
                    status = "today"
                elif days_ago <= 3:
                    status = "recent"
                elif days_ago <= 7:
                    status = "stale"
                else:
                    status = "expired"
            else:
                days_ago = None
                status = "none"

            tag = self._freshness_tag(status, days_ago)
            metrics[key] = {
                "label": label,
                "latest": latest,
                "days_ago": days_ago,
                "status": status,
                "tag": tag,
                "source": "apple_health",
            }

        # Overall AH latest
        ah_overall = self.ah.get_data_coverage()["end_date"]
        if ah_overall:
            ah_days_ago = (date.fromisoformat(today) - date.fromisoformat(ah_overall)).days
            if ah_days_ago <= 3:
                ah_status = "fresh"
            elif ah_days_ago <= 7:
                ah_status = "stale"
            else:
                ah_status = "expired"
        else:
            ah_days_ago = None
            ah_status = "none"

        xunji_refresh = (
            self.xunji_cache.get("_xunji_refresh_time")
            if self.xunji_cache is not None
            else None
        )
        xunji_latest = xunji_refresh.get("time") if xunji_refresh else None

        return {
            "ah_overall_latest": ah_overall,
            "xunji_latest": xunji_latest,
            "ah_days_ago": ah_days_ago,
            "ah_status": ah_status,
            "ah_tag": self._freshness_tag(ah_status, ah_days_ago),
            "metrics": metrics,
        }

    @staticmethod
    def _freshness_tag(status: str, days_ago: Optional[int]) -> str:
        if status in ("today", "fresh"):
            if days_ago == 0:
                return "[今天]"
            return f"[{days_ago}天前]"
        elif status == "recent":
            return f"[{days_ago}天前]"
        elif status == "stale":
            return f"[{days_ago}天前]"
        elif status == "expired":
            return f"[{days_ago}天未更新]"
        else:
            return "[未导入]"
