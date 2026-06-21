#!/bin/bash
# 交易员日报 + 自选池策略 - 每日盘前自动运行
set -e

DATE=$(date +%Y-%m-%d)
TRADER_DIR="/Users/scott/交易员"
LOG_DIR="$TRADER_DIR/logs"
SERVER="root@139.196.115.64"

mkdir -p "$LOG_DIR"

exec >> "$LOG_DIR/cron_$DATE.log" 2>&1

echo "=== $(date '+%Y-%m-%d %H:%M:%S') 开始 ==="

# 1. 运行 trader.py（日报 + 策略）
cd "$TRADER_DIR"
/usr/bin/python3 trader.py --date "$DATE" --strategy
echo "[trader] 日报 + 策略输出完成 (exit=$?)"

# 2. 部署到服务器
echo "[deploy] 部署日报..."
scp -o ConnectTimeout=10 "$TRADER_DIR/output/daily/latest.html" "$SERVER:/opt/trader/output/daily/" 
scp -o ConnectTimeout=10 "$TRADER_DIR/output/daily/trader_$DATE.html" "$SERVER:/opt/trader/output/daily/"

echo "[deploy] 部署策略..."
scp -o ConnectTimeout=10 "$TRADER_DIR/output/strategy/latest.html" "$SERVER:/opt/trader/output/strategy/"
scp -o ConnectTimeout=10 "$TRADER_DIR/output/strategy/strategy_$DATE.html" "$SERVER:/opt/trader/output/strategy/"

# 3. 信号跟踪反思
echo "[tracker] 运行信号跟踪..."
/usr/bin/python3 "$TRADER_DIR/modules/tracker.py" track --date "$DATE"
echo "[tracker] 信号跟踪完成 (exit=$?)"

echo "[deploy] 部署跟踪..."
ssh "$SERVER" "mkdir -p /opt/trader/output/contracts" 2>/dev/null || true
scp -o ConnectTimeout=10 "$TRADER_DIR/output/tracker/latest.html" "$SERVER:/opt/trader/output/tracker/"
scp -o ConnectTimeout=10 "$TRADER_DIR/output/tracker/tracker_$DATE.html" "$SERVER:/opt/trader/output/tracker/"

# 4. 同步契约文件到 ECS（修复 upstream_signals 断流根因）
echo "[sync] 同步契约文件到 ECS..."
scp -o ConnectTimeout=10 "$TRADER_DIR/data/alpha_latest.json"           "$SERVER:/opt/trader/output/contracts/" 2>/dev/null || echo "[sync] WARN: alpha_latest.json 同步失败"
scp -o ConnectTimeout=10 "$TRADER_DIR/data/event_narrative_latest.json" "$SERVER:/opt/trader/output/contracts/" 2>/dev/null || echo "[sync] WARN: event_narrative_latest.json 同步失败"
scp -o ConnectTimeout=10 "$TRADER_DIR/data/upstream_signals.jsonl"      "$SERVER:/opt/trader/output/contracts/" 2>/dev/null || echo "[sync] WARN: upstream_signals.jsonl 同步失败"
scp -o ConnectTimeout=10 "$TRADER_DIR/data/timing_history.json"         "$SERVER:/opt/trader/output/contracts/" 2>/dev/null || echo "[sync] WARN: timing_history.json 同步失败"
echo "[sync] 契约文件同步完成"

# 5. 自选池信号缓存 + 同步
echo "[watchlist] 计算自选池信号..."
/usr/bin/python3 "$TRADER_DIR/scripts/update_watchlist_signals.py"
echo "[watchlist] 自选池信号完成 (exit=$?)"
echo "[sync] 同步 watchlist_signals.json..."
scp -o ConnectTimeout=10 "$TRADER_DIR/data/watchlist_signals.json" "$SERVER:/opt/trader/output/contracts/" 2>/dev/null || echo "[sync] WARN: watchlist_signals.json 同步失败"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') 完成 ==="
