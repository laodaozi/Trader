#!/bin/bash
# 交易员日报 + 自选池策略 - 每日盘前自动运行
# v3: scheduler heartbeat + contracts sync (3-bridge)
set -e

DATE=$(date +%Y-%m-%d)
TRADER_DIR="/Users/scott/交易员"
LOG_DIR="$TRADER_DIR/logs"
SERVER="root@139.196.115.64"
SCHEDULER_URL="http://139.196.115.64/admin/api/scheduler/heartbeat"
SCHEDULER_TOKEN="cycleradar-scheduler"

_heartbeat() {
  curl -s -X POST "$SCHEDULER_URL" \
    -H "Content-Type: application/json" \
    -H "X-Scheduler-Token: $SCHEDULER_TOKEN" \
    -d "{\"stage\":\"$1\",\"status\":\"$2\",\"exit_code\":$3}" \
    > /dev/null 2>&1 || true
}

mkdir -p "$LOG_DIR"
exec >> "$LOG_DIR/cron_$DATE.log" 2>&1

echo "=== $(date '+%Y-%m-%d %H:%M:%S') 开始 ==="

# 1. 运行 trader.py（日报 + 策略）
cd "$TRADER_DIR"
/usr/bin/python3 trader.py --date "$DATE" --strategy
TRADER_EXIT=$?
echo "[trader] 日报 + 策略输出完成 (exit=$TRADER_EXIT)"
_heartbeat trader "$([ $TRADER_EXIT -eq 0 ] && echo done || echo failed)" $TRADER_EXIT

# 2. 信号跟踪反思
echo "[tracker] 运行信号跟踪..."
/usr/bin/python3 "$TRADER_DIR/modules/tracker.py" track --date "$DATE"
TRACKER_EXIT=$?
echo "[tracker] 信号跟踪完成 (exit=$TRACKER_EXIT)"
_heartbeat tracker "$([ $TRACKER_EXIT -eq 0 ] && echo done || echo failed)" $TRACKER_EXIT

# 2.5 同步契约文件到 gh-pages（3 文件桥：alpha_latest + event_narrative + upstream_signals）
echo "[contracts] 同步契约文件..."
git checkout gh-pages
mkdir -p contracts
for f in alpha_latest.json event_narrative_latest.json upstream_signals.jsonl; do
  if [ -f "data/$f" ]; then
    cp "data/$f" "contracts/"
    echo "  ✓ contracts/$f"
  else
    echo "  ✗ data/$f 不存在，跳过"
  fi
done

# 3. 提交产物到 gh-pages 并推送
echo "[git] 更新 gh-pages 分支..."
cp -r output/* .
git add daily/ strategy/ tracker/ contracts/
git commit -m "auto: trader $DATE" || echo "[git] 无变更，跳过"
git push origin gh-pages
GIT_EXIT=$?
git checkout main
echo "[git] gh-pages 推送完成 (exit=$GIT_EXIT)"
_heartbeat git_push "$([ $GIT_EXIT -eq 0 ] && echo done || echo failed)" $GIT_EXIT

# 4. 服务器拉取最新
echo "[server] 拉取服务器最新产物..."
ssh "$SERVER" "cd /opt/trader/output && git pull origin gh-pages" 2>&1
SYNC_EXIT=$?
echo "[server] 服务器同步完成 (exit=$SYNC_EXIT)"
_heartbeat server_sync "$([ $SYNC_EXIT -eq 0 ] && echo done || echo failed)" $SYNC_EXIT

echo "=== $(date '+%Y-%m-%d %H:%M:%S') 完成 ==="
