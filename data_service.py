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

    def __init__(self, ah_module=None):
        import apple_health as _ah
        self.ah = ah_module or _ah

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
        ah_dates = [v for v in all_dates.values() if v]
        ah_overall = max(ah_dates) if ah_dates else None
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

        xunji_latest = today

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
        if status == "today":
            return "✅ 今天"
        elif status == "recent":
            return f"🕐 {days_ago}天前"
        elif status == "stale":
            return f"⏳ {days_ago}天前"
        elif status == "expired":
            return f"⚠️ {days_ago}天未更新"
        else:
            return "📥 未导入"
