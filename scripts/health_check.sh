#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# CycleRadar Trader · 生产健康检查
#
# 用法:
#   bash scripts/health_check.sh           # 彩色文本报告
#   bash scripts/health_check.sh --json    # JSON 机器可读
#   bash scripts/health_check.sh --bare    # 单行状态 + 退出码
#
# 退出码:
#   0 = HEALTHY / DEGRADED (non-zero warn count)
#   1 = CRITICAL (has failures)
#
# 检查维度:
#   1. Process  — PM2 daemon, trader-admin port
#   2. Data     — upstream_signals, hotevents, strategy, tracker 新鲜度
#   3. Cron     — stock_agent 最近一次执行结果
#   4. System   — 磁盘、内存
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

# ── config ──
ADMIN_PORT="${CR_ADMIN_PORT:-3100}"
DATA_DIR="${CYCLERADAR_DATA_DIR:-}"
FORCE_DATA_DIR=""
OUTPUT_FORMAT="${1:-text}"   # text | json | bare
CURL_TIMEOUT=5

# 新鲜度阈值（小时）
UPSTREAM_MAX_AGE=24
HOTEVENTS_MAX_AGE=6
STRATEGY_MAX_AGE=48
TRACKER_MAX_AGE=48

# 磁盘/内存阈值
DISK_WARN_PCT=80
DISK_CRIT_PCT=90
MEM_WARN_MB=200
MEM_CRIT_MB=100

# 清洗参数
case "${1:-text}" in
    --json)   OUTPUT_FORMAT=json ;;
    --bare)   OUTPUT_FORMAT=bare ;;
esac

# ── helpers ──
RED=''; GREEN=''; YELLOW=''; NC=''
if [ -t 1 ] && [ "$OUTPUT_FORMAT" = "text" ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    NC='\033[0m'
fi

NOW_TS=$(date +%s)
NOW_ISO=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
NOW_LOCAL=$(date '+%Y-%m-%d %H:%M:%S %Z')
STAMP=$(date '+%Y%m%d-%H%M%S')

PASS=0; WARN=0; FAIL=0
RESULTS=()  # JSON 模式下累积 {"dim":"..","check":"..","status":"..","detail":".."}

# 跨平台 stat mtime
file_mtime() {
    local f="$1"
    if [ ! -f "$f" ] && [ ! -L "$f" ]; then
        echo "0"
        return
    fi
    # Linux
    local mt
    mt=$(stat -c %Y "$f" 2>/dev/null) || mt=""
    if [ -n "$mt" ]; then echo "$mt"; return; fi
    # macOS
    mt=$(stat -f %m "$f" 2>/dev/null) || mt="0"
    echo "${mt:-0}"
}

# 计算文件年龄（小时）
file_age_hours() {
    local f="$1"
    local mtime
    mtime=$(file_mtime "$f")
    if [ "$mtime" = "0" ]; then echo "-1"; return; fi
    echo $(( (NOW_TS - mtime) / 3600 ))
}

record() {
    local status="$1" dim="$2" check="$3" detail="$4"
    local icon emoji
    case "$status" in
        pass) ((++PASS)); icon="✓"; emoji="✅" ;;
        warn) ((++WARN)); icon="⚠"; emoji="⚠️"  ;;
        fail) ((++FAIL)); icon="✗"; emoji="❌" ;;
    esac
    RESULTS+=("$(printf '{"dim":"%s","check":"%s","status":"%s","detail":"%s"}' \
        "$dim" "$check" "$status" "$detail")")

    if [ "$OUTPUT_FORMAT" = "text" ]; then
        case "$status" in
            pass) printf "  ${GREEN}%s${NC} %s | %s\n" "$icon" "$check" "$detail" ;;
            warn) printf "  ${YELLOW}%s${NC} %s | %s\n" "$icon" "$check" "$detail" ;;
            fail) printf "  ${RED}%s${NC} %s | %s\n" "$icon" "$check" "$detail" ;;
        esac
    fi
}

