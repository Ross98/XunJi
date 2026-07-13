# 健康仪表盘设计

## 目标

在首页仪表盘 `/` 上增加 Apple Health 数据展示区，统一呈现健康指标。

## 布局

```
┌──────────────┬──────────────┬──────────────┐
│  HRV          │  静息心率     │  VO₂ Max      │
│ 最新值+日期    │ 最新值+日期    │ 最新值+日期    │
├──────────────┴──────────────┴──────────────┤
│  [HRV] [静息心率] [VO₂Max] ← 切换按钮    │
│  Chart.js 折线趋势图 (近90天)              │
├──────────────┬──────────────┬──────────────┤
│  今日步数     │  运动分钟     │  站立小时     │
└──────────────┴──────────────┴──────────────┘
```

## 数据源

来自 `apple_health_cache.sqlite`（已导入）：
- HRV: `HKQuantityTypeIdentifierHeartRateVariabilitySDNN`
- 静息心率: `HKQuantityTypeIdentifierRestingHeartRate`
- VO₂Max: `HKQuantityTypeIdentifierVO2Max`
- 步数: `HKQuantityTypeIdentifierStepCount`
- 运动分钟: `HKQuantityTypeIdentifierAppleExerciseTime`
- 站立小时: `HKCategoryTypeIdentifierAppleStandHour`

## 修改文件

- `routes/dashboard.py` — 增加 Apple Health 数据查询
- `templates/dashboard.html` — 增加健康区域模板 + Chart.js 图表

## 技术要点

- 趋势图使用 Chart.js line chart，X 轴日期，Y 轴指标值
- 图表切换通过 JS 控制，不刷新页面
- 今日活动数据取当天的 Apple Health 记录汇总
- 如果 Apple Health 缓存不存在或未导入，不显示该区域
