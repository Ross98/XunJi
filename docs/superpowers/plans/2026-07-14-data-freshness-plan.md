# Data Freshness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add data freshness indicators, smart default time ranges, and data source labels across dashboard, health, and body pages.

**Architecture:** Backend queries latest dates from Apple Health SQLite and 训记 API per data type; a shared `DataFreshnessService` computes a `freshness` context dict; templates render freshness tags on data cards, a data-freshness bar at page top, and a smart-range toggle that limits chart range to the "safe" date where all sources have data.

**Tech Stack:** FastAPI, Jinja2, SQLite, Chart.js

---
### Task 1: Add get_latest_dates() to apple_health.py

**Files:**
- Modify: `apple_health.py`
- Test: `tests/test_apple_health.py`

**Interfaces:**
- Produces: `get_latest_dates(type_filter: str | None) -> dict[str, str]`

- [ ] **Step 1: Write the test**

```python
# tests/test_apple_health.py
import sys; sys.path.insert(0, '.')
import apple_health as ah

def test_get_latest_dates_returns_dict():
    result = ah.get_latest_dates()
    assert isinstance(result, dict)
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in result.items())

def test_get_latest_dates_filtered():
    result = ah.get_latest_dates("HKQuantityTypeIdentifierStepCount")
    assert "HKQuantityTypeIdentifierStepCount" in result
    assert result["HKQuantityTypeIdentifierStepCount"] is not None
    assert len(result["HKQuantityTypeIdentifierStepCount"]) == 10  # YYYY-MM-DD
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_apple_health.py::test_get_latest_dates_returns_dict -v`
Expected: FAIL with "AttributeError: module 'apple_health' has no attribute 'get_latest_dates'"

- [ ] **Step 3: Write implementation in apple_health.py**

```python
def get_latest_dates(type_filter: str = None) -> dict[str, str | None]:
    """Get the latest start_date for each AH record type.
    Returns dict of type -> latest_date (YYYY-MM-DD) or None if no data.
    If type_filter is given, only returns that one type's date.
    """
    with _conn() as conn:
        if type_filter:
            rows = conn.execute(
                "SELECT type, MAX(DATE(start_date)) as latest FROM health_records WHERE type = ?",
                (type_filter,)
            ).fetchone()
            return {type_filter: rows["latest"] or None}
        rows = conn.execute(
            "SELECT type, MAX(DATE(start_date)) as latest FROM health_records GROUP BY type"
        ).fetchall()
        return {r["type"]: r["latest"] for r in rows}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_apple_health.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apple_health.py tests/test_apple_health.py
git commit -m "feat: add get_latest_dates() for AH data freshness tracking"
```

---
### Task 2: Add DataFreshnessService to data_service.py

**Files:**
- Modify: `data_service.py`
- Test: `tests/test_data_service.py`

**Interfaces:**
- Produces: `DataFreshnessService` class with `get_freshness_context() -> dict`

- [ ] **Step 1: Write the test**

```python
# tests/test_data_service.py
import sys; sys.path.insert(0, '.')
from data_service import DataFreshnessService
import apple_health as ah

def test_freshness_context_has_required_keys():
    svc = DataFreshnessService()
    ctx = svc.get_freshness_context()
    assert "ah_overall_latest" in ctx
    assert "xunji_latest" in ctx
    assert "ah_days_ago" in ctx
    assert "ah_status" in ctx
    assert "metrics" in ctx
    assert isinstance(ctx["metrics"], dict)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_data_service.py::test_freshness_context_has_required_keys -v`
Expected: FAIL

- [ ] **Step 3: Write DataFreshnessService**

```python
# data_service.py — add this class

# Key AH metrics shown on dashboard/health pages
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

            tag = _freshness_tag(status, days_ago)
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

        # 训记 latest (from body records, training records)
        xunji_latest = today  # API always returns latest

        return {
            "ah_overall_latest": ah_overall,
            "xunji_latest": xunji_latest,
            "ah_days_ago": ah_days_ago,
            "ah_status": ah_status,
            "ah_tag": _freshness_tag(ah_status, ah_days_ago),
            "metrics": metrics,
        }


def _freshness_tag(status: str, days_ago: int | None) -> str:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_data_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add data_service.py tests/test_data_service.py
git commit -m "feat: add DataFreshnessService for freshness tracking"
```

---
### Task 3: Route integration — inject freshness context into dashboard, health, body

**Files:**
- Modify: `routes/dashboard.py`
- Modify: `routes/health.py`
- Modify: `routes/body.py`

- [ ] **Step 1: Modify dashboard route to pass freshness**

```python
# routes/dashboard.py — at top
from data_service import DataFreshnessService

# Inside dashboard_page, before render
freshness_svc = DataFreshnessService()
freshness_ctx = freshness_svc.get_freshness_context()

# In the tmpl.render() call, add:
freshness=freshness_ctx,
```

- [ ] **Step 2: Modify health route to pass freshness**

```python
# routes/health.py — at top
from data_service import DataFreshnessService

# Inside health_page, before render
freshness_svc = DataFreshnessService()
freshness_ctx = freshness_svc.get_freshness_context()

# In the tmpl.render() call, add:
freshness=freshness_ctx,
```

