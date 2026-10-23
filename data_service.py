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
from training_records import training_records_view


@dataclass
class _ReadRequest:
    task: asyncio.Task
    waiters: int = 0


def _response_records(data: object, key: str, *, decode_json: bool = False,
                      skip_bad_rows: bool = False) -> Optional[list[dict]]:
    """Validate both API envelopes without confusing errors with empty results."""
    if not isinstance(data, dict) or data.get("success") is False:
        return None
    res = data.get("res")
    containers = [res, data] if isinstance(res, dict) else [data]
    recovered = None
    for container in containers:
        if key not in container:
            continue
        records = container[key]
        if decode_json and isinstance(records, str):
            try:
                records = json.loads(records)
            except (ValueError, RecursionError):
                continue
        if isinstance(records, list):
            if all(isinstance(row, dict) for row in records):
                return records
            if skip_bad_rows and not recovered:
                recovered = [row for row in records if isinstance(row, dict)]
    return recovered


def extract_trains(data: object) -> list[dict]:
    return training_records_view(_response_records(data, "trains", skip_bad_rows=True) or [])


def _valid_training_response(data: object) -> bool:
    records = _response_records(data, "trains")
    return records is not None and training_records_view(records) == records


def extract_body_records(data: object) -> list[dict]:
    return filter_body_records(_response_records(data, "records", decode_json=True, skip_bad_rows=True) or [])


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
    """Choose a canonical mode from one content/version snapshot when supported."""
    candidates = []
    keys = {mode: f"training:{day}:{mode}" for mode in (True, False)}
    read_many = getattr(cache, "get_many_with_mtime", None)
    snapshots = read_many(list(keys.values())) if callable(read_many) else None
    read_one = getattr(cache, "get_with_mtime", None)
    get_mtime = getattr(cache, "get_mtime", None)
    for mode, key in keys.items():
        if snapshots is not None:
            payload, version = snapshots.get(key, (None, None))
        elif callable(read_one):
            payload, version = read_one(key)
        else:
            # Legacy lightweight adapters have no atomic snapshot API.
            payload = cache.get(key)
            try:
                version = get_mtime(key) if callable(get_mtime) else None
            except (AttributeError, TypeError, ValueError):
                version = None
        records = _response_records(payload, "trains")
        if records is None:
            continue
        timestamp = _cache_version(version)
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


def _cache_version(value):
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class _BodyScope:
    start: str
    end: str
    types: Optional[frozenset[str]]
    limit: int

    @classmethod
    def from_key(cls, key):
        try:
            prefix, types, start, end, limit = key.split(":")
            start = date.min.isoformat() if start == "None" else start
            limit = int(limit)
            if (prefix != "body" or limit <= 0 or start > end
                    or date.fromisoformat(start).isoformat() != start
                    or date.fromisoformat(end).isoformat() != end or not types):
                return None
            return cls(start, end, None if types == "all" else frozenset(types.split(",")), limit)
        except (TypeError, ValueError):
            return None

    def includes(self, row):
        return (self.start <= row["datestr"] <= self.end
                and (self.types is None or row["type"] in self.types))

    def overlaps(self, other):
        return (self.start <= other.end and other.start <= self.end
                and (self.types is None or other.types is None or bool(self.types & other.types)))


def get_cached_body(cache, key: str) -> Optional[dict]:
    """Reconcile newer complete overlapping queries in a read-only display view."""
    read = getattr(cache, "get_with_mtime", None)
    versions = getattr(cache, "mtimes_by_prefix", None)
    if not callable(read) or not callable(versions):
        return cache.get(key)
    generation = cache.get_generation()
    cached, saved_at = read(key)
    return _reconcile_body_cache(cache, key, cached, saved_at, generation)


def _reconcile_body_cache(cache, key, cached, saved_at, generation):
    read = getattr(cache, "get_with_mtime", None)
    versions = getattr(cache, "mtimes_by_prefix", None)
    if not callable(read) or not callable(versions):
        return cached
    scope = _BodyScope.from_key(key)
    version = _cache_version(saved_at)
    if scope is None or version is None or not DataService.body_history_complete(cached, scope.limit):
        return cached
    overlays = []
    for candidate, timestamp in versions("body:").items():
        newer = _cache_version(timestamp)
        other = _BodyScope.from_key(candidate)
        if newer is None or newer <= version or other is None or not scope.overlaps(other):
            continue
        snapshot, timestamp = read(candidate)
        newer = _cache_version(timestamp)
        if (newer is not None and newer > version
                and DataService.body_history_complete(snapshot, other.limit)):
            overlays.append((newer, candidate, other, snapshot))
    if cache.get_generation() != generation:
        return None
    if not overlays:
        return cached
    records = [dict(row) for row in extract_body_records(cached) if scope.includes(row)]
    for _, _, other, snapshot in sorted(overlays, key=lambda item: item[:2]):
        records = [row for row in records if not other.includes(row)]
        records.extend(dict(row) for row in extract_body_records(snapshot)
                       if scope.includes(row) and other.includes(row))
    # Derived upstream fields (latest/by_type) belong to the original query.
    # Return one canonical record list; never persist a composed snapshot as fresh.
    return {"records": records, "_history_complete": True}


