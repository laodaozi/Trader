# Changelog · CycleRadar Trader

## [6.1.1] — 2026-06-20

### Added
- **wanjun screener 上线**：模型 2/8/10 接入 cron（Mon-Fri 15:35），`signals.js` 注册 `wanjun_models: 'alpha'`，产出信号写入 `upstream_signals.jsonl`
- **tracker_closer 日频 OHLC 裁决引擎**：日频从腾讯 API 拉 OHLC，HOLD→WIN/LOSE 裁决（窗口内 high≥target→WIN, low≤stop→LOSE），趋势策略胜率 54%，波段策略 26%
- **RSS 财经早餐第 9 源上线**：wewe-rss 手动订阅验证，9 源全部 status=1
- **/admin/architecture V6.1 里程碑更新**：标题/日期/9 源/cron/技术债/RSS 源全部刷新
- **watchlist 批量导入**：`POST /admin/trader/watchlist/batch` + textarea 多行粘贴（`代码 名称 备注`，分隔符支持空格/逗号/Tab），51 只测试通过
- **strategy 诊断表列头排序**：纯前端 `sortTable()`，6 列可点击升降序，默认得分降序
- **trader overview 文章统计摘要卡片**：活跃信源/累计文章/今日新增/最近更新，`sql.js` WASM 直读 `wewe-rss.db`，完全非阻塞

### Changed
- **tracker_closer 数据源切换**：AKShare（东方财富 API）ECS 被封 → 腾讯直连 OHLC
- **信号源健康度**：reflection 页新增 `buildSignalSourceStats()` 策略来源监控表（6 策略 🟢🟡🔴）

### Verified
- wanjun cron 15:35 首日产出信号，`STRATEGY_CATEGORY_MAP` 对齐
- tracker_closer 177 条信号裁决：趋势 54% WIN, 波段 26% WIN
- RSS 9 源 wewe-rss `/feeds` 页面 all status=1
- architecture 页版本号/hotevents cron/RSS 表格/技术债状态与源码一致
- watchlist 批量导入 51 只测试通过，duplicate 静默跳过
- strategy 排序 6 列正常，默认得分降序
- article summary 5 关键词命中，wewe-rss.db 读取 <100ms

---

## [6.1.0] — 2026-06-19

### Added
- **热点事件卡片**：hotevents 渲染到 `/m` 概览 tab（`/m/api/summary` 新增 `hotEvents` 字段，dashboard.ejs `buildHotEventCards()` 渲染 6 card）
- **策略反思页**：`/admin/trader/reflection` 新增策略信号来源健康度 section，`buildSignalSourceStats()` 生成 6 策略 🟢🟡🔴 监控表
- **contracts 路径同步**：`mobile.js` 三路径 contracts 读取（ECS `/opt/trader/output/contracts/` → Mac `~/交易员/data/` → fallback 内置默认）

---

## [5.2.0] — 2026-06-15

### Added
- **事件卡片可视化 1:1 移植**：生产 `dashboard.py:render_event_narrative()` L1106-1167 的彩色卡片结构完整迁移到 Node `/m` 信号Tab
  - **API 层**：`mobile.js` L777-799 events mapping 重构，透传 `rank`/`source`/`decay_days`/`sectors[{name,direction,logic}]`/`commodities[{name,direction}]`/`tickers[{code,name,reason}]`
  - **渲染层**：`app.js` L428-483 `buildCrEventItem()` 1:1 移植生产 HTML 结构（ev-card > ev-header(ev-rank + ev-title + ev-decay) > ev-interp > ev-tags > ev-stocks > ev-source）
  - **CSS**：`dashboard.ejs` L269-290 旧 `.cr-en-event-*` 6 条 → 生产 `.ev-*` 20 条（border-left 蓝、rank 蓝底 #4fc3f7、tag 红绿灰、stock 展开态）
- **移动端个股理由交互**：`toggleCrReason(id)` onclick 展开收起 + CSS `.ev-stock.expanded` 高亮（替代 PC 版 `title=` hover 不可用）
- **版本号双路径对齐**：`mobile.js` L319 default + `dashboard.ejs` EJS fallback → V5.2

