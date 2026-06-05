# 交易员 — A股个人交易体系

> 独立工作区，不与 CycleRadar/周期雷达 共享代码库。

## 工作区信息

- **路径**：`~/交易员/`
- **目标**：100-500万 个人A股账户，完整决策链自动化
- **技术栈**：Python + Finstep MCP + Claude API

## 五层架构

| Layer | 模块 | 功能 |
|-------|------|------|
| 1 | `modules/timing.py` | 市场温度计 + 8阶段 + 仓位建议 |
| 2 | `modules/sectors.py` | 活跃主线识别（龙虎+热点）|
| 3 | `modules/scanner.py` | 11模型量化扫描选股 |
| 4 | `modules/signals.py` | NX点+Fibonacci+MA买卖点 |
| 5 | `modules/account.py` | 持仓记录+账户健康检查 |
| 6 | `modules/strategy.py` + `modules/tracker.py` | 自选池策略 + 5/10/20天信号跟踪反思 |

## 工作规则

1. **仓位服从温度计**：总仓位不得超过 `timing.recommended_position` × 1.1
2. **好运哥规则**：连续2日亏损→WARNING，连续3日→强制清仓建议
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

## 服务访问

- 日报：http://139.196.115.64:8080/trader/
- 策略：http://139.196.115.64:8080/trader/strategy/
- 跟踪：http://139.196.115.64:8080/trader/tracker/

## 📌 阶段存档

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
