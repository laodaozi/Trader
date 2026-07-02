# CycleRadar Trader · 系统架构 V6.5

> 版本：V6.5
> 创建：2026-06-20
> 最后更新：2026-06-28（V3.9.6 Plan C: Admin文章生成入口 + 信源→写作管线桥接）
> 状态：活跃（当前真源）
> 配套文档：CONTEXT.md、ROADMAP.md、PROJECT_MEMORY.md
> 前版本：docs/architecture-v6.1.md（已归档，含 V6.0→V6.1 合并历史）

---

## 全景数据流

```
┌─────────────────────────────────────────────────────────────────────┐
│                          信源层                                      │
│                                                                     │
│  微信公众号（WeWe RSS × 8源）                                        │
│  杜牛牛 / 微策神机 / 数据宝 / 台球之门                               │
│  低吸波段王 / 财闻私享 / 小马白话期权 / 叙事平权                      │
│  → WeWe RSS DB：/opt/wewe-rss-deploy/data/wewe-rss.db（V3.9.6 接入）│
│                                                                     │
│  公开 RSS                                                           │
│  财联社 / 36氪 / 格隆汇 / 财新 / 金十 / 第一财经                    │
│                                                                     │
│  AKShare（行情 / 龙虎榜 / 公告 / 期货 / 历史 K 线）                 │
│                                                                     │
│  xlsx 自选池（手动维护，导入入口）                                    │
└────────────────────────┬────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────────────┐
│                       采集 & 处理层                                  │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ daily.py（主调度，每日 09:30 + 15:30）                       │   │
│  │  Step 1  微博热搜 + 企查查                                   │   │
│  │  Step 2  深交所互动易 + 热门30                               │   │
│  │  Step 3  微信信源（WeWe RSS）                                │   │
│  │  Step 4  全球宏观                                            │   │
│  │  Step 5  LLM 增强（event_narrative 生成）                    │   │
│  │  Step 6  资金流                                              │   │
│  │  Step 7  report_agent / stock_agent / ma_signals            │   │
│  │  Step 8  save_outputs → 三文件桥                            │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ run_article_pipeline.py（Admin手动/Cron，V3.9.6 Plan C 新增）│   │
│  │ 数据源：hot_enrichment.json（标注数据）+ WeWe RSS DB          │   │
│  │ 流程：标注→WeWe DB匹配→SOURCE_ROLES分组→写作Pipeline          │   │
│  │ → data/pipeline_status.json（生成状态输出）                   │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌──────────────────────┐  ┌──────────────────────────────────┐    │
│  │ rotation_factor.py   │  │ commodity_radar.py               │    │
│  │ 独立定时（V6.1 改造）│  │ 独立定时（V6.1 改造）            │    │
│  │ 每日盘后 16:00       │  │ 每日盘后 16:15                   │    │
│  │ 八因子 → ETF 信号    │  │ 5 品种 → 商品信号                │    │
│  │ + rotation_snapshots │  │ retry × 3 + 降级（上日数据）     │    │
│  └──────────────────────┘  └──────────────────────────────────┘    │
│                                                                     │
│  ┌──────────────────────┐  ┌──────────────────────────────────┐    │
│  │ scanner_daily.py（新）│  │ ma_signals_runner.py             │    │
│  │ 每日盘后 16:30       │  │ 每日 15:45（周一至周五）          │    │
│  │ 14模型扫描全市场      │  │ AKShare 公告 → 兼并重组信号      │    │
│  │ → scanner_log.jsonl  │  │                                  │    │
│  └──────────────────────┘  └──────────────────────────────────┘    │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ update_watchlist_signals.py（迁入，V6.1 激活）               │  │
│  │ 每日盘后 16:30                                               │  │
│  │ watchlist.json → signals_nx.py → watchlist_signals.json     │  │
│  └──────────────────────────────────────────────────────────────┘  │
└────────────────────────┬────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────────────┐
│                    信号总线 & 契约层                                  │
│                                                                     │
│  data/upstream_signals.jsonl      ← alpha/ETF/商品信号汇聚点        │
│                                                                     │
│  contracts/                                                         │
│  ├── alpha_latest.json            ← 当日 alpha 契约桥               │
│  ├── event_narrative_latest.json  ← 研判契约桥                      │
│  └── watchlist_signals.json       ← 自选池信号（V6.1 激活）         │
│                                                                     │
│  data/                                                              │
│  ├── rotation_snapshots.jsonl     ← 八因子日快照（V6.1 新增）       │
│  ├── hot_enrichment.json          ← LLM标注缓存（V3.9.6）+ 微信信源映射 │
│  ├── pipeline_status.json         ← 文章生成状态（V3.9.6 Plan C）   │
│  ├── tracker_log.jsonl            ← 5/10/20日反思记录（迁入）       │
│  ├── scanner_log.jsonl            ← 14模型命中日志（V6.1 新增）     │
│  ├── account.json                 ← 账户快照（迁入）                │
│  ├── positions.json               ← 持仓明细（迁入）                │
│  ├── trade_log.json               ← 交易记录（迁入）                │
│  ├── trader_strategy.jsonl        ← 自选池五维诊断（已有）           │
│  └── watchlist.json               ← 自选池（xlsx 导入 + Admin 管理） │
└────────────────────────┬────────────────────────────────────────────┘
                         ↓
         ┌───────────────┴───────────────┐
         ↓                               ↓
┌─────────────────────┐   ┌──────────────────────────────────────────┐
│   /m 移动端（三Tab） │   │         Admin 后台（九Tab，V6.5 新增文章仪表盘）                 │
│                     │   │                                          │
│  概览               │   │  ① 概览          /admin/trader           │
│  · 市场温度计        │   │  ② 自选池诊断    /admin/trader/strategy  │
│  · 今日研判          │   │  ③ 信号跟踪      /admin/trader/tracker   │
│  · 热点事件 ← P0补  │   │  ④ 策略回测      /admin/trader/backtest  │
│  · 好运指数          │   │  ⑤ 自选股        /admin/trader/watchlist │
│                     │   │  ⑥ 文章仪表盘★   /admin/articles         │
│  自选               │   │     · 状态卡片（标注/标的/生成）          │
│  · NX信号 ← P1激活  │   │     · 生成按钮（→POST /generate）        │
│  · 持仓 P&L ← P1新  │   │     · JSON状态   /admin/articles/status   │
│  · 买卖建议 ← P1新  │   │                                          │
│                     │   │  ⑦ 回撤统计      /admin/trader/drawdown   │
│  信号               │   │                                          │
│  · Alpha            │   │  ⑧ 策略反思★    /admin/trader/reflection │
│  · ETF ← P0激活     │   │     展示区：胜率矩阵/NX验证/14模型        │
│  · 商品 ← P0激活    │   │     探索区：历史回溯/因子趋势图           │
│  · 14模型标签 ← P2  │   │     诊断区：LLM 自动结论                  │
└─────────────────────┘   └──────────────────────────────────────────┘
```

