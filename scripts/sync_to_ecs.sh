#!/usr/bin/env bash
# CycleRadar Trader · sync local source to ECS production paths.
#
# Source of truth: /Users/scott/products/cycleradar-trader
# Runtime targets:
#   - /opt/cycleradar-trader: admin/data/scripts/docs project root
#   - /opt/cycleradar: legacy flat Python runtime used by stock_agent cron
set -euo pipefail

HOST="${CR_ECS_HOST:-root@139.196.115.64}"
ROOT="${CR_LOCAL_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
TRADER_ROOT="${CR_ECS_TRADER_ROOT:-/opt/cycleradar-trader}"
PY_ROOT="${CR_ECS_PY_ROOT:-/opt/cycleradar}"
MODE="${1:-apply}"

if [ "$MODE" != "apply" ] && [ "$MODE" != "--dry-run" ]; then
    echo "usage: $0 [apply|--dry-run]" >&2
    exit 2
fi

RSYNC_FLAGS=(-az --delete)
if [ "$MODE" = "--dry-run" ]; then
    RSYNC_FLAGS+=(--dry-run --itemize-changes)
fi

need_file() {
    local path="$1"
    if [ ! -e "$path" ]; then
        echo "missing required path: $path" >&2
        exit 1
    fi
}

sync_dir() {
    local src="$1" dst="$2"
    need_file "$src"
    rsync "${RSYNC_FLAGS[@]}" "$src/" "$HOST:$dst/"
}

sync_file() {
    local src="$1" dst="$2"
    need_file "$src"
    rsync "${RSYNC_FLAGS[@]}" "$src" "$HOST:$dst"
}

sync_admin_subdir() {
    local name="$1"
    sync_dir "$ROOT/admin/$name" "$TRADER_ROOT/admin/$name"
}

echo "== CycleRadar ECS sync =="
echo "host=$HOST"
echo "root=$ROOT"
echo "mode=$MODE"

echo "-- project runtime files"
sync_admin_subdir "models"
sync_admin_subdir "public"
sync_admin_subdir "routes"
sync_admin_subdir "views"
sync_file "$ROOT/admin/server.js" "$TRADER_ROOT/admin/server.js"
sync_file "$ROOT/admin/scheduler.js" "$TRADER_ROOT/admin/scheduler.js"
sync_file "$ROOT/admin/package.json" "$TRADER_ROOT/admin/package.json"
if [ -f "$ROOT/admin/package-lock.json" ]; then
    sync_file "$ROOT/admin/package-lock.json" "$TRADER_ROOT/admin/package-lock.json"
fi
rsync -az --exclude=backtest_reports "$ROOT/scripts/" "$HOST:$TRADER_ROOT/scripts/"
# V4.3: 运行时数据目录（upstream_signals.jsonl / hotevents_cache.json 等）
# 不加 --delete，保留 ECS 运行时产物（ohlc_cache/ 等）
# ⚠️ V5.2: 排除 ECS cron 运行时生成的文件（rebuild_trader_views.py 产物），防止 Mac 空壳覆盖
# ⚠️ upstream_signals.jsonl 是信号总线，Mac Pipeline A (report_agent) 和 ECS Pipeline B
#    (stock_agent) 双写——排除后由 post-sync merge 步骤处理
rsync -az \
  --exclude=backtest_reports \
  --exclude=trader_strategy.jsonl \
  --exclude=trader_tracker.jsonl \
  --exclude=upstream_signals.jsonl \
  --exclude=timing_history.json \
  --exclude=logs/ \
  --exclude=ohlc_cache/ \
  "$ROOT/data/" "$HOST:$TRADER_ROOT/data/"

# V5.2: timing_history.json — 本地是 symlink（→ ~/交易员/data/timing_history.json），
# data/ rsync 已排除，这里显式 -L 跟随 symlink 推真文件到 ECS
echo "-- sync timing_history.json (follow symlink)"
if [ -f "$HOME/交易员/data/timing_history.json" ]; then
  rsync -az "$HOME/交易员/data/timing_history.json" "$HOST:$TRADER_ROOT/data/timing_history.json"
  echo "  ✅ pushed real file (src: ~/交易员/data/timing_history.json)"
else
  echo "  ⚠️  ~/交易员/data/timing_history.json not found on Mac"
fi

# V5.2: 合并 upstream_signals.jsonl（Mac report_agent 信号 → 追加到 ECS，按 signal_id 去重）
# 先推 Mac 版本到 ECS /tmp，再在 ECS 侧执行合并
echo "-- merge upstream_signals (Mac report_agent → ECS)"
rsync -q "$ROOT/data/upstream_signals.jsonl" "$HOST:/tmp/upstream_signals_mac.jsonl" 2>/dev/null || {
  echo "  ⚠️  rsync upstream_signals to /tmp failed, merge skipped"
  # continue — 不阻断整个 sync 流程
}

