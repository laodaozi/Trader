#!/bin/bash
# ============================================================
# cron PATH fix
export PATH="/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"
# watchdog_pm2.sh - PM2 Process Watchdog for trader-admin
# Monitors: process status, restart count, memory usage
# Alerts:   restart increase → warn, process down → error
# Uses:     Node.js for reliable PM2 jlist JSON parsing
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_FILE="/opt/cycleradar-trader/data/watchdog/pm2_state.json"
CONF_FILE="${SCRIPT_DIR}/watchdog.conf"

# --- Load config ---
if [ -f "${CONF_FILE}" ]; then
  source "${CONF_FILE}"
fi

# --- Send DingTalk notification ---
send_wechat() {
  local title="$1" msg="$2"
  if [ -z "${SCT_KEY:-}" ]; then
    echo "[wechat_push] webhook not configured, skipping: ${title}" >&2
    return 0
  fi
  "${SCRIPT_DIR}/wechat_push.sh" "${title}" "${msg}"
}

# --- Fetch current state from PM2 ---
fetch_state() {
  local json
  json=$(pm2 jlist 2>/dev/null | node -e "
    const d = JSON.parse(require('fs').readFileSync('/dev/stdin', 'utf8'));
    const p = d.find(x => x.name === 'trader-admin');
    if (!p) {
      console.log(JSON.stringify({status:'not_found'}));
    } else {
      console.log(JSON.stringify({
        status: p.pm2_env.status,
        restarts: p.pm2_env.restart_time,
        pid: p.pid,
        memory_mb: +(p.monit.memory / 1048576).toFixed(1)
      }));
    }
  " 2>/dev/null || true)

  if [ -z "$json" ]; then
    # PM2 daemon may be down entirely
    echo '{"status":"unknown"}'
  else
    echo "$json"
  fi
}

# --- Load previous state ---
load_prev() {
  if [ -f "${STATE_FILE}" ]; then
    node -e "
      try {
        const s = JSON.parse(require('fs').readFileSync('${STATE_FILE}', 'utf8'));
        console.log(JSON.stringify(s));
      } catch(e) {
        console.log('{}');
      }
    " 2>/dev/null || echo '{}'
  else
    echo '{}'
  fi
}

# --- Write current state ---
write_state() {
  echo "$1" > "${STATE_FILE}"
  echo "[watchdog_pm2] state saved: restarts=$(echo "$1" | node -e "const s=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8')); console.log(s.restarts||'?')") 2>/dev/null"
}

# ============================================================
#  MAIN
# ============================================================

CUR=$(fetch_state)
PREV=$(load_prev)
CUR_STATUS=$(echo "$CUR" | node -e "const s=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8')); console.log(s.status||'')" 2>/dev/null || true)
CUR_RESTARTS=$(echo "$CUR" | node -e "const s=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8')); console.log(s.restarts||0)" 2>/dev/null || echo 0)
CUR_MEM=$(echo "$CUR" | node -e "const s=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8')); console.log(s.memory_mb||0)" 2>/dev/null || echo 0)
PREV_RESTARTS=$(echo "$PREV" | node -e "const s=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8')); console.log(s.restarts||0)" 2>/dev/null || echo 0)
PREV_STATUS=$(echo "$PREV" | node -e "const s=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8')); console.log(s.status||'')" 2>/dev/null || true)

# --- Detect conditions ---
NOW=$(date '+%H:%M:%S')
ALERTS=""

# 1) Process not found / unknown
if [ "$CUR_STATUS" = "not_found" ]; then
  ALERTS+="\n- **[ERROR]** trader-admin process not found in PM2 table (${NOW})"
  send_wechat "🔴 trader-admin DOWN: process not found" "PM2 jlist does not contain trader-admin | ${NOW}"
elif [ "$CUR_STATUS" = "unknown" ]; then
  ALERTS+="\n- **[ERROR]** PM2 daemon unreachable (${NOW})"
  send_wechat "🔴 PM2 daemon DOWN" "pm2 jlist returned nothing | ${NOW}"
# 2) Process stopped/crashed (was online, now offline)
elif [ "$CUR_STATUS" != "online" ]; then
  if [ "$PREV_STATUS" = "online" ] || [ "$PREV_STATUS" = "" ]; then
    ALERTS+="\n- **[ERROR]** trader-admin status: ${CUR_STATUS} (was: ${PREV_STATUS:-unknown}) (${NOW})"
    send_wechat "🔴 trader-admin DOWN: status=${CUR_STATUS}" "Status changed from ${PREV_STATUS:-unknown} to ${CUR_STATUS} | ${NOW}"
  fi
fi

# 3) Restart count increased (process crashed and restarted)
if [ "$CUR_RESTARTS" -gt "$PREV_RESTARTS" ] 2>/dev/null && [ "$PREV_RESTARTS" -gt 0 ] 2>/dev/null; then
  DELTA=$((CUR_RESTARTS - PREV_RESTARTS))
  ALERTS+="\n- **[WARN]** restarts increased by ${DELTA}: ${PREV_RESTARTS} → ${CUR_RESTARTS} (${NOW})"
  send_wechat "⚠️ trader-admin restarted (×${DELTA})" "restart count: ${PREV_RESTARTS} → ${CUR_RESTARTS} | status=${CUR_STATUS} | mem=${CUR_MEM}MB | ${NOW}"
fi

# 4) Memory threshold (optional: warn if > 250MB)
if [ "$(echo "$CUR_MEM > 250" | bc -l 2>/dev/null || echo 0)" = "1" ]; then
  ALERTS+="\n- **[WARN]** memory high: ${CUR_MEM}MB (threshold 250MB) (${NOW})"
  send_wechat "⚠️ trader-admin high memory: ${CUR_MEM}MB" "status=${CUR_STATUS} | restarts=${CUR_RESTARTS} | ${NOW}"
fi

# --- Report ---
if [ -z "$ALERTS" ]; then
  echo "[watchdog_pm2] OK (${NOW}) | status=${CUR_STATUS} restarts=${CUR_RESTARTS} mem=${CUR_MEM}MB"
else
  echo -e "[watchdog_pm2] ALERTS:${ALERTS}" >&2
fi

# --- Save state ---
write_state "$CUR"