---

## core/ 目录结构（V6.1 合并后）

```
core/
│
├── daily.py                      ← 主调度（不动）
├── rotation_factor.py            ← 八因子引擎（改为独立定时）
├── factor_agent.py               ← ETF/期货映射（不动）
├── commodity_radar.py            ← 商品信号（加 retry）
├── report_agent.py               ← 事件叙事（不动）
├── stock_agent.py                ← 五维评分（不动）
├── stock_analysis.py             ← NX 分析（不动）
│
│  ── 从 ~/交易员/modules/ 迁入 ──
├── scanner.py                    ← 14模型量化扫描（新迁入）
├── signals_nx.py                 ← NX买点+MA排列+Fib位（新迁入）
├── tracker_reflection.py         ← 5/10/20日反思框架（新迁入）
├── strategy_exec.py              ← 自选池诊断+评分（新迁入）
├── timing.py                     ← 市场温度计（新迁入，与 rotation_factor 对齐）
├── haoyun.py                     ← 好运哥仓位纪律（新迁入，与 haoyunge.js 对齐）
├── account.py                    ← 持仓管理+账户快照（新迁入）
├── sectors.py                    ← 活跃主线识别（新迁入）
├── diagnose.py                   ← 周线方向+资金+R:R（新迁入）
│
├── strategies/
│   ├── commodity_radar.py        ← 商品信号（已有）
│   ├── ma_signals.py             ← 兼并重组（已有）
│   └── ma_signals_runner.py      ← 兼并重组调度（已有）
│
├── backtest/
│   ├── backtest.py               ← 多周期回测（已有）
│   ├── param_scan.py             ← 参数扫描（已有）
│   └── backtest_runner.sh        ← 回测调度（已有）
│
├── writing/
│   ├── pipeline.py               ← 7角色写作管线（已有）
│   └── prompt_registry.py        ← 角色路由（已有）
│
└── scripts/
    ├── update_watchlist_signals.py ← 自选池信号更新（新迁入）
    ├── wanjun_screener.py         ← 万军筛选器（新迁入）
    ├── scanner_daily.py           ← 14模型日扫描入口（新建）
    ├── rotation_backtest.py       ← 历史回溯工具（新建）
    ├── import_watchlist.py        ← xlsx 导入工具（新建）
    └── run_article_pipeline.py    ← 文章生成桥接器（V3.9.6 Plan C 新建）★
```

