<p align="center">
  <img src="static/img/dashboard.png" alt="寻迹仪表盘" width="100%">
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python"></a>
  <a href="https://github.com/Ross98/XunJi/releases/tag/v2.0"><img src="https://img.shields.io/badge/release-v2.0-brightgreen.svg" alt="Release"></a>
</p>

# 寻迹 · 训记数据分析面板

基于 [训记](https://xunjiapp.cn) Open API + Apple Health 的个人健身数据分析仪表盘。

## v2.0 新功能

### Apple Health 集成
- 导入 Apple Health 导出数据（支持 59 种指标类型、9 种训练类型）
- 流式解析 1.3GB+ XML，逐批写入 SQLite 缓存
- 支持增量重复导入（`INSERT OR IGNORE`）

### 健康仪表盘
- **恢复指标** — HRV、静息心率、VO₂Max 最新值
- **今日活动** — 步数、活动分钟、站立时间（取前一日数据）
- **趋势图表** — HRV / 静息心率 / VO₂Max 近 90 天折线图（独立卡片）
- **健康评分** — 由 HRV / RHR / VO₂ / 步数 / 运动融合计算

### 恢复分析
- 训练日 vs 休息日的 HRV 对比
- 14 天 HRV 趋势图（训练日红色标记）
- 自动从训记 API + Apple Watch 合并训练计数

### 身体数据融合
- Apple Health 体重/体脂自动补充训记 API 空白日期
- 数据来源标记：训记 / 🍎

### 训练计划增强
- 热量赤字分析（基础代谢 + 活动消耗 - 饮食摄入）
- 基于 Apple Health 恢复状态（HRV / 静息心率）的强度建议
- 动作分类：推/拉/腿/核心/有氧 自动归类
- 赤字达标/缺口/剩余可视化

### 睡眠分析
- Apple Health 睡眠阶段导入（核心/深度/REM）
- 睡眠阶段分布图表（深睡/REM/浅睡/清醒）

## 功能清单

| 模块 | 功能 |
| --- | --- |
| **仪表盘** | 训练摘要、身体数据、健康指标、恢复分析 |
| **训练记录** | 日/周/月/年维度、日历视图、分析报告 |
| **饮食追踪** | 三餐记录、营养素统计、CRUD |
| **身体数据** | 体重/体脂/BMI（训记 + Apple Health 融合） |
| **健康数据** | HRV / 静息心率 / VO₂Max 趋势、睡眠分析 |
| **训练计划** | 自动生成、赤字分析、恢复上下文 |

## 快速开始

### 1. 配置 API Key

从训记 App → 设置 → Open API 获取密钥，创建 `.env`：

```bash
cp .env.example .env   # 或手动创建
```

| 变量 | 说明 |
| --- | --- |
| `XUNJI_API_KEY` | 训练数据 API Key |
| `XUNJI_FOOD_API_KEY` | 饮食数据 API Key |
| `XUNJI_FOOD_SEARCH_API_KEY` | 食物搜索 API Key |
| `XUNJI_BODY_API_KEY` | 身体数据 API Key |

### 2. 导入 Apple Health 数据

从 iPhone：设置 → 健康 → 导出所有健康数据 → 解压到 `Apple Health/apple_health_export/`
然后在 `/health` 页面点击"开始导入"（首次约 2-5 分钟）

### 3. 本地运行

```bash
pip install -r requirements.txt
uvicorn app:app --reload
```

打开 http://localhost:8000

### 4. Docker

```bash
docker build -t xunji .
docker run -p 8000:8000 --env-file .env xunji
```

## 项目结构

```
├── app.py                  # FastAPI 入口
├── app_state.py            # 全局状态
├── config.py               # 配置
├── api_client.py           # 训记 API HTTP 客户端
├── cache.py                # SQLite 缓存层
├── data_service.py         # 数据服务（缓存+API 读写）
├── analysis.py             # 数据分析函数
├── movements_cn.py         # 训练类型/动作中英文映射
├── apple_health.py          # Apple Health 导入 & 查询引擎
├── planner.py              # 训练计划生成器
├── routes/                 # 路由模块
│   ├── dashboard.py        # 仪表盘
│   ├── training.py         # 训练记录
│   ├── diet.py             # 饮食追踪
│   ├── body.py             # 身体数据
│   ├── health.py           # Apple Health 数据
│   └── plan.py             # 训练计划
├── templates/              # Jinja2 模板
├── static/                 # 静态资源
│   └── img/                # 项目图片
├── tests/                  # 单元测试
└── Dockerfile
```

## 技术栈

- **后端** — Python 3.11+, FastAPI, uvicorn
- **模板** — Jinja2, HTML, Chart.js
- **数据** — SQLite（Apple Health 缓存）, httpx（API）, pandas（分析）
- **测试** — pytest
- **部署** — Docker

## 数据

- **xunji_cache.sqlite** — 训记 API 响应缓存（自动过期）
- **apple_health_cache.sqlite** — Apple Health 数据缓存（一次性导入）

## 资料

- [训记官方动作中文名表](./docs/Xunji-movements.md)
- [训记 App](https://xunjiapp.cn) — iOS/Android
