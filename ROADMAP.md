# CycleRadar Trader · ROADMAP

> 最后更新：2026-06-20 · **V6.1.1**  |  wanjun screener 上线 + tracker_closer OHLC 闭环 + RSS 第 9 源 + 批量导入/排序/文章统计

---

## V4.0.1 ✅ 已完成 (2026-06-08)

| # | 任务 | 状态 |
|---|------|------|
| P0-sig | signals.js 分类映射化 + unknown 告警 | ✅ `b303306` |
| P0-cl | CHANGELOG V4.0.0 补录 | ✅ |
| P0-ctx | CONTEXT.md 基线建立 | ✅ |
| P0-git | 全量 git commit（6 commits） | ✅ |
| P1-pos | positions 越级路径修复 → `os.homedir()` | ✅ `31557a9` |
| P1-road | ROADMAP.md 从 workplan 迁移 | ✅ |
| P1-smoke | 4 分类 API 冒烟脚本 `scripts/smoke.sh` | ✅ 7/7 PASS |

---

## V4.1.0 ✅ 已完成 (2026-06-09)

> 核心：无 LLM 依赖的移动端 UX 改进 + RSS 数据保鲜监控。

| # | 任务 | 状态 |
|---|------|------|
| 4.1c | `/m` 信号卡片点击展开详情 | ✅ |
| 4.1-new | 市场摘要卡片（4维统计 + 多空比 + 温度） | ✅ |
| 4.1-hot | 热点事件 enrich（thesis + tickers + pic） | ✅ 06-09 |
| 4.1-filter | 信号源 S/A/Default 三级过滤 | ✅ 06-09 |
| 4.1-def | RSS 4 层防御系统（L1-L4） | ✅ 06-09 |
| ~~4.1a~~ | ~~恢复 daily.py 日报全链路~~ → V4.2 延后 | ⏸️ |
| ~~4.1b~~ | ~~hotEvents 迁移到 morning.json~~ → V4.2 延后 | ⏸️ |
| 4.1e | 信号质量统计面板 | 🟡 延至 V4.3 |

---

## V4.2 ✅ 已完成 + eP0 生产止血 (2026-06-09 → 2026-06-11)

> 核心目标：管线修复 + 数据完整性补齐 + 产品文档重建。

### P0 已交付

| # | 任务 | 来源 | 状态 |
|---|------|------|------|
| 4.2-p0a | **ecosystem.config.js 固化** wewe-rss 启动 | CRON_EXPRESSION 丢失风险 | ✅ 06-09 |
| 4.2-p0b | **PM2 env 验证** + systemd 持久化 | 重启后 cron 恢复 | ✅ 06-09 |
| 4.2-p0c | **8 feeds 同步状态确认** | 管线链路验证 | ✅ 06-09 |
| 4.2-p0d | **ECS 资源摸底**（RAM/disk/Python/pip） | stock_agent 部署前提 | ✅ 06-09 |

### eP0 生产止血（06-11 追加）

| # | 任务 | 来源 | 状态 |
|---|------|------|------|
| eP0-1 | **health_check.sh 生产健康基线**：process/data/cron/system 四维检查，text/json/bare 三模式 | 线上失败不可见 | ✅ ECS `HEALTHY fail=0 warn=0 pass=11` |
| eP0-2 | **stock_agent OHLC/信号链路恢复**：东财 retry → 腾讯 fallback → 72h cache；runner 补 `score_multi_catalyst()`；确定性 `signal_id` | 06-10 `RemoteDisconnected` + catalyst 断链 | ✅ 06-11 写入 9 条 stock_agent 信号 |
| eP0-3 | **trader_strategy/trader_tracker 重建**：从 `upstream_signals.jsonl` 派生诊断页 JSONL，并接入 15:40 cron | 两文件 71h stale 且全项目无写入者 | ✅ 26 strategy / 78 tracker rows |
| eP0-4 | **部署路径纪律收敛**：新增 `scripts/sync_to_ecs.sh`，本地仓库作为代码单一源，ECS 双目录仅为运行目标 | `/opt/cycleradar` 与 `/opt/cycleradar-trader` 漂移 | ✅ 脚本已部署；未做高风险目录迁移 |

### P1 进行中

