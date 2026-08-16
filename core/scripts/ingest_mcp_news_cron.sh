#!/bin/bash
# ingest_mcp_news_cron.sh — MCP 新闻灌入 cron 封装
# 用法：crontab -e 加入：
#   0 8 * * * /opt/cycleradar-trader/core/scripts/ingest_mcp_news_cron.sh
#   0 18 * * * /opt/cycleradar-trader/core/scripts/ingest_mcp_news_cron.sh
#
# V7.7: RSS token 过期期间，MCP 作为独立数据源保证 enrich 管线有米下锅
# 数据流：MCP news API → source_articles.db → enrich_hot_events.py 消费

set -euo pipefail

# ── 从集中密钥文件载入 API Keys ──
if [ -f /opt/cycleradar-trader/.env ]; then
  set -a
  source /opt/cycleradar-trader/.env
  set +a
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="/opt/cycleradar-trader/data/logs"
mkdir -p "$LOG_DIR"

LOG_FILE="$LOG_DIR/ingest_mcp_news_$(date +%Y%m%d-%H%M%S).log"

echo "[$(date)] 启动 ingest_mcp_news" | tee "$LOG_FILE"

/usr/bin/python3.9 "$SCRIPT_DIR/ingest_mcp_news.py" 2>&1 | tee -a "$LOG_FILE"

echo "[$(date)] ingest_mcp_news 完成" | tee -a "$LOG_FILE"

# 保留最近 7 天日志
find "$LOG_DIR" -name 'ingest_mcp_news_*.log' -mtime +7 -delete 2>/dev/null || true
