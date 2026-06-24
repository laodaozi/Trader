# CycleRadar Trader · CONTEXT

> 最后更新：2026-06-20 · **V6.1.1**  | wanjun screener 上线 + tracker_closer OHLC 闭环 + RSS 财经早餐第 9 源 + 批量导入/排序/文章统计

---

## 1. 项目定位

**CycleRadar Trader** 是 Scott 的个人 AI 投研辅助工具链，核心目标：

> 每个交易日打开 `/m`，5 秒内看到当日 4 分类信号（热点事件 / alpha / ETF / 商品），判断市场姿态，决定当日是否加仓/减仓/观望。

**不是**：量化交易系统、公开产品、团队协作工具。  
**是**：Scott 一个人用的、可信赖的每日投研 checklist。

---

## 2. 系统组成

### 2.1 项目结构（本地 Mac）

```
cycleradar-trader/
├── core/                          # Python 策略引擎
│   ├── strategies/
│   │   ├── report_agent.py        # 事件聚合 → Alpha → 行业预判 (2125行, V4.3: alpha_signals → /m 主 alpha 源)
│   │   ├── stock_agent.py         # 个股票池 Agent (ECS 已部署 06-10, pipeline 走通, 06-10 cron 产出 17 条信号)
│   │   ├── ma_signals.py          # 均线/公告异动
│   │   ├── rotation_factor.py     # 行业轮动
│   │   ├── wanjun_screener.py     # V6.1: 万军选股模型 2/8/10 (wanjun_models → upstream_signals.jsonl)
│   │   └── tracker_closer.py      # V6.1: 日频 OHLC 裁决引擎 (腾讯 API, HOLD→WIN/LOSE)
│   ├── scripts/
│   │   ├── enrich_hot_events.py   # LLM 热点事件增强 (289行)
│   │   └── enrich_morning.js      # LLM 日报增强 (289行)
│   ├── signals/
│   │   ├── upstream_signals.py    # 信号写入/读取
│   │   ├── signal_tracker.py      # 信号追踪
│   │   └── adapters/
│   │       └── stock_agent_adapter.py  # stock_agent → 标准信号 (06-10 产出 17 条)
│   ├── daily.py                   # 日报流水线 (2942行，需 Mac MCP，V4.4: alpha_latest.json + event_narrative_latest.json → ~/交易员/data/)
│   └── backtest/                  # 回测框架
├── admin/                         # Express 后台 (PM2 trader-admin, port 3100)
│   ├── server.js                  # 入口，挂载子路由 (mobile/scheduler)
│   ├── scheduler.js               # V5.0: 文件级调度器状态追踪 (heartbeat/getStatus/getHistory)
│   ├── routes/
│   │   ├── mobile.js              # /m 移动端 API (含 contracts 读取)
│   │   └── scheduler.js           # V5.0: 调度器 API (3 endpoints, X-Scheduler-Token auth)
│   ├── models/signals.js          # 信号数据层 (STRATEGY_CATEGORY_MAP)
│   └── public/mobile-assets/app.js  # 移动端 JS (概览今日研判 + CycleRadar alpha_latest) 
├── data/
│   ├── upstream_signals.jsonl     # 运行时信号总线 (V4.3: Pipeline A report_agent + Pipeline B stock_agent/ma_signals/rotation)
│   ├── hotevents_cache.json       # 热点事件缓存 (6条, LLM enrich 产出)
│   └── → ~/交易员/data/            # V4.4: 3 契约文件 (upstream_signals.jsonl / alpha_latest.json / event_narrative_latest.json)
└── docs/                          # 架构图、预览HTML、本文档
```

### 2.1.1 双 Pipeline 信号交换机

```
Pipeline A (Mac, 事件驱动 LLM)
  daily.py → report_agent.py → alpha_signals[]
     ↓ _write_trader_contract() [V4.4]
  ┌─ data/upstream_signals.jsonl            ←── 事件→sector→stock (4 条/日)
  ├─ data/alpha_latest.json                 ←── Alpha 快照 (entry/target/stop/thesis)
  └─ data/event_narrative_latest.json       ←── 叙事汇总 (events/sector_outlook/global_conclusion)
     ↓ sync_to_ecs.sh
  ECS /opt/cycleradar-trader/data/ (3 契约文件)
     ↓ signals.js
  /m alpha tab

Pipeline B (ECS, 规则引擎 — fallback)
  stock_agent.py / ma_signals.py / rotation_factor.py / wanjun_screener.py
     ↓
  ECS /opt/cycleradar-trader/data/upstream_signals.jsonl

tracker_closer 闭环 (ECS, cron 16:00 Mon-Fri)
  tracker_closer.py (腾讯 OHLC + HOLD 裁决)
     ↓
  ECS /opt/cycleradar-trader/data/trader_tracker.jsonl (WIN/LOSE 标记)
```

