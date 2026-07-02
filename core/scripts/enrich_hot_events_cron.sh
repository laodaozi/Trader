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
  # 优先检查 source_articles.db（用户手动投喂，D日晚 → D+1早生成）
  SOURCE_DB="/opt/cycleradar-trader/data/source_articles.db"
  TODAY=$(date +%Y-%m-%d)
  SOURCE_COUNT=$(python3.9 -c "
import sqlite3, sys
try:
    con = sqlite3.connect('$SOURCE_DB')
    n = con.execute("SELECT COUNT(*) FROM source_articles WHERE publish_date=?", ('$TODAY',)).fetchone()[0]
    # 也检查昨天（D日晚投喂 → D+1早生成，publish_date 是昨天）
    import datetime
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    n2 = con.execute("SELECT COUNT(*) FROM source_articles WHERE publish_date=?", (yesterday,)).fetchone()[0]
    print(max(n, n2))
except: print(0)
" 2>/dev/null || echo "0")

  # 取 wewe-rss articles 表最新时间（辅助判断）
  DB_LAST_TS=$(sqlite3 "$DB_PATH" "SELECT COALESCE(MAX(publish_time), 0) FROM articles;" 2>/dev/null || echo "0")

  # 取上次 enrichment 时间（兼容 list/dict 格式）
  ENRICH_LAST_TS=0
  if [ -f "$ENRICHMENT_PATH" ]; then
    ENRICH_LAST_TS=$(python3.9 -c "
import json, os
try:
    data = json.load(open('$ENRICHMENT_PATH'))
    # 用文件修改时间作为最可靠的 enrich 时间
    print(int(os.path.getmtime('$ENRICHMENT_PATH')))
except: print(0)
" 2>/dev/null || echo "0")
  fi

  # source_articles 有数据 → 直接跑（核心路径：D晚投喂 → D+1早生成）
  if [ "$SOURCE_COUNT" != "0" ]; then
    echo "[$(date)] source_articles 有 $SOURCE_COUNT 条记录，启动 enrichment"
  # 降级：wewe-rss 有新文章
  elif [ "$DB_LAST_TS" != "0" ] && [ "$ENRICH_LAST_TS" != "0" ] && [ "$DB_LAST_TS" -le "$ENRICH_LAST_TS" ]; then
    echo "[$(date)] RSS 无新文章，跳过 LLM 调用" | tee "$LOG_FILE"
    exit 0
  elif [ "$DB_LAST_TS" = "0" ]; then
    echo "[$(date)] RSS DB 无数据且 source_articles 为空，跳过" | tee "$LOG_FILE"
    exit 0
  else
    echo "[$(date)] wewe-rss 有新文章，启动 enrichment"
  fi
fi

/usr/bin/python3.9 "$SCRIPT_DIR/enrich_hot_events.py" "${PYTHON_ARGS[@]}" 2>&1 | tee "$LOG_FILE"

# 保留最近 7 天日志
find "$LOG_DIR" -name 'enrich_hot_events_*.log' -mtime +7 -delete 2>/dev/null || true
