#!/bin/bash
# 交易员 — 每日主流程
# launchd 于 08:45 触发，执行完整日报后 rsync 到服务器

set -uo pipefail
cd "$(dirname "$0")/.."

# ── 环境 ──────────────────────────────────────────────────
[ -f .env ] && source .env
[ -f ~/.config/cycleradar/.env ] && source ~/.config/cycleradar/.env

DATE=$(date +%Y-%m-%d)
LOG_DIR="output/logs"
mkdir -p "$LOG_DIR" "output/daily"
LOG="$LOG_DIR/trader_${DATE}.log"

echo "[$(date '+%H:%M:%S')] ====== 交易员启动 $DATE ======" | tee -a "$LOG"

# ── 0. 等待网络就绪（Mac 睡眠恢复后 DNS 可能还没好）────────
WAIT=0
until nslookup fintool-mcp.finstep.cn >/dev/null 2>&1; do
    WAIT=$((WAIT + 5))
    if [ $WAIT -ge 60 ]; then
        echo "[$(date '+%H:%M:%S')] ⚠ 网络超时，DNS 60s 内未就绪，退出" | tee -a "$LOG"
        exit 1
    fi
    echo "[$(date '+%H:%M:%S')] 等待网络... (${WAIT}s)" | tee -a "$LOG"
    sleep 5
done
echo "[$(date '+%H:%M:%S')] 网络就绪（等待 ${WAIT}s）" | tee -a "$LOG"

# ── 1. 运行主程序 ─────────────────────────────────────────
python3 trader.py --date "$DATE" 2>&1 | tee -a "$LOG"
EXIT_CODE=${PIPESTATUS[0]}

if [ $EXIT_CODE -ne 0 ]; then
    echo "[$(date '+%H:%M:%S')] ⚠ trader.py 退出码 $EXIT_CODE" | tee -a "$LOG"
fi

# ── 2. 生成 latest.html 固定链接 ─────────────────────────
HTML="output/daily/trader_${DATE}.html"
if [ -f "$HTML" ]; then
    cp "$HTML" "output/daily/latest.html"
    echo "[$(date '+%H:%M:%S')] latest.html 已更新" | tee -a "$LOG"
fi

# ── 3. 生成目录索引 ──────────────────────────────────────
python3 ops/gen_index.py 2>&1 | tee -a "$LOG"

# ── 4. rsync 到服务器（重试 3 次）────────────────────
REMOTE="root@139.196.115.64:/opt/trader/output/daily/"
echo "[$(date '+%H:%M:%S')] rsync → $REMOTE" | tee -a "$LOG"
RSYNC_OK=0
for attempt in 1 2 3; do
    rsync -az --delete output/daily/ "$REMOTE" 2>&1 | tee -a "$LOG"
    RSYNC_CODE=${PIPESTATUS[0]}
    if [ $RSYNC_CODE -eq 0 ]; then
        echo "[$(date '+%H:%M:%S')] ✅ 同步成功 (尝试 $attempt/3)" | tee -a "$LOG"
        RSYNC_OK=1
        break
    fi
    if [ $attempt -lt 3 ]; then
        echo "[$(date '+%H:%M:%S')] ⚠ rsync 失败，$((attempt*10))s 后重试..." | tee -a "$LOG"
        sleep $((attempt * 10))
    fi
done
if [ $RSYNC_OK -eq 0 ]; then
    echo "[$(date '+%H:%M:%S')] ⚠ rsync 全部失败，本地报告仍可用" | tee -a "$LOG"
fi

echo "[$(date '+%H:%M:%S')] ====== 完成 ======" | tee -a "$LOG"