### 2.2 ECS 部署 (root@139.196.115.64)

| 服务 | PM2 ID | 端口 | 启动方式 | 状态 |
|------|--------|------|----------|------|
| `simensuji-backend` | 0 | 3000 | fork | online |
| `trader-admin` | 3 | 3100 | fork | online |
| `wewe-rss` | 4 | 4000 | fork → **ecosystem.config.js** | online |

**wewe-rss 核心配置**：`/opt/wewe-rss-deploy/ecosystem.config.js`（V4.2 新增，单一事实来源）
- `CRON_EXPRESSION=35 5,8,17,22 * * *`（每日 4 次 feeds 同步）
- `DATABASE_URL=file:../data/wewe-rss.db`（SQLite）
- `PORT=4000, HOST=0.0.0.0`

**ECS 资源**：CentOS 8, Node v20.20.2, Python 3.9.6/3.6.8, 1.7GB RAM, 40GB 磁盘 (33GB free)

**PM2 持久化**：`systemctl enable pm2-root`，机器重启 → `pm2 resurrect`（从 dump.pm2 恢复全部进程 + env）

**V5.0 调度器 API**（`trader-admin:3100/admin/api/scheduler/`）：
| 端点 | 方法 | 认证 | 用途 |
|------|------|------|------|
| `/heartbeat` | POST | `X-Scheduler-Token` | cron 每阶段成功后回调 `{stage, status, exit_code}` |
| `/status` | GET | `X-Scheduler-Token` | 查询调度器状态（running/done/failed） |
| `/history?days=7` | GET | `X-Scheduler-Token` | 按日查询历史执行记录 |

**V5.0 3 契约文件桥**（`/opt/trader/output/contracts/` ← cron 2.5 sync ← Mac `~/交易员/data/`）：
| 文件 | 内容 | 消费端 |
|------|------|--------|
| `alpha_latest.json` | Alpha 信号快照（entry/target/stop/thesis/sector） | `/m/api/cycleradar.alpha_latest` → app.js _buildCrSignalDetail() |
| `event_narrative_latest.json` | 事件叙事汇总（events/sector_outlook/global_conclusion） | `/m/api/summary.event_narrative` → app.js buildNarrativeCard() |
| `upstream_signals.jsonl` | 信号总线（Pipeline A + Pipeline B） | `/m/api/cycleradar.alpha/etf/commodity` → signals.js |

**数据管道（V5.0 确定方案）**：
```
Mac ~/交易员/cron_daily.sh (launchd 08:07 Mon-Fri)
  step 1: trader.py → data/*.json
  step 2: tracker.py
  step 2.5: cp data/{alpha_latest,event_narrative_latest,upstream_signals}.json → contracts/
         → git push origin gh-pages
         → ssh ECS: cd /opt/trader/output && git pull origin gh-pages
  → ECS /opt/trader/output/contracts/ (3 契约文件)
  → mobile.js _getContractsPath() → /m 今日研判 + alpha_latest
```

**ECS cron 表（V6.1 最新）**：
| 时间 | 脚本 | 功能 |
|------|------|------|
| 06:22 | bridge_morning.js | WeChat bridge 心跳 |
| 06:27 | enrich_morning.js | 日报 LLM 增强 |
| */30 | monitor_rss_health.sh | RSS 健康监控 L1 |
| 0,2,4 | token_heartbeat.sh | Token 保活 |
| 0 8,18 | enrich_hot_events_cron.sh | 热点事件 LLM 增强 |
| **15:35 Mon-Fri** | **core/strategies/wanjun_screener.py** | **V6.1: 万军选股模型 2/8/10 (wanjun_models → upstream_signals.jsonl)** |
| **15:35 Mon-Fri** | **core/score.py + core/strategies/stock_agent_runner.py** | **盘后行业扫描 + 个股 alpha 信号（Pipeline B）** |
| **15:40 Mon-Fri** | **scripts/rebuild_trader_views.py** | **从 upstream_signals.jsonl 重建诊断视图** |
| **15:45 Mon-Fri** | **core/scripts/ma_signals_cron.sh** | **兼并重组信号采集（AKShare → ma_signals_runner → upstream）** |
| 15:50 Mon-Fri | scripts/update_tracker_verdicts.py | 每日胜率裁决引擎 |
| **16:00 Mon-Fri** | **core/tracker_closer.py** | **V6.1: 日频 OHLC 裁决 (腾讯 API, HOLD→WIN/LOSE → trader_tracker.jsonl)** |

