#!/bin/bash
# scanner_signals_cron.sh — cron 封装：14 模型扫描 → 共振计信 → 写入信号总线
# 用法：crontab -e 加入：40 15 * * 1-5 /opt/cycleradar-trader/core/scripts/scanner_signals_cron.sh
#
# V6.2 新建：scanner_signal_runner.py 的 cron wrapper
# 信源：scanner.py 14 模型（替换旧 wanjun_screener.py 11 模型）
# 产线：scanner.scan() → scanner_adapter.emit_scanner_signals() → upstream_signals.jsonl

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

LOG_FILE="$LOG_DIR/scanner_signals_cron_$(date +%Y%m%d-%H%M%S).log"
DATE_STR="$(date +%Y-%m-%d)"

echo "======================================================" | tee -a "$LOG_FILE"
echo "  scanner_signals cron — $DATE_STR" | tee -a "$LOG_FILE"
echo "======================================================" | tee -a "$LOG_FILE"

cd /opt/cycleradar-trader/core

PYTHONPATH=/opt/cycleradar-trader:/opt/cycleradar-trader/core:/opt/cycleradar-trader/core/signals:/opt/cycleradar-trader/core/signals/adapters
export PYTHONPATH
export CYCLERADAR_DATA_DIR=/opt/cycleradar-trader/data

/usr/bin/python3.9 scripts/scanner_signal_runner.py --date "$DATE_STR" >> "$LOG_FILE" 2>&1

EXIT_CODE=$?
if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ scanner_signals 完成 (exit $EXIT_CODE)" | tee -a "$LOG_FILE"
else
    echo "❌ scanner_signals 失败 (exit $EXIT_CODE)，见: $LOG_FILE" | tee -a "$LOG_FILE"
fi

exit $EXIT_CODE
