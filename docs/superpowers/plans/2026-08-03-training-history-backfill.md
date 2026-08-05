# Training History Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Incrementally cache the last 60 days of training history at a low request rate so movement tracking includes every recorded date in that window.

**Architecture:** `routes/training.py` owns one process-local asyncio task. It begins when `/training` is visited, scans today backwards, skips existing cache keys, and sends only one missing-date request every six seconds. Tracker code remains cache-based, so a refresh exposes newly fetched dates.

**Tech Stack:** Python 3.9, FastAPI, asyncio, SQLite cache, pytest.

## Global Constraints

- Scan at most 60 calendar days, including today.
- Strictly serialize requests and wait 6 seconds after every missing-date fetch.
- Skip dates with either `training:<date>:True` or `training:<date>:False` cache data.
- Failed date fetches must not fail the `/training` response or stop the scan.

---

### Task 1: Implement throttled backfill worker

**Files:**

- Modify: `routes/training.py:1-45`
- Create: `tests/test_training.py`

**Interfaces:**

- Consumes `cache.get(key)` and `await ds.get_training(date, full=True)`.
- Produces `async def _backfill_training_history(ds, cache, *, days: int = 60, delay_seconds: float = 6.0, today: date | None = None) -> None`.
- Produces `def _ensure_training_history_backfill(ds, cache) -> None`.

- [ ] **Step 1: Write failing worker test**

```python
import asyncio
from datetime import date

from routes.training import _backfill_training_history


class FakeCache:
    def __init__(self, keys):
        self.keys = keys

    def get(self, key):
        return {"res": {"trains": []}} if key in self.keys else None


class FakeDataService:
    def __init__(self):
        self.calls = []

    async def get_training(self, training_date, full=False):
        self.calls.append((training_date, full))
        return {"res": {"trains": []}}


def test_backfill_fetches_only_missing_dates_in_reverse_order():
    cache = FakeCache({"training:2026-08-02:True"})
    service = FakeDataService()

    asyncio.run(_backfill_training_history(
        service, cache, days=3, delay_seconds=0, today=date(2026, 8, 3)
    ))

    assert service.calls == [
        ("2026-08-03", True),
        ("2026-08-01", True),
    ]
```

- [ ] **Step 2: Verify red**

Run: `python3 -m pytest -q tests/test_training.py::test_backfill_fetches_only_missing_dates_in_reverse_order`

Expected: FAIL because `_backfill_training_history` is not defined.

- [ ] **Step 3: Add minimal worker and singleton task guard**

```python
_history_backfill_task = None


async def _backfill_training_history(ds, cache, *, days=60, delay_seconds=6.0, today=None):
    end_date = today or date.today()
    for offset in range(days):
        training_date = (end_date - timedelta(days=offset)).isoformat()
        if (cache.get(f"training:{training_date}:True") is not None
                or cache.get(f"training:{training_date}:False") is not None):
            continue
        try:
            await ds.get_training(training_date, full=True)
        except Exception:
            pass
        await asyncio.sleep(delay_seconds)


def _ensure_training_history_backfill(ds, cache):
    global _history_backfill_task
    if _history_backfill_task is None or _history_backfill_task.done():
        _history_backfill_task = asyncio.create_task(
            _backfill_training_history(ds, cache)
        )
```

- [ ] **Step 4: Verify green**

Run: `python3 -m pytest -q tests/test_training.py::test_backfill_fetches_only_missing_dates_in_reverse_order`

Expected: PASS.

- [ ] **Step 5: Commit task**

Run: `git add routes/training.py tests/test_training.py && git commit -m "feat(training): backfill recent history gradually"`

### Task 2: Start worker and verify tracker coverage

**Files:**

- Modify: `routes/training.py:218-241`
- Modify: `tests/test_training.py`

**Interfaces:**

- Consumes `_ensure_training_history_backfill(ds, cache) -> None`.
- Produces a `/training` response that launches no duplicate task and does not await backfill.

- [ ] **Step 1: Write failing singleton test**

```python
from unittest.mock import Mock

import routes.training as training


def test_ensure_backfill_does_not_create_second_live_task(monkeypatch):
    task = Mock()
    task.done.return_value = False
    monkeypatch.setattr(training, "_history_backfill_task", task)
    create_task = Mock()
    monkeypatch.setattr(training.asyncio, "create_task", create_task)

    training._ensure_training_history_backfill(Mock(), Mock())

    create_task.assert_not_called()
```

- [ ] **Step 2: Verify red**

Run: `python3 -m pytest -q tests/test_training.py::test_ensure_backfill_does_not_create_second_live_task`

Expected: FAIL until the singleton guard from Task 1 exists.

- [ ] **Step 3: Launch worker without awaiting it**

```python
@router.get("/training", response_class=HTMLResponse)
async def training_page(request: Request):
    ds = get_data_service()
    cache = get_cache()
    _ensure_training_history_backfill(ds, cache)
    today = date.today()
```

Use `cache` for later cache reads in `training_page`. Keep the existing 14-day warm-up and do not await the worker.

- [ ] **Step 4: Verify green and full regression suite**

Run: `python3 -m pytest -q tests/test_training.py && python3 -m pytest -q && git diff --check`

Expected: all tests pass; diff check produces no output.

- [ ] **Step 5: Commit task**

Run: `git add routes/training.py tests/test_training.py && git commit -m "feat(training): start history backfill in background"`