> ⚠️ **Python 路径已迁移（V5.0）**：cron 从 `/opt/cycleradar/` 切换到 `/opt/cycleradar-trader/core/`，旧目录已删除。
> PYTHONPATH=`/opt/cycleradar-trader/core:/opt/cycleradar-trader/core/signals:/opt/cycleradar-trader/core/signals/adapters`

---

## 3. 领域语言

### 3.1 信号合约（upstream_signals.jsonl 每行一条）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `signal_id` | string | 格式 `{STRATEGY}-{DATE}-{NNN}` | 唯一标识，去重基准 |
| `strategy` | string | 见策略表 | 产生该信号的策略名 |
| `asset` | string | 股票代码/商品名/行业名 | 标的标识符 |
| `asset_type` | string | `stock` / `sector` / `commodity` | 标的类型 |
| `direction` | string | `long` / `short` | 多空方向 |
| `confidence` | float | 0.0–1.0 | 信号置信度 |
| `expiry` | string | ISO 8601 datetime | 信号有效期，过期自动失效 |
| `metadata` | object | 策略自定义 | 附加信息（stock_name / tier / reasons / entry_price / target_price / stop_loss 等） |

### 3.2 策略表（STRATEGY_CATEGORY_MAP）

| 策略名 | 分类 | 描述 | 产出状态 |
|--------|------|------|----------|
| `report_agent` | alpha | **事件驱动 LLM 推股（Pipeline A 主 alpha 源）**，含 entry/target/stop/thesis/event_source/sector_context | ✅ V4.3：alpha_signals → /m |
| `stock_agent` | alpha | 个股 AI 筛选（催化剂+资金+共振），Pipeline B fallback | ✅ V4.2 补齐三字段；V4.3 降级为 fallback |
| `ma_signals` | alpha | 并购重组事件驱动 | ✅ 正常 |
| `wanjun_models` | alpha | **V6.1: 万军选股模型 2/8/10** (wanjun_screener.py → upstream_signals.jsonl) | ✅ V6.1 cron 15:35 Mon-Fri |
| `rotation_factor` | ETF | 行业轮动因子，带 ETF 代码 | ✅ 正常 |
| `commodity_radar` | 商品 | 原油/铜/黄金/白银/铁矿方向 | ✅ 正常 |
| `tracker_closer` | 闭环 | **V6.1: 日频 OHLC 裁决引擎** (腾讯 API, 趋势胜率 54% / 波段 26%) | ✅ V6.1 cron 16:00 Mon-Fri |

> ⚠️ **新增策略必须在 `STRATEGY_CATEGORY_MAP`（signals.js）注册**，否则归入 `unknown` 并打 `console.warn`。

### 3.3 四分类（CycleRadar 移动端呈现）

| 分类 | 图标 | 数据来源 | 对应字段 |
|------|------|----------|----------|
| 热点事件 | 🔥 | `hotevents_cache.json` → `events[]` | thesis / tickers / source |
| alpha | 📈 | `upstream_signals.jsonl`（report_agent + stock_agent + ma_signals，V4.3: report_agent 主源） | direction / confidence / expiry |
| ETF | 📊 | `upstream_signals.jsonl`（rotation_factor） | sector / etf_code |
| 商品 | 🛢️ | `upstream_signals.jsonl`（commodity_radar） | commodity / direction |

### 3.4 信号置信度约束

- `high` ≥ 0.80：可直接触发操作参考
- `medium` 0.60–0.79：需结合市场环境二次确认
- `low` < 0.60：仅作参考，不建议单独作为入场依据
- R:R（盈亏比）目标 ≥ 1.5
- `time_horizon` 仅支持 `2w`（两周）/ `1m`（一月）；`1w` 已被回测证伪为反向指标

---

## 4. 关键技术决策

