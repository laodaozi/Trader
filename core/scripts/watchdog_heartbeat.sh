#!/bin/bash
# watchdog_heartbeat.sh — HTTP 端点健康检查
# cron PATH fix
export PATH="/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"
#   每 5 分钟由 cron 触发
#   检测 /m 和 /admin/health 是否正常响应

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_FILE="${WATCHDOG_STATE_DIR:-/opt/cycleradar-trader/data/watchdog}/heartbeat_state.json"
ENDPOINTS=(
    "http://localhost:3100/m"
    "http://localhost:3100/admin/health"
)
TIMEOUT=10
FAIL_THRESHOLD=3  # 连续失败 N 次才告警

mkdir -p "$(dirname "$STATE_FILE")"

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
ALL_OK=true
FAILURE_LIST=""

# ---- 逐端点检测 ----
for URL in "${ENDPOINTS[@]}"; do
    HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' \
        --connect-timeout "$TIMEOUT" \
        --max-time "$TIMEOUT" \
        "$URL" 2>&1 || echo "000")

    if [ "$HTTP_CODE" != "200" ]; then
        ALL_OK=false
        FAILURE_LIST="${FAILURE_LIST}- ${URL} → HTTP ${HTTP_CODE}\n"
    fi
done

# ---- 读取上次连续失败次数 ----
CONSECUTIVE_FAILS=0
if [ -f "$STATE_FILE" ]; then
    CONSECUTIVE_FAILS=$(grep -oP "\"consecutive_fails\":\K\d+" "$STATE_FILE" 2>/dev/null || echo "0")
fi

# ---- 更新状态 ----
if $ALL_OK; then
    CONSECUTIVE_FAILS=0
else
    CONSECUTIVE_FAILS=$((CONSECUTIVE_FAILS + 1))
fi

cat > "$STATE_FILE" <<STATE_END
{
  "last_check": "$TIMESTAMP",
  "all_ok": $ALL_OK,
  "consecutive_fails": $CONSECUTIVE_FAILS,
  "failures": "$(echo -e "$FAILURE_LIST" | tr '\n' ' ')"
}
STATE_END

# ---- 达到阈值时告警 ----
if ! $ALL_OK && [ "$CONSECUTIVE_FAILS" -ge "$FAIL_THRESHOLD" ]; then
    bash "$SCRIPT_DIR/wechat_push.sh" \
        "trader-admin HTTP 不可达 (连续${CONSECUTIVE_FAILS}次)" \
        "**trader-admin** HTTP 端点持续无响应\n\n${FAILURE_LIST}\n> 连续失败: ${CONSECUTIVE_FAILS} 次（阈值 ${FAIL_THRESHOLD}）" \
        "error"
elif ! $ALL_OK; then
    # 早期告警用 warn
    bash "$SCRIPT_DIR/wechat_push.sh" \
        "trader-admin HTTP 异常 (${CONSECUTIVE_FAILS}/${FAIL_THRESHOLD})" \
        "**trader-admin** HTTP 端点失败\n\n${FAILURE_LIST}\n> 连续失败: ${CONSECUTIVE_FAILS}/${FAIL_THRESHOLD}（未达告警阈值）" \
        "warn" 2>/dev/null || true  # 早期不阻塞
fi

echo "[$TIMESTAMP] $( $ALL_OK && echo 'OK' || echo "FAIL($CONSECUTIVE_FAILS)" ) ${ENDPOINTS[*]}"
exit 0