# ── DATA_DIR 解析 ──
resolve_data_dir() {
    if [ -n "$FORCE_DATA_DIR" ]; then
        DATA_DIR="$FORCE_DATA_DIR"
        return
    fi
    if [ -n "${CYCLERADAR_DATA_DIR:-}" ]; then
        DATA_DIR="$CYCLERADAR_DATA_DIR"
        return
    fi
    # 推断：脚本在 scripts/ 下，data/ 在项目根
    local script_dir
    script_dir="$(cd "$(dirname "$0")" && pwd)"
    local proj_dir
    proj_dir="$(dirname "$script_dir")"
    if [ -d "$proj_dir/data" ]; then
        DATA_DIR="$proj_dir/data"
    else
        DATA_DIR="$proj_dir"
    fi
}
resolve_data_dir

# ════════════════════════════════════════════════
# Dimension 1: Process Health
# ════════════════════════════════════════════════
check_processes() {
    local dim="process"

    # 1a. PM2 daemon
    if pgrep -f 'PM2' >/dev/null 2>&1; then
        local pid
        pid=$(pgrep -f 'PM2' | head -1)
        record pass "$dim" "PM2 daemon" "PID ${pid}"
    else
        record fail "$dim" "PM2 daemon" "NOT running — check 'pm2 resurrect'"
    fi

    # 1b. trader-admin
    local http_code
    http_code=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout "$CURL_TIMEOUT" \
        "http://localhost:${ADMIN_PORT}/m/api/cycleradar" 2>/dev/null || echo "000")
    if [ "$http_code" = "200" ]; then
        record pass "$dim" "trader-admin" "port ${ADMIN_PORT} responding 200"
    elif [ "$http_code" = "000" ]; then
        record fail "$dim" "trader-admin" "port ${ADMIN_PORT} unreachable (connection refused/timeout)"
    else
        record fail "$dim" "trader-admin" "port ${ADMIN_PORT} returned HTTP ${http_code}"
    fi
}

# ════════════════════════════════════════════════
# Dimension 2: Data Freshness
# ════════════════════════════════════════════════
check_data_freshness() {
    local dim="data"

    check_one_file() {
        local path="$1" label="$2" max_h="$3" min_lines="${4:-1}"
        if [ ! -f "$path" ]; then
            record fail "$dim" "$label" "file not found: ${path}"
            return
        fi
        local age_h lines size_kb
        age_h=$(file_age_hours "$path")
        lines=$(wc -l < "$path" 2>/dev/null || echo "0")
        size_kb=$(( $(wc -c < "$path" 2>/dev/null || echo "0") / 1024 ))

        # 空文件特殊处理
        if [ "$min_lines" -gt 0 ] && [ "$lines" -lt "$min_lines" ]; then
            record warn "$dim" "$label" "0 lines, ${size_kb}KB (empty file)"
            return
        fi

        if [ "$age_h" -lt 0 ]; then
            record warn "$dim" "$label" "cannot stat mtime: ${path}"
        elif [ "$age_h" -le "$max_h" ]; then
            record pass "$dim" "$label" "${lines} lines, ${age_h}h old (≤${max_h}h)"
        elif [ "$age_h" -le $((max_h * 2)) ]; then
            record warn "$dim" "$label" "${lines} lines, ${age_h}h old (＞${max_h}h)"
        else
            record fail "$dim" "$label" "${lines} lines, ${age_h}h old (＞$((max_h * 2))h)"
        fi
    }

    # 2a. upstream_signals.jsonl
    check_one_file "${DATA_DIR}/upstream_signals.jsonl" "upstream_signals" "$UPSTREAM_MAX_AGE"

    # 2b. hotevents_cache.json (LLM enrich 产物)
    check_one_file "${DATA_DIR}/hotevents_cache.json" "hotevents_cache" "$HOTEVENTS_MAX_AGE" 0

    # 2c. trader_strategy.jsonl (孤儿文件，即使 stale 也是 warn 不是 fail)
    local strategy_file="${DATA_DIR}/trader_strategy.jsonl"
    if [ -f "$strategy_file" ]; then
        local age_h lines
        age_h=$(file_age_hours "$strategy_file")
        lines=$(wc -l < "$strategy_file" 2>/dev/null || echo "0")
        if [ "$age_h" -le "$STRATEGY_MAX_AGE" ]; then
            record pass "$dim" "trader_strategy" "${lines} rows, ${age_h}h old (≤${STRATEGY_MAX_AGE}h)"
        else
            # 已知孤儿文件，用 warn 而非 fail
            record warn "$dim" "trader_strategy" "${lines} rows, ${age_h}h old — orphaned (no known writer)"
        fi
    else
        record warn "$dim" "trader_strategy" "file not found (diagnosis/tracking tabs may be empty)"
    fi

    # 2d. trader_tracker.jsonl (同样孤儿文件)
    local tracker_file="${DATA_DIR}/trader_tracker.jsonl"
    if [ -f "$tracker_file" ]; then
        local age_h lines
        age_h=$(file_age_hours "$tracker_file")
        lines=$(wc -l < "$tracker_file" 2>/dev/null || echo "0")
        if [ "$age_h" -le "$TRACKER_MAX_AGE" ]; then
            record pass "$dim" "trader_tracker" "${lines} rows, ${age_h}h old (≤${TRACKER_MAX_AGE}h)"
        else
            record warn "$dim" "trader_tracker" "${lines} rows, ${age_h}h old — orphaned (no known writer)"
        fi
    else
        record warn "$dim" "trader_tracker" "file not found (tracking tab may be empty)"
    fi

    # 2e. articles.db
    local articles_db="${DATA_DIR}/articles.db"
    if [ -f "$articles_db" ]; then
        local size_kb
        size_kb=$(( $(wc -c < "$articles_db" 2>/dev/null || echo "0") / 1024 ))
        record pass "$dim" "articles_db" "${size_kb}KB"
    else
        record warn "$dim" "articles_db" "file not found (hot events may be empty)"
    fi
}

