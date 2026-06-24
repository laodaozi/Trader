#!/usr/bin/env bash
# monitor_rss_health.sh — RSS 数据管道健康监控（L1 监控层 + L2 告警层）
#
# 监控维度：
#   1. PM2 进程存活（wewe-rss）
#   2. DB 最后写入时间（articles 表整体新鲜度）
#   3. 逐 feed 新鲜度（每个公众号最后文章时间）
#   4. 错误日志异常（最近 1h 401 频率）
#
# 告警等级：
#   CRITICAL — 全部 feed 断流 >24h 或 PM2 挂了
#   WARN     — 任一 feed 断流 >6h 或部分 feed 断流
#   INFO     — 健康报告（每日摘要）
#
# 去重：同一告警条件 2h 内不重复（状态文件）
# 通道：stdout（cron 捕获）+ 日志文件 + DINGTALK_WEBHOOK（可选）
#
# 用法：
#   cron 每 30 分钟： 30 * * * * /opt/cycleradar-trader/core/scripts/monitor_rss_health.sh
#   手动触发：         bash monitor_rss_health.sh --force
#   日报模式：         bash monitor_rss_health.sh --daily
#
set -euo pipefail

# ── 配置 ──
DB_PATH="/opt/cycleradar-trader/admin/data/wewe-rss.db"
PM2_NAME="wewe-rss"
LOG_DIR="/opt/cycleradar-trader/data/logs"
STATE_FILE="$LOG_DIR/rss_health_state"
HEALTH_LOG="$LOG_DIR/rss_health.log"
PM2_ERROR_LOG="/root/.pm2/logs/wewe-rss-error.log"

# 阈值（小时）
WARN_THRESHOLD=6
CRITICAL_THRESHOLD=24
DEDUP_WINDOW=120  # 去重窗口（分钟）：同一告警不重复
ALERT_COUNT_THRESHOLD=10  # 最近 1h 内 401 错误数量阈值

FORCE=false
DAILY=false
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=true ;;
    --daily) DAILY=true ;;
  esac
done

mkdir -p "$LOG_DIR"
touch "$STATE_FILE"

# ── 工具函数 ──
NOW=$(date '+%Y-%m-%d %H:%M:%S')
NOW_TS=$(date +%s)

log() { echo "[$NOW] $*" >> "$HEALTH_LOG"; }

alert() {
  local level="$1"  # CRITICAL | WARN | INFO
  local key="$2"    # 去重键（如 "all_stale:24h"）
  local msg="$3"

  # 去重检查
  if ! $FORCE && [ "$level" != "INFO" ]; then
    local last
    last=$(grep "^${key}:" "$STATE_FILE" 2>/dev/null | tail -1 | cut -d':' -f2 || echo "0")
    local diff=$(( NOW_TS - last ))
    if [ "$diff" -lt $(( DEDUP_WINDOW * 60 )) ]; then
      log "[DEDUP] $level | $key | 距上次告警 ${diff}s，跳过"
      return
    fi
  fi

  # 写入状态
  if [ "$level" != "INFO" ]; then
    grep -v "^${key}:" "$STATE_FILE" > "${STATE_FILE}.tmp" 2>/dev/null || true
    echo "${key}:${NOW_TS}" >> "${STATE_FILE}.tmp"
    mv "${STATE_FILE}.tmp" "$STATE_FILE"
  fi

  # 日志
  local prefix
  case "$level" in
    CRITICAL) prefix="🚨" ;;
    WARN)     prefix="⚠️ " ;;
    INFO)     prefix="✅" ;;
    *)        prefix="📋" ;;
  esac
  log "$prefix [$level] $msg"

  # stdout（cron 会邮件发送）
  echo "$prefix [RSS-Health][$level] $NOW — $msg"

  # DingTalk webhook（如果配置了）
  if [ -n "${DINGTALK_WEBHOOK:-}" ]; then
    local dt_msg="[RSS-Health][$level] $NOW\\n$msg"
    curl -s -X POST "$DINGTALK_WEBHOOK" \
      -H "Content-Type: application/json" \
      -d "{\"msgtype\":\"text\",\"text\":{\"content\":\"$dt_msg\"}}" > /dev/null 2>&1 || true
  fi

  # 清理过期状态（保留最近 24h 的）
  awk -F':' -v cutoff=$((NOW_TS - 86400)) '$2 >= cutoff' "$STATE_FILE" > "${STATE_FILE}.tmp" 2>/dev/null || true
  mv "${STATE_FILE}.tmp" "$STATE_FILE"
}

