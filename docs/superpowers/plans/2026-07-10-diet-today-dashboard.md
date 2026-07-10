# 饮食今日看板实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**目标：** 将 `/diet` 首页改为今日看板 — 进度条、餐次展开、快速补记；现有 3-tab 分析移至 `/diet/history`

**架构：** FastAPI route 分拆 + client-side Mifflin-St Jeor 计算 + CSS 进度条

**技术栈：** Python 3.9+, FastAPI, Chart.js 4, CSS, localStorage

## 全局约束
- 目标值使用 Mifflin-St Jeor 公式，页面标注"估算值"字样
- 用户配置纯客户端 localStorage，不存后端
- 进度条用纯 CSS 宽度百分比，不用 Chart.js
- 写回前必须展示变更摘要并等待用户确认
- 无体重数据时使用默认值 70kg

---

### Task 1: analysis.py — get_latest_weight() + tests

**Files:**
- Modify: `analysis.py`
- Modify: `tests/test_analysis.py`

**Interfaces:**
- Produces: `get_latest_weight(records: list[dict]) -> float | None`

- [ ] **Step 1: 写测试**

```python
# tests/test_analysis.py — 添加到文件末尾
def test_get_latest_weight_empty():
    assert get_latest_weight([]) is None


def test_get_latest_weight_basic():
    records = [
        {"datestr": "2026-06-01", "type": "weight", "value": 75.0},
        {"datestr": "2026-06-07", "type": "weight", "value": 74.5},
        {"datestr": "2026-06-01", "type": "bodyfat", "value": 18.5},
    ]
    assert get_latest_weight(records) == 74.5


def test_get_latest_weight_only_bodyfat():
    records = [
        {"datestr": "2026-06-01", "type": "bodyfat", "value": 18.5},
        {"datestr": "2026-06-07", "type": "bodyfat", "value": 18.0},
    ]
    assert get_latest_weight(records) is None
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /Users/adam/Documents/XunJi && python3 -m pytest tests/test_analysis.py::test_get_latest_weight_empty tests/test_analysis.py::test_get_latest_weight_basic tests/test_analysis.py::test_get_latest_weight_only_bodyfat -v`
Expected: FAIL (function not defined)

- [ ] **Step 3: 实现函数**

在 `analysis.py` 末尾添加 `get_latest_weight`：

```python
def get_latest_weight(records: list[dict]) -> float | None:
    """Extract latest body weight from body records sorted by date."""
    weights: list[tuple[str, float]] = []
    for rec in records:
        if rec.get("type") == "weight":
            val = rec.get("value")
            if val is not None:
                weights.append((rec.get("datestr", ""), _safe_float(val)))
    if not weights:
        return None
    weights.sort(key=lambda x: x[0], reverse=True)
    return weights[0][1]
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /Users/adam/Documents/XunJi && python3 -m pytest tests/test_analysis.py -v`
Expected: PASS (34 tests)

- [ ] **Step 5: Commit**

```bash
git add analysis.py tests/test_analysis.py
git commit -m "feat: add get_latest_weight for body weight extraction"
```

---

### Task 2: routes/diet.py — 分拆 dashboard + history 路由

**Files:**
- Modify: `routes/diet.py`
- Create: `templates/diet_history.html`
- Create: `templates/diet.html` (新今日看板)
- Modify: `data_service.py`

**Interfaces:**
- Consumes: `get_latest_weight(records)` from Task 1
- Consumes: `data_service.get_body(start, end, types)`
- Produces: `GET /diet` (dashboard HTML), `GET /diet/history` (3-tab HTML)

- [ ] **Step 1: data_service.py 添加 get_latest_weight wrapper**

```python
# 在 data_service.py 现有 get_body 方法后添加
async def get_latest_weight(self, days_back: int = 30) -> float | None:
    from datetime import datetime, timedelta, timezone
    from analysis import get_latest_weight as _extract
    end = datetime.now(timezone.utc).isoformat()
    start = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()
    data = await self.get_body(start, end, types=["weight"], limit=50)
    records = data.get("res", {}).get("records", []) or data.get("records", [])
    return _extract(records)
```

