
# Xunji Analysis Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a lightweight web app that reads 训记 training/diet/body data via Open API, displays analysis dashboards with charts, and supports training plan generation.

**Architecture:** Python FastAPI backend with server-side Jinja2 templates, HTMX for page interactions, Chart.js for charts, SQLite for data cache. Single Docker container deployment.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, HTMX (CDN), Chart.js (CDN), SQLite, pandas, curl (for API calls)

## Global Constraints

- Python 3.11+ required (3.11-slim for Docker)
- FastAPI + uvicorn for server
- Jinja2 templates rendered server-side — no JS framework build step
- HTMX loaded from CDN for interactions (refresh, lazy-load)
- Chart.js loaded from CDN for charts
- API keys loaded from environment variables (`.env` / `~/.zshrc`)
- All API calls use `--compressed` / `Accept-Encoding: gzip` for gzip responses
- SQLite for local cache (read first, background refresh from API)
- pandas for all analysis/computation
- All routes mounted under FastAPI `app`
- Templates in `templates/` with Jinja2 `TemplateResponse`
- Static files in `static/`
- File paths are relative to project root `/Users/adam/Documents/XunJi/`

---

## File Structure

```
/Users/adam/Documents/XunJi/
├── app.py              # FastAPI app creation, lifespan, route mounting
├── config.py           # Env var loading
├── api_client.py       # HTTP client for all 3 skill APIs
├── cache.py            # SQLite cache operations
├── analysis.py         # Pandas analysis engine
├── planner.py          # Training plan generator
├── csv_importer.py     # Xunji CSV export parser
├── routes/
│   ├── __init__.py     # Router registration helper
│   ├── dashboard.py    # GET / dashboard
│   ├── training.py     # GET /training
│   ├── diet.py         # GET /diet
│   ├── body.py         # GET /body
│   ├── plan.py         # GET /plan, POST /plan
│   └── import_csv.py   # GET /import, POST /import
├── templates/
│   ├── base.html       # Layout shell (nav, HTMX, Chart.js CDN)
│   ├── dashboard.html
│   ├── training.html
│   ├── diet.html
│   ├── body.html
│   ├── plan.html
│   └── import_csv.html
├── static/
│   └── css/
│       └── app.css
├── requirements.txt
├── Dockerfile
└── tests/
    ├── conftest.py
    ├── test_cache.py
    ├── test_api_client.py
    └── test_analysis.py
```

## Task Breakdown

### Task 1: Project Scaffold + Config

**Files:**
- Create: `config.py`
- Create: `requirements.txt`
- Create: `Dockerfile`
- Create: `app.py`
- Create: `routes/__init__.py`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `config.py` exports `XunjiConfig` dataclass with all API keys and base URLs. `app.py` exports `app` (FastAPI instance) with lifespan. `routes/__init__.py` exports `register_routes(app)`.

- [ ] **Step 1: Create `config.py`**

```python
import os
from dataclasses import dataclass


@dataclass
class XunjiConfig:
    # Training API
    training_api_key: str
    training_base_url: str = "https://trains.xunjiapp.cn"

    # Diet API
    diet_api_key: str
    diet_search_api_key: str
    diet_base_url: str = "https://eatings.xunjiapp.cn"
    diet_search_base_url: str = "https://api.xunjiapp.cn"

    # Body API
    body_api_key: str
    body_base_url: str = "https://api.xunjiapp.cn"

    # App
    cache_path: str = "xunji_cache.sqlite"

    @classmethod
    def from_env(cls) -> "XunjiConfig":
        return cls(
            training_api_key=os.environ["XUNJI_API_KEY"],
            diet_api_key=os.environ["XUNJI_FOOD_API_KEY"],
            diet_search_api_key=os.environ["XUNJI_FOOD_SEARCH_API_KEY"],
            body_api_key=os.environ["XUNJI_BODY_API_KEY"],
        )
```

- [ ] **Step 2: Create `requirements.txt`**

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
jinja2>=3.1.0
httpx>=0.28.0
pandas>=2.2.0
aiofiles>=24.1.0
python-multipart>=0.0.9
python-dotenv>=1.0.0
```

- [ ] **Step 3: Create `app.py`**

```python
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader

from config import XunjiConfig
from routes import register_routes

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    auto_reload=True,
)

config = XunjiConfig.from_env()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    os.makedirs(STATIC_DIR / "css", exist_ok=True)
    yield
    # Shutdown


