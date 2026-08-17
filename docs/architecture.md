# CycleRadar Trader · 系统技术脉络 V10.1

> 版本：V10.1（单一真源 `/opt/cycleradar-trader/VERSION`）
> 最后更新：2026-08-16
> 状态：活跃（当前真源，作为后续讨论的基线）
> 配套文档：CONTEXT.md、ROADMAP.md、docs/data-contract.md（数据合同 v1.1）

> 本文件不再以文件名携带版本号（旧 `architecture-v6.1.md` 已归档）。版本号只活在 VERSION 文件里，由 `admin/routes/system.js getVersion()` 统一驱动，避免"文件名永远追不上版本"的漂移。

---

## 一、版本脉络（演进史）

| 版本 | 时间 | 核心变化 |
|---|---|---|
| V6.5 | 06-26 | 文章看板全链路（source_articles 信源正文库 + enrich 双路由 + Admin 文章仪表盘） |
| V7.1–7.2 | 07-01 | 信号 Tab Alpha 三档折叠 + 轮动热度卡；/m 事件叙事置顶 + Admin 导航重组 |
| V7.3–7.5 | 07-02~04 | 策略胜率卡 + 公众号草稿箱推送；event_catalog + confidence struct + run_manifest + decision_log；**5-Tab 导航** |
| V7.5.5 | 07-06 | 实时数据拉通 + NX 推断 + appVersion 修正 |
| V8.0–8.3 | 07~08 | World Monitor Tab + 快捷问答面板；**传导图谱闭环**（graph/api_bridge + transmission_graph + event_evolution + event_monitor + 盘后验证 reinforce） |
| V9.x | 08-06~16 | 本地迭代（未单独立 git tag）：快捷问答面板、今日行动清单、World Monitor 增强 |
| **V10.1** | **08-16** | **数据管线扩展**（datalake/backfill/monitors/MCP新闻）+ **数据源统一**（杀僵尸 tracker_log）+ **单一胜率源** + **导航 6→4 收敛** + **视觉统一** + **版本号对齐** |

> 注：V9 是本地迭代区间（代码注释含 V7.9/V8.0/V9.0 标记），git 中无独立提交，最终收敛为 V10.1。

---

## 二、数据层（信源 → 采集 → 数据湖 → 契约）

### 2.1 信源清单

| 类别 | 来源 | 落点 |
|---|---|---|
| 微信公众号 | WeWe RSS × 8 源（杜牛牛/微策神机/数据宝/台球之门/低吸波段王/财闻私享/小马白话期权/叙事平权） | `wewe-rss.db`（PostgreSQL） |
| 公开 RSS | 财联社 / 36氪 / 格隆汇 / 财新 / 金十 / 第一财经 | `source_articles.db` / `rss.db` |
| 行情数据 | AKShare（行情/龙虎榜/公告/期货/历史K线） | Parquet 数据湖 + 各类 jsonl |
| MCP 工具 | Finstep MCP（`http://fintool-mcp.finstep.cn`，统一重试/超时/SSE） | 按需调用（`core/trader_mcp.py`） |
| MCP 新闻 | `ingest_mcp_news.py`（8:00 + 18:00） | 事件库 / 新闻缓存 |
| 自选池 | xlsx 手动导入（`watchlist/import`，智能解析 Tab/表头/Excel 单引号 + 3 列价格映射） | `watchlist.json` |

### 2.2 数据湖（datalake）

- **`core/datalake.py`**（V7.6 融合自 ~/交易员，V10.1 扩展）：市场数据资产层，只管"日线 bars 的存储与查询"，不管选股/契约/仓位。
- 存储：Parquet 分区 `data/lake/market/daily/yyyy=YYYY/date=YYYY-MM-DD/part.parquet`
- 查询：DuckDB（毫秒级历史回查，替代 MCP 实时消耗）
- 写入：MCP primary source + wanjun fallback；schema = `daily_bar_v1`（symbol+date 为主键）
- 校验：freshness / coverage / source quality 三合一
- 回填：`backfill_akshare.py` / `backfill_fast.py` / `backfill_market.py`（历史数据补填）

### 2.3 采集管线（主调度 + cron）

**`core/daily.py`（日报主调度，含文章/晨报模式）**

