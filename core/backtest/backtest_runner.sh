#!/bin/bash
# backtest_runner.sh — 定时回测验证（轮动因子多周期 IC + 事件催化）
#
# 用法：
#   bash ops/backtest_runner.sh              # 用缓存数据回测（CI 默认）
#   bash ops/backtest_runner.sh --fetch      # 在线拉取数据后回测
#
# 触发时机：每天 03:00（盘前，数据已采集完毕）+ 16:00（盘后）
# 由 com.cycleradar.backtest.plist (launchd) 调度。

set -uo pipefail

PROJECT_DIR="/Users/scott/products/cycleradar-trader"
cd "$PROJECT_DIR"

# 加载环境变量（launchd 不继承 shell 环境，与 rss_collector.sh / loop_runner.sh 一致）
GLOBAL_ENV="$HOME/.config/cycleradar/.env"
if [ -f "$GLOBAL_ENV" ]; then
    set -a; source "$GLOBAL_ENV" 2>/dev/null || true; set +a
fi
if [ -f .env ]; then
    set -a; source .env 2>/dev/null || true; set +a
fi

# CI 默认 --from-cache（不重复打 MCP）；传 --fetch 则在线拉取
MODE_ARG="--from-cache"
if [ "${1:-}" = "--fetch" ]; then
    MODE_ARG=""
fi

LOG_DIR="$HOME/.config/cycleradar/logs"
mkdir -p "$LOG_DIR"
DATE=$(date +%Y-%m-%d)
DATED_LOG="${LOG_DIR}/${DATE}_backtest.log"

echo "=== backtest_runner.sh 启动 $(date) (mode=${MODE_ARG:-online}) ===" | tee -a "$DATED_LOG"

# python3 解析为 /usr/bin/python3（PATH 见 plist EnvironmentVariables）
python3 core/backtest/backtest.py $MODE_ARG 2>&1 | tee -a "$DATED_LOG"
EXIT_CODE=${PIPESTATUS[0]}

echo "=== 完成 $(date)，退出码: $EXIT_CODE ===" | tee -a "$DATED_LOG"
echo "BACKTEST_$([ $EXIT_CODE -eq 0 ] && echo OK || echo FAIL) date=$DATE exit=$EXIT_CODE ts=$(date +%s)" \
    >> "${LOG_DIR}/backtest_status.log"
exit $EXIT_CODE