| 决策 | 选择 | 原因 |
|------|------|------|
| 信号总线格式 | JSONL（非 DB） | 人类可读、便于 debug、无 schema 迁移成本 |
| 去重策略 | 消费端按 signal_id 取最新 | 生产端允许更新，消费端保证幂等 |
| hotEvents 数据源 | hotevents_cache.json（LLM enrich） | 含 thesis + tickers + pic，比 raw articles 丰富 |
| hotEvents DB 查询 | sql.js WASM 直连 | ECS GLIBC 2.28 不满足 better-sqlite3 需求，sql.js 零原生依赖 |
| hotEvents enrich | LLM 时序比较守卫，无新文章跳过 | Anthropic API，5min TTL 本地缓存 |
| RSS 数据保鲜 | 4 层防御（L1 监控 / L2 API 新鲜度 / L3 前端时效条 / L4 enrich 守卫） | 跨层冗余 |
| 信号源分级 | S 级全收 / A 级限流 ≤3 / 默认不限 | JS 层 post-filter，SQL 不加 LIMIT |
| wewe-rss 启动 | **ecosystem.config.js 固化**（V4.2） | PM2 `--update-env` 不可靠，配置文件为唯一真相源 |
| ECS 持久化 | systemd pm2-root + `pm2 resurrect` | 机器重启自动恢复全部进程 |
| hotEvents tickers 缺口 | ~~articles 表无 body 列~~ content 列已存在且 100% 填充，enrich 管线具备正文输入 | V4.2 已验证：P2a 待测试覆盖率 |
| stock_agent 运行 | ECS venv 已部署，pipeline 端到端走通；06-11 信号升级：补齐 reasons/resonance/price targets 三字段 | alpha 信号质量全面升级，06-11 ECS dry-run 17 条全字段齐全 |
| 生产健康基线 | `scripts/health_check.sh` 部署到 ECS，process/data/cron/system 四维检查，支持 text/json/bare | 06-11 当前 `HEALTHY fail=0 warn=0 pass=11` |
| 部署同步纪律 | 本地仓库为代码单一源；`scripts/sync_to_ecs.sh` 同步到 `/opt/cycleradar-trader` 与 legacy flat `/opt/cycleradar` | 避免 ECS 热修后本地漂移；目录物理迁移延后 |
| `/m` alpha 主源 | **Pipeline A（daily.py → report_agent → upstream_signals.jsonl）**，stock_agent fallback | V4.3：LLM 事件驱动推股含 thesis/sector/price，信息密度远超规则引擎 |
| freshness 语义修正 | `dataFreshness`（RSS，hotEvents 用）→ `signalFreshness`（newestTime，alpha/ETF/commodity 用） | V4.3：两字段分治，前端时效条用信号新鲜度替代 RSS 新鲜度 |
| wanjun screener 接入 | `wanjun_screener.py` 模型 2/8/10 cron 15:35 Mon-Fri, source=`wanjun_models` → `upstream_signals.jsonl` | V6.1：wanjun 选股系统化，对齐 STRATEGY_CATEGORY_MAP |
| tracker_closer OHLC 裁决 | 腾讯 API 日频 OHLC (atomic rewrite)，窗口内 high≥target→WIN, low≤stop→LOSE，趋势 54%/波段 26% | V6.1：AKShare 被封后切换，腾讯直连稳定性更优 |
| watchlist 批量导入 | 纯文本 textarea + 正则解析（`代码 名称 备注`），不引入 multer/xlsx 依赖 | V6.1：最简方案，51 只测试通过，duplicate 静默跳过 |
| strategy 诊断排序 | 纯前端 `sortTable()` (JS)，6 列可点击升降序，默认得分降序 | V6.1：不改后端路由，不引入查询参数排序 |
| 文章统计摘要卡片 | `sql.js` WASM 直读 `wewe-rss.db`，活跃信源/累计文章/今日新增/最近更新，完全非阻塞 | V6.1：better-sqlite3 因 GLIBC 不可用，sql.js 零原生依赖 |
| RSS 第 9 源 | 财经早餐 (S 级不限量) 手动订阅 wewe-rss 验证 status=1 | V6.1：wewe-rss 9 源全部激活 |
| signalSourceStats | reflection 页新增 `buildSignalSourceStats()` 6 策略 🟢🟡🔴 监控表 | V6.1：策略来源健康度可观测 |

---

## 5. 成功标准

### V4.1.0 ✅ Done
1. 信号卡片点击展开详情（有效期/置信度/行业/标签/阶段/排名）✅
2. 市场摘要卡片（4 维统计 + long/short 比 + 温度判断）✅
3. 热点事件 enrich（thesis 核心观点 + tickers 关键标的 + pic 缩略图）✅
4. RSS 4 层防御系统（L1-L4 数据保鲜监控）✅
5. 信号源 S/A/Default 三级过滤 ✅

