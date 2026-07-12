# Training Plan Recommendation — Design Spec

## Goal
Provide a data-driven next-session recommendation based on the user's recent training history and Apple Health recovery signals. The page shows what muscle group is most under-trained and which specific movements to prioritize.

## Data Sources
- **训记 API** (`api_trains_for_llm_v2`): last 30 days of training records (movements, sets, date)
- **Apple Health** (local SQLite): HRV, sleep summary for recovery context

## Algorithm

### Step 1 — Classify movements
Map each movement name to a primary muscle group using `classify_bodypart()` in `analysis.py`:
| Group | Examples |
|---|---|
| push | 卧推, 推胸, 飞鸟, 推举, 侧平举, 哑铃卧推, 哑铃飞鸟 ... |
| pull | 划船, 引体向上, 下拉, 弯举, 硬拉, 哑铃划船 ... |
| legs | 深蹲, 腿举, 弓步, 臀推, 罗马尼亚硬拉, 哑铃直腿硬拉 ... |
| core | 卷腹, 平板支撑, 俄罗斯转体, 平躺曲腿旋转, 仰卧起坐 ... |
| cardio | 步行, 跑步, 骑行, HIIT ... |

Movements that don't match any group → tagged as "other" and ignored.

### Step 2 — Score each group
For each muscle group, compute a **deficit score** (higher = more under-trained):

```
deficit = (day_penalty × max_days_observed) - group_frequency
```
- `group_frequency` = total sets × 10 + total sessions × 5 (weighted by training volume, not just session count)
- `day_penalty` = days since last session (longer gap → higher deficit)
- `max_days_observed` = max frequency across all groups (normalizes between groups)

Simpler approach used instead:
1. Count how many days each group was trained in last 30 days
2. Days since last training for each group
3. Deficit = (30 - group_days_ratio) × days_since_last
→ Higher = more urgent to train

### Step 3 — Recommend movements
For the most-deficient group, suggest movements the user:
- Has done before but hasn't done recently (highest priority)
- Has never done but belongs to the target group (lower priority)

Source: gathered from historical training data + built-in suggestion list per group.

### Step 4 — Recovery context
From Apple Health dashboard data:
- **HRV (30d trend)** vs training-day/rest-day baseline → 🟢/🟡/🔴
- **Last night sleep** → total hours + deep sleep ratio
- **Recommendation text**: "High recovery — ideal for heavy legs day" / "Moderate — keep to upper body" / "Low — rest or light cardio"

## UI Layout

```
┌─ 今日训练建议 ─────────────────────────┐
│  🟥 腿部训练不足（28天未专项训练）      │
│                                        │
│  建议动作:                              │
│  ▸ 深蹲  (上次训练: 28天前)             │
│  ▸ 罗马尼亚硬拉  (上次训练: 28天前)     │
│  ▸ 弓步    (从未训练过)                  │
│                                        │
│  恢复状态: 🟢 HRV良好 · 睡眠充足       │
│  适合中高强度训练                        │
└────────────────────────────────────────┘

┌─ 训练分布 (30天) ─────────────────────┐
│  push ████████░░░░ 4次  最近: 5天前   │
│  pull ██████░░░░░░ 3次  最近: 7天前   │
│  legs ██░░░░░░░░░░ 1次  最近: 28天前  │
│  core ██████░░░░░░ 3次  最近: 1天前   │
└────────────────────────────────────────┘
```

## Files to Change

| File | Change |
|---|---|
| `planner.py` | Rewrite: implement movement classification, deficit scoring, recommendation logic |
| `routes/plan.py` | Simplify: use cached training data, pass plan + recovery data to template |
| `templates/plan.html` | Redesign: show priority cards, distribution chart, recovery status |
| `routes/dashboard.py` | Export sleep_last / recovery data (already done) or make reusable |

## Non-goals
- No write-back to 训记 API (no plan creation)
- No calendar/scheduling UI
- No exercise catalog browser

## Edge Cases
- No training data in last 30 days → "No recent data, start with full body"
- All groups equally trained → "Balanced, continue current routine"
- Apple Health not available → show plan without recovery section
