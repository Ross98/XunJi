# 饮食食物搜索与输入体验改进

## 概述
改进添加食物 modal 的体验：支持常用单位选择、自定义食物创建、搜索降级建议。目标让用户不用称重也能按份记录。

## 改动一：单位选择
搜索选中的食物如果有 `units` 字段，在份量输入框旁显示单位下拉菜单。
- 默认选中 `g`
- 有 `units` 时列出所有可选单位，显示换算克重 `个 (55g)`
- 选非 `g` 单位时，amount 输入框变为数量（整数），后端按 `gram` 换算实际克重
- `updatePreview()` 用换算后的克重重新计算营养
- 写回 payload 的 `amount` 为实际克重，`unit` 为 `g`

## 改动二：自定义食物创建
搜索无匹配时或点 `[+ 自定义食物]` 按钮，打开自定义食物 modal：
- 表单：食物名、每100g热量/蛋白/碳水/脂肪、默认单位
- 确认后调 `POST /diet/custom_food` → 后端 `api_client.upsert_custom_food()` 
- 成功后自动用返回的 uniquekey 调 `POST /diet/upsert` 写回当日饮食记录
- 刷新页面

## 改动三：搜索降级建议
搜不到时改为显示：
- "未找到 XX，试试相近关键词？"
- `[+ 自定义食物]` 按钮

## 后端改动
- `routes/diet.py` 新增 `POST /diet/custom_food` 端点
- `data_service.py` 新增 `upsert_custom_food(payload)` wrapper

## 前端改动
- `templates/diet.html` 修改添加 modal 的单位选择 + 自定义食物 modal + 搜索降级 UI

## 未纳入范围
- 条形码扫描
- AI 拍照识别
- LLM 估算