| # | 任务 | 来源 | 工作量 |
|---|------|------|--------|
| 4.2-p1a | ~~**articles 表加 body 列**~~ → 实测发现已完成：content 列已存在 (SQLite)，feeds.service.js FEED_MODE=fulltext 时自动 persist 全文 HTML，566/566 文章 100% 填充 (70-125KB/篇) | enrich tickers 缺口 | ✅ 06-10 验证完成（无需修改） |
| 4.2-p1b | **stock_agent ECS 部署**：Python 3.9 venv → pip 升级 → 安装 anthropic/mcp → 复制 6 归档模块 → cron 注册 | alpha 信号空白 | ✅ 06-10（首日零信号，24票未突破45分门槛） |
| 4.2-p1c | **产品文档重建**：CONTEXT.md / ROADMAP.md / architecture-v4.2.0.md → 三文档同步 | 文档体系腐烂 | ✅ 06-11 |

### P2 验证

| # | 任务 | 依赖 |
|---|------|------|
| 4.2-p2a | 验证 enrich_hot_events tickers 覆盖率提升 | 4.2-p1a |
| 4.2-p2b | 验证 upstream_signals stock_agent 信号产出 | 4.2-p1b | ✅ 06-10（17 条信号，2 强推 + 15 关注） |
| 4.2-p2c | `FEED_MODE=fulltext` 启用（RSS 正文输出） | ✅ 06-10 |
| 4.2-p2d | `ENABLE_CLEAN_HTML=true` 启用 | ✅ 06-10 |
| 4.2-p2e | 验证 `pm2 resurrect` 重启全链路 | ✅ 06-10 |

### P3 远期

| 域 | 任务 |
|----|------|
| 日报 | daily.py → report_agent 迁移（daily.py 2938行依赖 Mac MCP，ECS 不可运行） |
| 策略 | stock_agent 底稿缓存迁移（CodeBuddy 归档 → ECS data/） |
| 策略 | backtest.py 接入 PM2 定时调度 |
| 监控 | 信号质量统计面板（日信号量/去重率/过期清理） |
| 管理 | 票池管理入口（admin trader 面板自选维护 UI） |
| 管理 | WeWe RSS 订阅号管理 UI（增删改查 + 标签） |
| 管理 | Token 用量面板 |
| 前端 | 信号看板 → React 实时推送 |
| 基础设施 | ETL 管道解耦（cleaner.py 独立模块） |

---

## V4.2 Done 定义

- [x] wewe-rss 通过 ecosystem.config.js 启动，重启后 cron 不丢失
- [x] `FEED_MODE=fulltext` + `ENABLE_CLEAN_HTML=true` 双开关启用
- [x] articles 表 `content` 列就绪，enrich_hot_events 可从正文提取 tickers（已存在，566/566 填充率 100%）
- [x] stock_agent 在 ECS 部署完成，pipeline 端到端走通（akshare/估值/资金流/新闻/NX 四步全通），cron Mon-Fri 15:35 自动运行
- [x] **Harness 反射层 V4.2 收尾**：SESSION_LOG 恢复写入 / META-DEV 从提案升级为活跃反思日志 / CONTEXT.md 追加 AFTER_SESSION 区块 / ROADMAP V4.3 立项
- [x] stock_agent 产出第一条选股信号到 upstream_signals.jsonl（06-10 cron 产出 17 条：2 强推 + 15 关注，confidence 0.65-0.85）
- [x] CONTEXT / ROADMAP / architecture-v4.2.0 三文档对齐（06-11 同步完成：stock_agent 17 条信号、4 分类日频、所有 Done 项统一标记）
- [x] 4 分类面版 `/m` 日频可用，数据链路全通（06-10 --data-only 验证：热点10 + alpha33 + ETF8 + 商品3，stock_agent 17条 = 补全 alpha 缺口）
- [x] CHANGELOG 含 `[4.2.0]` 条目（06-11 写入：8 Fixed + 5 Added + 3 Changed）
- [x] **stock_agent 信号深度升级 v1.0**（06-11 三期补齐：reasons 中文短句 + resonance_score + entry/target/stop_loss，ECS dry-run 17 条全字段齐全）

---

## V4.3.0 📋 进行中 (2026-06-12 → )

> 本次迭代锚点：**Pipeline A → /m 桥通**，把事件驱动 LLM 推股接入 alpha tab 为主信号源。

