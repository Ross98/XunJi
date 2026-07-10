# 饮食搜索与输入体验改进实现计划

**目标：** 添加食物 modal 支持单位选择、自定义食物创建、搜索降级建议

## Task 1: 后端 — custom_food 端点
- data_service.py: 添加 upsert_custom_food wrapper
- routes/diet.py: 添加 POST /diet/custom_food

## Task 2: 前端 — 添加 modal 单位选择
- 选中食物后显示 units 下拉菜单
- updatePreview 按选中 unit 换算克重计算营养
- 写回 payload 始终用 gram

## Task 3: 前端 — 自定义食物 modal + 搜索降级
- 自定义食物 modal HTML + JS
- 搜索无匹配时显示自定义食物按钮
- 创建成功 → 自动写回 + 刷新

## Task 4: 启动验证
