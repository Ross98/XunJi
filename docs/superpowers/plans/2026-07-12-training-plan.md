# Training Plan Recommendation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the existing placeholder plan page with a data-driven recommendation that shows which muscle group is most under-trained, which specific movements to prioritize, and recovery context from Apple Health.

**Architecture:** Local computation over last-30-day training data + Apple Health SQLite queries. No new API endpoints. Plan route loads from cache, computes deficit scores, renders priority UI.

**Tech Stack:** Python 3.9, Jinja2, FastAPI, existing Cache/Apple Health modules.

**Global Constraints**
- Use existing `classify_bodypart()` in `analysis.py` for movement→group mapping (already tested)
- Keep planner.py as a pure computation module (no I/O, no HTTP)
- Recovery data reuses Apple Health sleep/HRV fields already available
- No write-back to 训记 API
- All times in Beijing timezone (UTC+8)

---

### Task 1: Rewrite planner.py — Movement Classification & Deficit Scoring

**Files:**
- Modify: `planner.py` (complete rewrite of `generate_plan` and helpers)
- Test: `tests/test_planner.py`

**Interfaces:**
- Consumes: list of train dicts from 训记 API (same schema as `data_service.get_training`)
- Produces: `generate_plan(trains: list[dict]) → dict` with keys:
  - `groups`: list of `{name, count, days_since, sets, deficit}` per muscle group (sorted by deficit desc)
  - `focus_group`: `{name, deficit}` — the most under-trained group
  - `suggested_movements`: list of `{name, group, last_date, priority}` top recommendations
  - `recovery`: dict passed through from Apple Health (or None)

- [ ] **Step 1: Create test file with movement classification tests**

```python
# tests/test_planner.py
from planner import generate_plan, _classify_movement, _group_deficits

def test_classify_push():
    """推类动作正确归类"""
    assert _classify_movement("卧推") == "push"
    assert _classify_movement("哑铃飞鸟") == "push"
    assert _classify_movement("侧平举") == "push"

def test_classify_pull():
    assert _classify_movement("划船") == "pull"
    assert _classify_movement("哑铃弯举") == "pull"

def test_classify_legs():
    assert _classify_movement("深蹲") == "legs"
    assert _classify_movement("哑铃直腿硬拉") == "legs"

def test_classify_core():
    assert _classify_movement("俄罗斯转体") == "core"
    assert _classify_movement("平躺曲腿旋转") == "core"

def test_classify_cardio():
    assert _classify_movement("步行") == "cardio"
    assert _classify_movement("跑步") == "cardio"

def test_classify_unknown():
    assert _classify_movement("XYZ不存在") == "other"

def test_deficit_empty_trains():
    result = generate_plan([])
    assert result["focus_group"]["name"] == "full_body"
    assert len(result["suggested_movements"]) == 0

def test_deficit_single_group():
    """只有推类训练 → 其他三组都缺"""
    trains = [{
        "datestr": "2026-07-10",
        "movements": [{"name": "卧推", "sets": [{"done": True}, {"done": True}]}]
    }]
    result = generate_plan(trains)
    groups = {g["name"]: g for g in result["groups"]}
    assert groups["push"]["count"] == 1
    assert groups["legs"]["deficit"] > groups["push"]["deficit"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/xinyi/Documents/XunJi && python3 -m pytest tests/test_planner.py -v 2>&1
# Expected: FAIL (no module / no function defined)
```

- [ ] **Step 3: Write planner.py implementation**