- [ ] **Step 2: 重写 GET /diet（今日看板路由）**

```python
# routes/diet.py 替换 GET /diet
@router.get("/diet", response_class=HTMLResponse)
async def diet_today(request: Request):
    ds = get_data_service()
    today = date.today().isoformat()
    
    # 今日饮食记录
    diet_data = await ds.get_diet(today, today, detail=True)
    records = diet_data.get("res", {}).get("records", [])
    if not records:
        records = diet_data.get("records", [])
    
    # 最新体重
    body_data = await ds.get_body(
        (date.today() - timedelta(days=30)).isoformat(),
        today,
        types=["weight"],
        limit=50,
    )
    body_records = body_data.get("res", {}).get("records", [])
    if not body_records:
        body_records = body_data.get("records", [])
    body_weight = get_latest_weight(body_records)
    
    env = get_jinja_env()
    tmpl = env.get_template("diet.html")
    return HTMLResponse(tmpl.render(
        request=request,
        records=records,
        body_weight=body_weight,
        today_date=today,
    ))


# 新建 GET /diet/history（迁移现有 3-tab 逻辑）
@router.get("/diet/history", response_class=HTMLResponse)
async def diet_history(
    request: Request,
    range_days: int = Query(90, ge=1, le=365),
):
    ds = get_data_service()
    today = date.today()
    start = (today - timedelta(days=range_days - 1)).isoformat()
    end = today.isoformat()
    diet_data = await ds.get_diet(start, end, detail=True)
    records = diet_data.get("res", {}).get("records", [])
    if not records:
        records = diet_data.get("records", [])
    analysis = summarize_diet_detailed(records)
    dates_with_data = sorted(analysis["daily_calories"].keys())
    env = get_jinja_env()
    tmpl = env.get_template("diet_history.html")
    return HTMLResponse(tmpl.render(
        request=request,
        records=records,
        analysis=analysis,
        dates_with_data=dates_with_data,
        range_days=range_days,
    ))
```

保留 `GET /diet/search` 和 `POST /diet/upsert` 不变。

- [ ] **Step 3: 创建 diet_history.html（从当前 diet.html 提取 3-tab 内容）**

复制当前 `/Users/adam/Documents/XunJi/templates/diet.html` 的内容到 `diet_history.html`，作为独立的历史分析模板。顶部 block 标题改为"历史记录"。

```html
{% extends "base.html" %}
{% block title %}饮食历史 - 训记分析{% endblock %}
{% block content %}
<h1>饮食历史 <a href="/diet" style="font-size:14px;font-weight:400;color:#888;text-decoration:none;margin-left:12px;">← 返回今日</a></h1>
<!-- 从现有 diet.html 复制完整的 3-tab 内容：日历/分析/饮食管理 -->
{% endblock %}
{% block extra_scripts %}
<!-- 从现有 diet.html 复制完整的 JS 逻辑 -->
{% endblock %}
```

- [ ] **Step 4: 验证路由不报错**

Run: `cd /Users/adam/Documents/XunJi && python3 -c "from routes.diet import router; print('import ok')"`
Expected: import ok

- [ ] **Step 5: Commit**

```bash
git add data_service.py routes/diet.py templates/diet_history.html
git commit -m "feat: split diet routes into today dashboard and history"
```

---

### Task 3: 前端 — 今日看板 (templates/diet.html)

**Files:**
- Rewrite: `templates/diet.html`

- [ ] **Step 1: 实现完整看板 template**