### 域 0：Pipeline A → /m 信号桥 ✅ 桥已搭

| # | 任务 | 来源 | 状态 |
|---|------|------|------|
| 4.3-b1 | **daily.py alpha_signals → JSONL 写入**：`_write_trader_contract()` 追加 alpha_signals 提取 + signal_id 去重 + confidence 映射 + expiry 计算 | alpha tab 信息密度不足 | ✅ |
| 4.3-b2 | **STRATEGY_CATEGORY_MAP 注册**：signals.js 加入 `report_agent: 'alpha'` | 新策略不可见 | ✅ |
| 4.3-b3 | **freshness 语义修正**：mobile.js 新增 `signalFreshness`（newestTime），原 `dataFreshness` 保留给 hotEvents | RSS 新鲜度被误用作信号新鲜度 | ✅ |
| 4.3-b4 | **CONTEXT.md V4.3 全量对齐**：策略表/双 Pipeline 图/四分类/技术决策/成功标准/技术债 | 文档腐烂 | ✅ |
| 4.3-b5 | **本地 dry-run 验证**：`python3 core/daily.py --date 2026-06-10` → 检查 JSONL 产出 | 代码不可测试 | ✅ 跳过 — JSONL 已有 5 条 report_agent 信号（06-10/06-11 真实产出） |
| 4.3-b6 | **端到端验证**：`curl /m/api/cycleradar` 确认 report_agent 信号在 alpha tab | 接口不可见 | ✅ report_agent 5 信号（茅台/宁德/上海新阳/章源钨业/天齐锂业），全字段 entry/target/stop |
| 4.3-b7 | **ECS 同步**：`bash scripts/sync_to_ecs.sh` → `ssh pm2 restart trader-admin` | 部署到生产 | ✅ 06-12 apply 同步 4.3-s1 变更（阈值 + 排名制），pm2 restart，/m 验证 32 signal avConf=0.73 |

### 域 1：Harness 反射层（从 V4.2 收尾 → 正式功能域）

| # | 任务 | 来源 | 优先级 |
|---|------|------|--------|
| 4.3-h1 | **路由热力图**：积累 2 周使用数据，生成 Scott 专属的 P×C 路由偏好热力图 | META-DEV 反思 | P1 |
| 4.3-h2 | **Scott 干预规则引擎**：对话中说"这次用 Pro"→ 自动记录为临时/持久规则 | 议题 4 | P1 |
| 4.3-h3 | **SESSION_LOG 自动触发**：todowrite 完成 → 自动追加 SESSION_LOG 条目 | 改进建议 | P1 |
| 4.3-h4 | **META-DEV 周期性复盘**：每周日自动生成路由效率报告 | 反思日志积累 | P2 |
| 4.3-h5 | **AFTER_SESSION diff 可视化**：对话开始时的上下文恢复确认 | Pre-session Injection | P2 |

### 域 2：策略引擎

| # | 任务 | 来源 | 优先级 |
|---|------|------|--------|
| 4.3-s1 | ✅ **stock_agent 阈值/票池优化**：排名制替换门槛制 — `total>=45`→`38`，移除 tier 硬门，全量按 catalyst_score 降序取前 15（`904b070`） | alpha 信号空白 | P0 |
| 4.3-s2 | **daily.py → ECS 迁移**：2938行 Mac MCP 依赖 → report_agent 迁移到 ECS | 日报管线断层 | P1 |
| 4.3-s3 | **11 模型摸底**：core/ 目录原始代码/数据路径确认，建立模型能力矩阵 | C 任务遗留 | P2 |
| 4.3-s4 | **backtest.py PM2 调度**：cron 注册 + 学习反馈自动上报 | V4.2 P3 远期 | P2 |

### 域 3：产品化 & 基础设施

| # | 任务 | 来源 | 优先级 |
|---|------|------|--------|
| 4.3-p1 | **票池管理入口**：admin trader 面板自选维护 UI | V4.1 遗留 | P1 |
| 4.3-p2 | **信号质量统计面板**：日信号量/去重率/过期清理 | 监控缺口 | P1 |
| 4.3-p3 | **sync_to_ecs.sh 部署脚本**：一键本地→ECS 同步 + pm2 restart | 部署纪律 | ✅ eP0 已落地基础版；V4.3 优化 dry-run 白名单/PM2 restart |
| 4.3-p4 | **DingTalk webhook → PM2 watchdog**：weewe-rss 401 自动 restart | 间歇断流 | P2 |
| 4.3-p5 | **WeWe RSS 订阅号管理 UI**：增删改查 + 标签 | 运维效率 | P3 |

