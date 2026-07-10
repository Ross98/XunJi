# 饮食今日看板设计

## 概述
将饮食页面首页改为"今日看板"，展示当日饮食进度、自动计算推荐目标值、支持快速补记。现有历史分析（日历/分析/饮食管理 3 tab）移至 `/diet/history` 子页面。

## 页面结构

### 路由变化
```
GET /diet          → 今日看板（默认首页）
GET /diet/history  → 现有 3 tab 分析页（日历/分析/饮食管理）
```

现有导航栏 `/diet` 链接不变，指向今日看板。看板内增加"历史记录"链接指向 `/diet/history`。

### 今日看板布局
```
┌─ 今日日期 + [设置目标] [历史记录] ─────────────┐
│                                                  │
│ ┌─ 4 进度条区域 ──────────────────────────────┐  │
│ │ 热量     ██████████████░░░  1650 / 2000     │  │
│ │ 蛋白质   █████████████████    90 / 120      │  │
│ │ 碳水     ████████████░░░░░  150 / 250       │  │
│ │ 脂肪     █████████████░░░░   45 / 65        │  │
│ │ 进度条颜色：达标=#4caf50, 超标=#e53935       │  │
│ │ 底部标注："推荐值根据 Mifflin-St Jeor 公式估算" │  │
│ └─────────────────────────────────────────────┘  │
│                                                  │
│ ┌─ 餐次区域 ──────────────────────────────────┐  │
│ │ 早餐                      420/500kcal       │  │
│ │   ○ 鸡蛋 100g      165kcal P31 C0  F3.6    │  │
│ │   ○ 牛奶 250ml     125kcal P8  C10 F5      │  │
│ │   [+ 添加]                                  │  │
│ │                                              │  │
│ │ 午餐                      650/800kcal       │  │
│ │   ○ 米饭 200g      260kcal P5  C60 F1      │  │
│ │   [+ 添加]                                  │  │
│ │                                              │  │
│ │ 晚餐 — 未记录                    —           │  │
│ │   [+ 添加食物]                               │  │
│ │                                              │  │
│ │ 加餐 — 未记录                    —           │  │
│ │   [+ 添加食物]                               │  │
│ └─────────────────────────────────────────────┘  │
│                                                  │
│ 底部: 今日总计: 1650 / 2000 kcal (82.5%)         │
└──────────────────────────────────────────────────┘
```

## 数据流

### GET /diet（今日看板）
```
GET /diet:
  ├─ ds.get_diet(today, today, detail=True) → 今日饮食记录
  ├─ ds.get_body(start=30d前, end=today, types=[weight]) → 最新体重
  ├─ 渲染模板，传参：records, body_weight, today_date
  ├─ 用户配置在客户端 localStorage 中读取
  └─ 模板 JS 计算目标值并渲染看板
```

### POST /diet/upsert（快速补记）
- 复用现有写回端点
- modal 中完成搜索→选食物→填份量→预览→确认
- 成功后不整页 reload，前端异步更新今日列表+进度条
- meal_type 自动设为所属餐次

## 目标值计算（客户端 JS 实现）

### Mifflin-St Jeor 公式
```
calcBMR(weight, height, age, sex):
  男: 10 × weight + 6.25 × height - 5 × age + 5
  女: 10 × weight + 6.25 × height - 5 × age - 161

calcTDEE(bmr, activity_level):
  久坐=1.2, 轻度=1.375, 中度=1.55, 高度=1.725, 极高度=1.9
  return bmr × activity_level

calcTargets(weight, height, age, sex, activity_level):
  tdee = calcTDEE(calcBMR(...), activity_level)
  protein = weight_kg × 2.0  (g)
  fat = weight_kg × 1.0      (g)
  carbs = (tdee - protein×4 - fat×9) / 4  (g)
  return {cal: tdee, protein, carbs, fat}
```

### 参考依据
- BMR 公式：Mifflin-St Jeor, 1990, J Am Diet Assoc
- 蛋白 2.0g/kg：ISSN 运动营养推荐范围 1.6-2.2g/kg 取中值
- 脂肪 1.0g/kg：膳食指南脂肪供能 20-35% 的简化值
- 页面标注"估算值"字样

## 用户配置

### 首次配置流程
1. 第一次打开 `/diet` → 检测 localStorage 无 `diet_target_profile`
2. 自动弹出配置 modal
3. 填写：身高(cm) / 年龄 / 性别(男/女) / 活动水平(5档)
4. 确认后存 localStorage
5. 自动计算目标值并渲染看板

### 配置修改
- 看板顶部 `[设置目标]` 按钮 → 重新打开配置 modal
- 修改后覆盖 localStorage
- 不存后端，纯客户端

### localStorage 格式
```json
{
  "height": 175,
  "age": 28,
  "sex": "male",
  "activity_level": "moderate"
}
```

## 后端改动

### routes/diet.py
- 保留 `GET /diet`，改为今日看板路由
- 新增 `GET /diet/history`，迁移现有 3 tab 逻辑
- 保持 `GET /diet/search` 和 `POST /diet/upsert` 不变

### analysis.py
- 新增 `get_latest_weight(records: list[dict]) -> float | None` — 从身体数据中取最新体重
  - records 是 `query_body` 返回的记录列表
  - 找 type=weight 的记录，按 datestr 降序取第一条的 value

### data_service.py
- 新增 `get_latest_weight(types=["weight"])` — 简化调用，返回最近 30 天内的最新体重

## 前端改动

### 新文件: templates/diet.html（覆盖现有）
- 今日看板布局
- 4 进度条（CSS 宽度百分比，不用 Chart.js）
- 按餐次展开食物列表
- 空餐次显示"未记录" + 添加按钮
- 搜索添加 modal（复用现有 modal 结构）
- 配置 modal（身高/年龄/性别/活动水平）

### JS 逻辑
- 目标值计算函数（Mifflin-St Jeor）
- localStorage 读写
- 配置 modal 流程
- 进度条渲染
- 快速补记（POST → 异步更新，不整页 reload）

## 错误处理
- 无体重数据 → 弹窗提示"无体重数据，使用默认值 70kg"，可手动输入体重
- 无今日饮食记录 → 正常显示，4 进度条为 0，各餐次显示"未记录"
- API 失败 → toast 错误，不阻塞页面渲染
- localStorage 读写失败（隐私模式等）→ 兜底显示默认目标值

## 测试
- analysis.py: `test_get_latest_weight()` — 空列表/有重复/非 weight 类型
- routes/diet.py: 不测试渲染结果，只测试今天/历史路由返回 200

## 未纳入范围
- 编辑/删除已有记录（依赖 record ID 的后续任务）
- 阶段切换（增肌/减脂/维持）
- 手动覆盖单个目标值
- 今日 vs 上周平均对比
- 多天汇总/周报