```python
"""Training plan recommendation engine — pure computation, no I/O."""

from typing import Any
from collections import defaultdict
from analysis import classify_bodypart


def _classify_movement(name: str) -> str:
    """Map movement name to muscle group using classify_bodypart."""
    cat = classify_bodypart(name)
    if cat == "其他":
        # Fall back to keyword matching for known exercise patterns
        push_kw = {"推", "飞鸟", "臂屈伸", "俯卧撑", "前平举", "侧平举", "三头"}
        pull_kw = {"划船", "引体", "下拉", "面拉", "弯举", "二头", "划船", "耸肩"}
        legs_kw = {"深蹲", "腿举", "腿屈伸", "腿弯举", "弓步", "臀推", "硬拉", "提踵", "哈克"}
        core_kw = {"卷腹", "平板", "仰卧起坐", "举腿", "转体", "登山者", "熊爬"}
        cardio_kw = {"步行", "跑步", "骑行", "游泳", "椭圆机", "HIIT", "跳绳", "有氧", "行走"}
        for kw_set, group in [(push_kw, "push"), (pull_kw, "pull"), (legs_kw, "legs"),
                              (core_kw, "core"), (cardio_kw, "cardio")]:
            if any(k in name for k in kw_set):
                return group
        return "other"
    # Map analysis.py category to our group
    mapping = {"胸部": "push", "肩部": "push", "手臂": "push",
               "背部": "pull",
               "腿部": "legs", "臀部": "legs",
               "腹部": "core", "核心": "core",
               "有氧": "cardio"}
    return mapping.get(cat, "other")


def generate_plan(trains: list[dict]) -> dict[str, Any]:
    """Generate training deficit analysis from recent training records."""
    if not trains:
        return {
            "groups": [],
            "focus_group": {"name": "full_body", "deficit": 0},
            "suggested_movements": [],
        }

    # Per-group stats
    groups = {
        "push": {"name": "推力", "count": 0, "sets": 0, "last_date": "", "days_since": 0},
        "pull": {"name": "拉力", "count": 0, "sets": 0, "last_date": "", "days_since": 0},
        "legs": {"name": "腿部", "count": 0, "sets": 0, "last_date": "", "days_since": 0},
        "core": {"name": "核心", "count": 0, "sets": 0, "last_date": "", "days_since": 0},
        "cardio": {"name": "有氧", "count": 0, "sets": 0, "last_date": "", "days_since": 0},
    }

    # Per-movement tracking for recommendations
    mov_tracker: dict[str, dict] = {}
    today = max(t.get("datestr", "") for t in trains) if trains else ""

    for t in trains:
        d = t.get("datestr", "")
        for m in t.get("movements", []):
            name = m.get("name", "")
            if not name:
                continue
            g = _classify_movement(name)
            if g == "other":
                continue
            groups[g]["count"] += 1
            if d > groups[g]["last_date"]:
                groups[g]["last_date"] = d
            n_sets = sum(1 for s in m.get("sets", [])
                         if s.get("done") or not isinstance(s, dict) or True)
            # Count items in supersets
            for s in m.get("sets", []):
                if isinstance(s, dict):
                    if "items" in s:
                        groups[g]["sets"] += len(s["items"])
                    else:
                        groups[g]["sets"] += 1

            if name not in mov_tracker:
                mov_tracker[name] = {"group": g, "last_date": d, "count": 0}
            if d > mov_tracker[name]["last_date"]:
                mov_tracker[name]["last_date"] = d
            mov_tracker[name]["count"] += 1

    # Compute deficit score
    max_count = max(g["count"] for g in groups.values()) or 1
    from datetime import date
    for g in groups.values():
        if g["last_date"]:
            g["days_since"] = (date.fromisoformat(today) - date.fromisoformat(g["last_date"])).days
        else:
            g["days_since"] = 99
        # deficit = (1 - freq_ratio) × days_since
        freq_ratio = g["count"] / max_count
        g["deficit"] = round((1 - freq_ratio) * g["days_since"] + (1 - freq_ratio) * 20, 1)

    sorted_groups = sorted(groups.values(), key=lambda x: -x["deficit"])
    focus = sorted_groups[0]

    # Recommend movements for the focus group
    suggested = []
    for name, info in sorted(mov_tracker.items(), key=lambda x: -x[1]["count"]):
        if info["group"] == focus["name"]:
            last = info["last_date"]
            if last:
                days_ago = (date.fromisoformat(today) - date.fromisoformat(last)).days
            else:
                days_ago = 99
            if days_ago >= 3:
                suggested.append({
                    "name": name, "group": focus["name"],
                    "last_date": last, "days_ago": days_ago,
                })
    suggested = sorted(suggested, key=lambda x: -x["days_ago"])[:5]

    return {
        "groups": [{"key": k, **v} for k, v in groups.items()],
        "focus_group": {"key": list(groups.keys())[list(groups.values()).index(focus)], **focus},
        "suggested_movements": suggested,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/xinyi/Documents/XunJi && python3 -m pytest tests/test_planner.py -v 2>&1
# Expected: PASS (all tests)
```