### Changed
- **sector tag 格式变更**：V5.1 的 `sectors: [name_str,…]` → V5.2 的 `sectors: [{name,direction,logic},…]`，tag 渲染带利好/利空方向色
- **commodity 分离渲染**：news 与 sector tags 同排但独立逻辑，direction 看"多/空"（非"利好/利空"）
- **source 底行**：事件卡片底部显示信源（如 "RSS hot enrichment"），与生产一致

### Verified
- API 验证：12 events，全 8 字段 (rank/source/decay_days/sectors/commodities/tickers/interpretation/title) 全部输出
- CSS 验证：`.ev-card/.ev-rank/.ev-tag` 全部注入页面
- 版本验证：`<span>` 显示 V5.2
- 健康检查：10/11 pass（1 fail: hotevents_cache 66h stale 预先存在，非本次引入）
- 旧类名残留：零（grep `cr-en-event-*` 无匹配）

### Known Issues
- 30 日胜率仍为 placeholder `—`（待数据源接入）
- Event #6（原油大跌）源数据 `stock_impact: []`（LLM 未生成，非代码缺陷）
- hotevents_cache 66h stale（enrich cron 需排查，非 V5.2 引入）

---

## [5.1.0] — 2026-06-13

### Fixed
- **P0: `/m` 事件解读卡片 thesis/sectors/tickers 全空**：`mobile.js` L745-749 字段映射错误——代码读取 `e.thesis`/`e.sectors`/`e.tickers`，但 `event_narrative_latest.json` 源数据字段名为 `interpretation`/`sector_impact`/`stock_impact`。字段名不匹配导致 8 个事件全部返回空。修复：`mobile.js` 新增兼容映射（`interpretation→thesis` / `sector_impact[{sector,direction,logic}]→sectors[]` / `stock_impact[{code,name,logic}]→tickers[{code,name,reason:logic}]`）

### Added
- **统计栏 `buildCrStatsBar`**：信源(strategyCount/16) · 条数(active) · LLM置信(global_conclusion.confidence) · 30日胜率(placeholder —)
- **风险警告卡片**：`global_conclusion.risk_warnings[]` 渲染 ⚠️ styled risk cards
- **CSS**：`.cr-stats-bar` / `.cr-stat-item` / `.cr-risk-warnings` / `.cr-risk-item` / `.cr-summary-conf`
- **动态版本号**：`dashboard.ejs` `appVersion` 升级到 V5.1

### Changed
- **mobile.js** 字段映射层：对象→字符串变换（`sector_impact[{sector,...}]` → `sectors: [name]`，`stock_impact[{code,name,logic}]` → `tickers[{code,name,reason}]`）
- **app.js** 消费端保持统一契约 `thesis`/`sectors`/`tickers`，兼容性变换在 API 层完成

### Verified
- API 验证：8/8 events 有 thesis+sectors，7/8 events 有 tickers（event #6 原油大跌 stock_impact:[] 是源数据问题）
- HTML 验证：stats-bar / risk-warnings / event-cards / sector-tags / V5.1 全部渲染正常
- ECS 部署：`git push origin gh-pages` + `pm2 restart trader-admin` → `/m` 首页验证通过

### Known Issues
- 30 日胜率显示 `—`（placeholder），待数据源接入 `global_conclusion` 统计
- Event #6（原油大跌）源数据 `stock_impact: []`——LLM 未为该事件生成关联股票，非代码缺陷

---

## [4.2.0] — 2026-06-11

### Added
- **stock_agent 信号深度升级 v1.0**：3 phase 补齐空白字段
  - P1 reasons：`_catalyst_reasons()` 规则引擎，6 维 catalyst breakdown → 中文短句（如 `NX中性 +10 | 资金流入 +20`），0 分维度自动跳过
  - P2 resonance_score：`catalyst["breakdown"].get("resonance", 0)` 注入 info，15 当 in_top10_industry
  - P3 price targets：`analyze_stock()` 返回 `close_price`；runner 从 OHLC 收盘价 + `nx.elasticity_20d` 推导 entry/target/stop_loss。buy → target=close*(1+elast*3), stop=-8%；else → stop=-5%
  - ECS dry-run 验证：17 条信号全字段齐全（含 `entry=83.9` 等），sync 路径 `core/stock_agent.py` + `core/strategies/stock_agent_runner.py` + `core/stock_analysis.py`

