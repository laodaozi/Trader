#!/bin/bash
# sync-db.sh — 从 ECS 拉取 WEWE RSS 生产 DB 到本地 admin-panel
# 用法: ./sync-db.sh
# 依赖: ssh + scp 已配置免密登录到 ECS

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="$PROJECT_DIR/data"

REMOTE_HOST="${WEWE_ECS_HOST:-139.196.115.64}"
REMOTE_DB="${WEWE_REMOTE_PATH:-/opt/wewe-rss-deploy/data/wewe-rss.db}"
LOCAL_DB="$DATA_DIR/wewe-rss.db"
BACKUP_DIR="$DATA_DIR/backups"

# -------------------------------------------------------------------------
# 前置检查
# -------------------------------------------------------------------------
if ! command -v ssh &>/dev/null; then
  echo "[ERROR] ssh 未安装"
  exit 1
fi

if ! command -v scp &>/dev/null; then
  echo "[ERROR] scp 未安装"
  exit 1
fi

# 非交互式 SSH 可用性检查
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$REMOTE_HOST" "test -f $REMOTE_DB" 2>/dev/null; then
  echo "[ERROR] 无法免密连接到 $REMOTE_HOST，或远程 DB 不存在"
  echo "[HINT] 请确保: ssh $REMOTE_HOST 免密可用，且 $REMOTE_DB 存在"
  exit 1
fi

# -------------------------------------------------------------------------
# 本地准备
# -------------------------------------------------------------------------
mkdir -p "$DATA_DIR" "$BACKUP_DIR"

# 创建 .gitignore（如果不存在）
if [ ! -f "$DATA_DIR/.gitignore" ]; then
  cat > "$DATA_DIR/.gitignore" <<'EOF'
# 二进制数据库文件不纳入 Git
*.db
*.db-journal
*.db-wal
backups/
EOF
  echo "[INFO] 已创建 $DATA_DIR/.gitignore"
fi

# -------------------------------------------------------------------------
# 增量检查（跳过无变化的同步）
# -------------------------------------------------------------------------
REMOTE_SIZE=$(ssh "$REMOTE_HOST" "stat -c%s $REMOTE_DB" 2>/dev/null || echo 0)
if [ -f "$LOCAL_DB" ]; then
  LOCAL_SIZE=$(stat -f%z "$LOCAL_DB" 2>/dev/null || stat -c%s "$LOCAL_DB" 2>/dev/null || echo 0)
  if [ "$REMOTE_SIZE" = "$LOCAL_SIZE" ]; then
    echo "[SKIP] 本地 DB 与远程大小一致 (${REMOTE_SIZE} bytes)，无需同步"
    exit 0
  fi
fi

REMOTE_HASH=$(ssh "$REMOTE_HOST" "sha256sum $REMOTE_DB | cut -d' ' -f1" 2>/dev/null || echo "")
if [ -f "$LOCAL_DB" ] && [ -n "$REMOTE_HASH" ]; then
  LOCAL_HASH=$(shasum -a 256 "$LOCAL_DB" | cut -d' ' -f1)
  if [ "$REMOTE_HASH" = "$LOCAL_HASH" ]; then
    echo "[SKIP] 本地 DB 与远程 SHA256 一致，无需同步"
    exit 0
  fi
fi

# -------------------------------------------------------------------------
# 备份 + 同步
# -------------------------------------------------------------------------
if [ -f "$LOCAL_DB" ]; then
  BACKUP_NAME="wewe-rss-$(date +%Y%m%d-%H%M%S).db"
  cp "$LOCAL_DB" "$BACKUP_DIR/$BACKUP_NAME"
  echo "[BACKUP] 已备份 → $BACKUP_DIR/$BACKUP_NAME"
fi

echo "[SYNC] 正在从 $REMOTE_HOST 拉取..."
scp -q "$REMOTE_HOST:$REMOTE_DB" "$LOCAL_DB"

if [ $? -eq 0 ]; then
  NEW_SIZE=$(stat -f%z "$LOCAL_DB" 2>/dev/null || stat -c%s "$LOCAL_DB" 2>/dev/null || echo "?")
  echo "[OK] 同步完成 → $LOCAL_DB (${NEW_SIZE} bytes)"

  # 统计信息
  if command -v sqlite3 &>/dev/null; then
    echo "----------------------------------------"
    echo "  订阅号: $(sqlite3 "$LOCAL_DB" 'SELECT COUNT(*) FROM feeds')"
    echo "  文章数: $(sqlite3 "$LOCAL_DB" 'SELECT COUNT(*) FROM articles')"
    echo "  账号  : $(sqlite3 "$LOCAL_DB" 'SELECT COUNT(*) FROM accounts')"
    echo "----------------------------------------"
  fi
else
  echo "[FAIL] 同步失败！请检查网络和 SSH 连接"
  exit 1
fi