# ════════════════════════════════════════════════
# Dimension 3: Cron Execution
# ════════════════════════════════════════════════
check_cron() {
    local dim="cron"

    # 手动补跑或 cron 重跑后，信号总线是 stock_agent 是否恢复的最终事实来源。
    local today_ymd today_dash stock_agent_today
    today_ymd=$(date +%Y%m%d)
    today_dash=$(date +%Y-%m-%d)
    stock_agent_today=0
    if [ -f "${DATA_DIR}/upstream_signals.jsonl" ]; then
        stock_agent_today=$(grep -c "STOCK_AGENT-${today_ymd}-" "${DATA_DIR}/upstream_signals.jsonl" 2>/dev/null || echo "0")
        stock_agent_today=$(echo "$stock_agent_today" | tr -d '\n\r')
    fi

    # 3a. stock_agent cron
    local cr_log="${DATA_DIR}/logs/stock_agent_cron.log"
    if [ ! -f "$cr_log" ]; then
        if [ "$stock_agent_today" -gt 0 ]; then
            record pass "$dim" "stock_agent" "${today_dash}: ${stock_agent_today} signal rows in bus (log missing)"
        else
            record fail "$dim" "stock_agent" "log not found: ${cr_log}"
        fi
        return
    fi

    # 抽最近 100 行分析
    local recent
    recent=$(tail -100 "$cr_log" 2>/dev/null || echo "")

    # 最近一次运行时间戳
    local last_ts
    last_ts=$(echo "$recent" | grep -oP '\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}' | tail -1 || echo "")
    if [ -z "$last_ts" ]; then
        last_ts=$(echo "$recent" | grep -oP '\d{2}:\d{2}:\d{2}' | tail -1 || echo "")
    fi
    [ -z "$last_ts" ] && last_ts="unknown"

    # OHLC 失败计数
    local ohlc_fails signal_count
    ohlc_fails=$(echo "$recent" | grep -c "OHLC获取失败\|RemoteDisconnected\|Connection aborted" || echo "0")
    ohlc_fails=$(echo "$ohlc_fails" | tr -d '\n\r')
    signal_count=$(echo "$recent" | grep -ci "写入.*条信号" || echo "0")
    signal_count=$(echo "$signal_count" | tr -d '\n\r')

    if [ "$stock_agent_today" -gt 0 ]; then
        record pass "$dim" "stock_agent" "${today_dash}: ${stock_agent_today} signal rows in bus, latest log has ${ohlc_fails} OHLC failures"
    elif [ "$ohlc_fails" -gt 0 ]; then
        # 尝试抽总处理数
        local total_processed
        total_processed=$(echo "$recent" | grep -oP '共\d+只' | grep -oP '\d+' | tail -1 || echo "?")
        record fail "$dim" "stock_agent" "last ${last_ts}, ${ohlc_fails} OHLC failures, ${signal_count:-0} signal writes, ${total_processed} stocks"
    elif [ "$signal_count" -gt 0 ]; then
        record pass "$dim" "stock_agent" "last ${last_ts}, ${signal_count:-0} signal writes, 0 OHLC failures"
    else
        record warn "$dim" "stock_agent" "last ${last_ts}, 0 signal writes (may be normal — no signals generated)"
    fi

    # 3b. enrich_hot_events cron
    local enrich_log="${DATA_DIR}/logs/enrich_hot_events.log"
    if [ ! -f "$enrich_log" ]; then
        enrich_log=$(ls -t "${DATA_DIR}"/logs/enrich_hot_events_*.log 2>/dev/null | head -1 || true)
    fi
    if [ -f "$enrich_log" ]; then
        local enrich_ts
        enrich_ts=$(tail -20 "$enrich_log" 2>/dev/null | grep -oP '\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}' | tail -1 || echo "")
        if [ -n "$enrich_ts" ]; then
            record pass "$dim" "enrich_hot_events" "last ${enrich_ts}"
        else
            record warn "$dim" "enrich_hot_events" "log exists but no timestamp found"
        fi
    else
        record warn "$dim" "enrich_hot_events" "log not found: ${enrich_log}"
    fi
}

