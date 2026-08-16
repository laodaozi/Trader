# CycleRadar Trader — 数据合同 v1.1 (V10.1)

> **目标**: 所有 /m 和 /admin/trader 页面上展示的每一个数字，都必须能追溯到唯一的源文件、唯一的聚合规则和唯一的新鲜度定义。这是后续所有修复的地基。

## 0. 版本号 ✅ 已对齐 (V10.1)

| 属性 | 值 |
|---|---|
| 源文件 | `/opt/cycleradar-trader/VERSION` |
| 当前值 | `V10.1` |
| 可靠？ | ✅ **已对齐**——VERSION 文件 = nav badge = /m appVersion = app.js 缓存戳 |
| 读取方式 | `admin/routes/system.js` `getVersion()` 读 VERSION 文件；`/m` appVersion 与 `admin` badge 均由此驱动 |
| 兜底 | 读不到 VERSION 时 fallback = `V10.1`（mobile.js / admin.js / dashboard.ejs 三处已统一） |
| 变更历史 | ~~6.1.1 不可靠，页面各自硬编码~~ → V10.1 单一源对齐（本轮修复）|

---

## 1. 策略/选股

| 属性 | 值 |
|---|---|
| 源文件 | `data/trader_strategy.jsonl` (366KB, 745 条记录) |
| 读取模型 | `admin/models/trader-strategy.js` |
| 写入 Cron | `15:35 1-5` → `score.py --scan-all` → `strategies/stock_agent_runner.py` |
| 写入原子性 | ⚠️ 按行追加，无 rename 保护。Express 读时可能读到半截行（模型有 try/catch 跳过畸形行） |

**Schema（每行）:**
```json
{
  "date": "2026-08-12",
  "code": "002049",
  "name": "紫光国微",
  "nx": "buy|rising|sell",
  "ma_align": "多头排列|空头排列|...",
  "fib_zone": "0.382|0.5|0.618|...",
  "weekly_dir": "up|down|flat",
  "capital_dir": "inflow|outflow|平衡",
  "rr": 2.5,
  "model_hits": ["momentum", "volume"],
  "signal_type": "🔥进攻|✅买入|🕐埋伏|—观望",
  "strategy": "nx_breakout|ma_reversal|...",
  "score": 48,
  "entry_low": 70.0,
  "entry_high": 72.0,
  "stop_loss": 65.0,
  "take_profit": [80.0, 85.0],
  "error": null
}
```

**聚合规则：**

| 展示数字 | 聚合方式 | 展示位置 |
|---|---|---|
| 今日选股数 | 最新 date 去重 code 后 count | `/admin/trader` KPI, `/m` |
| 均分 | `sum(scores) / count` | `/admin/trader` KPI |
| 信号分布 | 按 signal_type 统计: 🔥进攻 / ✅买入 / 🕐埋伏 / —观望 | `/admin/trader` 信号条 |

**新鲜度规则:**
- 交易日 15:35 后 → 当日数据
- 非交易日 → 最近交易日数据（无"数据是昨天的"标注 ⚠️）
- 15:35 前 → 前一日数据

---

## 2. 信号跟踪

| 属性 | 值 |
|---|---|
| 源文件 | `data/trader_tracker.jsonl` (918KB, **2235 条记录**) |
| **已弃用源** | `data/tracker_log.jsonl` (177 条, ⚠️ 最后更新 2026-07-07 — **36 天前的僵尸文件**) |
| 读取模型 | `admin/models/trader-tracker.js` |
| 写入 Cron | `15:50 1-5` → `scripts/update_tracker_verdicts.py` |

