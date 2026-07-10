# 饮食页面设计

## 概述
在 XunJi 分析工具中新增完整的饮食 Tab 页面，提供 90 天饮食记录查看、图表分析、和写回功能。沿用训练页面已验证的 tab-bar 布局模式。

## API 能力
SKILL-diet.md 定义了完整的 Open API：
- **查询饮食记录** — `POST /open/food/query_gzip`，支持 date range + include_detail
- **搜索官方食物** — `POST /open_agent/food/search_gzip`，按关键词匹配
- **写回饮食记录** — `POST /open/food/upsert_gzip`，新增/修改/覆盖饮食记录
- **创建自定义食物** — `POST /open/food/custom/upsert_gzip`
- **查询/套用模板** — `POST /open/food/templates/list_gzip` / `apply_gzip`

当前 api_client.py 已实现：query_diet / search_food / upsert_diet。新增 upsert_custom_food 方法。

## 后端改动

### analysis.py
新增 `summarize_diet_detailed(records)` 函数，返回：
- `daily_calories` — 每日总热量 dict[date → kcal]
- `daily_macros` — 每日宏量营养素 dict[date → {protein, carbs, fat}]
- `meal_breakdown` — 数据源 dict[date → meal_type → [{name, amount, unit, ntr}]]
- `total_ratio` — 总热量中蛋白/碳水/脂肪百分比（按 kcal，1g 蛋白=4kcal，1g 碳水=4kcal，1g 脂肪=9kcal）
- `summary` — 日均热量、日均蛋白/碳水/脂肪、有记录天数

### routes/diet.py
- `GET /diet` — 扩大 range 为过去 90 天，传参 `range_days` 支持前端切换
- `GET /diet/search` — 代理搜索接口：keyword + limit → 返回食物列表
- `POST /diet/upsert` — 代理写回接口：接收确认后的 payload → 调用 api_client.upsert_diet()。遵循 SKILL 规则：不在前端写回前自动确认
- 返回的数据格式：{records, summary: summarize_diet_detailed(records), meal_breakdown, dates_with_data: [date list]}

### api_client.py
- 新增 `upsert_custom_food(payload)` 方法，调 `POST /open/food/custom/upsert_gzip`

## 页面结构 — 3 Tab

### Tab 1: 日历
- 90 天日历热力图（训练页频率日历同类实现）
- 有记录日期标记绿色点/绿色深浅强度
- 点选日期 → 自动切换到分析 tab 对应日期的详情视图？或者日历 tab 下方就展开当日三餐详情
- **设计决定：日历 tab 下方直接展开当日三餐详情**，点日期更新下方内容
- 当日详情包括：早餐/午餐/晚餐/加餐 四个 meal 区域
- 每个 meal 区域显示食物列表：食物名、份量、热量、蛋白/碳水/脂肪
- 选中的日期高亮

### Tab 2: 分析
- 四张 stat 卡片：日均热量 / 日均蛋白质 / 日均碳水 / 日均脂肪
- 热量折线图（range 切换：7 / 30 / 90 天）
- 宏量营养素堆叠柱状图（每日蛋白+碳水+脂肪堆叠，同 range）
- 宏量营养素占比 donut 图（按热量占比：蛋白质×4kcal, 碳水×4kcal, 脂肪×9kcal）
- 无数据时显示占位文本

### Tab 3: 饮食管理
- 日期选择器（默认今天，可选任意有记录的日期）
- 按餐次展示当日食物列表
- 每行显示：食物名 / 份量 / 热量 / 蛋白 / 碳水 / 脂肪
- 每条记录右侧有编辑(✎)和删除(✕)按钮
- 顶部有"添加记录"按钮
- 添加/编辑流程：
  1. 弹出 modal -> 选择餐次（早餐/午餐/晚餐/加餐）
  2. 输入食物名称搜索前端 → 调 `/diet/search` 后端代理 → 显示搜索结果
  3. 选择食物 → 输入份量（默认单位 g）
  4. 显示写入预览（食物名 + 每 100g 营养 + 按份量折算营养）
  5. 用户确认 → 调 `POST /diet/upsert`
  6. 成功 → 刷新当日数据
- 删除流程：确认弹窗 → 写回 empty/remove → 刷新
- **写回确认原则**：所有写回操作必须在用户确认后才发起 API 调用，SKILL-diet 规则强制执行

## 写回安全规则
- 所有编辑/添加/删除操作前展示变更摘要
- 未经用户确认不调用任何写回接口
- 搜索食物不走写回路径
- upsert 成功后刷新缓存

## 前端实现
- 模板：`templates/diet.html` — 全部重写，沿用 base.html 模式
- CSS：复用现有 app.css 中的 tab-bar、tab-panel、chart-container、stat、btn、grid-2 等 class。新增 diet-specific CSS（meal-section、food-row、modal 等）写在 diet.html 的 `<style>` 块中
- JS：页面内 `<script>` 块，内容：
  - Tab 切换逻辑（同 training 页模式）
  - Calendar 渲染（90 天热力格子，同 training 频率日历风格）
  - Chart.js 图表渲染（折线图、堆叠柱状图、donut 图）
  - Meal view 渲染（当日三餐展开）
  - Modal 交互（添加/编辑食物）
  - Search + upsert fetch 调用
- 范围切换按钮（7/30/90天）同 training 页 btn-range 模式
- 移动端适配：grid-2 → 单列，calendar 缩小

## 数据流

```
加载页面:
  GET /diet ──→ diet.py: diet_page() ──→ data_service.get_diet(90天) ──→ api_client.query_diet()
                 └──→ analysis.summarize_diet_detailed(records)
                 └──→ 返回 {records, summary, dates_with_data} 给模板

搜索食物:
  前端 ──→ GET /diet/search?keyword={}&limit=8 ──→ api_client.search_food() ──→ 训记 API

写回食物:
  前端确认 ──→ POST /diet/upsert {foods: [...]} ──→ api_client.upsert_diet() ──→ 训记 API
                 └──→ 返回成功 → 前端刷新数据
```

## 错误处理
- API 失败 → 页面内显示 toast/错误消息，不破坏现有视图
- 缓存：data_service.get_diet() 已有 sqlite 缓存，写回后清除对应日期的缓存
- 无数据 → 每个 tab 各自显示空状态文字
- 搜索无结果 → 显示"未找到匹配食物"

## 测试
- analysis.py: 测试 summarize_diet_detailed 的正确定输出
- routes/diet.py: 测试 GET /diet 返回正确格式
- 写回：暂不测试（依赖真实 API key）

## 未纳入范围
- 饮食模板查询/套用（方二阶段）
- 自定义食物创建（方二阶段）
- 周报/macros target 设置
- 多用户
