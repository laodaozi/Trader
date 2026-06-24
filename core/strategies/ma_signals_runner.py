#!/usr/bin/env python3
"""
ma_signals_runner.py — ECS 专用 兼并重组信号调度器

用法：
  cd /opt/cycleradar-trader/core && PYTHONPATH=/opt/cycleradar-trader/core:/opt/cycleradar-trader/core/signals python3 strategies/ma_signals_runner.py --date 2026-06-18
  cd /opt/cycleradar-trader/core && PYTHONPATH=/opt/cycleradar-trader/core:/opt/cycleradar-trader/core/signals python3 strategies/ma_signals_runner.py --date 2026-06-18 --dry-run

流程：
  1. collect_ma_signals(date) 从 AKShare 拉取当日 M&A 公告
  2. 转为标准信号（signal_id / timestamp / strategy / asset / direction / confidence / expiry / metadata）
  3. write_signal() 写入 upstream_signals.jsonl（去重）
  4. （可选）generate_ma_article(date) 生成兼并重组分析文章

信源：AKShare > stock_notice_report（资产重组 + 重大事项，按 MA_RELEVANT_TYPES 白名单过滤）
"""
from __future__ import annotations

import argparse
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

# ── 路径 ─────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
CORE_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(CORE_DIR))

# ── 加载密钥 ──────────────────────────────────────────
try:
    from dotenv import load_dotenv
    # 优先从集中式 .env（/opt/cycleradar-trader/.env）加载
    env_path = CORE_DIR.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv(CORE_DIR / ".env")
except ImportError:
    pass

from ma_signals import collect_ma_signals
from signals.upstream_signals import write_signal


# ── 信号强度映射 ──────────────────────────────────────
STRENGTH_TO_CONFIDENCE = {
    "high": 0.85,
    "medium": 0.65,
    "low": 0.50,
}
STRENGTH_TO_TIER = {
    "high": "S",
    "medium": "A",
    "low": "B",
}


def build_signals(ma_data: dict, date_str: str) -> list[dict]:
    """将 collect_ma_signals() 产出转为标准信号列表。"""
    announcements = ma_data.get("announcements", [])
    if not announcements:
        return []

    signals = []
    date_compact = date_str.replace("-", "")
    expiry_dt = datetime.fromisoformat(date_str) + timedelta(days=14)
    expiry_str = expiry_dt.isoformat()

    for i, ann in enumerate(announcements, 1):
        stock_code = ann.get("stock_code", "")
        stock_name = ann.get("stock_name", "")
        industry = ann.get("industry_hint", "其他")
        notice_type = ann.get("notice_type", "")

        # 信号强度 = 所属行业强度
        ind_sig = ma_data.get("by_industry", {}).get(industry, {})
        strength = ind_sig.get("strength", "low")
        confidence = STRENGTH_TO_CONFIDENCE.get(strength, 0.50)
        tier = STRENGTH_TO_TIER.get(strength, "B")

        signal = {
            "signal_id": f"ma_signals-{date_compact}-{i:03d}",
            "timestamp": datetime.now().isoformat(),
            "strategy": "ma_signals",
            "asset": stock_code,
            "asset_type": "stock",
            "direction": "long",
            "confidence": confidence,
            "expiry": expiry_str,
            "metadata": {
                "stock_name": stock_name,
                "tier": tier,
                "reasons": ["并购重组"],
                "notice_type": notice_type,
                "industry_hint": industry,
            },
        }
        signals.append(signal)

    return signals


def main():
    parser = argparse.ArgumentParser(description="ma_signals ECS runner")
    parser.add_argument("--date", required=True, help="分析日期 YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="不写入信号，仅打印")
    parser.add_argument("--article", action="store_true", help="同时生成兼并重组分析文章")
    args = parser.parse_args()

    date_str = args.date
    print(f"\n{'='*60}")
    print(f"  ma_signals ECS Runner — {date_str}")
    print(f"{'='*60}\n")

    # ── Step 1: 采集 M&A 公告 ──────────────────────────
    print("  [1/3] 采集 M&A 公告...")
    ma_data = collect_ma_signals(date_str)
    count = ma_data.get("count", 0)

    if count == 0:
        print("  → 当日无 M&A 公告数据")
        return

    print(f"  → {count} 条公告, {len(ma_data.get('by_industry', {}))} 个行业")
    print(f"    摘要: {ma_data.get('summary', '无')[:100]}")

    # ── Step 2: 构建信号 ───────────────────────────────
    print(f"\n  [2/3] 构建信号...")
    signals = build_signals(ma_data, date_str)

    if not signals:
        print("  → 无信号产出")
        return

    print(f"  → {len(signals)} 条信号:")
    for s in signals:
        tier = s["metadata"]["tier"]
        name = s["metadata"]["stock_name"]
        notice_type = s["metadata"]["notice_type"]
        conf = s["confidence"]
        print(f"    [{tier}] {s['asset']} {name} | {notice_type} | confidence={conf}")

    # ── Step 3: 写入信号 ───────────────────────────────
    if args.dry_run:
        print(f"\n  [dry-run] 跳过 {len(signals)} 条信号写入")
    else:
        print(f"\n  [3/3] 写入 upstream_signals.jsonl...")
        written = 0
        for sig in signals:
            try:
                write_signal(sig)
                written += 1
            except Exception as e:
                print(f"    ⚠ 写入失败: {sig['signal_id']} — {e}")
        print(f"  ✅ 写入 {written} 条信号")

    # ── Step 4: 可选生成文章 ───────────────────────────
    if args.article and not args.dry_run:
        print(f"\n  ── 生成兼并重组分析文章 ──")
        try:
            from writing.pipeline import generate_ma_article
            article = generate_ma_article(date_str, ma_data=ma_data)
            if article:
                # 保存到 output/article/
                import os
                article_dir = os.path.join(
                    os.environ.get("CYCLERADAR_DATA_DIR", "/opt/cycleradar-trader/data"),
                    "..", "output", "article"
                )
                os.makedirs(article_dir, exist_ok=True)
                filename = f"ma_{date_str.replace('-', '')}.html"
                filepath = os.path.join(article_dir, filename)
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(article.html)
                print(f"  ✅ 文章已保存: {filepath} ({article.word_count}字)")
            else:
                print(f"  ⚠ 文章生成返回 None（可能当日数据不足或 API 失败）")
        except Exception as e:
            print(f"  ❌ 文章生成异常: {e}")


if __name__ == "__main__":
    main()
