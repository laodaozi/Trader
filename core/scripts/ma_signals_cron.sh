#!/bin/bash
# ma_signals_cron.sh — cron 封装：载入密钥 → AKShare 拉取 M&A 公告 → 写入信号总线 → 日志
# 用法：放到 crontab，每日盘后执行 1 次
#
# V5.5 新建：ma_signals_runner.py 的 cron wrapper
# 信源：AKShare stock_notice_report（资产重组 + 重大事项，MA_RELEVANT_TYPES 白名单）
# 产线：collect_ma_signals → write_signal → upstream_signals.jsonl

set -euo pipefail

# ── 从集中密钥文件载入 API Keys ──────────────────────
if [ -f /opt/cycleradar-trader/.env ]; then
  set -a
  source /opt/cycleradar-trader/.env
  set +a
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="/opt/cycleradar-trader/data/logs"
mkdir -p "$LOG_DIR"

LOG_FILE="$LOG_DIR/ma_signals_cron_$(date +%Y%m%d-%H%M%S).log"
DATE_STR="$(date +%Y-%m-%d)"

echo "======================================================" | tee -a "$LOG_FILE"
echo "  ma_signals cron — $DATE_STR" | tee -a "$LOG_FILE"
echo "======================================================" | tee -a "$LOG_FILE"

cd /opt/cycleradar-trader/core

PYTHONPATH=/opt/cycleradar-trader/core:/opt/cycleradar-trader/core/signals:/opt/cycleradar-trader/core/signals/adapters
export PYTHONPATH
export CYCLERADAR_DATA_DIR=/opt/cycleradar-trader/data

/usr/bin/python3.9 strategies/ma_signals_runner.py --date "$DATE_STR" --article >> "$LOG_FILE" 2>&1

EXIT_CODE=$?
if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ ma_signals 完成 (exit $EXIT_CODE)" | tee -a "$LOG_FILE"
else
    echo "❌ ma_signals 失败 (exit $EXIT_CODE)，见: $LOG_FILE" | tee -a "$LOG_FILE"
fi

exit $EXIT_CODE