### V4.2.0 🎯 Done 定义（当前版本）
1. **body 列落地**：articles 表新增 content 列，LLM 可从正文提取 tickers
2. **stock_agent 部署**：ECS Python venv + MCP 联通，产出第一条信号到 upstream_signals.jsonl ✅（06-10 cron 产出 17 条）
3. **ecosystem.config.js 固化**：wewe-rss 不再依赖裸 `pm2 start`（✅ 已完成）
4. **enrich 管线全覆盖验证**：4 分类数据链路全部打通，`/m` 面版日频可用 ✅
5. **产品文档对齐**：CONTEXT / ROADMAP / architecture 三文档同步到 V4.2.0-dev ✅（06-11 对齐完成）

### V4.4.1 ✅ Done（契约扩展 — 3 文件桥）

1. **alpha_latest.json 契约**：daily.py `_write_trader_contract()` 新增 `output/alpha/{date}_alpha.json` → `~/交易员/data/alpha_latest.json`（entry/target/stop/thesis/sector_context）✅
2. **event_narrative_latest.json 契约**：daily.py `_write_trader_contract()` 新增事件叙事汇总 → `~/交易员/data/event_narrative_latest.json`（events/sector_outlook/global_conclusion）✅
3. **architecture.ejs 更新**：Signal Engine 区展示 3 契约文件 ✅
4. **trader CLAUDE.md 对齐**：trader 侧文档更新，支持 3 契约消费 ✅
5. **cron contracts sync**：cron_daily.sh v3 新增 step 2.5，cp 3 契约文件到 `contracts/` + git add ✅

### V5.0 🔗 当前版本（Phase 1 — 调度器 + 3 桥全链路接入 /m）

1. **Scheduler 模块**：`admin/scheduler.js` 文件级状态追踪（heartbeat/getStatus/getHistory），cron_daily.sh 每阶段回调 ✅
2. **Scheduler API**：`admin/routes/scheduler.js` 3 端点 + `X-Scheduler-Token` 认证，挂载在 `/admin/api/scheduler/` ✅
3. **Contracts 双路径**：`mobile.js` `_getContractsPath()` 优先 ECS `/opt/trader/output/contracts/`，fallback Mac `~/交易员/data/` ✅
4. **Overview 今日研判**：`app.js buildNarrativeCard()` 渲染 event_narrative（regime/action/thesis/events/risks），插入概览 tab 其次位 ✅
5. **CycleRadar alpha_latest 可视化**：`app.js _buildCrSignalDetail()` 展开详情展示合约快照（入场/目标/止损 + thesis + sector_context）✅
6. **Dashboard 动态版本**：`dashboard.ejs` 版本号从 `APP_VERSION` 环境变量读取 ✅

### V6.0 ✅ Done（移动端三 tab 重构 + Admin 视觉升级）

1. /m 三 tab 框架（概览/CycleRadar/自选）+ 温度仪表盘 ✅
2. 今日研判卡片（event_narrative 渲染）✅
3. alpha/ETF/商品三分类信号卡片 ✅
4. Admin 深色风格 + 7 tab 全功能 ✅
5. design-tokens 零 hex 规范全部迁移 ✅

### V6.1.0 ✅ Done（盘中+盘后闭环 — wanjun + tracker）

1. **wanjun screener 上线**：模型 2/8/10 接入 cron 15:35 Mon-Fri，`signals.js` 注册 `wanjun_models: 'alpha'` ✅
2. **tracker_closer OHLC 裁决**：177 条信号闭环，趋势胜率 54%，波段胜率 26% ✅
3. **RSS 第 9 源（财经早餐）**：wewe-rss 手动订阅，9 源全部 status=1 ✅
4. **/admin/architecture V6.1 里程碑更新**：cron 表/RSS 表格/技术债全部刷新 ✅

### V6.1.1 ✅ Done（管理后台增强 — 批量/排序/统计）

5. **watchlist 批量导入**：POST /admin/trader/watchlist/batch + textarea，51 只测试通过 ✅
6. **strategy 诊断排序**：纯前端 sortTable()，6 列可点击升降序 ✅
7. **trader overview 文章统计**：sql.js WASM 直读 wewe-rss.db，4 维摘要卡片 ✅
8. **signalSourceStats**：reflection 页策略来源 🟢🟡🔴 监控表 ✅
9. **CHANGELOG 补全**：[6.1.1] D-J 全部条目 ✅
10. **文档三件套对齐**：CONTEXT / ROADMAP / architecture 同步 V6.1.1 ✅

---

## 6. 已知技术债

