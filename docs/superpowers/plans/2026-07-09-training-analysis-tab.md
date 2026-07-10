# 训练分析 Tab — 实现计划

**目标：** 用训练分析 tab 替换部位分布饼图

## 步骤

### Step 1: analysis.py — 新增四个分析函数
- `training_duration_trend(trains)` → 每训练耗时（分钟）列表
- `calories_trend(trains)` → 每训练消耗热量（kcal）列表
- `heart_rate_trend(trains)` → 每训练平均/最高心率列表
- `training_type_distribution(trains)` → 各类型训练频次

### Step 2: 新增测试
- 每个函数一个测试用例（含空数据）

### Step 3: 更新 training.html
- 把"部位分布" tab 内容替换为"训练分析"的四个子图表

### Step 4: 更新 routes/training.py
- 在训练页渲染时传入新分析数据
- 移除旧的 `bodypart_data` 传递

### Step 5: 验证
- 页面渲染正常
- 测试通过
