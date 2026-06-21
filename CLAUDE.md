# 交易员 — A股个人交易体系

> 独立工作区，不与 CycleRadar/周期雷达 共享代码库。
> 远程部署路径：`/opt/trader/output/`

## 工作区信息

- **路径**：`~/交易员/`
- **远程**：`root@139.196.115.64:/opt/trader/output/`
- **目标**：100-500万 个人A股账户，完整决策链自动化
- **技术栈**：Python 3.9+ + Finstep MCP + Claude API

## 五层架构

| Layer | 模块 | 功能 |
|-------|------|------|
| 1 | `modules/timing.py` | 市场温度计 + 8阶段 + 仓位建议 |
| 1.5 | `modules/haoyun.py` | 好运哥仓位纪律调节器（月线/连亏/周阴线/市值新高） |
| 2 | `modules/sectors.py` | 活跃主线识别（龙虎+热点）|
| 3 | `modules/scanner.py` | 14模型量化扫描选股 |
| 4 | `modules/signals.py` | NX点+Fibonacci+MA买卖点 |
| 5 | `modules/account.py` | 持仓记录+账户健康检查 |
| 6 | `modules/strategy.py` + `modules/tracker.py` | 自选池策略 + 5/10/20天信号跟踪反思 |

## 工作规则

1. **仓位服从温度计**：总仓位不得超过 `timing.recommended_position` × 1.1
2. **好运哥仓位叠加层**：`haoyun.py` 在温度计仓位之上叠加 5 条规则（月跌幅空仓/连亏清仓/连亏降仓/周阴线降仓/市值新高提仓），输出 `haoyun_position` 作为最终仓位
3. **止损即止损**：跌破止损位自动告警，不犹豫
4. **中文优先**：结论先行，数据标注来源和日期
5. **不构成投资建议**：所有输出末尾标注免责声明
6. **MCP失败最多重试1次**，仍失败则报告

## 运行入口

```bash
python trader.py --date 2026-05-17          # 完整日报（择时+扫描+账户）
python trader.py --date 2026-05-17 --timing-only  # 仅择时
python trader.py --date 2026-05-17 --skip-scan    # 跳过扫描（快速模式）
python trader.py --date 2026-05-17 --dry-run      # 不调用API，打印数据包
python trader.py --date 2026-05-17 --strategy     # 含自选池策略页（40只诊断+评分排名）
python3 modules/strategy.py --date 2026-05-17     # 单独运行策略输出
python3 modules/scanner.py --date 2026-05-17      # 单独运行扫描（完整列表）
python3 modules/scanner.py --date 2026-05-17 --models qkxl htji  # 指定模型
python3 modules/scanner.py --date 2026-05-17 --models hydx nsdyy cqft  # 好运哥3模型
python3 modules/haoyun.py --date 2026-06-16 --position 0.25  # 好运哥仓位调节（独立运行）
python3 modules/pool.py                           # 显示票池
python3 modules/pool.py add 300474 景嘉微 --reason 回调狙击
python3 modules/pool.py remove 300474
python3 modules/pool.py refresh --date 2026-05-17
python3 modules/signals.py --date 2026-05-17 --codes 002371 300276  # 分析买卖点
python3 modules/signals.py --date 2026-05-17 --codes 002371 --all   # 含观望结果
python3 modules/tracker.py                           # 信号跟踪反思（5/10/20日前向绩效）
python3 modules/tracker.py track --date 2026-05-29   # 指定日期跟踪
python3 modules/tracker.py add 600519 贵州茅台 --reason "消费龙头"  # 新增标的到自选池
```

## MCP 配置

- **基地址**：`http://fintool-mcp.finstep.cn`
- **认证**：`MCP_SIGNATURE` 环境变量（来自 `.env`）
- **请求头**：`Accept: application/json, text/event-stream`

## 远程部署

- **服务器**：`root@139.196.115.64`
- **实际项目路径**：`/opt/trader/output/`（非 `/opt/cycleradar-trader/`，后者是 CycleRadar 平台）
- **Python**：必须用 `python3.9`（`python3` 是 3.6.8，不兼容 type hints）
- **同步命令**：`scp ~/交易员/modules/*.py root@139.196.115.64:/opt/trader/output/modules/`
- **验证**：先语法检查 `python3.9 -m py_compile *.py`，再 dry-run