| 级别 | 问题 | 影响 | 目标版本 |
|------|------|------|----------|
| ✅ P0 | CRON_EXPRESSION 裸 PM2 启动丢失 | wewe-rss cron 静默失效 | **V4.2 — 已修复** (ecosystem.config.js) |
| ✅ P0 | PM2 dump 含 env，重启可恢复 | 双重保险 | **V4.2 — 已验证** (systemd + resurrect) |
| ✅ P1 | articles 表 body 列 | content 列已存在（SQLite TEXT DEFAULT ''），feeds.service.js FEED_MODE=fulltext 时自动 persist，566/566 100% 填充，数据管线可用 | **V4.2 — 已验证** |
| ✅ P1 | stock_agent ECS 已部署且产出信号 | 06-10 cron 产出 17 条（2 强推 + 15 关注），信号空白已补齐，需持续观察日频稳定性 | **V4.2 — 已验证** |
| ✅ P2 | hotEvents CSV 子进程 → sql.js WASM 直连 | better-sqlite3 因 ECS GLIBC 2.28 缺失不可用，改用零原生依赖 WASM 方案 | **V4.2 — 已修复** (sql.js) |
| ✅ P2 | upstream_signals.jsonl 写入端无去重 | 可能重复写入 | **V4.2 — 已修复** (_SEEN_IDS + skip_dup) |
| 🟡 P3 | daily.py 日报管道 ECS 不可运行 | 日报依赖 Mac MCP，通过 JSONL bridge 间接馈送 /m | V4.3+（MCP server 迁移至 ECS 或 Mac cron + rsync） |
| 🟡 P3 | wewe-rss "暂无可用读书账号" 间歇阻塞 | feeds 同步中断 | V4.2 监控（非代码级） |
| 🟢 P3 | `event_narrative_latest.json` consumer 端无字段校验 | ~~V5.1 手动修复已知字段~~ V5.1 已加 `_validateEventNarrativeFields()`：producer 缺失/新增字段时 `console.warn` 告警 | ✅ V5.1 |
| 🟡 P2 | `mobile.js` 第2个调用点（信号Tab）冗余读文件（同请求 `narrative` 已读过） | 一次请求 2 次 `readFileSync` 同一个文件 | V5.2（缓存或传给变量） |
| 🟡 P3 | 30 日胜率 placeholder `—` | `global_conclusion` 暂无胜率统计字段，`buildCrStatsBar` 硬编码 `—` | V5.2（daily.py `_write_trader_contract()` 产出胜率统计） |
| 🟡 P3 | Event #6 原油大跌 `stock_impact: []` | LLM 未为该事件生成关联股票，属于 prompt 引导不足，非代码 bug | V5.2（调整 event_narrative prompt 引导股票关联） |
| 🔵 P3 | tickers 全空但 thesis 质量好 | enrich LLM 缺正文输入，但有标题可判断 | body 列落地后自然解决 |
| 🟢 V4.3 | Pipeline A→/m bridge 已搭建 | 每日本地 Mac cron 产出 + sync_to_ecs 推送到 ECS，需监控本地 Mac cron 执行稳定性 | V4.3 观测期 |
| 🟡 P2 | wanjun 模型 1/3-7/9/11 未接入 | 当前仅模型 2/8/10 cron 产出信号，1/3-7/9/11 待补充 | V6.2 |
| 🟡 P3 | ECS SSH 密钥未注册 | `id_ed25519` 公钥不在服务器 `authorized_keys`，需通过阿里云控制台绑定 | V6.2 (运维) |

---

## 7. 开发约定

- **每次改动后必须 `scp` 同步到 ECS + `pm2 restart trader-admin`**
- **wewe-rss 重启必须用 `pm2 start /opt/wewe-rss-deploy/ecosystem.config.js`**
- **每个功能版本提交前必须更新 CHANGELOG**
- **新策略上线前必须在 `STRATEGY_CATEGORY_MAP` 注册**
- ECS：`root@139.196.115.64`，项目路径 `/opt/cycleradar-trader`
- 本地路径：`/Users/scott/aichat-workspace/products/cycleradar-trader`

---

## 7.5 交付验收清单（每次功能完成后必须全绿才算交付）

> **规则**：每次说"完成"或"继续"前，AI 必须主动跑此清单。不全绿当场修，不靠用户肉眼发现。

