#!/bin/bash
# 交易员 · 早间择时 — 仅市场温度计 + 契约同步
set -e

DATE=$(date +%Y-%m-%d)
TRADER_DIR="/Users/scott/交易员"
LOG_DIR="$TRADER_DIR/logs"
SERVER="root@139.196.115.64"

mkdir -p "$LOG_DIR"

exec >> "$LOG_DIR/cron_morning_$DATE.log" 2>&1

echo "=== $(date '+%Y-%m-%d %H:%M:%S') 早间择时开始 ==="

# 1. 仅运行择时（市场温度计）
cd "$TRADER_DIR"
/usr/bin/python3 trader.py --date "$DATE" --timing-only
echo "[timing] 择时输出完成 (exit=$?)"

# 2. 同步 timing 契约文件到 ECS
echo "[sync] 同步 timing 到 ECS..."
ssh "$SERVER" "mkdir -p /opt/trader/output/contracts" 2>/dev/null || true
scp -o ConnectTimeout=10 "$TRADER_DIR/data/timing_history.json" "$SERVER:/opt/trader/output/contracts/" 2>/dev/null || echo "[sync] WARN: timing_history.json 同步失败"
echo "[sync] 完成"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') 早间择时完成 ==="