## 服务访问

- 日报：http://139.196.115.64:8080/trader/
- 策略：http://139.196.115.64:8080/trader/strategy/
- 跟踪：http://139.196.115.64:8080/trader/tracker/

## 📌 阶段存档

### TC-002 · 2026-06-16 · 好运哥交易体系集成

**已完成**：
- `modules/haoyun.py`（248行）：好运哥仓位纪律调节器 — 5 条规则链式执行（月跌幅<-5%空仓 → 连亏3日清仓 → 连亏2日×0.5 → 周阴线<-8%×0.3 → 市值新高×1.2）
- `modules/scanner.py`（1503行，+355行）：+3 模型（hydx 好运低吸/nsdyy 牛市第一阳/cqft 超强反弹）+ 龙一龙二过滤器 + 上证指数预取缓存
- `modules/timing.py`（+8行）：`adjust_position()` 叠加层，输出 `haoyun_position` / `haoyun_flags`
- `trader.py`（+12行）：`print_timing_report()` 显示好运调节 + health check 用 haoyun 仓位
- 远程同步到 `/opt/trader/output/` + Python 3.9 语法检查 + dry-run + live timing 全通过

**架构决策**：
- 仓位为叠加层：原温度计仓位 → 好运调节器 → 最终仓位，不混入市场温度计
- 好运低吸以规则 3/9（空中加油+从容低吸）为核心
- 龙一龙二过滤器：按板块分组 → 按涨幅排序 → 每板块取 top 2
- 牛市第一阳流通市值>500亿改 note 人工确认（避免每只票额外 MCP 调用）
- 上证指数缓存：scan() 入口预取 → 出口 reset → `_is_index_ma3_up()` 无参数

**关键文件**：
| 文件 | 行数 | 状态 |
|------|------|------|
| `modules/haoyun.py` | 248 | ✅ 独立运行 |
| `modules/scanner.py` | 1503 | ✅ 14 模型 |
| `modules/timing.py` | 271 | ✅ haoyun 叠加 |
| `trader.py` | ~410 | ✅ 3 处补丁 |

### TC-001 · 2026-05-28 · 初版 MVP 上线

**已完成**：
- `modules/strategy.py`（~670行）：40只自选池诊断 + 五维度打分 + 三档分类 + HTML 策略页
- `modules/tracker.py`（~680行）：5/10/20日前向跟踪 + 自建判定引擎 + 横轴透视表格 + CLI add
- `cron_daily.sh`：8:07 AM 周一至五自动运行 择时+扫描+策略+跟踪+部署
- Nginx 三路由：`/trader/` `/trader/strategy/` `/trader/tracker/`
- 票池交叉对比 Bug 已移除（票池≠自选池，窜数据了）

**当前局限**：
- 跟踪 126 条记录全部 NODATA（信号始于 0528，仅 1 日前向数据）
- 策略页评分权重待实战校准（NX buy +30 / MA full +20 等）
- scanner 模型 11 个中 7 个接入（qkxl 缺龙虎榜数据）

**下次启动可从以下任意点继续**：
- `python3 modules/tracker.py track --date 2026-05-30`  — 累积一天前向数据后重跑
- `python3 modules/strategy.py --date 2026-05-30`  — 新交易日策略扫描
- 新增标的：`python3 modules/tracker.py add <code> <name> --reason "..."`

**关键文件对照**：
| 文件 | 行数 | 状态 |
|------|------|------|
| `modules/strategy.py` | ~670 | ✅ 不含票池交叉对比 |
| `modules/tracker.py` | ~680 | ✅ 5/10/20 横轴表格 |
| `cron_daily.sh` | ~60 | ✅ 四步流水线 |
| `data/strategy_log.jsonl` | 240条 | 0528+0529 信号 |
| `data/tracker_log.jsonl` | 126条 | 全部 NODATA |
| `output/tracker/latest.html` | 25KB | ✅ 已部署 |