# ════════════════════════════════════════════════
# Dimension 4: System Resources
# ════════════════════════════════════════════════
check_system() {
    local dim="system"

    # 4a. Disk
    local disk_pct disk_line
    # 优先检查 /opt，退而求 /
    if df -h /opt >/dev/null 2>&1; then
        disk_line=$(df -h /opt | tail -1)
    else
        disk_line=$(df -h / | tail -1)
    fi
    disk_pct=$(echo "$disk_line" | awk '{print $5}' | tr -d '%')
    if [ "$disk_pct" -lt "$DISK_WARN_PCT" ]; then
        record pass "$dim" "disk" "${disk_pct}% used"
    elif [ "$disk_pct" -lt "$DISK_CRIT_PCT" ]; then
        record warn "$dim" "disk" "${disk_pct}% used (>${DISK_WARN_PCT}%)"
    else
        record fail "$dim" "disk" "${disk_pct}% used (>${DISK_CRIT_PCT}%)"
    fi

    # 4b. Memory (Linux only; macOS will skip gracefully)
    if command -v free >/dev/null 2>&1; then
        local mem_avail
        mem_avail=$(free -m 2>/dev/null | awk '/Mem:/{print $7}')
        if [ -n "$mem_avail" ]; then
            if [ "$mem_avail" -gt "$MEM_WARN_MB" ]; then
                record pass "$dim" "memory" "${mem_avail}MB available"
            elif [ "$mem_avail" -gt "$MEM_CRIT_MB" ]; then
                record warn "$dim" "memory" "${mem_avail}MB available (<${MEM_WARN_MB}MB)"
            else
                record fail "$dim" "memory" "${mem_avail}MB available (<${MEM_CRIT_MB}MB)"
            fi
        else
            record pass "$dim" "memory" "free available but no Mem: line? skipping"
        fi
    else
        record pass "$dim" "memory" "free cmd not available — skipping (non-Linux?)"
    fi
}