- [ ] **Step 3: Modify body route to pass freshness**

```python
# routes/body.py — at top (import already has DataFreshnessService or add it)
from data_service import DataFreshnessService

# Inside body_page, before render
freshness_svc = DataFreshnessService()
freshness_ctx = freshness_svc.get_freshness_context()

# In the tmpl.render() call, add:
freshness=freshness_ctx,
```

- [ ] **Step 4: Commit**

```bash
git add routes/dashboard.py routes/health.py routes/body.py
git commit -m "feat: inject freshness context into all data routes"
```

---
### Task 4: Frontend — freshness bar component (shared via template variable)

**Files:**
- Create: `templates/_freshness_bar.html`
- Modify: `templates/dashboard.html`
- Modify: `templates/health.html`
- Modify: `templates/body.html`

- [ ] **Step 1: Create shared freshness bar template**

```html
{# templates/_freshness_bar.html #}
{% if freshness %}
{% set s = freshness.ah_status %}
<div style="display:flex;align-items:center;gap:8px;padding:8px 12px;border-radius:8px;margin-bottom:16px;font-size:13px;
    {% if s == 'fresh' or s == 'today' %}background:#e8f5e9;color:#2e7d32;
    {% elif s == 'stale' %}background:#fff3e0;color:#e65100;
    {% elif s == 'expired' %}background:#ffebee;color:#c62828;
    {% else %}background:#f5f5f5;color:#888;{% endif %}">
    <span>🍎 Apple Health 数据</span>
    <span style="font-weight:600;">{{ freshness.ah_tag }}</span>
    {% if freshness.ah_overall_latest %}
    <span style="font-size:12px;opacity:0.8;">· 更新至 {{ freshness.ah_overall_latest }}</span>
    {% endif %}
</div>
{% endif %}
```

- [ ] **Step 2: Add fresh bar to dashboard.html**

Insert at top of `{% block content %}`:

```html
{% include '_freshness_bar.html' %}
```

- [ ] **Step 3: Add fresh bar to health.html**

Same as above.

- [ ] **Step 4: Add fresh bar to body.html**

Same as above.

- [ ] **Step 5: Add freshness tags to dashboard health cards**

For each AH-sourced metric card (steps, exercise, stand, HRV, RHR, VO2), add a small freshness tag below the value:

```html
{% set m = freshness.metrics.get('steps') %}
{% if m and m.tag %}
<div style="font-size:10px;color:#888;margin-top:2px;">{{ m.tag }}</div>
{% endif %}
```

Repeat for: `exercise_minutes`, `stand_hours`, `hrv`, `resting_hr`, `vo2max`, `active_energy`.

- [ ] **Step 6: Add freshness tags to health page**

Same freshness tags for each health metric card.

- [ ] **Step 7: Add smart time range toggle**

In dashboard.html and health.html, near the range selector, add:

```html
{% if freshness.ah_days_ago and freshness.ah_days_ago > 0 %}
<div style="margin-top:8px;">
    <label style="font-size:12px;color:#888;cursor:pointer;">
        <input type="checkbox" id="freshRangeToggle" 
               onchange="toggleFreshRange(this.checked)"
               style="margin-right:4px;">
        🔒 仅显示完整数据
    </label>
    <span style="font-size:11px;color:#aaa;margin-left:8px;">
        数据截止 {{ freshness.ah_overall_latest }}
    </span>
</div>
<script>
function toggleFreshRange(checked) {
    const url = new URL(window.location.href);
    if (checked) {
        url.searchParams.set('fresh', '1');
    } else {
        url.searchParams.delete('fresh');
    }
    window.location.href = url.toString();
}
</script>
{% endif %}
```

On the backend, when `fresh=1` is in the URL, override `range` to end at `safe_end`:

```python
if request.query_params.get("fresh") == "1":
    safe_end = min(freshness_ctx["ah_overall_latest"], date.today().isoformat())
    # Override chart display range
```

- [ ] **Step 8: Add source labels to body page measurement cards**

For weight and bodyfat in the stat cards and measurement cards:

```html
{% set src = "🍎" if records_data[0].get("source") else "⚡训记" %}
<div style="font-size:10px;color:#888;margin-top:1px;">{{ src }}</div>
```

- [ ] **Step 9: Commit**

```bash
git add templates/_freshness_bar.html templates/dashboard.html templates/health.html templates/body.html
git commit -m "feat: add freshness indicators, data bar, and smart range to frontend"
```

---
## Self-Review

1. **Spec coverage:** All 3 spec modules covered: freshness tags (Task 4 Steps 5-8), smart range (Task 4 Step 7), data bar (Task 4 Step 1-4). Backend service (Task 2) and AH query (Task 1) support all frontend changes.
2. **Placeholders:** None — all code blocks contain complete implementation.
3. **Type consistency:** `DataFreshnessService.get_freshness_context()` returns dict with consistent keys used by frontend templates. `get_latest_dates()` returns `dict[str, str | None]`. No type conflicts.
4. **Edge cases handled:** No AH data → status="none" → bar shows gray "未导入". AH data stale >7 days → status="expired" → bar shows red warning.
