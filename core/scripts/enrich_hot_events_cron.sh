#!/bin/bash
# enrich_hot_events_cron.sh — cron 封装：MCP ingest → LLM 增强 → 日志
# 用法：放到 crontab，建议每日执行 1-2 次
#
# V7.7: 移除 RSS 降级守卫，改检查 source_articles.db 是否有新数据
#   --force 可强制绕过守卫 + 重生成全部缓存

set -euo pipefail

# 从集中密钥文件载入 API Keys
if [ -f /opt/cycleradar-trader/.env ]; then
  set -a
  source /opt/cycleradar-trader/.env
  set +a
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="/opt/cycleradar-trader"
LOG_DIR="$PROJECT_ROOT/data/logs"
mkdir -p "$LOG_DIR"

LOG_FILE="$LOG_DIR/enrich_hot_events_$(date +%Y%m%d-%H%M%S).log"
SOURCE_DB="$PROJECT_ROOT/data/source_articles.db"
ENRICHMENT_PATH="$PROJECT_ROOT/data/hot_enrichment.json"

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

# ── Step 0: 先跑 MCP ingest 补充数据 ──
echo "[$(date)] MCP ingest 灌入..." | tee "$LOG_FILE"
/usr/bin/python3.9 "$SCRIPT_DIR/ingest_mcp_news.py" 2>&1 | tee -a "$LOG_FILE" || echo "[$(date)] MCP ingest 失败（不阻塞 enrich）" | tee -a "$LOG_FILE"

# ── 数据守卫：source_articles.db 无新文章则跳过 ──
if ! $FORCE; then
  if [ ! -f "$SOURCE_DB" ]; then
    echo "[$(date)] source_articles.db 不存在，跳过 LLM 调用" | tee -a "$LOG_FILE"
    exit 0
  fi

  TODAY=$(date +%Y-%m-%d)
  YESTERDAY=$(date -d "yesterday" +%Y-%m-%d 2>/dev/null || date -v-1d +%Y-%m-%d 2>/dev/null || echo "$TODAY")
  NEW_COUNT=$(sqlite3 "$SOURCE_DB" "SELECT COUNT(*) FROM source_articles WHERE fetch_status='success' AND publish_date >= '$YESTERDAY';" 2>/dev/null || echo "0")

  if [ "$NEW_COUNT" -eq 0 ]; then
    echo "[$(date)] source_articles.db 无近24h新文章，跳过 LLM 调用" | tee -a "$LOG_FILE"
    exit 0
  fi

  echo "[$(date)] source_articles.db 有 ${NEW_COUNT} 条新文章，启动 enrichment" | tee -a "$LOG_FILE"
fi

/usr/bin/python3.9 "$SCRIPT_DIR/enrich_hot_events.py" "${PYTHON_ARGS[@]}" 2>&1 | tee -a "$LOG_FILE"

# 保留最近 7 天日志
find "$LOG_DIR" -name 'enrich_hot_events_*.log' -mtime +7 -delete 2>/dev/null || true