---

## Admin 路由结构（V6.5）

```
admin/routes/
├── trader.js         ← 交易员主路由（V6.5 移除 /trader/article-stats 重复路由）
│   GET /admin/trader                    → 概览（现有）
│   GET /admin/trader/strategy           → 自选池诊断（现有）
│   GET /admin/trader/tracker            → 信号跟踪（现有）
│   GET /admin/trader/backtest           → 策略回测（现有）
│   GET /admin/trader/watchlist          → 自选股（现有）
│   GET /admin/trader/drawdown           → 回撤统计（现有）
│   GET /admin/trader/reflection         → 策略反思（V6.1 新增）★
│
├── articles.js       ← 文章仪表盘（V6.5 Plan C 独立路由）
│   GET /admin/articles                   → 仪表盘（文章列表 + 3状态卡片 + 生成按钮）
│   GET /admin/articles/status            → JSON状态（enrichment + pipeline）
│   POST /admin/articles/generate         → 触发 run_article_pipeline.py + 写入 flash
│   GET /admin/articles/stats（现有）     → 微信文章统计表
│
├── mobile.js         ← /m 路由（现有，补齐）
│   GET /m                               → 移动端主页
│   GET /m/api/summary                   → 概览数据（现有）
│   GET /m/api/haoyunge                  → 好运指数（现有）
│   GET /m/api/cycleradar                → 信号数据（现有）
│   GET /m/api/watchlist                 → 自选数据（现有骨架，V6.1 激活）
│   GET /m/api/reflection/summary        → 反思摘要（V6.1 新增）★
│
└── health.js         ← 健康检查（现有）
    GET /admin/health
    GET /admin/api/health
```

---

## 文章生成管线数据流（V6.5 Plan C 新增）

```
信源层
├── 信源爬虫 10+ → WeWe RSS DB（PostgreSQL，ECS /opt/wewe-rss-deploy/data/wewe-rss.db）
├── daily.py Step 5 → LLM增强 → hot_enrichment.json（公众号文章标注缓存）
│     字段：md5(title) 哈希 + mp_id + title + URL + has_tickers + tickers[] + summary + time
│
└── core/writing/source_registry.py → SOURCE_ROLES（信源权重/角色/tier/limit 映射）

                    ↓

桥接层（core/scripts/run_article_pipeline.py，新）
├── ① 读取 data/hot_enrichment.json → 提取 today_enriched（has_tickers=True）
├── ② 连接 WeWe RSS DB → sqlite3 /opt/wewe-rss-deploy/data/wewe-rss.db
├── ③ 匹配 md5(title)[:12] hash → 获取 HTML 全文
├── ④ 按 mp_id → SOURCE_ROLES 分组（权重排序）
├── ⑤ build_signals → 按 source tier/weight/limit 组织文章信号
├── ⑥ core/writing/pipeline.py → run_pipeline(signals) → 7角色写作管线
│      角色：Planner → Researcher → Drafter → Editor → Fact-Checker → SEO → Publisher
├── ⑦ core/writing/pipeline.py → save_articles(date, articles)
└── ⑧ 输出 data/pipeline_status.json {date, total, enriched, generated, files[], success}

                    ↓

展示层
├── /admin/articles 仪表盘
│   ├── 状态卡片：📋 今日标注（today/todayWithTickers）→ 🎯 有标的 → 📄 生成结果
│   ├── 生成按钮：POST /admin/articles/generate → spawn run_article_pipeline.py → redirect + flash
│   └── 文章列表：output/article/ 日刊文件
│
└── /admin/articles/status JSON API → 前端轮询/AJAX 获取生成进度
```

