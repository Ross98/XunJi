# 训练分析 Tab — 设计

## 背景

训练页面的"部位分布"饼图太抽象，改为"训练分析" tab，展示更多实际数据。

## 变更

### Tab 结构调整

| 位置 | 内容 |
|------|------|
| 第一个 tab | 容量趋势（不变） |
| **第二个 tab** | **训练分析（替代部位分布）** |
| 第三个 tab | 动作追踪（不变） |
| 第四个 tab | 频率日历（不变） |

### 训练分析 tab 内容

四个子图表纵向排列，每个等宽：

1. **训练时长趋势** — 折线图，横轴日期，纵轴分钟，基于 `start`/`end` 时间戳计算
2. **热量消耗趋势** — 柱状图，横轴日期，纵轴 kcal，基于 `calories` 字段
3. **心率区间** — 折线图，平均心率 + 最高心率双线，基于 `avgHeartRate`/`maxHeartRate`；无数据时显示提示
4. **训练类型分布** — 柱状图，横轴训练类型（步行/力量/跑步…），纵轴次数

### 数据流

- 分析函数统一加在 `analysis.py`，新增：
  - `training_duration_trend(trains)` → `list[{date, duration_min}]`
  - `calories_trend(trains)` → `list[{date, calories}]`
  - `heart_rate_trend(trains)` → `list[{date, avg, max}]`
  - `training_type_distribution(trains)` → `list[{name, count}]`
- 图表在模板中用 Chart.js 渲染
- 空数据时显示"暂无数据"

### 测试

- 每个新函数至少一个测试用例（含空数据边界）