# ════════════════════════════════════════════════
# Dimension 5: 信息管道（RSS + Enrich）
# ════════════════════════════════════════════════
check_pipeline() {
    local dim="pipeline"

    # 5a. wewe-rss 进程
    if pm2 list 2>/dev/null | grep -q "wewe-rss.*online"; then
        record pass "$dim" "wewe-rss-process" "pm2 online"
    else
        record warn "$dim" "wewe-rss-process" "not running in pm2"
    fi

    # 5b. wewe-rss DB 文章新鲜度
    local wewe_db="/opt/wewe-rss-deploy/data/wewe-rss.db"
    if [ -f "$wewe_db" ]; then
        local latest_ts
        latest_ts=$(sqlite3 "$wewe_db" "SELECT MAX(publish_time) FROM articles;" 2>/dev/null || echo "0")
        if [ -n "$latest_ts" ] && [ "$latest_ts" -gt 0 ]; then
            local now_ts age_h
            now_ts=$(date +%s)
            age_h=$(( (now_ts - latest_ts) / 3600 ))
            if [ "$age_h" -le 6 ]; then
                record pass "$dim" "wewe-rss-articles" "最新文章 ${age_h}h 前"
            elif [ "$age_h" -le 24 ]; then
                record warn "$dim" "wewe-rss-articles" "最新文章 ${age_h}h 前（>6h，可能漏更新）"
            else
                record fail "$dim" "wewe-rss-articles" "最新文章 ${age_h}h 前（>24h，RSS 管道可能中断）"
            fi
            local feed_count article_count
            feed_count=$(sqlite3 "$wewe_db" "SELECT COUNT(*) FROM feeds WHERE status=1;" 2>/dev/null || echo "?")
            article_count=$(sqlite3 "$wewe_db" "SELECT COUNT(*) FROM articles;" 2>/dev/null || echo "?")
            record pass "$dim" "wewe-rss-feeds" "${feed_count} 个订阅源 · ${article_count} 篇文章"
        else
            record warn "$dim" "wewe-rss-articles" "DB 存在但无文章记录"
        fi
    else
        record fail "$dim" "wewe-rss-db" "DB 不存在: ${wewe_db}"
    fi

    # 5c. hot_enrichment.json 新鲜度
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local enrich_file="${script_dir}/../data/hot_enrichment.json"
    if [ -f "$enrich_file" ]; then
        local age_h
        age_h=$(file_age_hours "$enrich_file")
        local count
        count=$(python3 -c "import json; d=json.load(open('${enrich_file}')); print(len(d))" 2>/dev/null || echo "?")
        if [ "$age_h" -le 12 ]; then
            record pass "$dim" "hot-enrichment" "${count} 条缓存，${age_h}h 前更新"
        else
            record warn "$dim" "hot-enrichment" "${count} 条缓存，${age_h}h 前更新（建议重跑 enrich_hot_events.py）"
        fi
    else
        record warn "$dim" "hot-enrichment" "文件不存在，热点事件无 AI 解读"
    fi
}

# ════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════

if [ "$OUTPUT_FORMAT" = "bare" ]; then
    # bare mode: 先静默跑一轮取计数
    OUTPUT_FORMAT=json
    check_processes
    check_data_freshness
    check_cron
    check_system
    check_pipeline
    OUTPUT_FORMAT=bare
    if [ "$FAIL" -gt 0 ]; then
        echo "CRITICAL fail=${FAIL} warn=${WARN} pass=${PASS}"
        exit 1
    elif [ "$WARN" -gt 0 ]; then
        echo "DEGRADED fail=${FAIL} warn=${WARN} pass=${PASS}"
        exit 0
    else
        echo "HEALTHY fail=${FAIL} warn=${WARN} pass=${PASS}"
        exit 0
    fi
fi

# 头
if [ "$OUTPUT_FORMAT" = "text" ]; then
    echo "━━━ CycleRadar Health Check ━━━"
    echo "Time: ${NOW_LOCAL}"
    echo "Data: ${DATA_DIR}"
    echo ""
fi

check_processes
echo "" 2>/dev/null || true
[ "$OUTPUT_FORMAT" = "text" ] && echo ""
check_data_freshness
[ "$OUTPUT_FORMAT" = "text" ] && echo ""
check_cron
[ "$OUTPUT_FORMAT" = "text" ] && echo ""
check_system
[ "$OUTPUT_FORMAT" = "text" ] && echo ""
check_pipeline

# 汇总
if [ "$OUTPUT_FORMAT" = "json" ]; then
    json_results=""
    for r in "${RESULTS[@]}"; do
        [ -n "$json_results" ] && json_results+=","
        json_results+="$r"
    done
    cat <<JSONEOF
{
  "check_id": "${STAMP}",
  "timestamp": "${NOW_ISO}",
  "data_dir": "${DATA_DIR}",
  "summary": {"pass": ${PASS}, "warn": ${WARN}, "fail": ${FAIL}},
  "status": "$([ "$FAIL" -gt 0 ] && echo "critical" || ([ "$WARN" -gt 0 ] && echo "degraded") || echo "healthy")",
  "checks": [${json_results}]
}
JSONEOF
else
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━"
    printf "  TOTAL:  pass=%d  warn=%d  fail=%d\n" "$PASS" "$WARN" "$FAIL"
    if [ "$FAIL" -gt 0 ]; then
        echo -e "  STATUS: ${RED}❌ CRITICAL${NC} — ${FAIL} failure(s)"
        exit 1
    elif [ "$WARN" -gt 0 ]; then
        echo -e "  STATUS: ${YELLOW}⚠ DEGRADED${NC} — ${WARN} warning(s)"
        exit 0
    else
        echo -e "  STATUS: ${GREEN}✅ HEALTHY${NC}"
        exit 0
    fi
fi
