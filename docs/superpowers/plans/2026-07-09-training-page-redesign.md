# 训练页面重新设计 — 实现计划

**目标：** 训练页面改为上下分块，上半部分日历+当天详情，下半部分 4 个分析标签页

## 文件变更

| 文件 | 操作 | 说明 |
|------|------|------|
| `analysis.py` | 修改 | 新增部位分类函数 |
| `routes/training.py` | 重写 | 多端点：主页、日历、日期详情 |
| `templates/training.html` | 重写 | 新布局 |
| `templates/training_calendar.html` | 新建 | HTMX 月历片段 |
| `templates/training_detail.html` | 新建 | HTMX 当天详情片段 |
| `static/css/app.css` | 追加 | 日历、热力图样式 |

## 任务步骤

### Step 1: analysis.py — 补充部位分类函数
- 新增 `classify_bodypart(name: str) → str` 将动作名归类为 "推力"/"拉力"/"腿部"/"核心"/"有氧"/"其他"
- 新增 `bodypart_distribution(trains: list[dict]) → dict[str, int]` 统计各部位训练频次

### Step 2: 重写 routes/training.py
- `GET /training` → 渲染完整页面（当月日历 + 最新训练日详情 + 四大图表数据）
- `GET /training/calendar/{year}/{month}` → 返回月历 HTML 片段（HTMX）
- `GET /training/{date}` → 返回指定日期训练详情 HTML 片段（HTMX）
- 日历标记：从缓存中查询 `training:*` 前缀的 key 提取存在数据的日期

### Step 3: 重写 templates/training.html
- 上半部分：日历（左）+ 当天详情（右）
- 下半部分：4 个 tab 的 Chart.js 图表，默认显示容量趋势
- 容量趋势：最近 7/30/90 天可选
- 部位分布：饼图
- 动作追踪：下拉框选动作，折线图显示重量变化
- 频率日历：90 天 CSS 网格热力图

### Step 4: 新建 templates/training_calendar.html
- 月历网格，有训练记录的日子显示圆点
- 点击日期触发 HTMX 加载详情

### Step 5: 新建 templates/training_detail.html
- 和现在 training.html 的详情部分一致

### Step 6: static/css/app.css — 追加样式
- 日历网格布局
- 热力图样式
- 标签页样式

### Step 7: 验证
- 页面渲染无误
- 日历切换正常
- 图表显示正确
- 测试通过
