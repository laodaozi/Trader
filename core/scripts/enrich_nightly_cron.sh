#!/bin/bash
# enrich_nightly_cron.sh — 22:30 晚间信源增强（ingest优先 → manual → RSS回退）
# 
# 流程：
#   1. 读取 ingest DB（/admin/ingest 手动填入的公众号全文）→ 调 LLM 增强  ← 核心优先路径
#   2. 读取 manual.jsonl（旧手动提交）→ 调 LLM 增强
#   3. 读取 WeWe RSS DB（公众号抓取）→ 调 LLM 增强（RSS失效时为空）
#   4. 增强结果写入 data/hot_enrichment.json（标题 hash 去重，ingest已覆盖的跳过）
#
# cron 排程：
#   30 22 * * * /opt/cycleradar-trader/core/scripts/enrich_nightly_cron.sh >> /opt/cycleradar-trader/data/logs/enrich_nightly.log 2>&1
#
# 依赖：
#   - data/sources/manual.jsonl（手动提交信源）
#   - /opt/wewe-rss-deploy/data/wewe-rss.db（WeWe RSS 数据库）
#   - core/scripts/enrich_hot_events.py（LLM 增强引擎，用 ANTHROPIC_API_KEY）
#   - .env 集中密钥文件

set -euo pipefail

PROJECT_ROOT="/opt/cycleradar-trader"
LOG_DIR="$PROJECT_ROOT/data/logs"
mkdir -p "$LOG_DIR"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log "🚀 enrich_nightly_cron.sh 启动"

# ── 1. 载入 API Keys ──
if [ -f "$PROJECT_ROOT/.env" ]; then
  set -a
  source "$PROJECT_ROOT/.env"
  set +a
else
  log "❌ 密钥文件不存在: $PROJECT_ROOT/.env"
  exit 1
fi

# ── 2. 处理 ingest DB（核心优先路径：/admin/ingest 手动填入的公众号全文）──
INGEST_COUNT=0
TODAY=$(date +%Y-%m-%d)
TMP_INGEST_JSON=$(mktemp /tmp/enrich_ingest_XXXXXX.json)
trap "rm -f '$TMP_INGEST_JSON'" EXIT

python3 "$PROJECT_ROOT/core/scripts/ingest_db.py" get_content "$TODAY" 2>/dev/null | python3.9 -c "
import json, sys
rows = json.loads(sys.stdin.read())
events = [
    {'title': r['title'], 'source': r['source_name'], 'content': r['content_text'], 'source_date': '$TODAY'}
    for r in rows if r.get('content_text') and len(r.get('content_text','')) > 100
]
print(len(events), file=sys.stderr)
with open('$TMP_INGEST_JSON', 'w', encoding='utf-8') as f:
    json.dump(events, f, ensure_ascii=False)
" 2>/tmp/ingest_count.txt

INGEST_COUNT=$(cat /tmp/ingest_count.txt 2>/dev/null || echo 0)

if [ "$INGEST_COUNT" -gt 0 ]; then
  log "📰 ingest DB: ${INGEST_COUNT} 条待增强"
  python3.9 "$PROJECT_ROOT/core/scripts/enrich_hot_events.py" --file "$TMP_INGEST_JSON" 2>&1 | grep -E '✅|❌|ERROR' || true
else
  log "📰 ingest DB: 今日无文章"
fi

# ── 3. 处理手动提交信源（manual.jsonl）──
MANUAL_JSONL="$PROJECT_ROOT/data/sources/manual.jsonl"
MANUAL_COUNT=0

if [ -f "$MANUAL_JSONL" ]; then
  # 将 manual.jsonl 转成 enrich_hot_events.py --file 能读的 JSON
  TMP_MANUAL_JSON=$(mktemp /tmp/enrich_manual_XXXXXX.json)
  trap "rm -f '$TMP_MANUAL_JSON'" EXIT

  # 只取未增强的条目
  python3 -c "
import json, sys