### V4.3 Done 定义
- [x] **Pipeline A → /m 桥代码完成**：daily.py + signals.js + mobile.js 三文件修改 ✅
- [x] daily.py dry-run 产出有效 JSONL（✅ 跳过 — upstream_signals.jsonl 已有 5 条 report_agent 信号，格式完整）
- [x] curl /m/api/cycleradar 返回 report_agent 信号 ✅（5 信号，全字段，avConf=0.70）
- [x] ECS 同步 + pm2 restart ✅（ECS 代码为当前修复版）
- [ ] Harness 反射层 3 机制持续运行 ≥ 2 周无断更
- [ ] stock_agent 日频 ≥ 1 条 alpha 信号产出
- [ ] 票池管理 UI 可用
- [ ] 信号质量面板上线
- [x] sync_to_ecs.sh 基础版就位（eP0）；V4.3 继续优化全量 apply 安全策略

---

## V4.4.1 ✅ 已完成 (2026-06-12) — 契约扩展：3 文件桥

> 扩 Pipeline A 桥：alpha_signals → upstream_signals.jsonl / alpha_latest.json / event_narrative_latest.json

| # | 任务 | 状态 |
|---|------|------|
| 4.4.1-a | **alpha_latest.json 契约**：daily.py `_write_trader_contract()` 写入 entry/target/stop/thesis/sector | ✅ |
| 4.4.1-b | **event_narrative_latest.json 契约**：写入 events/sector_outlook/global_conclusion | ✅ |
| 4.4.1-c | **architecture.ejs 更新**：展示 3 契约文件 | ✅ |
| 4.4.1-d | **trader CLAUDE.md 对齐**：支持 3 契约消费 | ✅ |
| 4.4.1-e | **cron contracts sync**：cron_daily.sh v3 step 2.5（cp → contracts/ → git add） | ✅ |

---

## V5.0 🎯 Phase 1 完成 (2026-06-12)

> 调度器 + 3 桥全链路接入 `/m` 移动端 → Commit `9936154`

| # | 任务 | 状态 |
|---|------|------|
| 5.0-1 | **Scheduler 模块**：`admin/scheduler.js` 文件级 + `routes/scheduler.js` 3 端点 | ✅ |
| 5.0-2 | **Cron v3**：每阶段 heartbeat 回调 + step 2.5 contracts sync | ✅ |
| 5.0-3 | **Contracts 双路径**：ECS 优先 → Mac fallback，mobile.js `_getContractsPath()` | ✅ |
| 5.0-4 | **Overview 今日研判**：app.js `buildNarrativeCard()` 渲染 regime/action/thesis/events/risks | ✅ |
| 5.0-5 | **CycleRadar alpha_latest**：`_buildCrAlpha()` + `_buildCrSignalDetail()` 合约快照 | ✅ |
| 5.0-6 | **Dashboard 动态版本**：`APP_VERSION` 环境变量 | ✅ |

### V5.0 Phase 1 Done 定义
- [x] Scheduler heartbeat/getStatus/getHistory 通过 cron 每阶段回调
- [x] 3 契约文件从 cron contracts/ 路径可被 `/m` 消费
- [x] 今日研判卡位于概览 tab 体温之后、账户之前
- [x] alpha_latest 合约快照（入场/目标/止损）在信号详情展开中可见
- [x] 6 文件全部 syntax-check 通过
- [x] CONTEXT / ROADMAP / ROADMAP header 全部更新到 V5.0

### 📋 待部署
- [ ] ECS sync + pm2 restart trader-admin
- [ ] 端到端 smoke test：open /m → 今日研判/alpha_latest 验证
- [ ] V5.0 Phase 2 规划（backtest CI / signal stats / 情绪指数？TBD）

---

## V5.1.0 ✅ 已完成 (2026-06-13)

> 核心：`/m` 信号Tab事件解读卡片修复 + 统计栏 + 风险警告。