# ── 检查 1: PM2 进程存活 ──
check_pm2() {
  if command -v pm2 &>/dev/null; then
    if pm2 list 2>/dev/null | grep -q "$PM2_NAME.*online"; then
      log "✅ PM2: wewe-rss online"
      return 0
    else
      local status
      status=$(pm2 list 2>/dev/null | grep "$PM2_NAME" || echo "NOT FOUND")
      alert "CRITICAL" "pm2_down" "wewe-rss PM2 进程异常: $status"
      return 1
    fi
  else
    log "⚠️  PM2 not found on this host"
    return 2
  fi
}

# ── 检查 2: DB 整体新鲜度（最后文章时间） ──
check_db_freshness() {
  local last_ts
  last_ts=$(sqlite3 "$DB_PATH" "SELECT COALESCE(MAX(publish_time), 0) FROM articles;" 2>/dev/null || echo "0")

  if [ "$last_ts" = "0" ] || [ -z "$last_ts" ]; then
    alert "CRITICAL" "db_empty" "DB 中无文章数据 — 可能从未拉取或 DB 损坏"
    return 1
  fi

  local age_hours=$(( (NOW_TS - last_ts) / 3600 ))
  local last_time
  last_time=$(date -d "@$last_ts" '+%Y-%m-%d %H:%M' 2>/dev/null || echo "未知")

  if [ "$age_hours" -ge "$CRITICAL_THRESHOLD" ]; then
    alert "CRITICAL" "db_all_stale:${age_hours}h" "所有 feed 最新文章距今 ${age_hours}h（$last_time），断流超过 ${CRITICAL_THRESHOLD}h"
  elif [ "$age_hours" -ge "$WARN_THRESHOLD" ]; then
    alert "WARN" "db_stale:${age_hours}h" "整体最新文章距今 ${age_hours}h（$last_time），关注是否断流"
  else
    log "✅ DB 最新文章: ${age_hours}h 前（$last_time）"
  fi

  echo "$age_hours"
}

# ── 检查 3: 逐 feed 新鲜度 ──
check_per_feed() {
  sqlite3 "$DB_PATH" "SELECT f.mp_name, COALESCE(MAX(a.publish_time), 0) FROM feeds f LEFT JOIN articles a ON a.mp_id = f.id GROUP BY f.id;" 2>/dev/null | \
  while IFS='|' read -r name last_ts; do
    if [ "$last_ts" = "0" ] || [ -z "$last_ts" ]; then
      log "⚠️  $name: 无文章（可能从未拉取）"
      continue
    fi
    local age=$(( (NOW_TS - last_ts) / 3600 ))
    local ltime
    ltime=$(date -d "@$last_ts" '+%m-%d %H:%M' 2>/dev/null || echo "?")
    if [ "$age" -ge "$CRITICAL_THRESHOLD" ]; then
      alert "WARN" "feed_${name}:${age}h" "[$name] 断流 ${age}h（最后: $ltime）"
    elif [ "$age" -ge "$WARN_THRESHOLD" ]; then
      log "⚠️  [$name] ${age}h 未更新（最后: $ltime）"
    else
      log "   [$name] ${age}h 前更新"
    fi
  done
}

# ── 检查 4: 401 错误频率 ──
check_401_rate() {
  if [ ! -f "$PM2_ERROR_LOG" ]; then
    log "   PM2 error log 不存在，跳过 401 检查"
    return
  fi

  local one_hour_ago
  one_hour_ago=$(date -d '1 hour ago' '+%Y-%m-%d %H:%M' 2>/dev/null || date -v-1H '+%Y-%m-%d %H:%M' 2>/dev/null || echo "")
  if [ -z "$one_hour_ago" ]; then
    return
  fi

  local count
  count=$(grep -c "暂无可用读书账号" "$PM2_ERROR_LOG" 2>/dev/null || echo "0")

  if [ "$count" -ge "$ALERT_COUNT_THRESHOLD" ]; then
    alert "CRITICAL" "token_401:${count}" "微信读书账号 token 过期！最近 1h 内 401 错误 ${count} 次 — 需要手动重新扫码"
  elif [ "$count" -gt 0 ]; then
    log "⚠️  最近 1h 内 401 错误 ${count} 次（阈值: $ALERT_COUNT_THRESHOLD）"
  fi
}