app = FastAPI(title="Xunji Analysis", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
register_routes(app)


def get_jinja_env():
    return jinja_env


def get_config():
    return config
```

- [ ] **Step 4: Create `routes/__init__.py`**

```python
from fastapi import FastAPI


def register_routes(app: FastAPI):
    from routes import (
        dashboard,
        training,
        diet,
        body,
        plan,
        import_csv,
    )
    app.include_router(dashboard.router)
    app.include_router(training.router)
    app.include_router(diet.router)
    app.include_router(body.router)
    app.include_router(plan.router)
    app.include_router(import_csv.router)
```

- [ ] **Step 5: Create `Dockerfile`**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 6: Verify app starts**

Run: `cd /Users/adam/Documents/XunJi && pip install -r requirements.txt -q && uvicorn app:app --port 8001 & sleep 2 && curl -s http://localhost:8001/docs | head -20`
Expected: Swagger UI HTML (FastAPI docs load)
Kill server: `kill %1 2>/dev/null`

- [ ] **Step 7: Commit**

```bash
cd /Users/adam/Documents/XunJi
git add config.py requirements.txt Dockerfile app.py routes/__init__.py
git commit -m "feat: project scaffold + config"
```

---

### Task 2: SQLite Cache Layer

**Files:**
- Create: `cache.py`
- Create: `tests/conftest.py`
- Create: `tests/test_cache.py`

**Interfaces:**
- Consumes: nothing standalone (imports sqlite3 only)
- Produces: `Cache` class with `get(key)` → `dict|None`, `set(key, data: dict)`, `get_mtime(key)` → `str|None`, `clear()`. Key format: `"training:2026-04-02"` / `"diet:2025-06-12:2026-09-12"` / `"body:weight:2026-01-01:2026-06-28"`.

- [ ] **Step 1: Create `tests/conftest.py`**

```python
import pytest
from cache import Cache


@pytest.fixture
def tmp_cache(tmp_path):
    db_path = tmp_path / "test_cache.sqlite"
    return Cache(str(db_path))
```

- [ ] **Step 2: Write tests for Cache**

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /Users/adam/Documents/XunJi && python -m pytest tests/test_cache.py -v 2>&1`
Expected: ImportError or ModuleNotFoundError (cache.py doesn't exist yet)

- [ ] **Step 4: Implement `cache.py`**

```python
import sqlite3
import json
from datetime import datetime, timezone
from typing import Optional


class Cache:
    def __init__(self, db_path: str = "xunji_cache.sqlite"):
        self._db_path = db_path
        self._init_db()

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    mtime TEXT NOT NULL
                )
            """)

    def _conn(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, key: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data FROM cache WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            return json.loads(row["data"])

    def set(self, key: str, data: dict):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, data, mtime) VALUES (?, ?, ?)",
                (key, json.dumps(data), datetime.now(timezone.utc).isoformat()),
            )

    def get_mtime(self, key: str) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT mtime FROM cache WHERE key = ?", (key,)
            ).fetchone()
            return row["mtime"] if row else None

    def clear(self):
        with self._conn() as conn:
            conn.execute("DELETE FROM cache")

    def keys_by_prefix(self, prefix: str) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT key FROM cache WHERE key LIKE ?", (prefix + "%",)
            ).fetchall()
            return [row["key"] for row in rows]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/adam/Documents/XunJi && python -m pytest tests/test_cache.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
cd /Users/adam/Documents/XunJi
git add cache.py tests/conftest.py tests/test_cache.py
git commit -m "feat: SQLite cache layer"
```

---

### Task 3: API Client Module

**Files:**
- Create: `api_client.py`
- Create: `tests/test_api_client.py`

**Interfaces:**
- Consumes: `config.py` → `XunjiConfig` (API keys and base URLs). `cache.py` → `Cache` instance.
- Produces: `XunjiAPIClient` class with methods `fetch_training(date: str, full: bool = False)` → `dict`, `upsert_training(data: dict)` → `dict`, `query_diet(start: str, end: str, detail: bool = True)` → `dict`, `search_food(keyword: str, limit: int = 8)` → `dict`, `upsert_diet(data: dict)` → `dict`, `query_body(start: str, end: str, types: list[str] | None = None)` → `dict`, `upsert_body(data: dict)` → `dict`.

- [ ] **Step 1: Write test file `tests/test_api_client.py`**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/adam/Documents/XunJi && python -m pytest tests/test_api_client.py -v`
Expected: ImportError

- [ ] **Step 3: Implement `api_client.py`**

```python
import json
from typing import Optional

import httpx

from config import XunjiConfig


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

    async def _post(self, url: str, headers: dict, payload: dict) -> dict:
        resp = await self._client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()

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

    # ── Body ──

    async def query_body(
        self,
        start_date: str,
        end_date: str,
        types: Optional[list[str]] = None,
        limit: int = 500,
    ) -> dict:
        url = build_body_url(self._config.body_base_url, "query")
        headers = make_headers(self._config.body_api_key)
        payload = {
            "start_date": start_date,
            "end_date": end_date,
            "include_latest": True,
            "include_records": True,
            "limit": limit,
            "offset": 0,
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/adam/Documents/XunJi && python -m pytest tests/test_api_client.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/adam/Documents/XunJi
git add api_client.py tests/test_api_client.py
git commit -m "feat: API client for all 3 skills"
```

---

### Task 4: Analysis Engine

**Files:**
- Create: `analysis.py`
- Create: `tests/test_analysis.py`

**Interfaces:**
- Consumes: nothing (expects raw dict data from API responses).
- Produces: Functions `summarize_training(trains: list) → dict` (returns total_volume, total_sets, movement_frequency, volume_by_date, top_movements), `summarize_diet(records: list) → dict` (returns daily_calories, macro_split, avg_protein, avg_carbs, avg_fat), `summarize_body(records: list) → dict` (returns weight_trend, bodyfat_trend, latest_by_type).

- [ ] **Step 1: Write test file `tests/test_analysis.py`**

```python
import pytest
import pandas as pd
from analysis import (
    summarize_training,
    summarize_diet,
    summarize_body,
    training_volume_by_date,
)


def test_training_volume_by_date_empty():
    result = training_volume_by_date([])
    assert result == {}


def test_training_volume_by_date_basic():
    trains = [
        {
            "datestr": "2026-04-02",
            "movements": [
                {
                    "name": "杠铃卧推",
                    "sets": [
                        {"weight": "60", "reps": "10"},
                        {"weight": "60", "reps": "8"},
                    ],
                }
            ],
        }
    ]
    result = training_volume_by_date(trains)
    assert "2026-04-02" in result
    # volume = (60*10 + 60*8) = 1080
    assert result["2026-04-02"] == 60 * 10 + 60 * 8


def test_summarize_body_empty():
    result = summarize_body([])
    assert result["weight_trend"] == []
    assert result["bodyfat_trend"] == []


def test_summarize_body_basic():
    records = [
        {"datestr": "2026-06-01", "type": "weight", "value": 75.0},
        {"datestr": "2026-06-07", "type": "weight", "value": 74.5},
        {"datestr": "2026-06-01", "type": "bodyfat", "value": 18.5},
    ]
    result = summarize_body(records)
    assert len(result["weight_trend"]) == 2
    assert result["weight_trend"][0]["value"] == 75.0
    assert len(result["bodyfat_trend"]) == 1


def test_summarize_diet_empty():
    result = summarize_diet([])
    assert result["daily_calories"] == {}


def test_summarize_diet_basic():
    records = [
        {
            "date": "2026-06-12",
            "meal_type": "lunch",
            "ntr": {"cal": 500, "protein": 30, "fat": 15, "carb": 50},
        },
        {
            "date": "2026-06-12",
            "meal_type": "dinner",
            "ntr": {"cal": 700, "protein": 40, "fat": 20, "carb": 60},
        },
    ]
    result = summarize_diet(records)
    assert "2026-06-12" in result["daily_calories"]
    assert result["daily_calories"]["2026-06-12"] == 1200
    assert result["macro_split"]["protein"] == 70.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/adam/Documents/XunJi && python -m pytest tests/test_analysis.py -v`
Expected: ImportError

- [ ] **Step 3: Implement `analysis.py`**

```python
from typing import Any

import pandas as pd


def training_volume_by_date(trains: list[dict]) -> dict[str, float]:
    """Calculate total training volume (weight × reps) grouped by date."""
    volumes: dict[str, float] = {}
    for train in trains:
        date = train.get("datestr", "")
        if not date:
            continue
        total = 0.0
        for mov in train.get("movements", []):
            for s in mov.get("sets", []):
                try:
                    w = float(s.get("weight", 0) or 0)
                    r = float(s.get("reps", 0) or 0)
                    total += w * r
                except (ValueError, TypeError):
                    pass
        volumes[date] = volumes.get(date, 0) + total
    return volumes


def training_movement_frequency(trains: list[dict]) -> dict[str, int]:
    """Count how many times each movement name appears."""
    freq: dict[str, int] = {}
    for train in trains:
        for mov in train.get("movements", []):
            name = mov.get("name", "")
            if name:
                freq[name] = freq.get(name, 0) + 1
    return freq


def training_set_count(trains: list[dict]) -> int:
    """Count total sets across all movements."""
    count = 0
    for train in trains:
        for mov in train.get("movements", []):
            count += len(mov.get("sets", []))
    return count


def summarize_training(trains: list[dict]) -> dict[str, Any]:
    volumes = training_volume_by_date(trains)
    return {
        "total_volume": sum(volumes.values()),
        "total_days": len(trains),
        "total_sets": training_set_count(trains),
        "volume_by_date": volumes,
        "movement_frequency": training_movement_frequency(trains),
    }


def summarize_diet(records: list[dict]) -> dict[str, Any]:
    daily_cal: dict[str, float] = {}
    daily_protein: dict[str, float] = {}
    daily_carbs: dict[str, float] = {}
    daily_fat: dict[str, float] = {}

    for rec in records:
        date = rec.get("date", "")
        ntr = rec.get("ntr", {})
        if not date:
            continue
        daily_cal[date] = daily_cal.get(date, 0) + float(ntr.get("cal", 0))
        daily_protein[date] = daily_protein.get(date, 0) + float(ntr.get("protein", 0))
        daily_carbs[date] = daily_carbs.get(date, 0) + float(ntr.get("carb", 0))
        daily_fat[date] = daily_fat.get(date, 0) + float(ntr.get("fat", 0))

    total_protein = sum(daily_protein.values())
    total_carbs = sum(daily_carbs.values())
    total_fat = sum(daily_fat.values())
    total_cal_all = sum(daily_cal.values())

    return {
        "daily_calories": daily_cal,
        "daily_protein": daily_protein,
        "daily_carbs": daily_carbs,
        "daily_fat": daily_fat,
        "macro_split": {
            "protein": round(total_protein, 1),
            "carbs": round(total_carbs, 1),
            "fat": round(total_fat, 1),
            "calories": round(total_cal_all, 1),
        },
        "avg_daily_calories": (
            round(total_cal_all / len(daily_cal), 1) if daily_cal else 0
        ),
    }


def summarize_body(records: list[dict]) -> dict[str, Any]:
    weight_trend = []
    bodyfat_trend = []
    circumference: dict[str, list] = {}

    for rec in records:
        entry = {"date": rec.get("datestr", ""), "value": rec.get("value")}
        t = rec.get("type", "")
        if t == "weight":
            weight_trend.append(entry)
        elif t == "bodyfat":
            bodyfat_trend.append(entry)
        else:
            circumference.setdefault(t, []).append(entry)

    return {
        "weight_trend": weight_trend,
        "bodyfat_trend": bodyfat_trend,
        "circumference": circumference,
    }


def body_latest(records: list[dict]) -> dict[str, Any]:
    """Return the latest value for each body metric type."""
    latest: dict[str, Any] = {}
    for rec in records:
        t = rec.get("type", "")
        if t and t not in latest:
            latest[t] = {"value": rec.get("value"), "date": rec.get("datestr", "")}
    return latest
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/adam/Documents/XunJi && python -m pytest tests/test_analysis.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/adam/Documents/XunJi
git add analysis.py tests/test_analysis.py
git commit -m "feat: analysis engine with pandas"
```

---

### Task 5: Base Template + CSS + Stub Routes

**Files:**
- Create: `templates/base.html`
- Create: `static/css/app.css`
- Create all route files (stubs with placeholder response)

**Interfaces:**
- Consumes: nothing (standalone template layer)
- Produces: Base template with nav bar, HTMX + Chart.js CDN links, CSS file. Each route file returns minimal rendered template.

- [ ] **Step 1: Create `templates/base.html`**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}训记分析{% endblock %}</title>
    <script src="https://unpkg.com/htmx.org@2.0.4"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
    <link rel="stylesheet" href="/static/css/app.css">
</head>
<body>
    <nav class="nav">
        <div class="nav-brand">训记分析</div>
        <ul class="nav-links">
            <li><a href="/" class="nav-link">总览</a></li>
            <li><a href="/training" class="nav-link">训练</a></li>
            <li><a href="/diet" class="nav-link">饮食</a></li>
            <li><a href="/body" class="nav-link">身体</a></li>
            <li><a href="/plan" class="nav-link">规划</a></li>
            <li><a href="/import" class="nav-link">导入</a></li>
        </ul>
    </nav>
    <main class="container">
        {% block content %}{% endblock %}
    </main>
</body>
</html>
```

- [ ] **Step 2: Create `static/css/app.css`**

```css
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; color: #333; }
.nav { background: #1a1a2e; color: #fff; padding: 0 24px; display: flex; align-items: center; height: 56px; }
.nav-brand { font-size: 18px; font-weight: 600; margin-right: 32px; }
.nav-links { list-style: none; display: flex; gap: 16px; }
.nav-link { color: #ccc; text-decoration: none; font-size: 14px; padding: 4px 8px; border-radius: 4px; }
.nav-link:hover { color: #fff; background: rgba(255,255,255,0.1); }
.container { max-width: 1200px; margin: 24px auto; padding: 0 16px; }
.card { background: #fff; border-radius: 8px; padding: 20px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.card-title { font-size: 16px; font-weight: 600; margin-bottom: 12px; color: #1a1a2e; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }
.stat { text-align: center; padding: 16px; }
.stat-value { font-size: 28px; font-weight: 700; color: #1a1a2e; }
.stat-label { font-size: 13px; color: #666; margin-top: 4px; }
.chart-container { position: relative; height: 300px; }
.refresh-bar { background: #e3f2fd; color: #1565c0; padding: 8px 16px; border-radius: 4px; margin-bottom: 16px; display: none; cursor: pointer; font-size: 14px; }
.btn { display: inline-block; padding: 8px 16px; border-radius: 4px; border: none; cursor: pointer; font-size: 14px; }
.btn-primary { background: #1a1a2e; color: #fff; }
.btn-primary:hover { background: #16213e; }
@media (max-width: 768px) { .grid-2, .grid-3 { grid-template-columns: 1fr; } }
```

- [ ] **Step 3: Create all route files with stubs**

Create `routes/dashboard.py`:
```python
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app import get_jinja_env

router = APIRouter(tags=["dashboard"])


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    env = get_jinja_env()
    tmpl = env.get_template("dashboard.html")
    return HTMLResponse(tmpl.render(request=request))
```

Create `routes/training.py`:
```python
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app import get_jinja_env

router = APIRouter(tags=["training"])


@router.get("/training", response_class=HTMLResponse)
async def training_page(request: Request):
    env = get_jinja_env()
    tmpl = env.get_template("training.html")
    return HTMLResponse(tmpl.render(request=request))
```

Create `routes/diet.py`:
```python
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app import get_jinja_env

router = APIRouter(tags=["diet"])


@router.get("/diet", response_class=HTMLResponse)
async def diet_page(request: Request):
    env = get_jinja_env()
    tmpl = env.get_template("diet.html")
    return HTMLResponse(tmpl.render(request=request))
```

Create `routes/body.py`:
```python
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app import get_jinja_env

router = APIRouter(tags=["body"])


@router.get("/body", response_class=HTMLResponse)
async def body_page(request: Request):
    env = get_jinja_env()
    tmpl = env.get_template("body.html")
    return HTMLResponse(tmpl.render(request=request))
```

Create `routes/plan.py`:
```python
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app import get_jinja_env

router = APIRouter(tags=["plan"])


@router.get("/plan", response_class=HTMLResponse)
async def plan_page(request: Request):
    env = get_jinja_env()
    tmpl = env.get_template("plan.html")
    return HTMLResponse(tmpl.render(request=request))
```

Create `routes/import_csv.py`:
```python
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app import get_jinja_env

router = APIRouter(tags=["import"])


@router.get("/import", response_class=HTMLResponse)
async def import_page(request: Request):
    env = get_jinja_env()
    tmpl = env.get_template("import_csv.html")
    return HTMLResponse(tmpl.render(request=request))
```

- [ ] **Step 4: Create stub page templates**

Each template extends `base.html` and overrides `content` block with `<h1>页面名</h1>`. Minimal, just to get the app rendering.

`templates/dashboard.html`:
```html
{% extends "base.html" %}
{% block title %}总览 - 训记分析{% endblock %}
{% block content %}
<div id="refresh-bar" class="refresh-bar" hx-get="/" hx-target="body" hx-swap="innerHTML">有新数据，点击刷新</div>
<h1>总览</h1>
<div class="grid-3">
    <div class="card"><div class="stat"><div class="stat-value">--</div><div class="stat-label">今日训练</div></div></div>
    <div class="card"><div class="stat"><div class="stat-value">--</div><div class="stat-label">本周训练天数</div></div></div>
    <div class="card"><div class="stat"><div class="stat-value">--</div><div class="stat-label">最新体重</div></div></div>
</div>
<div class="card">
    <div class="card-title">体重趋势</div>
    <div class="chart-container"><canvas id="weightChart"></canvas></div>
</div>
{% endblock %}
```

`templates/training.html`, `templates/diet.html`, `templates/body.html`, `templates/plan.html`, `templates/import_csv.html`: same pattern, `<h1>训练回顾</h1>` etc.

- [ ] **Step 5: Start app and verify pages render**

Run: `cd /Users/adam/Documents/XunJi && uvicorn app:app --port 8001 & sleep 2 && curl -s http://localhost:8001/ | grep -o '训记分析'`
Expected: output "训记分析" (base template renders)
Run: `curl -s http://localhost:8001/training | grep -o '训练回顾'`
Expected: "训练回顾"
Run: `curl -s http://localhost:8001/diet | grep -o '饮食'`
Expected: "饮食"
Kill server: `kill %1 2>/dev/null`

- [ ] **Step 6: Commit**

```bash
cd /Users/adam/Documents/XunJi
git add templates/ static/ routes/
git commit -m "feat: base template + CSS + route stubs"
```

---

### Task 6: Data Fetching & Cache Service Layer

**Files:**
- Modify: `app.py` (add startup data sync)
- Create: `services/` directory with data service

**Interfaces:**
- Consumes: `XunjiAPIClient` + `Cache` from earlier tasks
- Produces: `DataService` class that wraps client+cache: check cache first → return + background refresh → signal update available.

- [ ] **Step 1: Create services/data_service.py**

```python
import asyncio
from datetime import date, timedelta
from typing import Optional

from api_client import XunjiAPIClient
from cache import Cache


class DataService:
    def __init__(self, client: XunjiAPIClient, cache: Cache):
        self._client = client
        self._cache = cache
        self._update_available: set[str] = set()

    # ── Training ──

    async def get_training(self, datestr: str, full: bool = False) -> dict:
        key = f"training:{datestr}:{'full' if full else 'light'}"
        cached = self._cache.get(key)
        asyncio.create_task(self._bg_refresh_training(key, datestr, full))
        return cached or {"trains": [], "res": {"trains": []}}

    async def _bg_refresh_training(self, key: str, datestr: str, full: bool):
        try:
            result = await self._client.fetch_training(datestr, full)
            existing = self._cache.get(key)
            self._cache.set(key, result)
            if existing != result:
                self._update_available.add(key)
        except Exception:
            pass  # Silently fail background refresh

    # ── Diet ──

    async def get_diet(self, start_date: str, end_date: str) -> dict:
        key = f"diet:{start_date}:{end_date}"
        cached = self._cache.get(key)
        asyncio.create_task(self._bg_refresh_diet(key, start_date, end_date))
        return cached or {"records": [], "res": {"records": []}}

    async def _bg_refresh_diet(self, key: str, start_date: str, end_date: str):
        try:
            result = await self._client.query_diet(start_date, end_date)
            existing = self._cache.get(key)
            self._cache.set(key, result)
            if existing != result:
                self._update_available.add(key)
        except Exception:
            pass

    # ── Body ──

    async def get_body(self, start_date: str, end_date: str, types: Optional[list[str]] = None) -> dict:
        key = f"body:{start_date}:{end_date}:{','.join(types or [])}"
        cached = self._cache.get(key)
        asyncio.create_task(self._bg_refresh_body(key, start_date, end_date, types))
        return cached or {"records": [], "res": {"records": [], "latest": {}}}

    async def _bg_refresh_body(self, key: str, start_date: str, end_date: str, types: Optional[list[str]]):
        try:
            result = await self._client.query_body(start_date, end_date, types)
            existing = self._cache.get(key)
            self._cache.set(key, result)
            if existing != result:
                self._update_available.add(key)
        except Exception:
            pass

    # ── Update signal ──
    def check_updates(self) -> list[str]:
        updates = list(self._update_available)
        self._update_available.clear()
        return updates

    def clear_updates(self):
        self._update_available.clear()
```

- [ ] **Step 2: Initialize services in app.py lifespan**

Add to `app.py`:
```python
# Add imports
from api_client import XunjiAPIClient
from cache import Cache
from services.data_service import DataService

# Add globals
_cache: Optional[Cache] = None
_api_client: Optional[XunjiAPIClient] = None
_data_service: Optional[DataService] = None


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        _cache = Cache(config.cache_path)
    return _cache


def get_api_client() -> XunjiAPIClient:
    global _api_client
    if _api_client is None:
        _api_client = XunjiAPIClient(config)
    return _api_client


def get_data_service() -> DataService:
    global _data_service
    if _data_service is None:
        _data_service = DataService(get_api_client(), get_cache())
    return _data_service
```

- [ ] **Step 3: Verify app still starts**

Run: `cd /Users/adam/Documents/XunJi && python -c "from app import app; print('OK')"`
Expected: "OK"

- [ ] **Step 4: Commit**

```bash
cd /Users/adam/Documents/XunJi
mkdir -p services && git add services/ app.py
git commit -m "feat: data service layer with cache-first + bg refresh"
```

---

### Task 7: Dashboard Page (Full Implementation)

**Files:**
- Modify: `routes/dashboard.py` (add real data fetching)
- Modify: `templates/dashboard.html` (add real stats + charts)

**Interfaces:**
- Consumes: `get_data_service()`, `summarize_training()`, `summarize_body()`, `summarize_diet()`
- Produces: Full dashboard page with today/latest stats, weight trend chart.

- [ ] **Step 1: Update `routes/dashboard.py`**

```python
from datetime import date, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app import get_jinja_env, get_data_service
from analysis import summarize_training, summarize_body, body_latest

router = APIRouter(tags=["dashboard"])


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    ds = get_data_service()
    today = date.today().isoformat()
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    month_ago = (date.today() - timedelta(days=30)).isoformat()

    # Fetch data with cache-first + bg refresh
    training_data = await ds.get_training(today)
    body_data = await ds.get_body(month_ago, today, ["weight", "bodyfat"])

    # Extract records
    trains = training_data.get("res", {}).get("trains", [])
    body_records = body_data.get("res", {}).get("records", [])

    # Analyze
    summary = summarize_training(trains)
    body_summary = summarize_body(body_records)
    latest_body = body_latest(body_records)

    today_count = summary["total_days"]
    updates = ds.check_updates()

    env = get_jinja_env()
    tmpl = env.get_template("dashboard.html")
    return HTMLResponse(tmpl.render(
        request=request,
        today_count=today_count,
        total_sets=summary["total_sets"],
        latest_weight=latest_body.get("weight", {}).get("value", "--"),
        weight_trend=body_summary["weight_trend"],
        bodyfat_trend=body_summary["bodyfat_trend"],
        has_updates=len(updates) > 0,
    ))
```

- [ ] **Step 2: Update `templates/dashboard.html`**

```html
{% extends "base.html" %}
{% block title %}总览 - 训记分析{% endblock %}
{% block content %}
{% if has_updates %}
<div id="refresh-bar" class="refresh-bar" style="display:block" hx-get="/" hx-target="body" hx-swap="innerHTML">有新数据，点击刷新</div>
{% endif %}
<h1>总览</h1>
<div class="grid-3">
    <div class="card"><div class="stat"><div class="stat-value">{{ today_count }}</div><div class="stat-label">今日训练</div></div></div>
    <div class="card"><div class="stat"><div class="stat-value">{{ total_sets }}</div><div class="stat-label">今日总组数</div></div></div>
    <div class="card"><div class="stat"><div class="stat-value">{{ latest_weight }}</div><div class="stat-label">最新体重 (kg)</div></div></div>
</div>
<div class="card">
    <div class="card-title">体重趋势</div>
    <div class="chart-container">
        <canvas id="weightChart"></canvas>
    </div>
</div>
<script>
const weightData = {{ weight_trend | tojson | safe }};
if (weightData.length > 0) {
    new Chart(document.getElementById('weightChart'), {
        type: 'line',
        data: {
            labels: weightData.map(d => d.date),
            datasets: [{
                label: '体重 (kg)',
                data: weightData.map(d => d.value),
                borderColor: '#1a1a2e',
                tension: 0.3,
                fill: false
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: { y: { beginAtZero: false } }
        }
    });
}
</script>
{% endblock %}
```

- [ ] **Step 3: Start app and verify dashboard renders**

Run: `cd /Users/adam/Documents/XunJi && uvicorn app:app --port 8001 & sleep 2 && curl -s http://localhost:8001/ | grep -o '总览'`
Expected: "总览"
Kill server: `kill %1 2>/dev/null`

- [ ] **Step 4: Commit**

```bash
cd /Users/adam/Documents/XunJi
git add routes/dashboard.py templates/dashboard.html
git commit -m "feat: dashboard page with real data"
```

---

### Task 8: Training + Diet + Body Pages

**Files:**
- Modify: `routes/training.py`, `routes/diet.py`, `routes/body.py`
- Modify: `templates/training.html`, `templates/diet.html`, `templates/body.html`

- [ ] **Step 1: Update training route + template**

`routes/training.py`:
```python
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app import get_jinja_env, get_data_service
from analysis import summarize_training

router = APIRouter(tags=["training"])


@router.get("/training", response_class=HTMLResponse)
@router.get("/training/{datestr}", response_class=HTMLResponse)
async def training_page(request: Request, datestr: str = ""):
    ds = get_data_service()
    today = datestr or __import__("datetime").date.today().isoformat()
    training_data = await ds.get_training(today, full=True)
    trains = training_data.get("res", {}).get("trains", [])
    summary = summarize_training(trains)
    env = get_jinja_env()
    tmpl = env.get_template("training.html")
    return HTMLResponse(tmpl.render(
        request=request,
        date=today,
        trains=trains,
        summary=summary,
    ))
```

`templates/training.html`:
```html
{% extends "base.html" %}
{% block title %}训练回顾 - 训记分析{% endblock %}
{% block content %}
<h1>训练回顾</h1>
<div class="card">
    <form hx-get="/training" hx-target="body" hx-swap="innerHTML" style="display:flex;gap:8px;align-items:center;">
        <input type="date" name="datestr" value="{{ date }}" style="padding:6px;border:1px solid #ccc;border-radius:4px;">
        <button type="submit" class="btn btn-primary">查看</button>
    </form>
</div>
{% if trains %}
<div class="grid-3">
    <div class="card"><div class="stat"><div class="stat-value">{{ summary.total_days }}</div><div class="stat-label">训练数</div></div></div>
    <div class="card"><div class="stat"><div class="stat-value">{{ summary.total_sets }}</div><div class="stat-label">总组数</div></div></div>
    <div class="card"><div class="stat"><div class="stat-value">{{ "%.0f"|format(summary.total_volume) }}</div><div class="stat-label">总容量 (kg)</div></div></div>
</div>
<div class="card">
    <div class="card-title">训练详情</div>
    {% for train in trains %}
    <div style="margin-bottom:16px;padding:12px;background:#f9f9f9;border-radius:6px;">
        <strong>{{ train.title or "无标题" }}</strong>
        {% if train.note and train.note.text %}<span style="color:#666;font-size:13px;margin-left:8px;">{{ train.note.text }}</span>{% endif %}
        {% for mov in train.movements %}
        <div style="margin:8px 0 0 12px;font-size:14px;">
            <span style="font-weight:500;">{{ mov.name }}</span>
            {% if mov.difficulty %}<span style="color:#888;font-size:12px;">({{ mov.difficulty }})</span>{% endif %}
            <div style="color:#555;font-size:13px;margin-left:12px;">
            {% for s in mov.sets %}
                <span>{{ s.weight }}{{ s.unit }} × {{ s.reps }}次</span>
                {% if s.rpe %}<span style="color:#888;"> RPE {{ s.rpe }}</span>{% endif %}
                {% if not loop.last %} | {% endif %}
            {% endfor %}
            </div>
        </div>
        {% endfor %}
    </div>
    {% endfor %}
</div>
{% else %}
<div class="card"><p style="color:#888;">该日期无训练记录</p></div>
{% endif %}
{% endblock %}
```

- [ ] **Step 2: Update diet route + template**

`routes/diet.py`:
```python
from datetime import date, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app import get_jinja_env, get_data_service
from analysis import summarize_diet

router = APIRouter(tags=["diet"])


@router.get("/diet", response_class=HTMLResponse)
async def diet_page(request: Request):
    ds = get_data_service()
    today = date.today().isoformat()
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    diet_data = await ds.get_diet(week_ago, today)
    records = diet_data.get("res", {}).get("records", [])
    if not records:
        records = diet_data.get("records", [])
    summary = summarize_diet(records)
    env = get_jinja_env()
    tmpl = env.get_template("diet.html")
    return HTMLResponse(tmpl.render(
        request=request,
        records=records,
        summary=summary,
    ))
```

`templates/diet.html`:
```html
{% extends "base.html" %}
{% block title %}饮食记录 - 训记分析{% endblock %}
{% block content %}
<h1>饮食记录 <span style="font-size:14px;color:#888;font-weight:400;">（最近7天）</span></h1>
{% if summary.daily_calories %}
<div class="grid-2">
    <div class="card">
        <div class="card-title">每日热量</div>
        <div class="chart-container"><canvas id="calChart"></canvas></div>
    </div>
    <div class="card">
        <div class="card-title">营养概况</div>
        <div class="stat"><div class="stat-value">{{ "%.0f"|format(summary.avg_daily_calories) }}</div><div class="stat-label">日均热量 (kcal)</div></div>
        <div style="margin-top:12px;">
            <div>蛋白质: {{ "%.1f"|format(summary.macro_split.protein) }}g</div>
            <div>碳水: {{ "%.1f"|format(summary.macro_split.carbs) }}g</div>
            <div>脂肪: {{ "%.1f"|format(summary.macro_split.fat) }}g</div>
        </div>
    </div>
</div>
<script>
const calData = {{ summary.daily_calories | tojson | safe }};
const labels = Object.keys(calData);
const values = Object.values(calData);
if (labels.length > 0) {
    new Chart(document.getElementById('calChart'), {
        type: 'bar',
        data: { labels, datasets: [{ label: '热量 (kcal)', data: values, backgroundColor: '#4caf50' }] },
        options: { responsive: true, maintainAspectRatio: false }
    });
}
</script>
{% else %}
<div class="card"><p style="color:#888;">最近7天无饮食记录</p></div>
{% endif %}
{% endblock %}
```

- [ ] **Step 3: Update body route + template**

`routes/body.py`:
```python
from datetime import date, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app import get_jinja_env, get_data_service
from analysis import summarize_body, body_latest

router = APIRouter(tags=["body"])


@router.get("/body", response_class=HTMLResponse)
async def body_page(request: Request):
    ds = get_data_service()
    today = date.today().isoformat()
    three_months = (date.today() - timedelta(days=90)).isoformat()
    body_data = await ds.get_body(three_months, today)
    records = body_data.get("res", {}).get("records", [])
    if not records:
        records = body_data.get("records", [])
    summary = summarize_body(records)
    latest = body_latest(records)
    env = get_jinja_env()
    tmpl = env.get_template("body.html")
    return HTMLResponse(tmpl.render(
        request=request,
        weight_trend=summary["weight_trend"],
        bodyfat_trend=summary["bodyfat_trend"],
        latest=latest,
    ))
```

`templates/body.html`:
```html
{% extends "base.html" %}
{% block title %}身体数据 - 训记分析{% endblock %}
{% block content %}
<h1>身体数据</h1>
<div class="grid-2">
    <div class="card">
        <div class="card-title">体重趋势</div>
        <div class="chart-container"><canvas id="weightChart"></canvas></div>
    </div>
    <div class="card">
        <div class="card-title">体脂率趋势</div>
        <div class="chart-container"><canvas id="bfChart"></canvas></div>
    </div>
</div>
<div class="grid-3">
    <div class="card"><div class="stat"><div class="stat-value">{{ latest.get("weight",{}).get("value","--") }}</div><div class="stat-label">最新体重 (kg)</div></div></div>
    <div class="card"><div class="stat"><div class="stat-value">{{ latest.get("bodyfat",{}).get("value","--") }}</div><div class="stat-label">最新体脂率 (%)</div></div></div>
    <div class="card"><div class="stat"><div class="stat-value">{{ latest.get("weight",{}).get("date","--") }}</div><div class="stat-label">体重日期</div></div></div>
</div>
<script>
const wData = {{ weight_trend | tojson | safe }};
const bfData = {{ bodyfat_trend | tojson | safe }};
if (wData.length > 0) {
    new Chart(document.getElementById('weightChart'), {
        type: 'line',
        data: { labels: wData.map(d=>d.date), datasets: [{ label: '体重 (kg)', data: wData.map(d=>d.value), borderColor: '#1a1a2e', tension: 0.3, fill: false }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: false } } }
    });
}
if (bfData.length > 0) {
    new Chart(document.getElementById('bfChart'), {
        type: 'line',
        data: { labels: bfData.map(d=>d.date), datasets: [{ label: '体脂率 (%)', data: bfData.map(d=>d.value), borderColor: '#e53935', tension: 0.3, fill: false }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: false } } }
    });
}
</script>
{% endblock %}
```

- [ ] **Step 4: Verify all pages render**

Run: `cd /Users/adam/Documents/XunJi && uvicorn app:app --port 8001 & sleep 2 && curl -s http://localhost:8001/training | grep -q '训练回顾' && echo "training OK" && curl -s http://localhost:8001/diet | grep -q '饮食' && echo "diet OK" && curl -s http://localhost:8001/body | grep -q '身体' && echo "body OK"`
Expected: "training OK", "diet OK", "body OK"
Kill server

- [ ] **Step 5: Commit**

```bash
cd /Users/adam/Documents/XunJi
git add routes/training.py routes/diet.py routes/body.py templates/training.html templates/diet.html templates/body.html
git commit -m "feat: training, diet, body pages"
```

---

### Task 9: Plan Generator + Page

**Files:**
- Create: `planner.py`
- Modify: `routes/plan.py`
- Modify: `templates/plan.html`

- [ ] **Step 1: Implement `planner.py`**

```python
from typing import Any


def generate_plan(recent_trains: list[dict]) -> dict[str, Any]:
    """Generate a simple training plan suggestion based on recent history."""
    movements_seen: dict[str, int] = {}
    last_date = ""

    for train in recent_trains:
        if train.get("datestr", "") > last_date:
            last_date = train["datestr"]
        for mov in train.get("movements", []):
            name = mov.get("name", "")
            if name:
                movements_seen[name] = movements_seen.get(name, 0) + 1

    # Sort by frequency (least frequent = prioritize)
    sorted_movements = sorted(movements_seen.items(), key=lambda x: x[1])

    suggestion = {
        "based_on_last_date": last_date,
        "total_recent_days": len(recent_trains),
        "movements_used": len(sorted_movements),
        "suggested_focus": _suggest_focus(sorted_movements),
        "suggested_movements": [m[0] for m in sorted_movements[:6]],
    }
    return suggestion


def _suggest_focus(sorted_movements: list[tuple[str, int]]) -> str:
    """Suggest a training focus based on movement distribution."""
    push = {"卧推", "推胸", "飞鸟", "臂屈伸", "俯卧撑", "推举", "前平举", "侧平举"}
    pull = {"划船", "引体向上", "高位下拉", "面拉", "弯举", "硬拉"}
    legs = {"深蹲", "腿举", "腿屈伸", "腿弯举", "弓步", "臀推", "罗马尼亚硬拉"}

    push_count = sum(1 for m, _ in sorted_movements if any(p in m for p in push))
    pull_count = sum(1 for m, _ in sorted_movements if any(p in m for p in pull))
    legs_count = sum(1 for m, _ in sorted_movements if any(p in m for p in legs))

    least = min((push_count, "推力"), (pull_count, "拉力"), (legs_count, "腿部"), key=lambda x: x[0])
    if least[0] == 0:
        return f"建议优先训练{least[1]}，近期未涉及"
    return f"训练分布较均匀，可继续均衡发展"
```

- [ ] **Step 2: Write test for planner**

`tests/test_planner.py`:
```python
import pytest
from planner import generate_plan, _suggest_focus


def test_generate_plan_empty():
    result = generate_plan([])
    assert result["total_recent_days"] == 0
    assert result["suggested_movements"] == []


def test_generate_plan_basic():
    trains = [
        {"datestr": "2026-04-02", "movements": [{"name": "杠铃卧推"}, {"name": "哑铃飞鸟"}]},
        {"datestr": "2026-04-04", "movements": [{"name": "深蹲"}, {"name": "腿举"}]},
    ]
    result = generate_plan(trains)
    assert result["total_recent_days"] == 2
    assert result["movements_used"] == 4
    assert "哑铃飞鸟" in result["suggested_movements"]


def test_suggest_focus():
    # Only push movements
    result = _suggest_focus([("杠铃卧推", 3), ("哑铃飞鸟", 2)])
    assert "拉力" in result or "腿部" in result
```

- [ ] **Step 3: Run planner tests**

Run: `cd /Users/adam/Documents/XunJi && python -m pytest tests/test_planner.py -v`
Expected: 3 passed

- [ ] **Step 4: Update route + template**

`routes/plan.py`:
```python
from datetime import date, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app import get_jinja_env, get_data_service
from analysis import summarize_training
from planner import generate_plan

router = APIRouter(tags=["plan"])


@router.get("/plan", response_class=HTMLResponse)
@router.get("/plan/{days}", response_class=HTMLResponse)
async def plan_page(request: Request, days: int = 14):
    ds = get_data_service()
    today = date.today().isoformat()
    start = (date.today() - timedelta(days=days)).isoformat()

    # Fetch recent training data
    recent_trains = []
    d = (date.today() - timedelta(days=days))
    while d <= date.today():
        ds_str = d.isoformat()
        data = await ds.get_training(ds_str)
        trains = data.get("res", {}).get("trains", [])
        recent_trains.extend(trains)
        d += timedelta(days=1)

    plan = generate_plan(recent_trains)
    summary = summarize_training(recent_trains)

    env = get_jinja_env()
    tmpl = env.get_template("plan.html")
    return HTMLResponse(tmpl.render(
        request=request,
        plan=plan,
        summary=summary,
        days=days,
    ))
```

`templates/plan.html`:
```html
{% extends "base.html" %}
{% block title %}训练规划 - 训记分析{% endblock %}
{% block content %}
<h1>训练规划</h1>
<div class="card">
    <div class="card-title">基于最近 {{ days }} 天的训练分析</div>
    <p>训练天数: {{ summary.total_days }} | 总组数: {{ summary.total_sets }} | 总容量: {{ "%.0f"|format(summary.total_volume) }} kg</p>
    <p style="margin-top:8px;font-weight:500;">{{ plan.suggested_focus }}</p>
</div>
{% if plan.suggested_movements %}
<div class="card">
    <div class="card-title">建议包含的动作</div>
    <ul style="padding-left:20px;">
    {% for m in plan.suggested_movements %}
        <li style="margin:4px 0;">{{ m }}</li>
    {% endfor %}
    </ul>
</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Verify plan page renders**

Run: `cd /Users/adam/Documents/XunJi && uvicorn app:app --port 8001 & sleep 2 && curl -s http://localhost:8001/plan | grep -q '训练规划' && echo "plan OK"`
Kill server

- [ ] **Step 6: Commit**

```bash
cd /Users/adam/Documents/XunJi
git add planner.py tests/test_planner.py routes/plan.py templates/plan.html
git commit -m "feat: training plan generator + page"
```

---

### Task 10: CSV Import Page

**Files:**
- Create: `csv_importer.py`
- Modify: `routes/import_csv.py`
- Modify: `templates/import_csv.html`

- [ ] **Step 1: Implement `csv_importer.py`**

```python
import csv
import io
from typing import Any


def parse_xunji_csv(content: str) -> list[dict]:
    """Parse a 训记 exported CSV into structured records.
    Expected format: rows with columns like date, exercise, set, weight, reps, etc.
    """
    reader = csv.DictReader(io.StringIO(content))
    records = []
    for row in reader:
        records.append(dict(row))
    return records


def csv_to_training_payload(records: list[dict]) -> dict[str, Any]:
    """Convert CSV records to a training upsert payload structure.
    Groups rows by date and movement name.
    """
    from collections import defaultdict

    by_date: dict[str, dict] = defaultdict(lambda: {"movements": defaultdict(list)})

    for rec in records:
        datestr = rec.get("date", rec.get("Date", ""))
        movement = rec.get("exercise", rec.get("movement", rec.get("Movement", "")))
        if not datestr or not movement:
            continue

        set_data = {
            "weight": rec.get("weight", rec.get("Weight", "0")),
            "reps": rec.get("reps", rec.get("Reps", "0")),
            "done": True,
        }
        unit = rec.get("unit", rec.get("Unit", "kg"))
        if unit:
            set_data["unit"] = unit

        by_date[datestr]["movements"][movement].append(set_data)

    trains = []
    for datestr, data in by_date.items():
        movements = []
        for name, sets in data["movements"].items():
            movements.append({"name": name, "sets": sets})
        trains.append({"datestr": datestr, "movements": movements})

    return {"trains": trains}


def preview_csv(content: str) -> dict[str, Any]:
    """Return a summary preview of CSV content for user confirmation."""
    records = parse_xunji_csv(content)
    payload = csv_to_training_payload(records)
    trains = payload.get("trains", [])
    total_sets = sum(len(m["sets"]) for t in trains for m in t["movements"])
    return {
        "total_records": len(records),
        "total_days": len(trains),
        "total_sets": total_sets,
        "dates": [t["datestr"] for t in trains],
        "movements": list(set(m["name"] for t in trains for m in t["movements"])),
    }
```

- [ ] **Step 2: Update import route**

`routes/import_csv.py`:
```python
from fastapi import APIRouter, Request, UploadFile, Form
from fastapi.responses import HTMLResponse
from app import get_jinja_env, get_data_service
from csv_importer import preview_csv, csv_to_training_payload

router = APIRouter(tags=["import"])


@router.get("/import", response_class=HTMLResponse)
async def import_page(request: Request):
    env = get_jinja_env()
    tmpl = env.get_template("import_csv.html")
    return HTMLResponse(tmpl.render(request=request, preview=None))


@router.post("/import/preview", response_class=HTMLResponse)
async def import_preview(request: Request, csv_file: UploadFile):
    content = (await csv_file.read()).decode("utf-8")
    preview = preview_csv(content)
    env = get_jinja_env()
    tmpl = env.get_template("import_csv.html")
    return HTMLResponse(tmpl.render(
        request=request, preview=preview, csv_content=content
    ))


@router.post("/import/confirm", response_class=HTMLResponse)
async def import_confirm(request: Request, csv_content: str = Form(...)):
    payload = csv_to_training_payload(
        __import__("csv_importer").parse_xunji_csv(csv_content)
    )
    ds = get_data_service()
    client = __import__("app").get_api_client()
    result = await client.upsert_training({
        **payload,
        "schema_version": "train_open_api_v2",
        "client_request_id": f"csv-import-{__import__('time').time()}",
        "dry_run": False,
    })
    env = get_jinja_env()
    tmpl = env.get_template("import_csv.html")
    return HTMLResponse(tmpl.render(
        request=request, result="导入成功" if result.get("success") else f"导入失败: {result}"
    ))
```

- [ ] **Step 3: Update template**

`templates/import_csv.html`:
```html
{% extends "base.html" %}
{% block title %}CSV 导入 - 训记分析{% endblock %}
{% block content %}
<h1>CSV 导入</h1>
{% if result %}
<div class="card"><p>{{ result }}</p></div>
{% elif preview %}
<div class="card">
    <div class="card-title">导入预览</div>
    <p>解析到 {{ preview.total_records }} 行数据</p>
    <p>涉及 {{ preview.total_days }} 天，{{ preview.total_sets }} 组</p>
    <p>日期: {{ preview.dates | join(", ") }}</p>
    <p>动作: {{ preview.movements | join(", ") }}</p>
    <form hx-post="/import/confirm" hx-target="body" hx-swap="innerHTML">
        <input type="hidden" name="csv_content" value="{{ csv_content }}">
        <button type="submit" class="btn btn-primary" style="margin-top:12px;">确认导入</button>
    </form>
</div>
{% else %}
<div class="card">
    <div class="card-title">上传训记导出的 CSV</div>
    <form hx-post="/import/preview" hx-target="body" hx-swap="innerHTML" enctype="multipart/form-data">
        <input type="file" name="csv_file" accept=".csv" style="margin-bottom:12px;display:block;">
        <button type="submit" class="btn btn-primary">预览</button>
    </form>
</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Verify**

Run: `cd /Users/adam/Documents/XunJi && uvicorn app:app --port 8001 & sleep 2 && curl -s http://localhost:8001/import | grep -q 'CSV' && echo "import OK"`
Kill server

- [ ] **Step 5: Commit**

```bash
cd /Users/adam/Documents/XunJi
git add csv_importer.py routes/import_csv.py templates/import_csv.html
git commit -m "feat: CSV import with preview and confirm flow"
```

---

### Task 11: Polish + Final Integration

**Files:**
- Modify: `app.py` (final touches, CORS if needed)
- Modify: `requirements.txt` (add missing deps)

- [ ] **Step 1: Finalize imports and ensure everything works together**

Run: `cd /Users/adam/Documents/XunJi && python -c "from app import app; from api_client import XunjiAPIClient; from cache import Cache; from analysis import summarize_training; from planner import generate_plan; from csv_importer import parse_xunji_csv; print('All imports OK')"`
Expected: "All imports OK"

- [ ] **Step 2: Run all tests**

Run: `cd /Users/adam/Documents/XunJi && python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 3: Final commit**

```bash
cd /Users/adam/Documents/XunJi
git add -A
git commit -m "feat: final integration and polish"
```