class DataService:
    def __init__(self, cache: Cache, api_client: XunjiAPIClient):
        self._cache = cache
        self._client = api_client
        self._training_attempts = {}
        self._training_requests: dict[tuple[str, Optional[int]], _ReadRequest] = {}
        self._body_requests: dict[tuple[str, Optional[int]], _ReadRequest] = {}
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

    def _current_cache_fallback(self, generation, read):
        """A failed request may reuse only a still-present snapshot in its generation."""
        if self._cache_generation() != generation:
            return None
        current = read()
        return current if self._cache_generation() == generation else None

    def _discard_invalid_cache(self, key, snapshot, generation):
        discard = getattr(self._cache, "delete_if_unchanged", None)
        if generation is not None and callable(discard):
            discard(key, snapshot, generation)
        else:
            self._cache.delete(key)

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
        return _valid_training_response(data)

    async def get_training(self, date: str, full: bool = False, *, force_refresh: bool = False) -> dict:
        if self._closing:
            raise RuntimeError("Data service is closed")
        cache_key = f"training:{date}:{full}"
        cached, cached_mode = resolve_training_cache(self._cache, date)
        needs_full = full and cached_mode is False and bool(extract_trains(cached))
        if self._is_valid_training_response(cached) and not force_refresh and not needs_full:
            return cached
        generation = self._cache_generation()
        return await self._shared_read(self._training_requests, (cache_key, generation),
            lambda: self._load_training(cache_key, date, full, cached, generation))

    async def _load_training(self, cache_key, date, full, cached, generation):
        now = monotonic()
        self._training_attempts = {key: started for key, started in self._training_attempts.items()
                                   if now - started < 30}
        attempt = (date, full)
        previous = self._training_attempts.get(attempt)
        cooldown = 30 if full else 15
        if cached is not None and previous is not None and now - previous < cooldown:
            current = self._current_cache_fallback(generation, lambda: get_cached_training(self._cache, date))
            if current is not None:
                return current
        exact = self._cache.get(cache_key)
        if exact is not None and not self._is_valid_training_response(exact) and not extract_trains(exact):
            self._discard_invalid_cache(cache_key, exact, generation)
        self._training_attempts[attempt] = now
        try:
            data = await self._client.fetch_training(date, full)
        except Exception:
            current = self._current_cache_fallback(generation, lambda: get_cached_training(self._cache, date))
            if current is not None:
                return current
            raise
        if self._is_valid_training_response(data):
            self._save_read_result(cache_key, data, generation)
            return data
        current = self._current_cache_fallback(generation, lambda: get_cached_training(self._cache, date))
        return current if current is not None else data

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
        if isinstance(data, dict) and data.get("_cache_fallback"):
            return False
        records = _response_records(data, "records", decode_json=True)
        return records is not None and (len(records) < limit or data.get("_history_complete") is True)

    def _body_snapshot_fallback(self, start, end, types, generation):
        """Use a compatible saved query only after failure; never promote it to fresh cache."""
        if self._cache_generation() != generation:
            return None
        keys = getattr(self._cache, "keys_by_prefix", None)
        if not callable(keys):
            return None
        requested_types = set(types) if types else None
        candidates = []
        for key in keys("body:"):
            scope = _BodyScope.from_key(key)
            lower = start or date.min.isoformat()
            if scope is None or scope.start > lower or scope.end < lower:
                continue
            if scope.types is not None and (requested_types is None or not requested_types.issubset(scope.types)):
                continue
            read = getattr(self._cache, "get_with_mtime", None)
            if callable(read):
                snapshot, saved_at = read(key)
            else:
                snapshot = self._cache.get(key)
                get_mtime = getattr(self._cache, "get_mtime", None)
                saved_at = get_mtime(key) if callable(get_mtime) else None
            if not self.body_history_complete(snapshot, scope.limit):
                continue
            version = _cache_version(saved_at) or datetime.min.replace(tzinfo=timezone.utc)
            candidates.append((version, scope.end, saved_at, snapshot, key))
        if not candidates or self._cache_generation() != generation:
            return None
        _, upper, saved_at, snapshot, key = max(candidates, key=lambda candidate: candidate[:2])
        snapshot = _reconcile_body_cache(self._cache, key, snapshot, saved_at, generation)
        if snapshot is None or self._cache_generation() != generation:
            return None
        records = [dict(row) for row in extract_body_records(snapshot)
                   if (start is None or row["datestr"] >= start) and row["datestr"] <= min(end, upper)
                   and (requested_types is None or row["type"] in requested_types)]
        return {"records": records, "_cache_fallback": {"saved_at": saved_at, "end_date": upper}}

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
        cached = get_cached_body(self._cache, cache_key)
        complete_cache = self.body_history_complete(cached, limit)
        if complete_cache and not force_refresh:
            return cached
        generation = self._cache_generation()
        request_key = (cache_key, generation)
        return await self._shared_read(self._body_requests, request_key,
            lambda: self._load_body_history(
                cache_key, start_date, end_date, types, limit, cached, generation))

    @staticmethod
    async def _shared_read(requests, request_key, load):
        """Share a read; only its final departing waiter may cancel the work."""
        pending = requests.get(request_key)
        if pending is None:
            pending = _ReadRequest(asyncio.create_task(load()))
            requests[request_key] = pending
        pending.waiters += 1
        try:
            return await asyncio.shield(pending.task)
        finally:
            pending.waiters -= 1
            if pending.waiters == 0:
                if requests.get(request_key) is pending:
                    del requests[request_key]
                if not pending.task.done():
                    pending.task.cancel()
                    await asyncio.gather(pending.task, return_exceptions=True)

    def _body_read_fallback(self, cache_key, start_date, end_date, types, limit, generation):
        current = self._current_cache_fallback(generation, lambda: get_cached_body(self._cache, cache_key))
        if self.body_history_complete(current, limit):
            return current
        return self._body_snapshot_fallback(start_date, end_date, types, generation)

    async def _load_body_history(self, cache_key, start_date, end_date, types, limit, cached, generation):
        if cached is not None and not self._is_valid_body_response(cached):
            self._discard_invalid_cache(cache_key, cached, generation)
        try:
            data = await self._fetch_body_history(start_date, end_date, types, limit)
        except Exception:
            fallback = self._body_read_fallback(cache_key, start_date, end_date, types, limit, generation)
            if fallback is not None:
                return fallback
            raise
        if self._is_valid_body_response(data):
            self._save_read_result(cache_key, data, generation)
            return data
        fallback = self._body_read_fallback(cache_key, start_date, end_date, types, limit, generation)
        return fallback if fallback is not None else data

    # ── Diet Search / Upsert ──

    async def search_food(self, keyword: str, limit: int = 8) -> dict:
        return await self._client.search_food(keyword, limit)

    async def upsert_diet(self, payload: dict) -> dict:
        return await self._client.upsert_diet(payload)

    async def upsert_custom_food(self, payload: dict) -> dict:
        return await self._client.upsert_custom_food(payload)

    async def close(self):
        self._closing = True
        tasks = [pending.task for requests in (self._training_requests, self._body_requests)
                 for pending in requests.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._body_requests.clear()
        self._training_requests.clear()
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
        all_dates = self.ah.get_latest_dates()
        today = date.today()

        # Per-metric freshness
        metrics = {}
        for key, ah_type, label in KEY_AH_METRICS:
            latest = all_dates.get(ah_type)
            days_ago, status = self._date_freshness(latest, today)

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
        ah_days_ago, ah_status = self._date_freshness(ah_overall, today, overall=True)

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
    def _date_freshness(latest, today, *, overall=False):
        if latest is None:
            return None, "none"
        try:
            parsed = date.fromisoformat(latest)
            if parsed.isoformat() != latest:
                raise ValueError("Noncanonical health date")
        except (TypeError, ValueError):
            return None, "unknown"
        days_ago = (today - parsed).days
        if days_ago < 0:
            return None, "future"
        if days_ago == 0:
            return 0, "fresh" if overall else "today"
        if days_ago <= 3:
            return days_ago, "fresh" if overall else "recent"
        return days_ago, "stale" if days_ago <= 7 else "expired"

    @staticmethod
    def _freshness_tag(status: str, days_ago: Optional[int]) -> str:
        if status == "unknown":
            return "[日期未确认]"
        if status == "future":
            return "[未来日期，待核对]"
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