**Schema（每行）:**
```json
{
  "code": "002049",
  "name": "紫光国微",
  "signal_date": "2026-06-12",
  "horizon": 5,
  "entry": 71.51,
  "stop": 65.79,
  "targets": [81.94],
  "result": "HIT|MISS|EXPIRED|PENDING|NEUTRAL",
  "max_return": null,
  "max_dd": null,
  "final_return": null,
  "hit_target": null,
  "hit_stop": null,
  "days_to_target": null,
  "days_to_stop": null,
  "n_bars": 0,
  "track_date": "2026-06-19",
  "signal_type": "✅买入",
  "strategy": "nx_breakout",
  "score": 52
}
```

**result 枚举:**
- `HIT` — 命中止盈目标
- `MISS` — 触发止损
- `EXPIRED` — 到期未触发任何条件（OHLC 数据缺失导致无法裁决）
- `PENDING` — 仍在跟踪窗口内
- `NEUTRAL` — 波动不足，不做裁决

**实际计数 (2026-08-12):**

| result | 数量 | 占比 |
|---|---|---|
| HIT | 52 | 2.3% |
| MISS | 32 | 1.4% |
| EXPIRED | 1,848 | **82.7%** |
| PENDING | 293 | 13.1% |
| NEUTRAL | 10 | 0.4% |
| **总计** | **2,235** | |

**聚合规则：**

| 展示数字 | 聚合方式 | 展示位置 | 可靠？ |
|---|---|---|---|
| 跟踪记录总数 | `records.length` | `/admin/trader` KPI | ✅ |
| 20日胜率 | `HIT / (HIT+MISS)` for horizon=20 | `/admin/trader` 结论行 | ✅ |
| 全站胜率（口径A）| `win/(win+lose)`，win=HIT, lose=MISS，EXPIRED/PENDING/NEUTRAL 不入分母 | `/m` 概览、strategy-report、insights、reflection 矩阵 | ✅ **单一源** |

**口径 A（全站统一，V10.1）:**
- 唯一计算源：`admin/models/trader-tracker.js` `globalWinRateByStrategy()`
- strategy-report + insights.buildStrategyStats **委托调用**此函数，不再各自实现
- 活文件 = `data/trader_tracker.jsonl`（HIT/MISS 枚举）；废弃 `tracker_log.jsonl`（WIN/LOSE 枚举）不再被任何代码读取

**关键问题:**
- ✅ **已修 (V10.1)**：`mobile.js _readAllTracker`、`trader.js REFLECTION_PATH`、strategy-report、insights 曾误读僵尸文件 `tracker_log.jsonl` → 全部改读活文件 `trader_tracker.jsonl`（命中率 0%→39%，票数 20→140）
- ✅ **已修 (V10.1)**：全站胜率收敛为单一源 `globalWinRateByStrategy()`，消除多处口径不一致
- ⚠️ **遗留**：EXPIRED 占比高——绝大多数信号因 OHLC 回填失败无法裁决，损害回测可信度（上游数据问题，超本轮范围）

---

## 3. 市场体温

| 属性 | 值 |
|---|---|
| 源文件 | `data/timing_history.json` (5.5KB) |
| ⚠️ 最后更新 | **2026-08-05** — 7 天前 |
| 写入者 | 未在 cron 中找到明确写入任务（可能由定时脚本间歇写入） |

**Schema:**
```json
{
  "history": [
    {
      "date": "2026-08-05",
      "temperature": 81,
      "phase": "震荡回调",
      "index_direction": "down"
    }
  ]
}
```

**聚合规则:**

| 展示数字 | 聚合方式 | 展示位置 |
|---|---|---|
| 市场温度 | `history[-1].temperature` 取整 | `/admin/trader` KPI, `/m` |
| 阶段 | `history[-1].phase` | `/admin/trader` KPI 副标题 |
| 建议 | 硬编码规则: phase 包含"上涨/进攻"→积极 / "回调"且温度>60→控制仓位 / "回调"→观望 / "震荡"→高抛低吸 | `/m` |

**🟡 问题:**
- 7 天未更新——页面上没有"数据可能过期"的标注
- 温度 81° "即将见顶"是真实信号，但被标了 warn 色——用户分不清这是"系统出问题"还是"市场出问题"

