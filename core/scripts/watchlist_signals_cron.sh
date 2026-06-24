#!/bin/bash
# watchlist_signals_cron.sh — 每日计算自选池信号缓存
# 产出 watchlist_signals.json → contracts 目录（mobile.js 消费）+ cycleradar data（备份）
# V6.0 新建

set -euo pipefail

# ── 载入密钥 ──────────────────────────────
if [ -f /opt/cycleradar-trader/.env ]; then
  set -a
  source /opt/cycleradar-trader/.env
  set +a
fi

DATE_STR="$(date +%Y-%m-%d)"
TRADER_DIR="/opt/trader/output"
CONTRACTS_DIR="/opt/trader/output/contracts"
CYCLERADAR_DATA="/opt/cycleradar-trader/data"
LOG_DIR="$CYCLERADAR_DATA/logs"
mkdir -p "$LOG_DIR" "$CONTRACTS_DIR"

LOG_FILE="$LOG_DIR/watchlist_signals_$(date +%Y%m%d-%H%M%S).log"

echo "======================================================" | tee -a "$LOG_FILE"
echo "  watchlist_signals cron — $DATE_STR" | tee -a "$LOG_FILE"
echo "======================================================" | tee -a "$LOG_FILE"

# ── 阶段 1: 运行 trader 侧的 watchlist 信号脚本 ──
echo "[1/2] 运行 update_watchlist_signals.py ..." | tee -a "$LOG_FILE"
cd "$TRADER_DIR"

/usr/bin/python3.9 scripts/update_watchlist_signals.py >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "❌ update_watchlist_signals 失败 (exit $EXIT_CODE)，见: $LOG_FILE" | tee -a "$LOG_FILE"
    exit $EXIT_CODE
fi

echo "✅ 脚本完成" | tee -a "$LOG_FILE"

# ── 阶段 2: 部署到 contracts（mobile.js 消费） + cycleradar data（备份）──
echo "[2/2] 部署 watchlist_signals.json ..." | tee -a "$LOG_FILE"

SRC="$TRADER_DIR/data/watchlist_signals.json"
if [ ! -f "$SRC" ]; then
    echo "❌ 输出文件不存在: $SRC" | tee -a "$LOG_FILE"
    exit 1
fi

cp "$SRC" "$CONTRACTS_DIR/watchlist_signals.json"
cp "$SRC" "$CYCLERADAR_DATA/watchlist_signals.json"
echo "✅ 部署完成: $CONTRACTS_DIR/watchlist_signals.json" | tee -a "$LOG_FILE"
echo "✅ 备份: $CYCLERADAR_DATA/watchlist_signals.json" | tee -a "$LOG_FILE"

exit 0
