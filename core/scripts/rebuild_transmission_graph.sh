#!/bin/bash
# rebuild_transmission_graph.sh — 每日从 transmission_signals.jsonl 重建图谱
# cron: 0 22 * * 1-5  （每个交易日 22:00，在 21:30 信号生成后运行）
# 依赖：transmission_signals.jsonl 已由 event_monitor.py 在 21:30 写出

set -e

PROJECT_ROOT="/opt/cycleradar-trader"
SIGNALS_FILE="$PROJECT_ROOT/data/transmission_signals.jsonl"
GRAPH_FILE="$PROJECT_ROOT/data/transmission_graph.json"
LOG_FILE="$PROJECT_ROOT/data/logs/rebuild_graph.log"
PYTHON="/usr/bin/python3.9"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] rebuild_transmission_graph.sh 开始" >> "$LOG_FILE"

# 检查信号文件是否存在且新鲜（48h 内）
if [ ! -f "$SIGNALS_FILE" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: signals 文件不存在: $SIGNALS_FILE" >> "$LOG_FILE"
    exit 1
fi

# 文件年龄检查（小时）
FILE_AGE_H=$(( ($(date +%s) - $(stat -c %Y "$SIGNALS_FILE" 2>/dev/null || stat -f %m "$SIGNALS_FILE")) / 3600 ))
if [ "$FILE_AGE_H" -gt 48 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN: signals 文件已超 48h（${FILE_AGE_H}h），跳过重建" >> "$LOG_FILE"
    exit 0
fi

# 从 signals 构建图谱（调用 transmission_graph.py CLI）
cd "$PROJECT_ROOT"
PYTHONPATH="$PROJECT_ROOT" "$PYTHON" -c "
import sys
sys.path.insert(0, '.')
from core.graph.transmission_graph import TransmissionGraph, GRAPH_PATH
g = TransmissionGraph.build_from_signals()
g.save()
print(g.summary())
print('saved to:', GRAPH_PATH)
" >> "$LOG_FILE" 2>&1

EXIT_CODE=$?
if [ $EXIT_CODE -ne 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: 图谱重建失败 exit=$EXIT_CODE" >> "$LOG_FILE"
    exit $EXIT_CODE
fi

# 验收：检查输出文件大小 > 1KB
GRAPH_SIZE=$(stat -c %s "$GRAPH_FILE" 2>/dev/null || stat -f %z "$GRAPH_FILE" 2>/dev/null || echo 0)
if [ "$GRAPH_SIZE" -lt 1024 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN: 图谱文件过小 (${GRAPH_SIZE}B)，可能异常" >> "$LOG_FILE"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] OK: 图谱重建完成 (${GRAPH_SIZE}B)" >> "$LOG_FILE"
fi