---

## 4. 账户快照

| 属性 | 值 |
|---|---|
| 源文件 | `data/positions.json` (4.8KB, 今日更新) |
| 写入者 | 推测 PM2 定时任务写入 |

**Schema:**
```json
{
  "account_state": "normal|warning|danger",
  "meta": {
    "position_ratio": 0.65,
    "available_cash": 50000,
    "total_capital": 200000,
    "last_updated": "2026-08-12T06:43:00Z"
  },
  "holds": [
    {
      "code": "002049",
      "name": "紫光国微",
      "cost": 71000,
      "current_value": 72500,
      "pnl_pct": 2.1
    }
  ]
}
```

**聚合规则:**

| 展示数字 | 聚合方式 | 展示位置 |
|---|---|---|
| 持仓数 | `holds.length` | `/m` |
| 总市值 | `sum(current_value)` | `/m` |
| 可用现金 | `meta.available_cash` | `/m` |
| 仓位比例 | `meta.position_ratio` | `/m` |

---

## 5. 策略反思

| 属性 | 值 |
|---|---|
| 源文件 | `data/strategy_reflection.json` (7.6KB, 今日更新) |
| 写入 Cron | `strategy_reflection_cron`（时间未在 crontab 中明确列出） |

**用于：** `/admin/trader` 首页 LLM 反思摘要区块

---

## 6. 事件叙事

| 属性 | 值 |
|---|---|
| 源文件 | `data/event_narrative_latest.json` (31KB, 今日更新) |
| 写入 Cron | 晨间 6:22 → `bridge_morning.js` → `enrich_morning_cron.sh` |

**用于：** `/admin/trader` 首页"今日主线"区块, `/m` narrative 区块

---

## 7. 流水线心跳

| 属性 | 值 |
|---|---|
| 判断方式 | 检查 `data/logs/` 下对应 cron log 文件的 mtime |
| 判定规则 | mtime 日期 === 今日 → `ran: true`, 否则 `ran: false` |

**受监控管线：**
| key | label | cron 时间 |
|---|---|---|
| scanner | Scanner 14模型 | 15:35 |
| ma_signals | 兼并重组信号 | ? |
| reflection | LLM策略反思 | ? |
| stock_agent | Stock Agent | 15:35 |

**🟡 问题：**
- 仅检查文件修改时间，不检查执行是否成功
- 如果 cron 跑了但报错退出，依然显示 ✅
- ma_signals 和 reflection 的 cron 时间不明确

---

## ⚠️ 数据写入安全性

| 问题 | 风险 | 当前保护 |
|---|---|---|
| cron 写 JSONL 时 Express 并发读取 | 可能读到半截行 | 模型 try/catch 跳过畸形行（**不报错，静默丢弃**） |
| 无原子写入 | 不完全写入被读到 | 无 rename 保护 |
| 僵尸文件 | `tracker_log.jsonl` 36天未更新仍存在 | 代码注释说明已弃用，但容易混淆 |

---

## 🔴 优先级行动项（关联后续步骤）

| # | 行动 | 状态 |
|---|---|---|
| 1 | 废弃 `tracker_log.jsonl`，所有代码改读活文件 | ✅ 已完成 (V10.1) |
| 2 | 全站胜率收敛为单一源（口径 A） | ✅ 已完成 (V10.1) |
| 3 | VERSION 文件更新 + 页面统一读取 | ✅ 已完成 (V10.1) |
| 4 | 导航 6→4 收敛，删除死页面（comparison / mobile/dashboard） | ✅ 已完成 (V10.1) |
| 5 | EXPIRED 标签注明"（20日窗口）"或改为全量统计 | ⬜ 遗留 |
| 6 | timing_history.json 超期未更新时前端标注 | ⬜ 遗留 |
| 7 | 模型读取 JSONL 增加 mtime 检查 / 新鲜度字段 | ⬜ 遗留 |