# ── 日报模式（--daily） ──
print_daily_summary() {
  echo ""
  echo "━━━━━━ RSS 健康日报 $(date '+%Y-%m-%d %H:%M') ━━━━━━"
  echo ""

  # 整体统计
  local total
  total=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM articles;" 2>/dev/null || echo "?")
  local last_time
  local last_ts
  last_ts=$(sqlite3 "$DB_PATH" "SELECT COALESCE(MAX(publish_time), 0) FROM articles;" 2>/dev/null || echo "0")
  if [ "$last_ts" != "0" ]; then
    last_time=$(date -d "@$last_ts" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "未知")
  else
    last_time="无数据"
  fi

  local db_age=0
  if [ "$last_ts" != "0" ]; then
    db_age=$(( (NOW_TS - last_ts) / 3600 ))
  fi

  echo "📊 总体: $total 篇文章 | 最新: $last_time (${db_age}h 前)"

  # PM2 状态
  local pm2_status="未知"
  if command -v pm2 &>/dev/null; then
    pm2_status=$(pm2 list 2>/dev/null | grep "$PM2_NAME" | awk '{print $4}' || echo "NOT FOUND")
  fi
  echo "🔧 PM2: wewe-rss — $pm2_status"

  # 账号状态
  local acc_status
  acc_status=$(sqlite3 "$DB_PATH" "SELECT id || ' (' || name || ') status=' || status FROM accounts LIMIT 1;" 2>/dev/null || echo "无账号")
  echo "👤 账号: $acc_status"

  # 401 频率
  local err_count
  err_count=$(grep -c "暂无可用读书账号" "$PM2_ERROR_LOG" 2>/dev/null || echo "0")
  if [ "$err_count" -gt 0 ]; then
    echo "⚠️  最近 401 错误: ${err_count} 次（需关注 token 状态）"
  fi

  echo ""
  echo "── 逐 feed 状态 ──"
  echo ""

  sqlite3 "$DB_PATH" "SELECT f.mp_name, COALESCE(MAX(a.publish_time), 0), COUNT(a.id) FROM feeds f LEFT JOIN articles a ON a.mp_id = f.id GROUP BY f.id ORDER BY f.mp_name;" 2>/dev/null | \
  while IFS='|' read -r name last_ts cnt; do
    if [ "$last_ts" = "0" ] || [ -z "$last_ts" ]; then
      printf "  %-16s %4s篇  无文章\n" "$name" "$cnt"
    else
      local age=$(( (NOW_TS - last_ts) / 3600 ))
      local ltime
      ltime=$(date -d "@$last_ts" '+%m-%d %H:%M' 2>/dev/null || echo "?")
      local icon="✅"
      if [ "$age" -ge "$CRITICAL_THRESHOLD" ]; then icon="🚨"; elif [ "$age" -ge "$WARN_THRESHOLD" ]; then icon="⚠️ "; fi
      printf "  $icon %-14s %4s篇  %s (%dh)\n" "$name" "$cnt" "$ltime" "$age"
    fi
  done

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""

  alert "INFO" "daily_summary" "日报: $total 篇, 最新 ${db_age}h 前, PM2=$pm2_status, 401错误=${err_count}次"

  # 记录到专用日报日志
  echo "[$NOW] total=$total last_ts=$last_ts age=${db_age}h pm2=$pm2_status 401=${err_count} feeds=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM feeds;" 2>/dev/null || echo "?")" >> "$LOG_DIR/rss_daily.log"
}

# ── 主流程 ──
main() {
  echo ""
  echo "━━━ RSS Health Monitor ━━━"
  echo "Time: $NOW"
  echo ""

  if $DAILY; then
    print_daily_summary
    exit 0
  fi

  # 检查 1: PM2
  check_pm2

  # 检查 2: DB 整体新鲜度
  local db_age
  db_age=$(check_db_freshness)

  # 检查 3: 逐 feed
  check_per_feed

  # 检查 4: 401 错误频率
  check_401_rate

  # 汇总
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  RSS Health Check Complete"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
}

main