### Fixed
- **P0: daily.py ECS/本地模块分叉修复**：9 模块依赖 audit → 6 缺失函数 stub → 3 runtime bug 修复（NameError date→date_str / backtest/ missing __init__ / verify missing _check_price_targets）→ Phase 1-9 零错误 `--data-only` 跑通
- **P1: stock_agent 信号空白补齐**：ECS Python 3.9 venv 部署完成 → cron Mon-Fri 15:35 自动运行 → 06-10 产出 17 条选股信号（2 强推 + 15 关注，confidence 0.65-0.85，含中信/中国平安等）
- **P1: 4 分类面版日频验证**：`--data-only` 全链路通过（热点 10 + alpha 33 + ETF 8 + 商品 3），`/m` 移动端正常渲染
- **P2: hotEvents CSV 子进程 → sql.js WASM 直连**：ECS CentOS 8 GLIBC 2.28 不满足 better-sqlite3 预编译 binary 的 2.29 需求 → 改用 sql.js (WASM, zero native deps)。`_queryHotEventsFromDB()` 和 `_getRssHealth()` 迁移为 `async` + `initSqlJs()` + `new SQL.Database(buffer)` 模式。`package.json` 移除 better-sqlite3，新增 sql.js ^1.14.1
- **P2: upstream_signals.jsonl 写入端去重**：`write_signal()` 新增 `skip_dup` 参数（默认 True），模块加载时预读全部 `signal_id` 到 `_SEEN_IDS` set，命中即跳过追加。`skip_dup=False` 支持 intentional update 场景

### Added
- **Harness 反射层落地**：SESSION_LOG 恢复写入（06-10/06-11 条目） + META-DEV 从提案升级为活跃反思日志 + CONTEXT.md §9 AFTER_SESSION 注入区块 + ROADMAP V4.3 立项（14 任务 × 3 域）
- **产品文档三件套对齐**：CONTEXT.md / ROADMAP.md / architecture-v4.2.0.md 同步到 V4.2.0-dev，stock_agent 状态、Done 定义、技术债统一标记
- **V4.3 路线图**：3 功能域 14 任务（Harness 反射层 5 + 策略引擎 4 + 产品化 5）

### Changed
- **stock_agent 状态**：α 分类从 "零产出/信号空白" → "17 条信号已产出"，Done 定义 check off
- **daily.py → ECS 同步**：9 文件 scp 到 `root@139.196.115.64:/opt/cycleradar-trader/`
- **core/ 目录 layout**：本地采纳 ECS flat 布局（删除 strata/ 嵌套），6 stale 副本清理
- **V4.2 Done 定义更新**：Harness 反射层收尾纳入 Done 范围（SESSION_LOG / META-DEV / AFTER_SESSION 三文件落地）

### Known Issues
- stock_agent 日频稳定性待观察（06-10 首日 17 条，需连续 ≥3 天验证）
- enrich_hot_events tickers 覆盖率待 P2a 验证（body 列已就绪）
- wewe-rss "暂无可用读书账号" 间歇阻塞（非代码级，监控即可）
- daily.py 2938 行仍依赖 Mac MCP，ECS 不可运行（V4.3 迁移）

---

## [4.2.0-dev] — 2026-06-09

### Fixed
- **P0: ecosystem.config.js 固化 wewe-rss 启动**：`CRON_EXPRESSION` 写入 PM2 配置文件，解决裸 `pm2 start` 后 env 丢失导致 cron 静默失效。配置文件含完整 env 块（CRON_EXPRESSION/HOST/PORT/DATABASE_URL），为唯一启动入口。
- **P0: PM2 持久化双保险**：`systemctl enable pm2-root` + `pm2 save` → dump.pm2 含 CRON_EXPRESSION。机器重启 → `pm2 resurrect` 自动恢复全部进程。

### Changed
- **wewe-rss 启动命令标准化**：`pm2 start /opt/wewe-rss-deploy/ecosystem.config.js` 取代 `pm2 start dist/main.js --update-env`
- **V4.2 Done 定义更新**：聚焦管线修复（body 列 / stock_agent / 文档重建），原"日报管道恢复"延至 V4.3

