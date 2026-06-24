#!/usr/bin/env bash
# CycleRadar 4 分类 API 冒烟自检
# 用法：bash scripts/smoke.sh [HOST]
# 默认 host: http://localhost
set -euo pipefail

HOST="${1:-http://localhost}"
ENDPOINT="${HOST}/m/api/cycleradar"
PASS=0; FAIL=0; WARN=0

say()   { printf "  %-8s %s\n" "$1" "$2"; }
pass()  { PASS=$((PASS+1)); say "✅ PASS" "$*"; }
fail()  { FAIL=$((FAIL+1)); say "❌ FAIL" "$*"; }
warn()  { WARN=$((WARN+1)); say "⚠️  WARN" "$*"; }

echo "━━━ CycleRadar Smoke Test ━━━"
echo "Host: ${HOST}"
echo "Time: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo ""

# ── 1. 连通性 ──
echo "1. Connectivity"
HTTP_CODE=$(curl -s -o /tmp/cr_smoke.json -w '%{http_code}' --connect-timeout 5 "${ENDPOINT}" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
  pass "HTTP ${HTTP_CODE} ${ENDPOINT}"
else
  fail "HTTP ${HTTP_CODE} ${ENDPOINT} (check PM2: trader-admin online?)"
  exit 1
fi

# ── 2. JSON 解析 ──
echo ""
echo "2. JSON structure"
if command -v python3 &>/dev/null; then
  CHECK=$(python3 -c "
import json
with open('/tmp/cr_smoke.json') as f:
    d = json.load(f)
for k in ['summary','hotEvents','alpha','etf','commodity','signals']:
    if k not in d:
        print(f'MISSING_KEY:{k}')
        exit(1)
print('OK')
" 2>&1)
  if [ "$CHECK" = "OK" ]; then
    pass "All 6 required keys present (summary/hotEvents/alpha/etf/commodity/signals)"
  else
    fail "Missing key(s): ${CHECK}"
  fi
else
  warn "python3 not found, skipping structural check"
fi

# ── 3. 分类非空验证 ──
echo ""
echo "3. Category health"
CATEGORIES=$(python3 -c "
import json
with open('/tmp/cr_smoke.json') as f:
    d = json.load(f)
cats = {k: len(v) if isinstance(v, list) else 'not-list' for k, v in d.items() if k in ('alpha','etf','commodity','hotEvents')}
for k, v in cats.items():
    print(f'{k}={v}')
" 2>&1)

ALL_EMPTY=true
while IFS='=' read -r key val; do
  if [ "$val" = "not-list" ]; then
    fail "${key} is not an array"
    ALL_EMPTY=false
  elif [ "$val" -gt 0 ] 2>/dev/null; then
    pass "${key}: ${val} items"
    ALL_EMPTY=false
  else
    warn "${key}: empty (may be normal outside trading hours)"
  fi
done <<< "$CATEGORIES"

if $ALL_EMPTY; then
  warn "All 4 categories empty — check upstream_signals.jsonl & wewe-rss.db"
fi

# ── 4. unknown 分类告警（V4.0.1+） ──
echo ""
echo "4. Unknown signals check"
UNKNOWN_COUNT=$(python3 -c "
import json
with open('/tmp/cr_smoke.json') as f:
    d = json.load(f)
print(len(d.get('unknown', [])))
" 2>&1)
if [ "$UNKNOWN_COUNT" = "0" ]; then
  pass "No unknown signals (all strategies mapped)"
else
  warn "${UNKNOWN_COUNT} unknown signal(s) — check PM2 logs for [signals] unknown category warnings"
fi

# ── 汇总 ──
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━"
printf "  TOTAL:  pass=%d  warn=%d  fail=%d\n" "$PASS" "$WARN" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "  STATUS: ❌ FAILED — ${FAIL} failure(s) need attention"
  exit 1
elif [ "$WARN" -gt 0 ]; then
  echo "  STATUS: ⚠️  DEGRADED — ${WARN} warning(s), review above"
  exit 0
else
  echo "  STATUS: ✅ ALL PASS"
  exit 0
fi