### Admin 文章仪表盘 UI 交互

```
┌─────────────────────────────────────────────────────────────┐
│  📋 今日标注: 0        🎯 有标的: 0        📄 今日生成: 0   │
│                                                             │
│  [ 🚀 生成今日日报 ]  ← POST → spawn → 等待 → redirect     │
│      ↑ disabled when todayWithTickers == 0                  │
└─────────────────────────────────────────────────────────────┘
```

```
数据输入层
├── data/tracker_log.jsonl          ← 5/10/20日信号跟踪结果
│     字段：code/name/signal_type/strategy/entry/stop/targets/
│           result(HIT/MISS/NEUTRAL)/max_return/max_dd/final_return/n_bars
│
├── data/trader_strategy.jsonl      ← 自选池五维诊断历史
│     字段：date/code/name/nx/ma_align/fib_zone/weekly_dir/capital_dir/rr/score
│
├── data/scanner_log.jsonl          ← 14模型日扫描命中（V6.1 新建）
│     字段：date/code/name/models[]/label/entry/reasons
│
└── data/rotation_snapshots.jsonl   ← 八因子日快照（V6.1 新建）
      字段：date/factors{A1..E1}/top_industries[]/top_etfs[]/
            top_commodities[]/market_concentration/limit_up_count/main_line_days

                    ↓
聚合计算层（新建 admin/models/reflection.js）
├── getStrategyWinRate()     → 按策略×周期聚合胜率（来自 tracker_log）
├── getNXEffectiveness()     → NX状态×胜率交叉分析（tracker × strategy）
├── getScannerHitRate()      → 14模型命中率排名（来自 scanner_log）
├── getFactorSnapshots()     → 八因子趋势数据（来自 rotation_snapshots）
└── getRotationBacktest()    → 历史回溯（按需，调 rotation_backtest.py）

                    ↓
展示层（新建 admin/views/trader/reflection.ejs）
├── 区块 A：展示型
│   ├── A1 策略胜率矩阵（热力表）
│   ├── A2 NX 有效性（三行对比表）
│   └── A3 14模型命中率排名（可排序表）
│
├── 区块 B：探索型
│   ├── B1 历史回溯工具（日期选择器 → 触发 rotation_backtest.py）
│   ├── B2 八因子趋势折线图（30日）
│   └── B3 三联动验证（行业×大宗×ETF 延迟天数矩阵）
│
└── 区块 C：诊断型
    └── LLM 自动结论（每日生成，复用 report_agent 框架）
```

---

## ~/交易员/ → cycleradar-trader 合并映射

```
源（~/交易员/）                    目标（cycleradar-trader/）
─────────────────────────────────────────────────────────────
modules/scanner.py              → core/scanner.py
modules/signals.py              → core/signals_nx.py
modules/tracker.py              → core/tracker_reflection.py
modules/strategy.py             → core/strategy_exec.py
modules/timing.py               → core/timing.py
modules/haoyun.py               → core/haoyun.py
modules/account.py              → core/account.py
modules/sectors.py              → core/sectors.py
modules/diagnose.py             → core/diagnose.py
scripts/update_watchlist*.py    → core/scripts/update_watchlist_signals.py
scripts/wanjun_screener.py      → core/scripts/wanjun_screener.py

data/pool.json                  → data/watchlist.json（合并）
data/strategy_log.jsonl         → data/trader_strategy.jsonl（合并字段）
data/tracker_log.jsonl          → data/tracker_log.jsonl（新增）
data/trade_log.json             → data/trade_log.json（新增）
data/positions.json             → data/positions.json（新增）
data/timing_history.json        → data/timing_history.json（新增）
data/alpha_latest.json          → contracts/alpha_latest.json（对齐字段）
```

