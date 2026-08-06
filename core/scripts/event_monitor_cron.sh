#!/bin/bash
# event_monitor_cron.sh — 每日事件驱动信号检测 + 传导图谱追踪
#
# 流程：
#   1. 拉取政策/限产/供需/财报相关新闻（finstep MCP REST API）
#   2. 过 T1-T7 事件检测器（财报/供给冲击/政策催化/行政限产/猪周期等）
#   3. 触发信号写入 event_signals.jsonl
#   4. V8.3: 自动追踪传导图谱 → transmission_signals.jsonl
#
# cron 排程：
#   30 21 * * * /opt/cycleradar-trader/core/scripts/event_monitor_cron.sh >> /opt/cycleradar-trader/data/logs/event_monitor.log 2>&1
#
# 依赖：
#   - .env 集中密钥文件（MCP_SIGNATURE, ANTHROPIC_API_KEY）
#   - core/signals/event_monitor.py（事件检测引擎）
#   - core/graph/transmission_graph.py, event_evolution.py（传导图谱）
#   - python3.9 + dotenv + requests

set -euo pipefail

PROJECT_ROOT="/opt/cycleradar-trader"
LOG_DIR="$PROJECT_ROOT/data/logs"
mkdir -p "$LOG_DIR"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log "🚀 event_monitor_cron.sh 启动"

# ── 1. 载入 API Keys ──
if [ -f "$PROJECT_ROOT/.env" ]; then
  set -a
  source "$PROJECT_ROOT/.env"
  set +a
else
  log "❌ 密钥文件不存在: $PROJECT_ROOT/.env"
  exit 1
fi

# ── 2. 检查 Python 依赖 ──
python3.9 -c "import dotenv, requests, json, sys; print('deps ok')" 2>&1 || {
  log "❌ Python 依赖缺失"
  exit 1
}

# ── 3. 运行事件监测引擎 ──
export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"

log "🔍 运行事件监测引擎..."
python3.9 "$PROJECT_ROOT/core/signals/event_monitor.py" --run 2>&1 | tail -20

SIGNAL_COUNT=$(wc -l < "$PROJECT_ROOT/data/event_signals.jsonl" 2>/dev/null || echo 0)
TX_COUNT=$(wc -l < "$PROJECT_ROOT/data/transmission_signals.jsonl" 2>/dev/null || echo 0)

log "📉 应用权重衰减..."
python3.9 -c "
from core.graph.transmission_graph import TransmissionGraph
g = TransmissionGraph.load()
n = g.decay_all_edges(factor=0.995)
g.save()
import sys; print('decayed', n, 'edges', file=sys.stderr)
" 2>&1

log "✅ event_monitor_cron.sh 完成 (累计信号=${SIGNAL_COUNT}, 传导信号=${TX_COUNT})"