```html
{% extends "base.html" %}
{% block title %}今日饮食 - 训记分析{% endblock %}
{% block content %}
<h1 style="display:flex;justify-content:space-between;align-items:center;">
    <span>今日饮食 <span style="font-size:14px;font-weight:400;color:#888;">{{ today_date }}</span></span>
    <span>
        <button class="btn" style="font-size:13px;padding:4px 10px;" id="settingsBtn">⚙ 设置目标</button>
        <a href="/diet/history" class="btn" style="font-size:13px;padding:4px 10px;text-decoration:none;">📊 历史记录</a>
    </span>
</h1>

<!-- 进度条 -->
<div class="card" id="progressCard">
    <div class="progress-bar-row">
        <span class="progress-label">热量</span>
        <div class="progress-track"><div class="progress-fill" id="pbar-cal" style="width:0%;background:#4caf50;"></div></div>
        <span class="progress-value" id="pval-cal">0 / 0 kcal</span>
    </div>
    <div class="progress-bar-row">
        <span class="progress-label">蛋白质</span>
        <div class="progress-track"><div class="progress-fill" id="pbar-protein" style="width:0%;background:#e53935;"></div></div>
        <span class="progress-value" id="pval-protein">0 / 0 g</span>
    </div>
    <div class="progress-bar-row">
        <span class="progress-label">碳水</span>
        <div class="progress-track"><div class="progress-fill" id="pbar-carbs" style="width:0%;background:#2196f3;"></div></div>
        <span class="progress-value" id="pval-carbs">0 / 0 g</span>
    </div>
    <div class="progress-bar-row">
        <span class="progress-label">脂肪</span>
        <div class="progress-track"><div class="progress-fill" id="pbar-fat" style="width:0%;background:#ff9800;"></div></div>
        <span class="progress-value" id="pval-fat">0 / 0 g</span>
    </div>
    <div style="font-size:11px;color:#aaa;margin-top:8px;text-align:center;">推荐值根据 Mifflin-St Jeor 公式估算</div>
</div>

<!-- 餐次区域 -->
<div class="card" id="mealsCard">
    <h3 style="font-size:15px;font-weight:600;margin-bottom:12px;" id="todaySummary">今日合计: 0 / 0 kcal (0%)</h3>
    <div id="mealsContainer"></div>
</div>

<!-- 配置 Modal -->
<div class="modal-overlay" id="configModal">
    <div class="modal-box" style="max-width:360px;">
        <div class="modal-title">设置个人信息</div>
        <div class="form-group">
            <label>身高 (cm)</label>
            <input type="number" id="cfgHeight" value="175" min="100" max="250">
        </div>
        <div class="form-group">
            <label>年龄</label>
            <input type="number" id="cfgAge" value="30" min="10" max="120">
        </div>
        <div class="form-group">
            <label>性别</label>
            <select id="cfgSex">
                <option value="male">男</option>
                <option value="female">女</option>
            </select>
        </div>
        <div class="form-group">
            <label>活动水平</label>
            <select id="cfgActivity">
                <option value="sedentary">久坐 (几乎不运动)</option>
                <option value="light" selected>轻度 (每周1-3天)</option>
                <option value="moderate">中度 (每周3-5天)</option>
                <option value="active">高度 (每周6-7天)</option>
                <option value="extreme">极高度 (每天高强度)</option>
            </select>
        </div>
        {% if not body_weight %}
        <div class="form-group">
            <label>体重 (kg) <span style="color:#e53935;font-size:11px;">* 未从 App 获取到数据</span></label>
            <input type="number" id="cfgWeight" value="70" min="30" max="300" step="0.1">
        </div>
        {% endif %}
        <div class="modal-actions">
            <button class="btn btn-primary" id="cfgSaveBtn" style="width:100%;">保存</button>
        </div>
    </div>
</div>

<!-- 添加食物 Modal（复用现有搜索 modal） -->
<div class="modal-overlay" id="foodModal">
    <div class="modal-box">
        <div class="modal-title" id="modalTitle">添加食物</div>
        <div class="form-group">
            <label>餐次</label>
            <select id="modalMealType">
                <option value="breakfast">早餐</option>
                <option value="lunch">午餐</option>
                <option value="dinner">晚餐</option>
                <option value="snack">加餐</option>
            </select>
        </div>
        <div class="form-group">
            <label>食物名称</label>
            <input type="text" id="modalFoodName" placeholder="输入关键词搜索...">
            <div class="search-results" id="modalSearchResults"></div>
        </div>
        <div class="form-group" id="modalAmountGroup" style="display:none;">
            <label>份量 (g)</label>
            <input type="number" id="modalAmount" value="100" min="1">
            <div class="preview-box" id="modalPreview"></div>
        </div>
        <div class="modal-actions">
            <button class="btn" style="background:#eee;" onclick="closeFoodModal()">取消</button>
            <button class="btn btn-primary" id="modalConfirmBtn" disabled>确认添加</button>
        </div>
    </div>
</div>

<!-- Toast -->
<div class="toast" id="toast"></div>
{% endblock %}

{% block extra_scripts %}
<script>
const dietRecords = {{ records | tojson | safe }};
let bodyWeight = {{ body_weight if body_weight else 'null' }};

// ── Mifflin-St Jeor 目标计算 ──
const ACTIVITY_FACTORS = { sedentary: 1.2, light: 1.375, moderate: 1.55, active: 1.725, extreme: 1.9 };

function calcBMR(weight, height, age, sex) {
    return sex === 'male'
        ? 10 * weight + 6.25 * height - 5 * age + 5
        : 10 * weight + 6.25 * height - 5 * age - 161;
}

function calcTargets(weight, height, age, sex, activity) {
    const bmr = calcBMR(weight, height, age, sex);
    const tdee = bmr * (ACTIVITY_FACTORS[activity] || 1.375);
    const protein = weight * 2.0;
    const fat = weight * 1.0;
    const carbs = (tdee - protein * 4 - fat * 9) / 4;
    return { cal: Math.round(tdee), protein: Math.round(protein), carbs: Math.round(carbs), fat: Math.round(fat) };
}

// ── 用户配置 (localStorage) ──
const CONFIG_KEY = 'diet_target_profile';

function loadConfig() {
    try {
        const raw = localStorage.getItem(CONFIG_KEY);
        return raw ? JSON.parse(raw) : null;
    } catch(e) { return null; }
}

function saveConfig(cfg) {
    try { localStorage.setItem(CONFIG_KEY, JSON.stringify(cfg)); } catch(e) {}
}

function showConfigModal() {
    const cfg = loadConfig() || {};
    document.getElementById('cfgHeight').value = cfg.height || 175;
    document.getElementById('cfgAge').value = cfg.age || 30;
    document.getElementById('cfgSex').value = cfg.sex || 'male';
    document.getElementById('cfgActivity').value = cfg.activity || 'light';
    document.getElementById('configModal').classList.add('active');
}

function hideConfigModal() {
    document.getElementById('configModal').classList.remove('active');
}

document.getElementById('settingsBtn').addEventListener('click', showConfigModal);

document.getElementById('cfgSaveBtn').addEventListener('click', function() {
    const cfg = {
        height: parseInt(document.getElementById('cfgHeight').value) || 175,
        age: parseInt(document.getElementById('cfgAge').value) || 30,
        sex: document.getElementById('cfgSex').value,
        activity: document.getElementById('cfgActivity').value,
    };
    // If no body weight from API, use manual weight input
    if (!bodyWeight) {
        bodyWeight = parseFloat(document.getElementById('cfgWeight').value) || 70;
    }
    saveConfig(cfg);
    hideConfigModal();
    renderDashboard();
});

// ── Dashboard 渲染 ──
function calcTodayTotals(records) {
    let totals = { cal: 0, protein: 0, carbs: 0, fat: 0 };
    records.forEach(function(r) {
        const ntr = r.ntr || {};
        const amt = r.amount || 0;
        const factor = (r.unit || 'g') === 'g' ? amt / 100 : 1;
        totals.cal += (ntr.cal || 0) * factor;
        totals.protein += (ntr.protein || 0) * factor;
        totals.carbs += (ntr.carb || 0) * factor;
        totals.fat += (ntr.fat || 0) * factor;
    });
    return {
        cal: Math.round(totals.cal),
        protein: Math.round(totals.protein),
        carbs: Math.round(totals.carbs),
        fat: Math.round(totals.fat),
    };
}

function renderProgressBar(id, current, target, color) {
    const pct = target > 0 ? Math.min(current / target * 100, 100) : 0;
    const bar = document.getElementById(id);
    if (bar) {
        bar.style.width = pct + '%';
        bar.style.background = pct >= 100 ? '#e53935' : color;
    }
}

function renderProgressBars(totals, targets) {
    renderProgressBar('pbar-cal', totals.cal, targets.cal, '#4caf50');
    renderProgressBar('pbar-protein', totals.protein, targets.protein, '#e53935');
    renderProgressBar('pbar-carbs', totals.carbs, targets.carbs, '#2196f3');
    renderProgressBar('pbar-fat', totals.fat, targets.fat, '#ff9800');
    document.getElementById('pval-cal').textContent = totals.cal + ' / ' + targets.cal + ' kcal';
    document.getElementById('pval-protein').textContent = totals.protein + ' / ' + targets.protein + ' g';
    document.getElementById('pval-carbs').textContent = totals.carbs + ' / ' + targets.carbs + ' g';
    document.getElementById('pval-fat').textContent = totals.fat + ' / ' + targets.fat + ' g';
}

function renderMeals(records) {
    const container = document.getElementById('mealsContainer');
    const mealLabels = { breakfast: '早餐', lunch: '午餐', dinner: '晚餐', snack: '加餐' };
    const mealOrder = ['breakfast', 'lunch', 'dinner', 'snack'];
    
    // Group records by meal type
    const grouped = {};
    records.forEach(function(r) {
        const mt = r.meal_type || 'other';
        if (!grouped[mt]) grouped[mt] = [];
        grouped[mt].push(r);
    });
    
    let grandTotalCal = 0;
    let html = '';
    mealOrder.forEach(function(mt) {
        const foods = grouped[mt];
        const label = mealLabels[mt] || mt;
        if (foods && foods.length > 0) {
            let mealCal = 0;
            let rows = '';
            foods.forEach(function(f) {
                const ntr = f.ntr || {};
                const amt = f.amount || 0;
                const factor = (f.unit || 'g') === 'g' ? amt / 100 : 1;
                const cal = Math.round((ntr.cal || 0) * factor);
                mealCal += cal;
                const p = (ntr.protein || 0) * factor;
                const c = (ntr.carb || 0) * factor;
                const ft = (ntr.fat || 0) * factor;
                rows += '<div class="food-row">' +
                    '<span class="food-name">' + f.name + '</span>' +
                    '<span class="food-amount">' + amt + (f.unit || 'g') + '</span>' +
                    '<span class="food-cal">' + cal + 'kcal</span>' +
                    '<span class="food-macros">P' + p.toFixed(1) + ' C' + c.toFixed(1) + ' F' + ft.toFixed(1) + '</span>' +
                    '</div>';
            });
            grandTotalCal += mealCal;
            html += '<div class="meal-section">' +
                '<div class="meal-header"><span>' + label + '</span><span class="meal-cal">' + mealCal + ' kcal</span></div>' +
                rows +
                '<button class="btn" style="font-size:12px;padding:2px 8px;margin-top:4px;" onclick="openAddFood(\'' + mt + '\')">+ 添加</button>' +
                '</div>';
        } else {
            html += '<div class="meal-section">' +
                '<div class="meal-header"><span>' + label + '</span><span class="meal-cal" style="color:#ccc;">— 未记录</span></div>' +
                '<button class="btn" style="font-size:12px;padding:2px 8px;" onclick="openAddFood(\'' + mt + '\')">+ 添加食物</button>' +
                '</div>';
        }
    });
    container.innerHTML = html;
    
    // Update summary
    const targets = calcTargets(bodyWeight || 70, ...);
    // Actually re-calc below
    return grandTotalCal;
}

function renderDashboard() {
    const cfg = loadConfig();
    if (!cfg) {
        showConfigModal();
        return;
    }
    
    const weight = bodyWeight || 70;
    const targets = calcTargets(weight, cfg.height, cfg.age, cfg.sex, cfg.activity);
    const totals = calcTodayTotals(dietRecords);
    
    renderProgressBars(totals, targets);
    
    const pct = targets.cal > 0 ? Math.round(totals.cal / targets.cal * 100) : 0;
    document.getElementById('todaySummary').textContent = '今日合计: ' + totals.cal + ' / ' + targets.cal + ' kcal (' + pct + '%)';
    
    renderMeals(dietRecords);
}

// ── 添加食物 Modal ──
let addFoodMealType = 'breakfast';
let selectedFood = null;
let searchTimer = null;

function openAddFood(mealType) {
    addFoodMealType = mealType || 'breakfast';
    selectedFood = null;
    document.getElementById('modalTitle').textContent = '添加食物 - ' + (addFoodMealType === 'breakfast' ? '早餐' : addFoodMealType === 'lunch' ? '午餐' : addFoodMealType === 'dinner' ? '晚餐' : '加餐');
    document.getElementById('modalMealType').value = addFoodMealType;
    document.getElementById('modalFoodName').value = '';
    document.getElementById('modalAmount').value = '100';
    document.getElementById('modalAmountGroup').style.display = 'none';
    document.getElementById('modalSearchResults').innerHTML = '';
    document.getElementById('modalPreview').innerHTML = '';
    document.getElementById('modalConfirmBtn').disabled = true;
    document.getElementById('modalConfirmBtn').textContent = '确认添加';
    document.getElementById('foodModal').classList.add('active');
}

function closeFoodModal() {
    document.getElementById('foodModal').classList.remove('active');
}

document.getElementById('modalFoodName').addEventListener('input', function() {
    const q = this.value.trim();
    if (searchTimer) clearTimeout(searchTimer);
    if (q.length < 1) { document.getElementById('modalSearchResults').innerHTML = ''; return; }
    searchTimer = setTimeout(function() { doSearch(q); }, 300);
});

function doSearch(keyword) {
    fetch('/diet/search?keyword=' + encodeURIComponent(keyword) + '&limit=8')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            const results = document.getElementById('modalSearchResults');
            const foods = data.foods || [];
            if (foods.length === 0) {
                results.innerHTML = '<div style="padding:8px;color:#888;font-size:13px;">未找到匹配食物</div>';
                return;
            }
            results.innerHTML = '';
            foods.forEach(function(f) {
                const div = document.createElement('div');
                div.className = 'search-result-item';
                const ntr = f.ntr || {};
                div.innerHTML = '<div class="sr-name">' + f.name + '</div><div class="sr-ntr">每100g: ' + (ntr.cal || '-') + 'kcal | P' + (ntr.protein || '-') + ' C' + (ntr.carb || '-') + ' F' + (ntr.fat || '-') + '</div>';
                div.addEventListener('click', function() {
                    document.querySelectorAll('.search-result-item').forEach(function(i) { i.classList.remove('selected'); });
                    this.classList.add('selected');
                    selectedFood = f;
                    document.getElementById('modalAmountGroup').style.display = 'block';
                    updatePreview();
                    document.getElementById('modalConfirmBtn').disabled = false;
                });
                results.appendChild(div);
            });
        })
        .catch(function(e) { showToast('搜索失败: ' + e.message); });
}

document.getElementById('modalAmount').addEventListener('input', updatePreview);

function updatePreview() {
    if (!selectedFood) return;
    const ntr = selectedFood.ntr || {};
    const amt = parseFloat(document.getElementById('modalAmount').value) || 0;
    const factor = amt / 100;
    document.getElementById('modalPreview').innerHTML =
        '<div class="pv-row"><span>每100g营养:</span><span>' + (ntr.cal || 0) + ' kcal | P' + (ntr.protein || 0) + ' C' + (ntr.carb || 0) + ' F' + (ntr.fat || 0) + '</span></div>' +
        '<div class="pv-row"><span>实际摄入 (' + amt + 'g):</span><span>' + ((ntr.cal || 0) * factor).toFixed(0) + ' kcal | P' + ((ntr.protein || 0) * factor).toFixed(1) + ' C' + ((ntr.carb || 0) * factor).toFixed(1) + ' F' + ((ntr.fat || 0) * factor).toFixed(1) + '</span></div>';
}

document.getElementById('modalConfirmBtn').addEventListener('click', function() {
    if (!selectedFood) return;
    const btn = this;
    const ntr = selectedFood.ntr || {};
    const amt = parseFloat(document.getElementById('modalAmount').value) || 100;
    const mealType = document.getElementById('modalMealType').value;

    const foodItem = {
        date: '{{ today_date }}',
        meal_type: mealType,
        name: selectedFood.name,
        amount: amt,
        unit: 'g',
        ntr: { cal: ntr.cal || 0, protein: ntr.protein || 0, carb: ntr.carb || 0, fat: ntr.fat || 0 },
        uniquekey: selectedFood.uniquekey || '',
    };

    btn.disabled = true;
    btn.textContent = '提交中...';

    fetch('/diet/upsert', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ foods: [foodItem] }),
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        showToast('添加成功');
        closeFoodModal();
        location.reload();
    })
    .catch(function(e) {
        showToast('请求失败: ' + e.message);
        btn.disabled = false;
        btn.textContent = '确认添加';
    });
});

// ── Toast ──
function showToast(msg, duration) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.classList.add('show');
    setTimeout(function() { t.classList.remove('show'); }, duration || 2000);
}

// ── Init ──
if (!loadConfig()) {
    showConfigModal();
} else {
    renderDashboard();
}
</script>
{% endblock %}
```

