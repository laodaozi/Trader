#!/usr/bin/env bash
# monitor_rss_health.sh — 数据源管道健康监控（source_articles.db，V7.7 起）
#
# 监控维度：
#   1. source_articles.db 文件存在性
#   2. 最新 created_at 新鲜度（与 mobile.js _getSourceArticlesHealth 对齐）
#   3. 近 24h 文章数量（0 条告警 CRITICAL）
#   4. LLM 产出管线新鲜度（V7.9 — reflection/narrative/alpha mtime >26h WARN）
#
# 告警等级：
#   CRITICAL — DB 不存在 / 近24h 0 条 / 断流 >24h
#   WARN     — 断流 6-24h
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
# V7.7: 原 WeWe RSS / wewe-rss.db / PM2 监控已废弃，改为监控 source_articles.db
#
set -euo pipefail

# ── 配置 ──
SOURCE_DB_PATH="/opt/cycleradar-trader/data/source_articles.db"
LOG_DIR="/opt/cycleradar-trader/data/logs"
STATE_FILE="$LOG_DIR/rss_health_state"
HEALTH_LOG="$LOG_DIR/rss_health.log"

# 阈值（小时）
WARN_THRESHOLD=6
CRITICAL_THRESHOLD=24
DEDUP_WINDOW=120  # 去重窗口（分钟）：同一告警不重复

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
  local key="$2"    # 去重键
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
  echo "$prefix [DataSource-Health][$level] $NOW — $msg"

  # DingTalk webhook（如果配置了）
  if [ -n "${DINGTALK_WEBHOOK:-}" ]; then
    local dt_msg="[DataSource-Health][$level] $NOW\\n$msg"
    curl -s -X POST "$DINGTALK_WEBHOOK" \
      -H "Content-Type: application/json" \
      -d "{\"msgtype\":\"text\",\"text\":{\"content\":\"$dt_msg\"}}" > /dev/null 2>&1 || true
  fi

  # 清理过期状态（保留最近 24h 的）
  awk -F':' -v cutoff=$((NOW_TS - 86400)) '$2 >= cutoff' "$STATE_FILE" > "${STATE_FILE}.tmp" 2>/dev/null || true
  mv "${STATE_FILE}.tmp" "$STATE_FILE"
}

# ── 检查 1: DB 文件存在性 ──
check_db_exists() {
  if [ ! -f "$SOURCE_DB_PATH" ]; then
    alert "CRITICAL" "db_missing" "source_articles.db 不存在: $SOURCE_DB_PATH — MCP ingest 未运行或路径错误"
    return 1
  fi
  log "✅ source_articles.db 存在: $SOURCE_DB_PATH"
  return 0
}

# ── 检查 2: 最新 created_at 新鲜度（与 mobile.js _getSourceArticlesHealth 逻辑对齐）──
check_db_freshness() {
  local last_ts_str
  last_ts_str=$(sqlite3 "$SOURCE_DB_PATH" \
    "SELECT MAX(created_at) FROM source_articles WHERE fetch_status='success';" \
    2>/dev/null || echo "")

  if [ -z "$last_ts_str" ] || [ "$last_ts_str" = "NULL" ]; then
    alert "CRITICAL" "db_empty" "source_articles 表无 fetch_status=success 的记录 — ingest 从未成功写入"
    echo "9999"
    return 1
  fi

  # 兼容 SQLite ISO8601 字符串（2026-07-24T10:00:00）
  local last_ts
  last_ts=$(date -d "$last_ts_str" +%s 2>/dev/null \
    || date -j -f "%Y-%m-%dT%H:%M:%S" "${last_ts_str%%.*}" +%s 2>/dev/null \
    || echo "0")

  if [ "$last_ts" = "0" ]; then
    log "⚠️  created_at 解析失败: $last_ts_str"
    echo "9999"
    return 1
  fi

  local age_hours=$(( (NOW_TS - last_ts) / 3600 ))
  local last_time
  last_time=$(date -d "@$last_ts" '+%Y-%m-%d %H:%M' 2>/dev/null \
    || date -r "$last_ts" '+%Y-%m-%d %H:%M' 2>/dev/null \
    || echo "$last_ts_str")

  if [ "$age_hours" -ge "$CRITICAL_THRESHOLD" ]; then
    alert "CRITICAL" "db_stale:${age_hours}h" "source_articles 断流 ${age_hours}h（最新: $last_time）— MCP ingest 可能挂了"
  elif [ "$age_hours" -ge "$WARN_THRESHOLD" ]; then
    alert "WARN" "db_degraded:${age_hours}h" "source_articles ${age_hours}h 未更新（最新: $last_time）"
  else
    echo "✅ source_articles 最新记录: ${age_hours}h 前（$last_time）"
  fi
}

# ── 检查 3: 近 24h 文章数量 ──
check_article_count() {
  local yesterday
  yesterday=$(date -d '1 day ago' '+%Y-%m-%d' 2>/dev/null \
    || date -v-1d '+%Y-%m-%d' 2>/dev/null \
    || echo "")

  if [ -z "$yesterday" ]; then
    log "   date 命令不支持，跳过近24h数量检查"
    return
  fi

  local count
  count=$(sqlite3 "$SOURCE_DB_PATH" \
    "SELECT COUNT(*) FROM source_articles WHERE fetch_status='success' AND publish_date >= '$yesterday';" \
    2>/dev/null || echo "0")

  if [ "$count" = "0" ]; then
    alert "CRITICAL" "no_recent_articles" "近 24h source_articles 0 条新文章 — enrich 无米下锅"
  elif [ "$count" -lt 5 ]; then
    alert "WARN" "low_article_count:${count}" "近 24h 仅 ${count} 条文章，数量偏低"
  else
    echo "✅ 近 24h 文章数: ${count} 条"
  fi
}

