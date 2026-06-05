#!/bin/bash
# 交易员日报 + 自选池策略 - 每日盘前自动运行
# v2: rsync → git push gh-pages，产物纳入版本管理
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

# 2. 信号跟踪反思
echo "[tracker] 运行信号跟踪..."
/usr/bin/python3 "$TRADER_DIR/modules/tracker.py" track --date "$DATE"
echo "[tracker] 信号跟踪完成 (exit=$?)"

# 3. 提交产物到 gh-pages 并推送
echo "[git] 更新 gh-pages 分支..."
git checkout gh-pages
cp -r output/* .
git add daily/ strategy/ tracker/
git commit -m "auto: trader $DATE" || echo "[git] 无变更，跳过"
git push origin gh-pages
git checkout main
echo "[git] gh-pages 推送完成"

# 4. 服务器拉取最新
echo "[server] 拉取服务器最新产物..."
ssh "$SERVER" "cd /opt/trader/output && git pull origin gh-pages" 2>&1
echo "[server] 服务器同步完成"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') 完成 ==="