---

## 关键因子说明（行业轮动八因子）

| 因子 | 名称 | 数据来源 | 计算逻辑 | 在轮动探索中的角色 |
|---|---|---|---|---|
| A1 | 超额收益 | AKShare 行情 | 行业近5日涨幅 vs 上证指数 | 最直接的强弱信号 |
| A2 | 涨停热度 | AKShare 龙虎榜 | 行业内涨停数/总数占比 | 反映资金追捧强度 |
| B1 | 资金确认 | AKShare 资金流 | 行业主力净流入额 | 验证涨幅是否有资金支撑 |
| B2 | 融资热度 | AKShare 融资融券 | 行业融资买入环比增速 | 杠杆资金的方向 |
| C1 | 估值安全 | AKShare 估值 | 行业 PE/PB 历史分位 | 判断是否过热/有安全垫 |
| D1 | 产业资本 | AKShare 公告 | 重要股东增减持方向 | 最聪明钱的方向 |
| D2 | 机构持仓 | AKShare 机构 | 基金持仓变化方向 | 中期趋势确认 |
| E1 | 舆情共振 | WeWe RSS 微信信源 | 行业相关文章热度 | 散户/媒体关注度 |

**三联动链路（行业→ETF→大宗）**：

```
行业因子评分（A1+A2+B1 三分因子）
        ↓ INDUSTRY_ETF_MAP（50+行业映射）
ETF 方向信号（long/short + 置信度）
        ↓ INDUSTRY_FUTURES_MAP（18个品种映射）
商品期货方向信号（long/short + 置信度）
        ↓ 反向验证
商品上涨 → 对应行业强化（相互印证）
```

**历史验证目标场景**：

| 时间段 | 现象 | 验证假设 |
|---|---|---|
| 2025-11 ~ 2026-02 | 大宗（铜/油/煤）持续火爆 | B1资金确认 + D1产业资本是否最早信号？ |
| 2026-01 ~ 2026-06 | 半导体持续主线 | A1超额收益 + A2涨停热度的连续性是否是持续信号？ |
| 2026-04 ~ 2026-06 | 1:9极致分化 | market_concentration > 0.8 + E1舆情共振是否同步出现？ |

---

## 版本历史对照

| 版本 | 核心变化 |
|---|---|
| V3.9.5 | 三轨并行（回测CI/管理后台/信号合约） |
| V4.3 | Pipeline A → /m 桥，report_agent/stock_agent |
| V5.0 | 调度器 + 三文件桥全链路 |
| V5.4 | Admin P1 双任务（自选股入口 + 文章统计） |
| V6.0 | /m 三Tab 框架，Admin 七Tab |
| **V6.1** | **~/交易员/ 全量合并 + 策略反思模块 + 轮动探索** |
| **V6.5** | **V3.9.6 Plan C：Admin文章生成入口（run_article_pipeline.py桥接器）+ Admin九Tab + article-stats路由去重** |
| **V6.5 生态** | 产出：ecs-diff.sh误报bug发现（#18-19）、架构图全同步 |

---

## 技术债（本版本不处理）

| 债项 | 描述 | 计划版本 |
|---|---|---|
| tb-1 | ECS output/ 残骸清理 | V6.2 |
| tb-2 | .env 密钥安全迁移 | V6.2 |
| tb-3 | /m 仍使用旧 app.js（V6.0 dashboard.ejs 骨架待接管） | V6.2 |
| tb-4 | ~/交易员/ 归档时间待定 | 合并验证完成后 |
| tb-5 | rotation_snapshots 历史数据不补填 | 按需用 rotation_backtest.py 回溯 |
| tb-6 | ecs-diff.sh 只扫描 admin/ 目录 + 误报 DIFF=0 | 核心脚本文件和纯 Python 模块不可见，需修复扫描范围 + SSH md5 比对逻辑 |
