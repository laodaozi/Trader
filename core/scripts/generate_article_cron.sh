#!/bin/bash
# generate_article_cron.sh — cron 封装：生成 CycleRadar 公众号文章
# 每日 09:00 自动执行（工作日）
# 依赖：hot_enrichment.json 由 08:00 的 enrich_hot_events_cron.sh 生成

set -euo pipefail

# 从集中密钥文件载入 API Keys
if [ -f /opt/cycleradar-trader/.env ]; then
  set -a
  source /opt/cycleradar-trader/.env
  set +a
fi

LOG_DIR="/opt/cycleradar-trader/data/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/generate_article_$(date +%Y%m%d-%H%M%S).log"

DATE=$(date +%Y-%m-%d)
ARTICLE_FILE="/opt/cycleradar-trader/data/articles/article_$(date +%Y%m%d).md"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting article generation for $DATE" | tee "$LOG_FILE"

# 检查文章是否已生成（避免重复调用 API）
if [ -f "$ARTICLE_FILE" ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Article already exists: $ARTICLE_FILE, skip" | tee -a "$LOG_FILE"
  exit 0
fi

# 检查 hot_enrichment.json 是否存在
if [ ! -f /opt/cycleradar-trader/data/hot_enrichment.json ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] hot_enrichment.json not found, skip" | tee -a "$LOG_FILE"
  exit 0
fi

# 执行生成
/usr/bin/python3.9 /opt/cycleradar-trader/core/scripts/generate_article.py \
  --date "$DATE" 2>&1 | tee -a "$LOG_FILE"

# 清理超过 7 天的日志
find "$LOG_DIR" -name 'generate_article_*.log' -mtime +7 -delete 2>/dev/null || true

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Done" | tee -a "$LOG_FILE"