```
Phase 1    collect_data()        → 数据采集（信源全量）
Phase 1.5  check_data_integrity()→ 完整性门禁（BLOCK 中止 / DEGRADED 降级）
Phase 1.8  event_monitor.run_daily() → 事件驱动信号采集（T1/T2/T3/T4 检测器）
Phase 2    LLM 事件解读          → rotation 报告 + alpha 信号注入
           规则引擎 fallback（rotation_factor，LLM 失败时兜底）
```

**cron 调度时间表（实时读取 `crontab -l`，单一真相，动态生成）**

{{CRON_TABLE}}

**watchdog（持续）**：守护 / 心跳 / RSS 健康 / 管线健康等持续性任务均以 cron 形式存在，见上方实时调度表。

### 2.4 数据契约（关键文件，详见 docs/data-contract.md）

{{CONTRACTS_TABLE}}

**写入安全性（V10.1 已收敛部分）**
- cron 写 JSONL 时 Express 并发读 → 模型 try/catch 跳过畸形行（静默丢弃，无 rename 保护）
- 单一胜率源：`models/trader-tracker.js globalWinRateByStrategy()`（口径 A：`win/(win+lose)`，EXPIRED/PENDING/NEUTRAL 不入分母）
- 版本号单一源：VERSION 文件 = nav badge = /m appVersion = app.js 缓存戳

---

## 三、策略层（信号引擎）

### 3.1 14 模型量化扫描（`core/scanner.py`）

候选池：龙虎榜（~30 只）+ 热点行业龙头（~50 只），去重后约 80-100 只；每板块最多保留 2 只（龙一龙二过滤）。

| 模型 | 缩写 | 逻辑 |
|---|---|---|
| 钱坤寻龙 | qkxl | 龙虎榜涨停 × 热点板块 × 资金净流入 |
| 主升狙击 | zsji | 横盘突破 12 月新高 × 量价确认 |
| 回调狙击 | htji | 前期涨停波段 × 回调企稳 |
| 向上缺口 | xsqk | 跳空高开未回补 × 顺势 |
| 中线狙击 | zxji | MA60 向上 × 站上 MA5 × 放量 |
| 波段雄鹰 | bdxy | 多头排列缩量休整后放量启动 |
| 弱转强 | rzq | 横盘弱势转强放量突破 MA20 |
| 缩量地板 | sldb | 缩量到极致后放量启动 |
| 涨停回踩 | ztht | 涨停后回踩 MA5 不破不阴 |
| 高位整理 | gwzl | 高位横盘后突破创新高 |
| 均线共振 | jxgz | MA5/10/20 粘合后金叉共振 |
| 好运低吸 | hydx | 强势股回调缩量企稳 MA10 支撑 |
| 牛市第一阳 | nsdyy | 大流通盘首阳突破 爆量 MA3 向上 |
| 超强反弹 | cqft | 涨停后深度回调 强反弹承接 |

### 3.2 八因子轮动引擎（`core/rotation_factor.py`）

规则引擎先行——因子本身产出结论，LLM 只润色。

| 因子 | 名称 | 触发条件 |
|---|---|---|
| A1 | 超额收益 | 行业涨幅 − 沪深300 ≥ 3% |
| A2 | 涨停热度 | 涨停数 ≥ 3 |
| B1 | 资金确认 | 主力净流入 > 0 |
| B2 | 融资热度 | 融资余额 TOP30 且净买入 > 0 |
| C1 | 估值安全 | PE 历史分位 < 30% |
| D1 | 产业资本 | 近 5 日大宗交易净买入 > 0 |
| D2 | 机构持仓 | 龙虎榜机构净买入 > 0 |
| E1 | 舆情共振 | ETF 中枢突破或情绪共振 |

衍生指标：轮动强度（当期−上期）、轮动持续性（连续 TOP10 期数）、轮动质量（持续期数×当期得分）。三联动链路：行业 → ETF（50+ 映射）→ 商品期货（18 品种）→ 反向验证。

### 3.3 市场温度计 + 好运哥仓位（`core/timing.py` + `core/haoyun.py`）

- **timing.py**：市场温度计（8 阶段判断 + 仓位建议），数据源 Finstep MCP（涨跌家数/涨停数/上证日K）。
- **haoyun.py**：仓位纪律调节器，叠加在 timing 之上：大盘月跌幅 < −5% → 空仓；连续 3 日亏损 → 清仓；连续 2 日亏损 → ×0.5；周阴线 > 8% → ×0.3；账户创新高 → ×1.2。