```bash
# 一键验收脚本（在 Mac 执行）
ssh root@139.196.115.64 'python3.9 -c "
import json, urllib.request, datetime

def check(name, ok, detail=\"\"):
    icon = \"✅\" if ok else \"❌\"
    print(icon, name, detail)
    return ok

results = []
now = datetime.datetime.now().isoformat()
data = \"/opt/cycleradar-trader/data\"

# 1. timing — 非 symlink，有数据，最新日期在近7天内
import os
tf = data+\"/timing_history.json\"
if os.path.islink(tf):
    results.append(check(\"timing_history\", False, \"symlink 未替换\"))
else:
    h = json.load(open(tf)).get(\"history\",[])
    latest = max((r.get(\"date\",\"\") for r in h), default=\"\")
    results.append(check(\"timing_history\", bool(h) and latest >= \"2026-01-01\", \"%d条 latest=%s\" % (len(h),latest)))

# 2. strategy > 0 条
sl = [l for l in open(data+\"/trader_strategy.jsonl\") if l.strip()]
results.append(check(\"trader_strategy\", len(sl)>0, \"%d条\" % len(sl)))

# 3. tracker > 0 条
tl = [l for l in open(data+\"/trader_tracker.jsonl\") if l.strip()]
results.append(check(\"trader_tracker\", len(tl)>0, \"%d条\" % len(tl)))

# 4. upstream_signals 含 report_agent 今日信号
sigs = [json.loads(l) for l in open(data+\"/upstream_signals.jsonl\") if l.strip()]
today = now[:10]
ra_today = [s for s in sigs if s.get(\"strategy\")==\"report_agent\" and (today in s.get(\"signal_id\",\"\") or today.replace(\"-\",\"\") in s.get(\"signal_id\",\"\"))]
results.append(check(\"report_agent今日信号\", len(ra_today)>0, \"%d条\" % len(ra_today)))

# 5. /m API: narrative 非 null
resp = json.loads(urllib.request.urlopen(\"http://localhost:3100/m/api/summary\").read())
n = resp.get(\"narrative\")
results.append(check(\"/m narrative\", bool(n), n.get(\"market_regime\",\"?\") if n else \"NULL\"))

# 6. /m API: alpha > 0 且有 alphaLatest 可 match
cr = json.loads(urllib.request.urlopen(\"http://localhost:3100/m/api/cycleradar\").read())
alpha = cr.get(\"alpha\",[])
al_codes = set(s[\"code\"] for s in (cr.get(\"alpha_latest\") or {}).get(\"signals\",[]))
can_match = [s for s in alpha if s[\"asset\"] in al_codes]
results.append(check(\"/m alpha信号\", len(alpha)>0, \"%d条 可match=%d\" % (len(alpha), len(can_match))))

# 7. /dashboard/ 301 (不跟随 redirect)
import urllib.error
class _NoRedir(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None
opener = urllib.request.build_opener(_NoRedir)
try:
    opener.open("http://localhost:80/dashboard/")
    results.append(check("/dashboard/ 301", False, "未重定向"))
except urllib.error.HTTPError as e:
    results.append(check("/dashboard/ 301", e.code in (301,302), "HTTP %d" % e.code))
except Exception as ex:
    results.append(check("/dashboard/ 301", False, str(ex)))

all_ok = all(results)
print(\"\\n\" + (\"🎯 全部通过\" if all_ok else \"⚠️  有项目未通过，需修复后再交付\"))
"
'
```

**验收失败时的处理规则**：
- `timing NULL` → `rsync ~/交易员/data/timing_history.json ECS:data/`
- `strategy/tracker 空` → 重跑 `rebuild_trader_views.py`（需先确保 stock_agent 已写入信号）
- `report_agent 今日无信号` → 检查 `alpha_latest.json` 是否已由 `daily.py` 产出，手动注入
- `narrative NULL` → 检查 `/opt/trader/output/contracts/` 三文件是否存在
- `alpha 可 match=0` → 检查 `app.js _buildCrAlpha` 是否已部署最新版本
- `/dashboard/ 非301` → 检查 nginx 配置

---

## 8. AI 协作记忆体系

| 文件 | 位置 | 用途 |
|------|------|------|
| `SESSION_LOG.md` | `~/.claude/SESSION_LOG.md` | 跨项目统一时间线，每次对话自动追加 |
| `PROJECT_INDEX.md` | `~/.claude/PROJECT_INDEX.md` | 工作区路径 → 项目名映射 |
| `META-DEV.md` | `docs/META-DEV.md` | 本项目的 AI 协作元反思 |
| `CONTEXT.md` | `products/cycleradar-trader/CONTEXT.md` | **本文档** — 研发手册唯一真相源 |
| `ROADMAP.md` | `products/cycleradar-trader/ROADMAP.md` | 版本路线 + 任务清单 |
| `V4.2-DEV-PLAN.md` | `docs/V4.2-DEV-PLAN.md` | V4.2 技术开发项详细方案 |
| `architecture-v6.1.md` | `docs/architecture-v6.1.md` | 三子系统架构文档（管线 + /m + Admin + cron + 契约） |