- [ ] **Step 5: Commit**

```bash
cd /Users/xinyi/Documents/XunJi && git add planner.py tests/test_planner.py && git commit -m "feat: rewrite planner with movement classification and deficit scoring"
```

---

### Task 2: Update plan route to pass recovery context

**Files:**
- Modify: `routes/plan.py`

**Interfaces:**
- Consumes: `generate_plan(trains)`, Apple Health `get_sleep_summary()`, dashboard `recovery` block
- Produces: `/plan` route that renders template with `plan` + `summary` + `recovery` vars

- [ ] **Step 1: Update plan route**

```python
# routes/plan.py
from datetime import date, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app_state import get_jinja_env, get_data_service, get_cache
from analysis import summarize_training
from planner import generate_plan
from movements_cn import apply_movement_cn_to_trains

router = APIRouter(tags=["plan"])


@router.get("/plan", response_class=HTMLResponse)
async def plan_page(request: Request):
    ds = get_data_service()
    today = date.today().isoformat()

    # Fetch last 30 days of training from cache
    cache = get_cache()
    recent_trains = []
    for i in range(30):
        d = (date.today() - timedelta(days=i)).isoformat()
        cached = cache.get(f"training:{d}:False") or cache.get(f"training:{d}:True")
        if cached:
            trains = cached.get("res", {}).get("trains", []) or cached.get("trains", [])
            recent_trains.extend(trains)

    recent_trains = apply_movement_cn_to_trains(recent_trains)

    plan = generate_plan(recent_trains)
    summary = summarize_training(recent_trains)

    # Apple Health recovery context
    recovery = {}
    try:
        import apple_health as ah
        if ah.HEALTH_DB_PATH.exists():
            start = (date.today() - timedelta(days=30)).isoformat()
            sleep_data = ah.get_sleep_summary(start, today)
            if sleep_data:
                sl = sleep_data[0]
                st = sl.get("AsleepUnspecified", 0) + sl.get("AsleepCore", 0) + sl.get("AsleepREM", 0) + sl.get("AsleepDeep", 0)
                recovery["sleep_hours"] = round(st / 60, 1)
                recovery["sleep_deep_pct"] = round(sl.get("AsleepDeep", 0) / st * 100, 0) if st else 0
    except Exception:
        pass

    env = get_jinja_env()
    tmpl = env.get_template("plan.html")
    return HTMLResponse(tmpl.render(
        request=request,
        plan=plan,
        summary=summary,
        recovery=recovery,
        days=30,
    ))
```

- [ ] **Step 2: Commit**

```bash
cd /Users/xinyi/Documents/XunJi && git add routes/plan.py && git commit -m "feat: update plan route with Apple Health recovery context"
```

---

### Task 3: Redesign plan template

**Files:**
- Modify: `templates/plan.html`

- [ ] **Step 1: Write template**