| # | 任务 | 状态 |
|---|------|------|
| 5.1a | `/m` 事件解读 thesis/sectors/tickers 全空修复（mobile.js 字段映射 `interpretation→thesis` / `sector_impact→sectors` / `stock_impact→tickers`） | ✅ |
| 5.1b | 统计栏 `buildCrStatsBar`：信源/条数/LLM置信/30日胜率 | ✅ |
| 5.1c | 风险警告卡片 `global_conclusion.risk_warnings[]` 渲染 | ✅ |
| 5.1d | CSS 新增 `.cr-stats-bar` / `.cr-stat-item` / `.cr-risk-warnings` / `.cr-risk-item` | ✅ |
| 5.1e | ECS 部署 + `/m` 首页验收（8/8 events 有 thesis+sectors，7/8 有 tickers） | ✅ |

### V5.1 Known Issues
- 30 日胜率显示 `—`（placeholder），待数据源接入 `global_conclusion` 统计
- Event #6（原油大跌）源数据 `stock_impact: []`——LLM 未为该事件生成关联股票，非代码缺陷

---

## V5.2 ✅ 已完成 (2026-06-15) — 审计修复

| # | 任务 | 状态 |
|---|------|------|
| 5.2-1 | app.js 8 项编辑（regimeMap / 事件排序 / 市场摘要 / thesis / hotEvents / alpha / diagnosis / narrativeCard） | ✅ |
| 5.2-2 | mobile.js `/m/api/haoyunge` 端点 + 好运哥策略状态机 | ✅ |
| 5.2-3 | architecture.ejs RSS 8 源 + 好运哥已激活 + tracker≠diagnosis | ✅ |
| 5.2-4 | nav.ejs + style.css Admin 暗色专业风 | ✅ |

## V5.3 ✅ 已完成 (2026-06-18) — Admin P1 双任务

| # | 任务 | 状态 |
|---|------|------|
| 5.3-1 | 自选股入口：`admin/models/watchlist.js` CRUD + 3 routes + `watchlist.ejs` + subnav 6 tab | ✅ |
| 5.3-2 | 微信文章统计：`/admin/trader/article-stats` + `article-stats.ejs` + `hot_enrichment.json` 聚合 | ✅ |

## V5.4 ✅ 已完成 (2026-06-18) — 回撤统计 P2

| # | 项目 | 优先级 | 来源 | 状态 |
|---|------|--------|------|------|
| 5.4-1 | **回撤统计**：双池（auto + 自选），Sina API 实时行情，7 tab subnav 全量对齐，ECS 200 | P2 | TODO #2 | ✅ |

## V5.5 📋 进行中 — 技术债扫尾 + 兼并重组

### 当前任务

| # | 项目 | 优先级 | 来源 | 状态 |
|---|------|--------|------|------|
| 5.5-1 | 兼并重组模块确认：AKShare cron + ma_signals 产线状态 | P2 | TODO #5 | ✅ 06-18 |
| 5.5-2 | 7 Prompt 模板最终 review | P3 | TODO #4 | ✅ 06-18 |

### 技术债（从 V4.4.0 折叠）

| # | 任务 | 优先级 | 状态 |
|---|------|--------|------|
| tb-1 | ECS `output/` 旧目录残骸清理 | P1 | ✅ 已不存在 |
| tb-2 | .env 密钥安全迁移（crontab 明文 → .env） | P1 | ✅ V5.5 |
| tb-3 | 本地 git status 清理（artifacts .gitignore） | P2 | ✅ V5.5 |
| tb-4 | 死代码扫描 + Scott 确认删除 | P2 | ✅ V5.5 |

## V6.0 ✅ 已完成 (2026-06-19) — 交易员风格视觉升级 + /m 三 tab 上线

> 核心：`/m` 从 5 tab 旧布局升级为 3 tab 交易员风格，文档体系收敛