# 合并：读取 ECS 已有 signal_id 集合，追加 /tmp 中未出现过的 report_agent 信号
ssh "$HOST" "
  if [ ! -f /tmp/upstream_signals_mac.jsonl ]; then
    echo '  (no Mac upstream_signals to merge, skipped)'
  else
    python3.9 -c \"
import json, os
data_dir = '$TRADER_ROOT/data'
ecs_file = os.path.join(data_dir, 'upstream_signals.jsonl')
mac_tmp = '/tmp/upstream_signals_mac.jsonl'

seen_ids = set()
if os.path.exists(ecs_file):
    for line in open(ecs_file):
        if line.strip():
            try: seen_ids.add(json.loads(line).get('signal_id',''))
            except: pass

new_count = 0
with open(ecs_file, 'a') as f:
    for line in open(mac_tmp):
        if not line.strip(): continue
        try:
            sig = json.loads(line)
            sid = sig.get('signal_id','')
            if sig.get('strategy') == 'report_agent' and sid and sid not in seen_ids:
                f.write(line if line.endswith('\n') else line + '\n')
                seen_ids.add(sid)
                new_count += 1
        except: pass
print(f'  appended {new_count} new report_agent signals (ECS total: {len(seen_ids)} unique signal_ids)')
    \"
    rm -f /tmp/upstream_signals_mac.jsonl
  fi
" 2>&1 || echo "  ⚠️  merge step failed (non-fatal, stock_agent cron will repopulate ECS data)"
sync_file "$ROOT/CONTEXT.md" "$TRADER_ROOT/CONTEXT.md"
sync_file "$ROOT/ROADMAP.md" "$TRADER_ROOT/ROADMAP.md"
sync_file "$ROOT/CHANGELOG.md" "$TRADER_ROOT/CHANGELOG.md"

# V5.0: Python 策略引擎 — 部署到 core/（单一真源，cron 从此指向 $TRADER_ROOT/core）
# 同时向后兼容保留 flat legacy layout 至 $PY_ROOT（等待 cron 切换后可删）
echo "-- core Python strategy engine -> $TRADER_ROOT/core"
rsync -az "$ROOT/core/" "$HOST:$TRADER_ROOT/core/"

# V6.1: watchlist_signals.json → /opt/trader/output/contracts/（/m/api/watchlist 优先读此路径）
echo "-- sync watchlist_signals.json -> contracts path"
if [ -f "$ROOT/data/watchlist_signals.json" ]; then
  ssh "$HOST" "mkdir -p /opt/trader/output/contracts"
  rsync -az "$ROOT/data/watchlist_signals.json" "$HOST:/opt/trader/output/contracts/watchlist_signals.json"
  echo "  ✅ pushed to /opt/trader/output/contracts/watchlist_signals.json"
else
  echo "  ⚠️  $ROOT/data/watchlist_signals.json not found, skipping"
fi

echo "-- legacy flat Python runtime (backward compat, to be removed after cron cutover)"
sync_file "$ROOT/core/score.py" "$PY_ROOT/score.py"
sync_file "$ROOT/core/stock_agent.py" "$PY_ROOT/stock_agent.py"
sync_file "$ROOT/core/stock_analysis.py" "$PY_ROOT/stock_analysis.py"
sync_file "$ROOT/core/ma_signals.py" "$PY_ROOT/ma_signals.py"
sync_file "$ROOT/core/event_agent.py" "$PY_ROOT/event_agent.py"
sync_file "$ROOT/core/factor_agent.py" "$PY_ROOT/factor_agent.py"
sync_file "$ROOT/core/report_agent.py" "$PY_ROOT/report_agent.py"
sync_file "$ROOT/core/rotation_factor.py" "$PY_ROOT/rotation_factor.py"
sync_file "$ROOT/core/thesis_extractor.py" "$PY_ROOT/thesis_extractor.py"
sync_file "$ROOT/core/verify.py" "$PY_ROOT/verify.py"
sync_file "$ROOT/core/strategies/commodity_radar.py" "$PY_ROOT/commodity_radar.py"
sync_file "$ROOT/core/strategies/stock_agent_runner.py" "$PY_ROOT/stock_agent_runner.py"
sync_file "$ROOT/core/signals/upstream_signals.py" "$PY_ROOT/upstream_signals.py"
sync_file "$ROOT/core/signals/adapters/stock_agent_adapter.py" "$PY_ROOT/stock_agent_adapter.py"

if [ "$MODE" = "apply" ]; then
    echo "-- remote permissions + smoke checks"
    ssh "$HOST" "chmod +x '$TRADER_ROOT/scripts/health_check.sh' '$TRADER_ROOT/scripts/rebuild_trader_views.py' '$PY_ROOT/score.py' '$PY_ROOT/stock_analysis.py' && CYCLERADAR_DATA_DIR='$TRADER_ROOT/data' '$TRADER_ROOT/scripts/health_check.sh' --bare"
fi

echo "sync complete"