### Added
- **ECS 资源摸底**：1.7GB RAM / 40GB disk (33GB free) / Python 3.9.6 / pip 9.0.3
- **产品文档重建**：`CONTEXT.md` V4.2.0-dev / `ROADMAP.md` 重写 / `docs/architecture-v4.2.0.md` 三子系统新建

### Known Issues
- articles 表无 body 列 → enrich tickers 全空，待 Prisma migration（P1）
- stock_agent ECS 零产出，缺 MCP + 6 归档 Python 模块（P1）
- `getAvailableAccount()` 间歇返回空 → "暂无可用读书账号" 阻塞 cron

---

## [4.1.0] — 2026-06-08

### Added
- **信号卡片可展开详情**：点击任意信号卡片展开，展示有效期倒计时、置信度等级、行业关联、阶段/排名、完整标签列表。`toggleCrCard()` 实现无跳转原地展开/收起。
- **市场摘要卡片**：CycleRadar 顶部新增合成卡片，展示活跃信号总数 / 多头 / 空头 / 策略数 4 维统计，long/short 比例 → 温度判断（进攻/均衡/防守）含操作建议（加仓关注/持仓观察/减仓观望）。
- `/m/api/cycleradar` 返回新增 `signal_id` 字段，供前端展开/调试使用。

### Changed
- **V4.1 策略转向**：日报管道恢复（report_agent → morning.json）延至 V4.2。V4.1 聚焦无 LLM 依赖的移动端 UX 改进（展开卡片 + 摘要卡片）。
- `CONTEXT.md` 更新：Done 定义拆分为 V4.1（UX）/V4.2（日报），技术债重新分级。

### Technical
- `mobile.js:formatSignal()` 新增 `signal_id` 输出。
- `app.js` 新增 `buildCrMarketSummary()` / `_buildCrSignalDetail()` / `toggleCrCard()` / `attachCrExpandHandlers()` 4 个函数。
- `dashboard.ejs` 新增 60 行 CSS（`.cr-sig-expandable` / `.cr-sig-detail` / `.cr-summary-card`）。
- 日报阻塞根因确认：`daily.py` 2938 行依赖 Mac MCP 服务（news.get_alpha_morning / score.py / stock_analysis.py / verify.py），ECS 无 Python 环境无 MCP 无法运行。`wewe-rss.db` articles 表仅有 `title + pic_url`，无正文字段，LLM 事件解读缺核心输入。延至 V4.2。

## [4.0.1] — 2026-06-08

### Fixed
- `signals.js` 分类映射改为显式 `STRATEGY_CATEGORY_MAP` + `ASSET_TYPE_CATEGORY_MAP`，未知策略归 `unknown` 并 `console.warn` 告警，杜绝静默误归 alpha
- `mobile.js` + `trader.js` positions 路径从 `../../../../交易员/...` 改为 `os.homedir()` 对齐 `core/daily.py` XDG 标准

### Added
- `CONTEXT.md`（新建）：领域语言基线（信号合约 / 策略表 / 4分类定义 / 置信度约束 / 成功标准）
- `ROADMAP.md`（新建）：从 workplan-2026Q3 迁移至 V4 ECS+PM2 基线，标注 V4.0→V4.1→V4.2 路线
- `scripts/smoke.sh`：7 项 API 冒烟自检（连通性 / JSON结构 / 4分类非空 / unknown 告警），ECS 实测 7/7 PASS

### Changed
- CHANGELOG 标题从 `WeWe RSS Fork` → `CycleRadar Trader`

---

## [4.0.0] — 2026-06-08

### Added · CycleRadar 4 分类改造（V4.0.0 核心交付）
- **`admin/models/signals.js`**（新增）
  - `getDashboardData()`：读 `data/upstream_signals.jsonl`，按 `signal_id` 去重取最新、按 expiry 过滤活跃，产出 summary / byStrategy / byAssetType / signals
  - `getCycleradarCategories()`：signals 映射为 alpha / ETF / 商品 三类
- **`admin/routes/mobile.js`**（新增）
  - `GET /m/api/cycleradar`：返回 `{ summary, hotEvents, alpha, etf, commodity, signals, byStrategy, byAssetType }`
  - `_getHotEvents()`：sqlite3 CLI CSV 模式查 `admin/data/wewe-rss.db`，返回 24h 内最新10条文章作为热点事件
