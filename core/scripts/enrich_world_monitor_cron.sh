#!/bin/bash
# enrich_world_monitor_cron.sh — V8.0 世界监测定时入口
# 用法：crontab 每小时或每日执行
#   本脚本负责：采集数据 + LLM 增强 → enriched.json → contracts.json（/m 前端消费）
#
# 管线：market/sector/commodity 采集 → LLM 判词 → 合约转换（前端桥梁）

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

LOG_FILE="$LOG_DIR/world_monitor_$(date +%Y%m%d-%H%M%S).log"

echo "[$(date)] V8.0 World Monitor 启动" | tee "$LOG_FILE"

/usr/bin/python3.9 -u "$SCRIPT_DIR/enrich_world_monitor.py" \
  2>&1 | tee -a "$LOG_FILE"

echo "[$(date)] LLM增强完成，生成前端合约…" | tee -a "$LOG_FILE"

/usr/bin/python3.9 -u "$SCRIPT_DIR/generate_world_contract.py" \
  2>&1 | tee -a "$LOG_FILE"

echo "[$(date)] 完成" | tee -a "$LOG_FILE"

# 保留最近 7 天日志
find "$LOG_DIR" -name 'world_monitor_*.log' -mtime +7 -delete 2>/dev/null || true
