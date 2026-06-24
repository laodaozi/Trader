#!/bin/bash
# snapshot_alpha.sh — 每日保存 alpha_latest.json 快照
# 用于 calc_30d_winrate.py 回溯比较
# Cron: 15:41 后执行（generate_contracts.py 已产出 alpha_latest.json）

set -euo pipefail

SRC="/opt/trader/output/contracts/alpha_latest.json"
SNAP_DIR="/opt/trader/output/snapshots"
DATE=$(date +%Y%m%d)
DEST="${SNAP_DIR}/alpha_${DATE}.json"

mkdir -p "$SNAP_DIR"

if [ -f "$SRC" ]; then
    cp "$SRC" "$DEST"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] snapshot_alpha: saved → $DEST ($(wc -c < "$DEST") bytes)"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] snapshot_alpha: SKIP — source missing: $SRC"
    exit 0  # 非致命：当天若 generate_contracts 失败，不报错
fi

# 清理 90 天前的旧快照
find "$SNAP_DIR" -name "alpha_*.json" -mtime +90 -delete 2>/dev/null || true
