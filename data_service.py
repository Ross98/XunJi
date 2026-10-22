from datetime import date, datetime, timezone
import json
import math
from typing import Optional

from api_client import XunjiAPIClient
from cache import Cache


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


def get_cached_training(cache, day: str, full: bool = False) -> Optional[dict]:
    """A newer populated variant can supersede an older empty cache entry."""
    key = f"training:{day}:{full}"
    other_key = f"training:{day}:{not full}"
    primary = cache.get(key)
    if extract_trains(primary):
        return primary
    other = cache.get(other_key)
    if _response_records(primary, "trains") is None:
        return other if _response_records(other, "trains") is not None else None
    if not extract_trains(other):
        return primary
    # Preserve a newer authoritative empty response (for example, a deletion).
    get_mtime = getattr(cache, "get_mtime", None)
    if get_mtime is not None:
        try:
            primary_time = datetime.fromisoformat(get_mtime(key))
            other_time = datetime.fromisoformat(get_mtime(other_key))
            if other_time > primary_time:
                return other
        except (TypeError, ValueError):
            pass
    return primary


class DataService:
    def __init__(self, cache: Cache, api_client: XunjiAPIClient):
        self._cache = cache
        self._client = api_client

    # ── Training ──

    def _mark_xunji_refresh(self) -> None:
        self._cache.set(
            "_xunji_refresh_time",
            {"time": datetime.now(timezone.utc).isoformat()},
        )

    @staticmethod
    def _is_valid_training_response(data: object) -> bool:
        return _response_records(data, "trains") is not None

    async def get_training(self, date: str, full: bool = False) -> dict:
        cache_key = f"training:{date}:{full}"
        cached = self._cache.get(cache_key)
        if self._is_valid_training_response(cached):
            return get_cached_training(self._cache, date, full)
        if cached is not None:
            self._cache.delete(cache_key)
        data = await self._client.fetch_training(date, full)
        if self._is_valid_training_response(data):
            self._cache.set(cache_key, data)
            self._mark_xunji_refresh()
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

    @staticmethod
    def _is_valid_body_response(data: object) -> bool:
        return _response_records(data, "records", decode_json=True) is not None

    async def get_body(
        self,
        start_date: str,
        end_date: str,
        types: Optional[list[str]] = None,
        limit: int = 500,
        force_refresh: bool = False,
    ) -> dict:
        type_str = ",".join(types) if types else "all"
        cache_key = f"body:{type_str}:{start_date}:{end_date}:{limit}"
        if not force_refresh:
            cached = self._cache.get(cache_key)
            if self._is_valid_body_response(cached):
                return cached
            if cached is not None:
                self._cache.delete(cache_key)
        data = await self._client.query_body(start_date, end_date, types, limit)
        if self._is_valid_body_response(data):
            self._cache.set(cache_key, data)
            self._mark_xunji_refresh()
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
