#!/bin/bash
# enrich_morning_cron.sh — cron wrapper for enrich_morning.js
# 从集中密钥文件载入 API Keys → 执行 Node 脚本
# 替代 crontab 明文：DEEPSEEK_API_KEY=sk-xxx node enrich_morning.js
#
# 用法（crontab）：
#   27 6 * * * /opt/cycleradar-trader/core/scripts/enrich_morning_cron.sh >> /opt/cycleradar-trader/data/logs/enrich_morning.log 2>&1

set -euo pipefail

# 从集中密钥文件载入 API Keys
if [ -f /opt/cycleradar-trader/.env ]; then
  set -a
  source /opt/cycleradar-trader/.env
  set +a
else
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] ❌ 密钥文件不存在: /opt/cycleradar-trader/.env" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="/opt/cycleradar-trader/data/logs"
mkdir -p "$LOG_DIR"

exec /usr/local/bin/node "$SCRIPT_DIR/enrich_morning.js" "$@"