---

## 9. AFTER_SESSION (会前注入，最后更新 2026-06-20)

> 每次对话结束时 AI 自动更新。下次对话开始时 AI 读取此区块，3 秒恢复上下文。

### 本次会话（2026-06-20 — V6.1.1 产品文档三件套对齐）

- **上下文恢复**：上次对话完成 V6.1.1 全部代码部署，但 CONTEXT.md / ROADMAP.md 仍标记 V6.0，CHANGELOG.md 缺 Items D-J
- **完成操作**：三文档本地编辑完成（ECS SSH 密钥未通，待上传），CHANGELOG 补全 [6.1.0] + [6.1.1]，CONTEXT 升至 V6.1.1，ROADMAP 新增 V6.1 section
- **当前阻塞**：ECS SSH 连接（id_ed25519 不在 authorized_keys），文件暂存本地，需另寻上传方式

### 文件变更清单（本次会话）

| 文件 | 操作 | 内容 |
|------|------|------|
| `CHANGELOG.md` | 修改 | 插入 [6.1.0] (A-C) + [6.1.1] (D-J) 完整条目 |
| `CONTEXT.md` | 修改 | 头部 V6.0→V6.1.1；§2.1 加 wanjun/tracker；§2.2 cron 加 wanjun 15:35/tracker 16:00；§3.2 策略表加 wanjun_models/tracker_closer；§4 加 8 项关键决策；§5 加 V6.0/V6.1.0/V6.1.1 成功标准；§6 技术债加 wanjun 缺失模型+SSH；§8 architecture-v6.0→v6.1；§9 全部重写 |
| `ROADMAP.md` | 修改 | V6.0→V6.1.1；新增 V6.1.0/V6.1.1 章节；milestone 表补两行；本地快速启动路径更新 |

### 当前状态（V6.1.1 全部完工，2026-06-20）
- 版本：**V6.1.1 — 代码层 100% 部署，文档层本地就绪待上传**
- ECS：PM2 3 进程在线（trader-admin/wewe-rss/simensuji），cron wanjun 15:35 + tracker 16:00 正常
- ✅ V6.1.0 完工：wanjun screener / tracker_closer OHLC / RSS 9 源 / arch V6.1
- ✅ V6.1.1 完工：批量导入 / 策略排序 / 文章统计 / signalSourceStats / CHANGELOG
- ⚠️ 三文档本地已编辑，待上传 ECS `/opt/cycleradar-trader/`
- ✅ ~~ECS SSH 不通（id_ed25519 密钥未注册）~~ → **已解决**：实际用户是 `root`（非 `ops`），密钥一直有效，三文档已上传 ECS `/opt/cycleradar-trader/`

### V6.1.1 功能边界
```
✅ 全部完工:
──────────────────────────────────────── ─────────────────────
wanjun screener 模型 2/8/10 cron 15:35      tracker_closer OHLC 闭环 (54%/26%)
RSS 财经早餐第 9 源                          /admin/architecture V6.1
watchlist 批量导入 (textarea + 正则)         strategy 诊断表列头排序 (纯前端)
trader overview 文章统计卡片 (sql.js)        signalSourceStats (reflection)
RSS 信源分级 S/A/默认 (9 源)                 VERSION=6.1.1 + CHANGELOG 完整
CONTEXT.md V6.1.1                            ROADMAP.md V6.1.1

⚠️ 待补:
────────────────────────────────────────
wanjun 模型 1/3-7/9/11 (V6.2)
三文档已上传 ECS ✅（root@139.196.115.64 密钥有效）
```

### 下一次执行的正确顺序
1. **wanjun 扩展**：模型 1/3-7/9/11 接入（V6.2，续上次计划）
2. **验证 tracker_closer**：周一 16:00 后检查 tracker_closer.log，确认裁决继续正常产出

### 根因教训（持续积累）
12. **🔴 文档漂移**：代码部署完但文档没更新 = 下次会话 AI 从文档读到过期信息，必然误判当前状态。修复纪律：代码 deploy 后必须同步 CONTEXT/ROADMAP/CHANGELOG 三件套，缺一不可
13. **🔴 SSH 用户名记错（ops→root 浪费数小时）**：全项目文档都写 `root@139.196.115.64`，但凭记忆用了 `ops`，整个会话被阻塞在假问题上。教训：查 SSH 用户第一步 grep 项目文档，不要凭记忆
