#!/bin/bash
# 交易员 — 每日主流程
# launchd 于 08:45 触发，执行完整日报 + 策略 + 跟踪后 git push 到服务器
#
# 三阶段流水线：
#   1. trader.py --strategy   → 择时+扫描+信号+账户+诊断+策略 HTML
#   2. tracker.py track       → 5/10/20 天前向跟踪反思
#   3. git push gh-pages      → 推产物到 GitHub，服务器 pull

set -uo pipefail
cd "$(dirname "$0")/.."

# ── 环境 ──────────────────────────────────────────────────
[ -f .env ] && source .env
[ -f ~/.config/cycleradar/.env ] && source ~/.config/cycleradar/.env

DATE=$(date +%Y-%m-%d)
LOG_DIR="output/logs"
mkdir -p "$LOG_DIR" "output/daily" "output/strategy" "output/tracker"
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

# ── 1. 运行主程序（择时+扫描+信号+账户+诊断+策略 HTML）───
python3 trader.py --date "$DATE" --strategy 2>&1 | tee -a "$LOG"
EXIT_CODE=${PIPESTATUS[0]}

if [ $EXIT_CODE -ne 0 ]; then
    echo "[$(date '+%H:%M:%S')] ⚠ trader.py 退出码 $EXIT_CODE" | tee -a "$LOG"
fi

# ── 2. 生成 latest.html 固定链接 ─────────────────────────
for section in daily strategy; do
    HTML="output/${section}/trader_${DATE}.html"
    if [ -f "$HTML" ]; then
        cp "$HTML" "output/${section}/latest.html"
        echo "[$(date '+%H:%M:%S')] ${section}/latest.html 已更新" | tee -a "$LOG"
    fi
done

# ── 3. 生成目录索引 ──────────────────────────────────────
python3 ops/gen_index.py 2>&1 | tee -a "$LOG"

# ── 4. 信号跟踪反思（5/10/20 天前向绩效）────────────
echo "[$(date '+%H:%M:%S')] 信号跟踪..." | tee -a "$LOG"
python3 modules/tracker.py track --date "$DATE" 2>&1 | tee -a "$LOG"
TRACKER_EXIT=${PIPESTATUS[0]}
if [ -f "output/tracker/latest.html" ]; then
    echo "[$(date '+%H:%M:%S')] tracker/latest.html 已更新" | tee -a "$LOG"
elif [ $TRACKER_EXIT -ne 0 ]; then
    echo "[$(date '+%H:%M:%S')] ⚠ tracker 退出码 $TRACKER_EXIT" | tee -a "$LOG"
fi

# ── 5. git push → gh-pages ─────────────────────────────
echo "[$(date '+%H:%M:%S')] git push gh-pages..." | tee -a "$LOG"

cd output

# 确保 output 是 gh-pages 分支的独立克隆
if [ ! -d ".git" ]; then
    echo "[$(date '+%H:%M:%S')] ⚠ output/ 不是 git 仓库，跳过 git push" | tee -a "$LOG"
    echo "[$(date '+%H:%M:%S')] ====== 完成 ======" | tee -a "$LOG"
    exit 0
fi

# 检查当前分支
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
if [ "$BRANCH" != "gh-pages" ]; then
    echo "[$(date '+%H:%M:%S')] ⚠ 当前分支是 $BRANCH 不是 gh-pages，跳过 git push" | tee -a "$LOG"
    cd ..
    echo "[$(date '+%H:%M:%S')] ====== 完成 ======" | tee -a "$LOG"
    exit 0
fi

git add -A
git diff --cached --stat | tee -a "$LOG"
git commit -m "auto: $DATE 日报+策略+跟踪" 2>&1 | tee -a "$LOG" || echo "  (无变更或提交失败)" | tee -a "$LOG"

# git push 到 GitHub（重试 3 次）
GIT_PUSH_OK=0
for attempt in 1 2 3; do
    git push origin gh-pages 2>&1 | tee -a "$LOG"
    GIT_CODE=${PIPESTATUS[0]}
    if [ $GIT_CODE -eq 0 ]; then
        echo "[$(date '+%H:%M:%S')] ✅ git push 成功 (尝试 $attempt/3)" | tee -a "$LOG"
        GIT_PUSH_OK=1
        break
    fi
    if [ $attempt -lt 3 ]; then
        echo "[$(date '+%H:%M:%S')] ⚠ git push 失败，$((attempt*10))s 后重试..." | tee -a "$LOG"
        sleep $((attempt * 10))
    fi
done

if [ $GIT_PUSH_OK -eq 0 ]; then
    echo "[$(date '+%H:%M:%S')] ⚠ git push 全部失败，本地报告仍可用" | tee -a "$LOG"
fi

cd ..

# ── 6. 服务器 pull ─────────────────────────────────────
echo "[$(date '+%H:%M:%S')] 服务器 pull..." | tee -a "$LOG"
ssh root@139.196.115.64 '
    cd /opt/trader/output && \
    git pull origin gh-pages --ff-only
' 2>&1 | tee -a "$LOG"
SERVER_CODE=${PIPESTATUS[0]}
if [ $SERVER_CODE -eq 0 ]; then
    echo "[$(date '+%H:%M:%S')] ✅ 服务器 pull 成功" | tee -a "$LOG"
else
    echo "[$(date '+%H:%M:%S')] ⚠ 服务器 pull 失败 (exit $SERVER_CODE)" | tee -a "$LOG"
fi

echo "[$(date '+%H:%M:%S')] ====== 完成 ======" | tee -a "$LOG"