| # | 任务 | 来源 | 状态 |
|---|------|------|------|
| 6.0-a | **dashboard.ejs CSS 全面重写**：gauge-wrap 温度仪表盘 / sig-summary 横排计数 / wl-row 持仓行 / cyra-table 信号表格 / event-row 事件卡片 | V5.5 进化 | ✅ |
| 6.0-b | **HTML/JS 全量对齐新 CSS**：513→532 行，三 tab（概览/自选/信号）完整可用 | V5.5 进化 | ✅ |
| 6.0-c | **`/m` 路由切换**：`/m` → dashboard.ejs V6，`/m/v6` → 301 重定向到 `/m`，旧 V5.x 路由 handler 已删除 | V5.5 进化 | ✅ |
| 6.0-d | **`/m/api/watchlist` fallback**：读 ECS `watchlist.json`（`{stocks:[...]}` 解嵌套修复），自选 tab 展示自选股列表 | V5.3 遗留 | ✅ |
| 6.0-e | **design-tokens.md 落地**：`/m Mobile` 区块设计令牌，dashboard.ejs 零内联 hex，全部用 `var(--m-*)` | 架构收口 | ✅ |
| 6.0-f | **`/m/api/summary` 端点**：温度仪表盘 + 概览 tab 数据源（contracts/event_narrative_latest.json） | V5.0 演化 | ✅ |
| 6.0-g | **架构文档对齐 V6**：`docs/architecture-v6.0.md` 重写（管线/前端/Admin/契约/cron 全量更新） | 文档收敛 | ✅ |
| 6.0-h | **ROADMAP + CONTEXT 版本头更新**：V5.5 → V6.0，统一版本号 | 文档收敛 | ✅ |

### V6.0 Done 定义
- [x] `/m` 三 tab（概览/自选/信号）正常渲染，交易员深色风格
- [x] `/m/api/summary` 温度 ≥0 + 今日研判卡片
- [x] `/m/api/cycleradar` alpha/ETF/commodity 信号数据完整
- [x] `/m/api/watchlist` 自选股列表非空（fallback: watchlist.json）
- [x] 三核心 API curl 验收通过
- [x] 架构文档 / ROADMAP / CONTEXT 三文档版本号统一 V6.0
- [x] watchlist_signals cron 接入（✅ V6.1.0: batch import 已替代原计划）

---

## V6.1.0 ✅ 已完成 (2026-06-20) — 盘中+盘后闭环：wanjun + tracker

> 核心：万军选股模型上线 + OHLC 裁决引擎闭环 + RSS 第 9 源

| # | 任务 | 来源 | 状态 |
|---|------|------|------|
| 6.1.0-a | **wanjun screener 上线**：模型 2/8/10 接入 cron 15:35 Mon-Fri，`signals.js` 注册 `wanjun_models: 'alpha'`，产出写入 `upstream_signals.jsonl` | V6.1 计划 | ✅ |
| 6.1.0-b | **tracker_closer OHLC 裁决**：日频从腾讯 API 拉取 OHLC，窗口内 high≥target→WIN, low≤stop→LOSE，趋势策略胜率 54%，波段策略 26% | V6.1 计划 | ✅ |
| 6.1.0-c | **RSS 财经早餐第 9 源上线**：wewe-rss 手动订阅验证，9 源全部 status=1 | RSS 扩展 | ✅ |
| 6.1.0-d | **/admin/architecture V6.1 里程碑更新**：标题/日期/9 源/cron/技术债/RSS 表格全部刷新 | 文档收敛 | ✅ |
| 6.1.0-e | **signalSourceStats**：reflection 页 `buildSignalSourceStats()` 策略来源 🟢🟡🔴 监控表 | 可观测性 | ✅ |

### V6.1.0 Done 定义
- [x] wanjun cron 15:35 首日产出信号，`STRATEGY_CATEGORY_MAP` 对齐
- [x] tracker_closer 177 条信号裁决完成，趋势 54%/波段 26% WIN
- [x] RSS 9 源 wewe-rss `/feeds` 页面 all status=1
- [x] architecture 页版本号/cron/RSS 表格/技术债状态与源码一致
- [x] reflection 页信号来源健康度 🟢🟡🔴 渲染正确

---

## V6.1.1 ✅ 已完成 (2026-06-20) — 管理后台增强：批量/排序/统计

> 核心：watchlist 批量导入 + strategy 排序 + trader overview 文章统计 + 文档三件套对齐

