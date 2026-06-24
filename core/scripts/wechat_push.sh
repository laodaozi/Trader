#!/bin/bash
# wechat_push.sh — Server酱微信推送
# Usage: wechat_push.sh <title> <message> [level]
# level: info (default) | warn | error（仅影响标题前缀图标）

set -euo pipefail

TITLE="${1:-}"
MESSAGE="${2:-}"
LEVEL="${3:-info}"

# ---- 配置 ----
CONFIG_FILE="$(dirname "$0")/watchdog.conf"
if [ -f "$CONFIG_FILE" ]; then
    source "$CONFIG_FILE"
fi

SCT_KEY="${SCT_KEY:-}"
LOG_DIR="${WATCHDOG_LOG_DIR:-/opt/cycleradar-trader/data/logs}"
LOG_FILE="$LOG_DIR/wechat_push.log"

# ---- 前置检查 ----
if [ -z "$SCT_KEY" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] SKIP: SCT_KEY 未配置" >> "$LOG_FILE"
    exit 0
fi

# ---- level → 图标 ----
case "$LEVEL" in
    error)  ICON="🚨" ;;
    warn)   ICON="⚠️"  ;;
    *)      ICON="ℹ️"  ;;
esac

# ---- 发送 ----
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
HOSTNAME=$(hostname)
FULL_TITLE="${ICON} ${TITLE}"
FULL_MSG="${MESSAGE}\n\n时间: ${TIMESTAMP} | 主机: ${HOSTNAME}"

# URL-encode
ENCODED_TITLE=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$FULL_TITLE")
ENCODED_MSG=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$FULL_MSG")

HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' \
    "https://sctapi.ftqq.com/${SCT_KEY}.send?title=${ENCODED_TITLE}&desp=${ENCODED_MSG}" \
    --connect-timeout 10 --max-time 10 2>&1)

if [ "$HTTP_CODE" = "200" ]; then
    echo "[$TIMESTAMP] OK  ${FULL_TITLE}" >> "$LOG_FILE"
else
    echo "[$TIMESTAMP] FAIL HTTP=${HTTP_CODE} ${FULL_TITLE}" >> "$LOG_FILE"
    exit 1
fi