events = []
with open('$MANUAL_JSONL', 'r') as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get('enriched'):
            continue  # 跳过已增强的
        events.append({
            'title': e.get('title', '(无标题)'),
            'source': e.get('source', '手动提交'),
            'content': e.get('content', ''),
        })
print(json.dumps(events, ensure_ascii=False))
" > "$TMP_MANUAL_JSON"

  MANUAL_COUNT=$(python3 -c "import json; print(len(json.load(open('$TMP_MANUAL_JSON'))))")
  
  if [ "$MANUAL_COUNT" -gt 0 ]; then
    log "📝 手动信源: ${MANUAL_COUNT} 条待增强"
    python3.9 "$PROJECT_ROOT/core/scripts/enrich_hot_events.py" --file "$TMP_MANUAL_JSON" 2>&1 | tail -5
    # 标记已增强 → 原子替换 manual.jsonl（flock 防竞态，id 匹配防误标）
    LOCK_FILE="${MANUAL_JSONL}.lock"
    (
      flock -x 200
      python3.9 -c "
import json, sys, os

cache_path = '$PROJECT_ROOT/data/hot_enrichment.json'
manual_path = '$MANUAL_JSONL'

# 收集 cache 中已有的 title 集合（兼容 list/dict）
enriched_titles = set()
try:
    cache = json.loads(open(cache_path).read())
    items = cache if isinstance(cache, list) else list(cache.values())
    for item in items:
        if isinstance(item, dict) and 'title' in item:
            enriched_titles.add(item['title'])
except Exception as e:
    print(f'  ↳ cache read error: {e}', file=sys.stderr)

lines = open(manual_path).readlines()
updated = 0
tmp_path = manual_path + '.tmp.' + str(os.getpid())
with open(tmp_path, 'w') as f:
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            f.write(line + '\n')
            continue
        # 用 title 匹配（id 尚未透传到 cache），但只标记 enriched=false 的条目
        if not e.get('enriched') and e.get('title', '') in enriched_titles:
            e['enriched'] = True
            updated += 1
        f.write(json.dumps(e, ensure_ascii=False) + '\n')
os.replace(tmp_path, manual_path)
print(f'  ↳ 标记 {updated} 条为已增强', file=sys.stderr)
"
    ) 200>"$LOCK_FILE"
  else
    log "📝 手动信源: 无待增强条目"
  fi
else
  log "📝 手动信源: manual.jsonl 不存在，跳过"
fi

# ── 4. 处理 RSS 信源（回退） ──
WEWE_DB="/opt/wewe-rss-deploy/data/wewe-rss.db"
RSS_COUNT=0

if [ -f "$WEWE_DB" ]; then
  RSS_COUNT=$(python3.9 -c "
import sqlite3
db = sqlite3.connect('$WEWE_DB')
cur = db.cursor()
cur.execute(\"SELECT COUNT(*) FROM articles WHERE publish_time >= strftime('%s','now','-1 day')\")
print(cur.fetchone()[0])
" 2>/dev/null || echo 0)

  if [ "$RSS_COUNT" -gt 0 ]; then
    log "📡 RSS 信源: ${RSS_COUNT} 条（24h内）"
    python3.9 "$PROJECT_ROOT/core/scripts/enrich_hot_events.py" 2>&1 | tail -5
  else
    log "📡 RSS 信源: 24h 内无新文章"
  fi
else
  log "📡 RSS 信源: DB 不存在，跳过"
fi

# ── 5. 汇总 ──
log "✅ enrich_nightly_cron.sh 完成 (ingest=${INGEST_COUNT}, manual=${MANUAL_COUNT}, rss=${RSS_COUNT})"

# ── 6. 写 pipeline_status.json ──
python3 -c "
import json
from pathlib import Path
status = {'date': '$(date +%Y-%m-%d)', 'enriched': $((INGEST_COUNT + MANUAL_COUNT + RSS_COUNT)), 'generated': 0, 'run_at': '$(date -u +%Y-%m-%dT%H:%M:%SZ)'}
Path('$PROJECT_ROOT/data/pipeline_status.json').write_text(json.dumps(status, ensure_ascii=False, indent=2))
"
