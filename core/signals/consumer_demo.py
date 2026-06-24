"""
consumer_demo.py — 信号消费者演示：读取活跃信号并生成 Markdown 汇总

演示 downstream_signals.jsonl → read_active_signals() → 结构化汇总 的完整链路。
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from upstream_signals import read_active_signals, read_latest_signals

def generate_summary():
    """生成当前活跃信号汇总 Markdown。"""
    active = read_active_signals()
    active.sort(key=lambda s: s.get("confidence", 0), reverse=True)

    lines = [
        f"# 周期雷达 · 活跃信号汇总",
        f"",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 活跃信号总数：{len(active)}",
        f"",
        "| 策略 | 标的 | 方向 | 置信度 | 有效期至 |",
        "|------|------|------|--------|----------|",
    ]

    for sig in active:
        strategy = sig.get("strategy", "?")
        asset = sig.get("asset", "?")
        direction = {"long": "📈 多", "short": "📉 空", "neutral": "➖ 中性"}.get(sig.get("direction"), "?")
        confidence = f"{sig.get('confidence', 0):.0%}"
        expiry = sig.get("expiry", "?")[:10] if sig.get("expiry") else "?"
        lines.append(f"| {strategy} | {asset} | {direction} | {confidence} | {expiry} |")

    lines.extend([
        "",
        "---",
        "",
        "## 按策略分组",
        "",
    ])

    by_strategy: dict[str, list] = {}
    for sig in active:
        s = sig.get("strategy", "unknown")
        by_strategy.setdefault(s, []).append(sig)

    for strategy, sigs in sorted(by_strategy.items()):
        lines.append(f"### {strategy}（{len(sigs)} 条）")
        lines.append("")
        for sig in sigs:
            asset = sig.get("asset", "?")
            direction = sig.get("direction", "?")
            confidence = sig.get("confidence", 0)
            meta = sig.get("metadata", {})
            extra = ""
            if strategy == "stock_agent":
                extra = f" — {meta.get('tier', '')} | 催化分 {meta.get('catalyst_score', '?')}"
            elif strategy == "ma_signals":
                extra = f" — {meta.get('notice_type', '')} | 强度 {meta.get('strength', '?')}"
            elif strategy == "rotation_factor":
                extra = f" — {meta.get('stage', '')} | 得分 {meta.get('score_auto', '?')}"
            lines.append(f"- {asset} **{direction}** ({confidence:.0%}){extra}")
        lines.append("")

    lines.extend([
        "---",
        "",
        "> ⚠️ 仅供参考，不构成投资建议。",
    ])

    return "\n".join(lines)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="信号消费者演示")
    parser.add_argument("--output", "-o", help="输出 Markdown 文件路径")
    parser.add_argument("--print", "-p", action="store_true", help="打印到终端")
    parser.add_argument("--seed", action="store_true", help="先写入 3 条样例信号用于演示")
    args = parser.parse_args()

    if args.seed:
        print("写入 3 条样例信号...")
        from upstream_signals import write_signal
        import uuid
        now = datetime.now().astimezone()
        from datetime import timedelta

        samples = [
            {
                "signal_id": str(uuid.uuid4()),
                "timestamp": now.isoformat(),
                "strategy": "stock_agent",
                "asset": "600019",
                "asset_type": "stock",
                "direction": "long",
                "confidence": 0.85,
                "expiry": (now + timedelta(days=7)).isoformat(),
                "metadata": {"stock_name": "宝钢股份", "tier": "强推", "catalyst_score": 7.5, "resonance_score": 3, "reasons": ["行业共振", "PE低位"], "industry": "钢铁"},
            },
            {
                "signal_id": str(uuid.uuid4()),
                "timestamp": now.isoformat(),
                "strategy": "ma_signals",
                "asset": "601899",
                "asset_type": "stock",
                "direction": "long",
                "confidence": 0.85,
                "expiry": (now + timedelta(days=14)).isoformat(),
                "metadata": {"stock_name": "紫金矿业", "notice_type": "收购出售资产", "strength": "high", "industry_hint": "有色金属", "industry_count": 5},
            },
            {
                "signal_id": str(uuid.uuid4()),
                "timestamp": now.isoformat(),
                "strategy": "rotation_factor",
                "asset": "通信",
                "asset_type": "sector",
                "direction": "long",
                "confidence": 0.72,
                "expiry": (now + timedelta(days=5)).isoformat(),
                "metadata": {"score_auto": 6.2, "stage": "确认", "rank": 3, "active_factors": ["A1", "B1", "E1"], "intensity": 45.0},
            },
        ]
        for s in samples:
            write_signal(s)
            print(f"  ✓ {s['strategy']} {s['asset']} {s['direction']}")

    summary = generate_summary()

    if args.print or not args.output:
        print(summary)

    if args.output:
        Path(args.output).write_text(summary, encoding="utf-8")
        print(f"\n✓ 汇总已写入 {args.output}")

if __name__ == "__main__":
    main()