# ── 日报模式（--daily） ──
print_daily_summary() {
  echo ""
  echo "━━━━━━ 数据源健康日报 $(date '+%Y-%m-%d %H:%M') ━━━━━━"
  echo ""

  if [ ! -f "$SOURCE_DB_PATH" ]; then
    echo "🚨 source_articles.db 不存在！"
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    return
  fi

  # 总体统计
  local total
  total=$(sqlite3 "$SOURCE_DB_PATH" \
    "SELECT COUNT(*) FROM source_articles WHERE fetch_status='success';" \
    2>/dev/null || echo "?")

  local last_ts_str
  last_ts_str=$(sqlite3 "$SOURCE_DB_PATH" \
    "SELECT MAX(created_at) FROM source_articles WHERE fetch_status='success';" \
    2>/dev/null || echo "")

  local db_age=0
  local last_time="无数据"
  if [ -n "$last_ts_str" ] && [ "$last_ts_str" != "NULL" ]; then
    local last_ts
    last_ts=$(date -d "$last_ts_str" +%s 2>/dev/null \
      || date -j -f "%Y-%m-%dT%H:%M:%S" "${last_ts_str%%.*}" +%s 2>/dev/null \
      || echo "0")
    if [ "$last_ts" != "0" ]; then
      db_age=$(( (NOW_TS - last_ts) / 3600 ))
      last_time=$(date -d "@$last_ts" '+%Y-%m-%d %H:%M:%S' 2>/dev/null \
        || date -r "$last_ts" '+%Y-%m-%d %H:%M:%S' 2>/dev/null \
        || echo "$last_ts_str")
    fi
  fi

  # 近24h数量
  local yesterday
  yesterday=$(date -d '1 day ago' '+%Y-%m-%d' 2>/dev/null \
    || date -v-1d '+%Y-%m-%d' 2>/dev/null || echo "")
  local recent_count="?"
  if [ -n "$yesterday" ]; then
    recent_count=$(sqlite3 "$SOURCE_DB_PATH" \
      "SELECT COUNT(*) FROM source_articles WHERE fetch_status='success' AND publish_date >= '$yesterday';" \
      2>/dev/null || echo "?")
  fi

  echo "📊 总体: $total 篇（fetch_status=success）| 最新: $last_time (${db_age}h 前)"
  echo "📅 近 24h 新增: ${recent_count} 篇"
  echo "📁 DB 路径: $SOURCE_DB_PATH"
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""

  alert "INFO" "daily_summary" "日报: total=$total recent24h=${recent_count} age=${db_age}h"

  echo "[$NOW] total=$total recent24h=${recent_count} age=${db_age}h db=$SOURCE_DB_PATH" >> "$LOG_DIR/rss_daily.log"
}

# ── 检查 4: LLM 产出管线新鲜度（V7.9 — 反思/日报 cron 静默停摆检测）──
# 教训：strategy_reflection 两次静默停摆（schema 漂移 / ThinkingBlock / 冻结路径），
#       monitor 只盯数据源不盯 LLM 产出 → 陷阱记了没设防。此检查补齐。
LLM_STALE_THRESHOLD=26  # 小时：反思每日 8:30 跑，>26h 未刷新即异常
check_llm_outputs() {
  local data_dir="/opt/cycleradar-trader/data"
  # 目标：文件 → 期望刷新周期描述
  local -a files=("strategy_reflection.json" "event_narrative_latest.json" "alpha_latest.json")
  for f in "${files[@]}"; do
    local path="$data_dir/$f"
    if [ ! -f "$path" ]; then
      alert "CRITICAL" "llm_missing:$f" "$f 不存在 — LLM 产出管线从未成功"
      continue
    fi
    local mtime age_h
    mtime=$(stat -c %Y "$path" 2>/dev/null || stat -f %m "$path" 2>/dev/null || echo 0)
    age_h=$(( (NOW_TS - mtime) / 3600 ))
    if [ "$age_h" -ge "$LLM_STALE_THRESHOLD" ]; then
      alert "WARN" "llm_stale:$f:${age_h}h" "$f 已 ${age_h}h 未刷新 — 对应 cron 可能静默崩溃（查 log）"
    else
      echo "✅ $f: ${age_h}h 前刷新"
    fi
  done
}

# ── 主流程 ──
main() {
  echo ""
  echo "━━━ DataSource Health Monitor (source_articles.db) ━━━"
  echo "Time: $NOW"
  echo ""

  if $DAILY; then
    print_daily_summary
    exit 0
  fi

  # 检查 1: DB 文件存在
  check_db_exists || exit 1

  # 检查 2: 新鲜度（直接调用，不用命令替换，alert 输出不被吞）
  check_db_freshness

  # 检查 3: 近24h数量（同上）
  check_article_count

  # 检查 4: LLM 产出管线新鲜度（V7.9）
  check_llm_outputs

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  DataSource Health Check Complete"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
}

main