- **`admin/public/mobile-assets/app.js`**（新增）
  - 重写 CycleRadar 渲染：`buildCrCategorySections()` → 4 个分类 section 替换旧 flat list + heatmap
  - 新增 `_h()` XSS 转义、`formatRelativeTime()` 相对时间
- **`admin/views/mobile/dashboard.ejs`**（新增）
  - 5 tab 移动端仪表盘：总览 / 信号 / 交易 / 回测 / CycleRadar
  - `.cr-event-card`、`.cr-ico` 热点事件卡片 CSS

### Added · 后台模块（V4.0.0 补全）
- `admin/routes/`：articles / dashboard / templates / trader
- `admin/models/`：trader-backtest / trader-strategy / trader-tracker
- `admin/views/`：articles / dashboard / partials / trader / comparison / admin templates

### Added · 文档与工具
- `docs/`：architecture-v3.9.6 系列架构图 HTML、preview-dashboard / preview-trader 预览
- `docs/prompt-templates/`：01-07 策略 prompt 模板库
- `core/writing/`：写作 pipeline（pipeline.py / prompt_registry.py / source_registry.py）
- `output/`：策略运行输出归档

### Fixed
- `admin/models/account.js`：better-sqlite3 require 健壮化，修复 ECS 冷启动 crash
- `admin/server.js`：注册全部路由，修复 404 问题

### Infrastructure
- 分类映射：`commodity_radar` → 商品，`rotation_factor` → ETF，`stock_agent`/`ma_signals` → alpha
- ECS 验证：hotEvents=2, alpha=4, etf=2, commodity=2；PM2 `trader-admin` online ✓

---

## [3.9.5] — 2026-06-07

### Fixed
- **Fix 1: 401 日级黑名单** `trpc.service.js` L68-74
  - `WeReadError401` 不再永久 DISABLE。改为 `blockedAccountsMap.set(today, blocked)`，隔天自动重试。
  - 根因：proxy 节点偶尔过载导致请求反射 401，非账号被封。永久 DISABLE 造成误杀后需人工进 DB 解封。
  - 生产验证：`MP_WXS_3233243226`(财闻私享) 18:43 由 `status_changed: INVALID→active`。
- **Fix 2: 0-based 分页兜底** `trpc.service.js` L139-143
  - `res.length === 0 && page === 1` → `return this.getMpArticles(mpId, 0, retryCount)`。
  - 根因：部分 MP 接口 page=1 返回空而 page=0 有数据，为 0/1-based 兼容性差异。
  - 生产验证：`MP_WXS_3233243226` 19:15 触发 Fix2，Sync Articles=45。
- **Fix 3: 30s 重试** `trpc.service.js` L156-162
  - `articles.length === 0` → `await new Promise(resolve => setTimeout(resolve, 30*1e3))` → 重试一次。
  - 注意：`let articles`（非 `const`），上游逻辑需要可变声明。
  - 根因：proxy 节点间歇性空返回，非分页尽头。
  - 生产验证：`MP_WXS_3242358265`(微策神机) 19:16 触发 Fix3，重试后 Sync Articles=6。

### Added
- `wewe_health.py` v2.0：6 维度健康检查（accounts/PM2/feeds/401/Fix触发/空返回率），PID 显示修复。
- `ops/WEWE_RSS_运维手册.md`：9 章 SOP，含 Token 保活机制 + 故障速查表。
- `底稿/已知问题.md`：W1-W5 故障追踪 + 根因分析。

### Changed
- Token 保活分析：确认微信读书无 refresh 端点，session 2-3h 滑动窗口。现有 cron `35 5,8,17,22 * * *`，22→5 间隙 7h 存在过期风险。
- PM2 日志输出增加 Fix 触发标记便于审计。

---

## [3.9.4] — 2026-06-05 ~ 2026-06-07

### Fixed
- `MP_WXS_3233243226`(财闻私享) DISABLED → INVALID status_changed 问题。更换 token 后手动到 `/feeds` 页点 Refresh → 触发 `addMpArticlesAndUpdateFeed` → `has_history=1`。

### Known Issues
- `MP_WXS_3191151316`(台球之门)：`"台球"` 被微信读书搜索屏蔽，`wxs2mp` 返回空。无法通过正常流程添加 feed。
- feed DISABLED 后即使重新激活，部分 UI 仍显示空数据，需切到 `/articles` 页手动触发 refresh。