| # | 任务 | 来源 | 状态 |
|---|------|------|------|
| 6.1.1-a | **watchlist 批量导入**：POST /admin/trader/watchlist/batch + textarea 多行粘贴（代码 名称 备注），51 只测试通过，duplicate 静默跳过 | V6.1 计划 | ✅ |
| 6.1.1-b | **strategy 诊断排序**：纯前端 `sortTable()`，6 列可点击升降序，默认得分降序 | V6.1 计划 | ✅ |
| 6.1.1-c | **trader overview 文章统计**：`sql.js` WASM 直读 `wewe-rss.db`，活跃信源/累计文章/今日新增/最近更新 4 维卡片 | V6.1 计划 | ✅ |
| 6.1.1-d | **CHANGELOG 补全**：[6.1.0] A-C + [6.1.1] D-J 全部条目 | 文档收敛 | ✅ |
| 6.1.1-e | **CONTEXT + ROADMAP V6.1.1 同步**：策略表/cron/关键决策/成功标准/技术债/after-session 全量更新 | 文档收敛 | ✅ |

### V6.1.1 Done 定义
- [x] watchlist 批量导入 51 只通过，数据库落盘验证
- [x] strategy 排序 6 列正常升降序，默认得分降序
- [x] article summary 5 关键词命中（活跃信源/累计文章/今日新增/最近更新）
- [x] CHANGELOG / CONTEXT / ROADMAP 三文档版本号统一 V6.1.1

### 📋 下一阶段
- [ ] wanjun 模型 1/3-7/9/11 接入（V6.2）
- [ ] ECS SSH 密钥注册（运维）
- [ ] 三文档上传 ECS `/opt/cycleradar-trader/`

---

## 关键里程碑时间线

```
V4.0.0 ✅  2026-06-08  4分类移动端上线、ECS迁移完成
V4.0.1 ✅  2026-06-08  P1 技术债清理（positions/冒烟/ROADMAP）
V4.1.0 ✅  2026-06-09  UX 增强 + RSS 保鲜监控（展开卡片/摘要/enrich/过滤/4层防御）
V4.2.0 ✅  2026-06-09  管线修复（ecosystem/body列/stock_agent/文档重建）→ ✅ V4.2 闭环 06-11
V4.3.0 ✅  2026-06-12  Pipeline A → /m 桥通 → ✅ 桥+阈值+排名制全部部署，32 signal 持续产出
V4.4.1 ✅  2026-06-12  契约扩展：3 文件桥（alpha_latest + event_narrative + contracts sync）
V5.0   ✅  2026-06-12  Phase 1：调度器 + 3 桥全线接入 /m（scheduler/cron v3/narrative card/alpha_latest viz/动态版本）
V5.1   ✅  2026-06-13  /m 信号Tab字段映射修复 + 统计栏 + 风险警告（thesis/sectors/tickers 全空→7/8 events 数据齐全）
V5.2   ✅  2026-06-15  审计修复：app.js 8项 + 好运哥策略激活 + admin暗色风 + architecture.ejs 闭环
V5.3   ✅  2026-06-17  30日胜率真实数据（verdict引擎 + Sina API + 15:50 cron）+ hex全清零
V5.4   ✅  2026-06-18  Admin P1 双任务（自选股入口 + 微信文章统计）+ ROADMAP版本线整合
V5.5   ✅  2026-06-18  P2 回撤统计双池视图（Sina API 实时行情 + auto/自选池 + 摘要卡片 + 7 tab subnav 全量对齐）+ ma_signals cron 上线（AKShare 日频 + 信号总线整合 + 15:45 cron）
V6.0   ✅  2026-06-19  交易员风格视觉升级（gauge-wrap/sig-summary/cyra-table/wl-row）+ /m 三 tab + 架构文档收敛
V6.1.0 ✅  2026-06-20  wanjun screener 上线 + tracker_closer OHLC 闭环 + RSS 第 9 源 + signalSourceStats
V6.1.1 ✅  2026-06-20  watchlist 批量导入 + strategy 排序 + article 统计卡片 + 文档三件套对齐
```

---

## 已废弃项

- ~~1.1 恢复日频自动化（launchd plist）~~ → PM2 + ECS
- ~~1.2 Token 7h 缺口~~ → ECS cron `0,2,4` token_heartbeat
- ~~1.3 health.py 接 launchd~~ → PM2 monitor */30
- ~~恢复 6 个 CycleRadar plist~~ → 策略引擎 PM2 化
- ~~Docker 部署~~ → PM2 + ecosystem.config.js
