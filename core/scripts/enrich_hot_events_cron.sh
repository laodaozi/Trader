#!/bin/bash
# enrich_hot_events_cron.sh — cron 封装：设 API key → 跑 LLM 增强 → 日志
# 用法：放到 crontab，建议每日执行 1-2 次（跟随 wewe-rss 同步节奏）
#
# V4.2 新增降级守卫：RSS 断流时自动跳过 LLM 调用，省 API credit
#   --force 可强制绕过守卫 + 重生成全部缓存

set -euo pipefail

# 从集中密钥文件载入 API Keys（替代 crontab 明文 / 脚本内 hardcode）
# ⚠️ 此文件为 chmod 600，不纳入 git
if [ -f /opt/cycleradar-trader/.env ]; then
  set -a
  source /opt/cycleradar-trader/.env
  set +a
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="/opt/cycleradar-trader/data/logs"
mkdir -p "$LOG_DIR"

LOG_FILE="$LOG_DIR/enrich_hot_events_$(date +%Y%m%d-%H%M%S).log"
DB_PATH="/opt/cycleradar-trader/admin/data/wewe-rss.db"
ENRICHMENT_PATH="/opt/cycleradar-trader/data/hot_enrichment.json"

# 解析参数
FORCE=false
PYTHON_ARGS=()
for arg in "$@"; do
  if [ "$arg" = "--force" ]; then
    FORCE=true
    PYTHON_ARGS+=("--force")
  else
    PYTHON_ARGS+=("$arg")
  fi
done

# ── V4.2 降级守卫：无新文章则跳过 ──
if ! $FORCE; then
  # 取 articles 表最新时间
  DB_LAST_TS=$(sqlite3 "$DB_PATH" "SELECT COALESCE(MAX(publish_time), 0) FROM articles;" 2>/dev/null || echo "0")

  # 取上次 enrichment 时间（从缓存文件中所有 enriched_at 取最大值）
  ENRICH_LAST_TS=0
  if [ -f "$ENRICHMENT_PATH" ]; then
    ENRICH_LAST_TS=$(python3 -c "
import json
try:
    data = json.load(open('$ENRICHMENT_PATH'))
    max_ts = 0
    for v in data.values():
        if isinstance(v, dict) and 'enriched_at' in v:
            ts = v['enriched_at']
            max_ts = max(max_ts, int(ts) if isinstance(ts, (int, float)) else 0)
    print(max_ts)
except: print(0)
" 2>/dev/null || echo "0")
  fi

  # 如果 DB 最新文章没比上次 enrich 更新，跳过
  if [ "$DB_LAST_TS" != "0" ] && [ "$ENRICH_LAST_TS" != "0" ] && [ "$DB_LAST_TS" -le "$ENRICH_LAST_TS" ]; then
    echo "[$(date)] RSS 无新文章 (DB最后: $(date -d @$DB_LAST_TS '+%m-%d %H:%M' 2>/dev/null || echo $DB_LAST_TS), 上次enrich: $(date -d @$ENRICH_LAST_TS '+%m-%d %H:%M' 2>/dev/null || echo $ENRICH_LAST_TS))，跳过 LLM 调用" | tee "$LOG_FILE"
    exit 0
  elif [ "$DB_LAST_TS" = "0" ]; then
    echo "[$(date)] RSS DB 无数据，跳过 LLM 调用" | tee "$LOG_FILE"
    exit 0
  fi

  echo "[$(date)] DB 有新文章 (DB最后: $(date -d @$DB_LAST_TS '+%m-%d %H:%M' 2>/dev/null || echo $DB_LAST_TS) > 上次enrich: $(date -d @$ENRICH_LAST_TS '+%m-%d %H:%M' 2>/dev/null || echo $ENRICH_LAST_TS))，启动 enrichment"
fi

/usr/bin/python3.9 "$SCRIPT_DIR/enrich_hot_events.py" "${PYTHON_ARGS[@]}" 2>&1 | tee "$LOG_FILE"

# 保留最近 7 天日志
find "$LOG_DIR" -name 'enrich_hot_events_*.log' -mtime +7 -delete 2>/dev/null || true