### 3.4 事件驱动监测（`core/signals/event_monitor.py`）

事件识别 → 三维过滤（relevance 相关性 / novelty 新颖度 / impact 冲击力）→ 波段窗口判断 → 信号输出。不做截面强弱排名，只捕捉"事件发生 → 确定性波段窗口打开"的机会。

### 3.5 传导图谱（`core/graph/`）

轻量有向图（adjacency list，无 networkx 依赖），Node：event/sector/stock/factor；Edge：AFFECTS/CONTAINS/UPSTREAM/DOWNSTREAM/CORRELATES。

三级闭环：
```
1. build_from_library()  从 event_library.json 构建初始图
2. bfs_from_event()      事件触发时 BFS 遍历传导链
3. reinforce_edge()      盘后收益率验证 → 边权重自进化（16:30 verify_event_signals + reinforce_from_verify）
```

配套：`api_bridge.py`（图 API 桥）、`event_evolution.py`（演化管线：trace_transmission → signal_to_narrative → reinforce）、`verify_event_price.py`（盘后价格验证脚本）。

### 3.6 回测框架（`core/min_backtest.py`）

最小回测框架 v1.0：数据源切 datalake（本地 Parquet，毫秒级，无 MCP 消耗）；`batch_backtest()` 支持 14 模型 × N 股票，结果写 `backtest_winrate.json` 供 confidence 模块读取；保留单股单模型 CLI 向后兼容。

---

## 四、展示层

### 4.1 Admin 后台（6 Tab 主导航）

| Tab | 路由 | 说明 |
|---|---|---|
| 周期雷达 | `/admin/dashboard` | 首页 KPI |
| 📱 看板 | `/m` | 移动端（新窗口） |
| 交易员 | `/admin/trader` | 核心工作台（4 组子导航） |
| 文章 | `/admin/articles` | 文章仪表盘 |
| 模板 | `/admin/templates` | Prompt 模板 |
| 🟢 健康 | `/admin/health` | 健康检查 |
| 架构 | `/admin/architecture` | 本页（动态渲染 Markdown + 实时状态） |

**交易员子导航（V10.1 6→4 组收敛）**：今日行动 / 信号复盘 / 自选池 / 市场状态

| 组 | 收敛页面 |
|---|---|
| 今日行动 | 概览（/trader）、策略诊断（/strategy）、回测（/backtest）、模型库（/model-library）、事件流（/event-feed） |
| 信号复盘 | 信号跟踪（/tracker）、个股跟踪（/tracker/stock/:code）、回撤（/drawdown）、反思（/reflection） |
| 自选池 | 自选股（/watchlist，含导入/批量删除/分层） |
| 市场状态 | 轮动（/rotation） |

### 4.2 /m 移动端（5 Tab）

| Tab | data-tab | 内容 |
|---|---|---|
| 概览 | overview | 市场体温 / 今日行动清单 / 操作建议 / 信号分布 / TOP 信号股 / 快捷问答面板 |
| 自选 | watchlist | 自选池信号 / 持仓 P&L / 买卖建议 |
| 信号 | cycleradar | Alpha/ETF/商品信号 + 完整事件解读 |
| 世界 | world | 全球市场监测（A股大盘 + 行业轮动 + 商品期货） |
| 传导 | graph | 传导图谱可视化 + 信号级联 |

### 4.3 路由结构（`admin/routes/`）

`admin.js` / `trader.js` / `articles.js` / `mobile.js` / `dashboard.js` / `brief.js` / `discover.js` / `system.js` / `templates.js` / `health.js` / `scheduler.js` / `recover.js`

---

## 五、体验层

- **导航收敛**：交易员 6→4 组（今日行动/信号复盘/自选池/市场状态），删死页面 `comparison.ejs`（/compare→404）、`mobile/dashboard.ejs`。
- **视觉统一**：`--cr-*` 语义色令牌（design-tokens.md + components.css + style.css），消除硬编码色 + 暗底悬空浅色变量。
- **版本对齐**：VERSION 文件 = nav badge = /m appVersion = app.js 缓存戳（单一源 `getVersion()` 驱动，读不到时 fallback V10.1）。

---

## 六、已知技术债（实时检测）

{{TECHDEBT_TABLE}}

> 已处置：tb-5 旧 `architecture-v6.1.md` 已归档为 `architecture-v6.1.md.archived`（本文件 `architecture.md` 已接管，不再单列）。