---

## [3.9.3] — 2026-06-05

### Fixed
- ECS 上 `docker compose` 部署因 Docker Hub 拉取镜像频繁 timeout，切换为 PM2 + Node.js 直布方案（`dist/` 编译后产物，无 Docker 依赖）。
- `Error: cannot find module '../xxx.js'`：dist 目录相对引用路径问题，手动修正。
- PM2 restart 后 `4000` 端口未释放（Node `server.timeout` 问题），`--kill-timeout 5000` 解决。

### Changed
- 部署方式：Docker → PM2 + `node /opt/wewe-rss-deploy/dist/main.js`
- 启动脚本统一至 `/opt/wewe-rss-deploy/start.sh`


### Fixed
- **Fix 1: 401 日级黑名单** `trpc.service.js` L68-74
  - `WeReadError401` 不再永久 DISABLE。改为 `blockedAccountsMap.set(today, blocked)`，隔天自动重试。
  - 根因：proxy 节点偶尔过载导致请求反射 401，非账号被封。永久 DISABLE 造成误杀后需人工进 DB 解封。
  - 生产验证：`MP_WXS_3233243226`(财闻私享) 18:43 由 `status_changed: INVALID→active`。
- **Fix 2: 0-based 分页兜底** `trpc.service.js` L139-143
  - `res.length === 0 && page === 1` → `return this.getMpArticles(mpId, 0, retryCount)`。
  - 根因：部分 MP 接口 page=1 返回空而 page=0 有数据，为 0/1-based 兼容性差异。
  - 生产验证：`MP_WXS_3233243226` 19:15 触发 Fix2，Sync Articles=45。
- **Fix 3: 30s 重试** `trpc.service.js` L156-162
  - `articles.length === 0` → `await new Promise(resolve => setTimeout(resolve, 30*1e3))` → 重试一次。
  - 注意：`let articles`（非 `const`），上游逻辑需要可变声明。
  - 根因：proxy 节点间歇性空返回，非分页尽头。
  - 生产验证：`MP_WXS_3242358265`(微策神机) 19:16 触发 Fix3，重试后 Sync Articles=6。

### Added
- `wewe_health.py` v2.0：6 维度健康检查（accounts/PM2/feeds/401/Fix触发/空返回率），PID 显示修复。
- `ops/WEWE_RSS_运维手册.md`：9 章 SOP，含 Token 保活机制 + 故障速查表。
- `底稿/已知问题.md`：W1-W5 故障追踪 + 根因分析。

### Changed
- Token 保活分析：确认微信读书无 refresh 端点，session 2-3h 滑动窗口。现有 cron `35 5,8,17,22 * * *`，22→5 间隙 7h 存在过期风险。
- PM2 日志输出增加 Fix 触发标记便于审计。

---

## [3.9.4] — 2026-06-05 ~ 2026-06-07

### Fixed
- `MP_WXS_3233243226`(财闻私享) DISABLED → INVALID status_changed 问题。更换 token 后手动到 `/feeds` 页点 Refresh → 触发 `addMpArticlesAndUpdateFeed` → `has_history=1`。

### Known Issues
- `MP_WXS_3191151316`(台球之门)：`"台球"` 被微信读书搜索屏蔽，`wxs2mp` 返回空。无法通过正常流程添加 feed。
- feed DISABLED 后即使重新激活，部分 UI 仍显示空数据，需切到 `/articles` 页手动触发 refresh。

---

## [3.9.3] — 2026-06-05

### Fixed
- ECS 上 `docker compose` 部署因 Docker Hub 拉取镜像频繁 timeout，切换为 PM2 + Node.js 直布方案（`dist/` 编译后产物，无 Docker 依赖）。
- `Error: cannot find module '../xxx.js'`：dist 目录相对引用路径问题，手动修正。
- PM2 restart 后 `4000` 端口未释放（Node `server.timeout` 问题），`--kill-timeout 5000` 解决。

### Changed
- 部署方式：Docker → PM2 + `node /opt/wewe-rss-deploy/dist/main.js`
- 启动脚本统一至 `/opt/wewe-rss-deploy/start.sh`
