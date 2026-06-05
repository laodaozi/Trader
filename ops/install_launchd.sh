#!/bin/bash
# 安装 launchd 定时任务 — 每日 08:45 自动运行交易员日报
# 用法：bash ops/install_launchd.sh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_SRC="$PROJECT_DIR/ops/launchd/com.trader.daily.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.trader.daily.plist"
LABEL="com.trader.daily"

echo "📂 项目目录: $PROJECT_DIR"
echo "📋 安装到: $PLIST_DST"

mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$PROJECT_DIR/output/logs"

# 替换占位符，生成最终 plist
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$PLIST_SRC" > "$PLIST_DST"

# 卸载旧版本（如存在）
launchctl unload "$PLIST_DST" 2>/dev/null || true

# 加载新版本
launchctl load "$PLIST_DST"

echo ""
echo "✅ $LABEL 已安装"
echo "   触发时间：每日 08:45"
echo "   日志目录：$PROJECT_DIR/output/logs/"
echo ""
echo "常用命令："
echo "  launchctl list | grep trader        # 查看状态"
echo "  launchctl unload $PLIST_DST         # 停用"
echo "  launchctl start $LABEL              # 立即触发一次"