```html
{% extends "base.html" %}
{% block title %}训练规划 - 训记分析{% endblock %}
{% block content %}

<h1>训练规划</h1>

{% if plan.groups %}

<!-- 今日训练建议 -->
<div class="card">
    <div class="card-title">今日训练建议</div>
    <div style="display:flex;align-items:flex-start;gap:12px;">
        <div style="font-size:48px;line-height:1;">
            {% if plan.focus_group.key == 'legs' %}🦵
            {% elif plan.focus_group.key == 'push' %}💪
            {% elif plan.focus_group.key == 'pull' %}🏋️
            {% elif plan.focus_group.key == 'core' %}🧘
            {% elif plan.focus_group.key == 'cardio' %}🏃
            {% elif plan.focus_group.key == 'full_body' %}🔄
            {% else %}🏋️{% endif %}
        </div>
        <div style="flex:1;">
            <p style="font-size:16px;font-weight:600;margin-bottom:4px;">
                {% if plan.focus_group.key == 'full_body' %}
                训练均衡，继续保持
                {% else %}
                <strong style="color:#e53935;">{{ plan.focus_group.name }}</strong> 训练不足
                {% endif %}
            </p>
            <p style="font-size:13px;color:#888;margin-bottom:8px;">
                {% if plan.focus_group.key != 'full_body' %}
                最近 {{ plan.focus_group.count }} 次训练 · {{ plan.focus_group.days_since }} 天未专项训练
                {% else %}
                最近 30 天各部位训练分布均匀
                {% endif %}
            </p>
            {% if plan.suggested_movements %}
            <div style="font-size:13px;color:#555;">
                <div style="font-weight:500;margin-bottom:4px;">建议动作:</div>
                {% for m in plan.suggested_movements %}
                <div style="display:flex;align-items:center;gap:8px;padding:3px 0;">
                    <span style="color:#1a1a2e;">▸ {{ m.name }}</span>
                    <span style="font-size:11px;color:#999;">{{ m.days_ago }} 天前</span>
                </div>
                {% endfor %}
            </div>
            {% endif %}
        </div>
    </div>
</div>

<!-- 恢复状态 -->
{% if recovery.sleep_hours %}
<div class="card">
    <div class="card-title">恢复状态</div>
    <div style="display:flex;gap:16px;">
        <div class="stat" style="flex:1;">
            <div class="stat-value">{{ recovery.sleep_hours }}<span style="font-size:14px;color:#888;">h</span></div>
            <div class="stat-label">昨晚睡眠</div>
        </div>
        <div class="stat" style="flex:1;">
            <div class="stat-value">{{ "%.0f"|format(recovery.sleep_deep_pct) }}%</div>
            <div class="stat-label">深睡占比</div>
        </div>
    </div>
    <div style="font-size:13px;color:#555;margin-top:8px;padding:8px 12px;background:#f9f9f9;border-radius:6px;">
        {% if recovery.sleep_hours >= 7 and recovery.sleep_deep_pct >= 15 %}
        🟢 恢复良好，适合中高强度训练
        {% elif recovery.sleep_hours >= 6 %}
        🟡 恢复一般，建议中等强度
        {% else %}
        🔴 睡眠不足，建议休息或低强度有氧
        {% endif %}
    </div>
</div>
{% endif %}

<!-- 训练分布 (30天) -->
<div class="card">
    <div class="card-title">训练分布 (30天)</div>
    {% set max_count = plan.groups|map(attribute='count')|max %}
    {% for g in plan.groups %}
    {% if g.key != 'cardio' %}
    <div style="margin-bottom:10px;">
        <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:3px;">
            <span>{{ g.name }}</span>
            <span style="color:#888;">{{ g.count }} 次{% if g.last_date %} · 最近{{ g.days_since }}天{% endif %}</span>
        </div>
        <div style="height:14px;background:#eee;border-radius:7px;overflow:hidden;">
            <div style="height:100%;width:{{ (g.count / max_count * 100)|int if max_count else 0 }}%;background:{% if g.deficit > 50 %}#e53935{% elif g.deficit > 20 %}#ff9800{% else %}#4caf50{% endif %};border-radius:7px;"></div>
        </div>
    </div>
    {% endif %}
    {% endfor %}
</div>

{% else %}

<div class="card" style="text-align:center;padding:40px;">
    <p style="font-size:16px;color:#888;">暂无训练数据，开始训练后将在此显示建议</p>
</div>

{% endif %}

{% endblock %}
```

- [ ] **Step 2: Commit**

```bash
cd /Users/xinyi/Documents/XunJi && git add templates/plan.html && git commit -m "feat: redesign plan page with deficit analysis and recovery context"
```

---

### Task 4: Clean up old plan route parameter

**Files:**
- Modify: `routes/plan.py` (remove old `{days}` route pattern)

- [ ] **Step 1: Verify old `/plan/{days}` route is removed** (already done in Task 2 — single route without days param)

- [ ] **Step 2: Test the page loads**

```bash
cd /Users/xinyi/Documents/XunJi && \
export XUNJI_API_KEY="..." && \
export XUNJI_FOOD_API_KEY="..." && \
export XUNJI_FOOD_SEARCH_API_KEY="..." && \
export XUNJI_BODY_API_KEY="..." && \
python3 -m uvicorn app:app --host 127.0.0.1 --port 8002 --loop asyncio --http h11 &
sleep 3 && curl -s http://127.0.0.1:8002/plan | head -20
# Expected: 200 OK, rendered HTML
```

- [ ] **Step 3: Kill test server and commit**

```bash
kill $(lsof -ti:8002) 2>/dev/null
```
