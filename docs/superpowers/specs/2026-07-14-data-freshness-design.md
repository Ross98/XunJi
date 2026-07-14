# 数据新鲜度标注与智能时间范围

## 背景

系统数据来源分为两类：
- 训记 API：训练、饮食、身体数据（实时同步）
- Apple Health 导入：步数、活动分钟、站立小时、HRV、静息心率、VO2Max、睡眠

AH 数据需要用户手动导入 XML，因此哪个时间点有最新数据是不确定的。
训记数据则每次刷新页面都是最新的。这种时间差导致图表统计、趋势展示中存在数据缺口。

## 目标

1. 用户一眼能看出页面上的数据是新的还是旧的
2. 默认状态下，只展示所有数据源都有数据的完整时间段
3. 需要时也能切到全量数据

## 方案

### 1. 数据新鲜度标签

后端新增 `DataFreshnessService`，提供：

```
get_ah_latest_date(type) -> str | None   # 某个 AH 数据类型的最后记录日期
get_ah_overall_latest() -> str            # 所有 AH 数据的最后日期
get_xunji_latest() -> str                 # 所有训记数据的最新日期
get_freshness_status(type) -> dict        # {latest: str, days_ago: int, is_fresh: bool, source: str}
```

在前端每个数据卡片右下角小字标注：

| 状态 | 标签 | 颜色 |
|---|---|---|
| 今天有数据 | `✅ 今天` | 绿色 |
| 1-3天前 | `🕐 N天前` | 灰色 |
| 4-7天前 | `⏳ N天前` | 橙色 |
| >7天无数据 | `⚠️ N天未更新` | 红色 |
| 训记实时数据 | `⚡ 实时` | 蓝色 |

涉及卡片：
- 仪表盘：步数、活动分钟、站立小时、HRV、静息心率、VO2Max、睡眠评分
- 健康页：同上
- 身体页：体重/体脂数据来源标注（训记 vs AH）

### 2. 智能默认时间范围

后端逻辑：

```python
ah_latest = get_ah_overall_latest()     # 所有AH数据最后日期
xunji_latest = get_xunji_latest()       # 训记数据最后日期
safe_end = min(ah_latest, xunji_latest) # 安全截止日
```

- 图表/统计默认以 `safe_end` 为截止日期
- 页面顶部加一个切换按钮 `显示完整数据`，点击后恢复全量
- safe_end 距今天超过3天的，切换按钮改为橙色

涉及页面：仪表盘趋势图、健康页时间序列图表

### 3. 数据截止提示条

在每个涉及 AH 数据的页面顶部（卡片区域上方）加一个极小提示条：

```
┌─────────────────────────────────────────────┐
│ Apple Health 数据更新至 7月12日              │
│ 训练数据已是最新                            │
└─────────────────────────────────────────────┘
```

- 绿色背景：AH 数据在 3 天内
- 橙色背景：AH 数据超过 3 天
- 红色背景：AH 数据超过 7 天
- 无数据时：`📥 请导入 Apple Health 数据`

涉及页面：仪表盘、健康页、身体页

### 4. 后端模块结构

在 `data_service.py` 中新增 `DataFreshnessService` 类：

- 查询 AH SQLite 获取各类别的最大 start_date
- 查询训记 API 获取体重/身体数据的最新日期
- 所有查询结果缓存 5 分钟（`cache.py`）
- 提供模板渲染所需的所有新鲜度变量

### 5. 模板变量

每个涉及页面收到的额外变量：

```python
{
    "freshness": {
        "ah_overall_latest": "2026-07-12",
        "xunji_latest": "2026-07-14",
        "safe_end": "2026-07-12",
        "ah_days_ago": 2,
        "ah_status": "fresh" | "stale" | "expired" | "none",
        "metrics": {
            "steps": {"latest": "2026-07-12", "days_ago": 2, "is_fresh": True},
            "hrv": {...},
            # ... 每个 AH 指标
        }
    }
}
```

### 6. 边界情况

- AH 未导入过任何数据：提示条显示"请导入"，标签显示"未导入"
- AH 部分类型有数据、部分没有：有数据的标时间，没有的标"无数据"
- safe_end 早于 range_start：按 range_start 为准，不切断
- 训记 API 当天无数据（没训练/没测量）：取最近有数据的日期
- 切换"显示完整数据"后所有新鲜度标签仍然保留

## 涉及文件

- `apple_health.py` — 新增 get_latest_dates() 批量查询
- `data_service.py` — 新增 DataFreshnessService
- `routes/dashboard.py` — 路由注入 freshness 变量
- `routes/health.py` — 同上
- `routes/body.py` — 同上
- `templates/base.html` 或独立组件 — 提示条模板
- `templates/dashboard.html` — 卡片标签 + 智能范围
- `templates/health.html` — 卡片标签 + 智能范围
- `templates/body.html` — 数据来源标注

## 不包含

- 不改变数据存储结构
- 不改动现有图表渲染逻辑
- 不在后端新增独立 API 端点