注意：需要在 app.css 中追加进度条样式：

```css
.progress-bar-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 10px;
}
.progress-bar-row:last-child { margin-bottom: 0; }
.progress-label {
    flex: 0 0 50px;
    font-size: 13px;
    font-weight: 500;
    color: #555;
    text-align: right;
}
.progress-track {
    flex: 1;
    height: 16px;
    background: #eee;
    border-radius: 8px;
    overflow: hidden;
}
.progress-fill {
    height: 100%;
    border-radius: 8px;
    transition: width 0.3s, background 0.3s;
}
.progress-value {
    flex: 0 0 110px;
    font-size: 12px;
    color: #888;
    text-align: left;
}
```

- [ ] **Step 2: 验证页面渲染**

启动 server 后访问 `http://localhost:8000/diet`，检查：
1. 配置 modal 弹出（首次访问）
2. 填完配置后进度条显示
3. 餐次区域展开/空餐次
4. 添加食物 modal 工作正常

- [ ] **Step 3: Commit**

```bash
git add templates/diet.html static/css/app.css
git commit -m "feat: today dashboard with progress bars, targets, quick add"
```

---

### Task 4: 启动验证

- [ ] **Step 1: 启动 server**

Run: `cd /Users/adam/Documents/XunJi && pkill -f "uvicorn.*xunji" 2>/dev/null; sleep 1 && nohup python3 -m uvicorn app:app --reload --port 8000 > /tmp/uvicorn.log 2>&1 &`

- [ ] **Step 2: 验证功能**
1. `/diet` → 今日看板渲染，首次访问弹出配置
2. `设置目标` → 可修改配置
3. `[+ 添加食物]` → 搜索 → 选取 → 填份量 → 预览 → 确认 → 成功后刷新
4. `历史记录` → `/diet/history` 3-tab 正常渲染

- [ ] **Step 3: 最终 commit**

```bash
git add -A && git commit -m "feat: complete diet today dashboard"
```
