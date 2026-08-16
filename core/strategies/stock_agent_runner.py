#!/usr/bin/env python3
"""
stock_agent_runner.py — ECS 专用 stock_agent 调度器

用法：
  cd /opt/cycleradar && PYTHONPATH=/opt/cycleradar python3 stock_agent_runner.py --date 2026-06-09
  cd /opt/cycleradar && PYTHONPATH=/opt/cycleradar python3 stock_agent_runner.py --date 2026-06-09 --dry-run

流程：
  1. score.py --scan-all 获取 TOP10 行业 + 排名
  2. stock_agent.build_stock_pool() 构建票池并分析
  3. stock_agent_adapter.emit_signals() 写入 upstream_signals.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# ── 路径 ─────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

# ── 标准库依赖 ────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(SCRIPT_DIR / ".env")

import score
from stock_agent import INDUSTRY_LEADERS, build_smart_pool, build_stock_pool, score_multi_catalyst, _catalyst_reasons
from stock_agent_adapter import emit_signals

# ── V7.8: 五维技术分析（MA/斐波/NX），ECS MCP 已验证可用 ──
try:
    import sys as _sys
    # 需要项目根 /opt/cycleradar-trader 在 sys.path，因为 import 路径是 core.strategy_exec
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from core.strategy_exec import analyze_one as _analyze_one
    _FIVE_DIM_AVAILABLE = True
except Exception as _e:
    _FIVE_DIM_AVAILABLE = False
    print(f"  ⚠ five-dim import failed: {_e}")


def get_scan_top10(date_str: str) -> list[dict]:
    """从 score.py scan 结果读取 TOP10 行业。"""
    scan_path = score.RAW_DIR / f"{date_str}_scan.json"
    if not scan_path.exists():
        print(f"  ⚠ scan 文件不存在: {scan_path}")
        return []
    with open(scan_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("rankings", [])[:10]


def main():
    parser = argparse.ArgumentParser(description="stock_agent ECS runner")
    parser.add_argument("--date", required=True, help="分析日期 YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="不写入信号，仅打印")
    args = parser.parse_args()

    date_str = args.date
    print(f"\n{'='*60}")
    print(f"  stock_agent ECS Runner — {date_str}")
    print(f"{'='*60}\n")

    # ── Step 1: 获取 scan_top10 ───────────────────────
    scan_top10 = get_scan_top10(date_str)
    if not scan_top10:
        print("  → scan_top10 为空，尝试运行 score.py --scan-all ...")
        sys.exit(1)

    top_industries = [r["name"] for r in scan_top10[:10]]
    print(f"  TOP10 行业: {', '.join(top_industries)}")

    # ── Step 2: 构建票池 ─────────────────────────────
    print(f"\n  ── 构建票池 ──")
    
    # 使用 build_stock_pool（基础版），避免 SMART_POOL 对 inst_data/hot_top30 的依赖
    result = build_stock_pool(
        date_str=date_str,
        top_industries=top_industries,
        scan_top10=scan_top10,
        wechat_sources={},  # ECS 无微信信源
        from_cache=False,
    )

    stock_data = result.get("stock_data", {})
    stats = result.get("stats", {})
    print(f"  龙头股: {stats.get('leaders', 0)} 只")
    print(f"  微信票源: {stats.get('wechat_new', 0)} 只")
    print(f"  热度兜底: {stats.get('hot_fallback', 0)} 只")
    print(f"  总票池: {len(stock_data)} 只")

    # ── Step 3: 选股 ─────────────────────────────────
    top10_names = {r.get("name") for r in scan_top10[:10]}
    code_to_industry = {}
    for industry, codes in INDUSTRY_LEADERS.items():
        for stock_code in codes:
            code_to_industry[stock_code] = industry

    picks = []
    for code, info in stock_data.items():
        industry = info.get("industry") or code_to_industry.get(code, "")
        catalyst = score_multi_catalyst(info, {
            "inst_net": 0,
            "in_top10_industry": industry in top10_names if industry else False,
            "cap_tier": info.get("cap_tier", "中盘"),
        })
        info["catalyst_score"] = catalyst["total_score"]
        info["catalyst_tier"] = catalyst["tier"]
        info["catalyst_breakdown"] = catalyst["breakdown"]
        info["reasons"] = _catalyst_reasons(catalyst["breakdown"])
        info["resonance_score"] = catalyst["breakdown"].get("resonance", 0)  # 15 if in_top10_industry

        # Phase 3: 从 OHLC 收盘价 + NX 弹性推导 entry/target/stop
        close_price = info.get("close_price")
        if close_price and isinstance(close_price, (int, float)) and close_price > 0:
            nx = info.get("nx", {})
            elasticity = nx.get("elasticity_20d", 3.0) / 100.0  # 日均振幅，转小数
            nx_sig = nx.get("nx_signal", "neutral")
            info["entry_price"] = close_price
            if nx_sig == "buy":
                info["target_price"] = round(close_price * (1 + elasticity * 3), 2)
                info["stop_loss"] = round(close_price * 0.92, 2)   # -8%
            else:
                info["target_price"] = round(close_price * (1 + elasticity * 2), 2)
                info["stop_loss"] = round(close_price * 0.95, 2)   # -5%

        tier = catalyst["tier"]
        catalyst_score = catalyst["total_score"]
        name = info.get("name") or info.get("stock_name") or code

        picks.append({
            "date": date_str,
            "code": code,
            "name": name,
            "tier": tier,
            "catalyst_score": catalyst_score,
            "resonance_score": info.get("resonance_score", 0),
            "reasons": info.get("reasons", []),
            "entry_price": info.get("entry_price"),
            "target_price": info.get("target_price"),
            "stop_loss": info.get("stop_loss"),
            "industry": industry,
            "catalyst_breakdown": catalyst["breakdown"],
            # 五维占位，analyze_one 在排序后补全
            "nx": "neutral",
            "ma_align": "unknown",
            "fib_zone": "unknown",
            "weekly_dir": industry,
            "capital_dir": "中性",
            "rr": None,
            "model_hits": [],
            "signal_type": "✅ 买入",
        })

    # 按 catalyst_score 降序排序，取前 15
    picks.sort(key=lambda p: -p["catalyst_score"])
    picks = picks[:15]

    if not picks:
        print("\n  ⚠ 无选股信号产出")
        return

    # ── V7.8: 五维技术分析补全（MA/斐波/NX）────────────────
    if _FIVE_DIM_AVAILABLE:
        print(f"\n  ── 五维技术分析（MA/斐波/NX） ──")
        for p in picks:
            try:
                tech = _analyze_one(p["code"], p["name"], date_str)
                p["nx"]         = tech.get("nx", "neutral")
                p["ma_align"]   = tech.get("ma_align", "unknown")
                p["fib_zone"]   = tech.get("fib_zone", "unknown")
                p["weekly_dir"] = tech.get("weekly_dir") or p.get("industry", "")
                p["capital_dir"]= tech.get("capital_dir", "中性")
                p["rr"]         = tech.get("rr")
                p["model_hits"] = tech.get("model_hits", [])
                p["signal_type"]= tech.get("signal_type", "✅ 买入")
                # 用 analyze_one 的更精准 entry/stop/target 覆盖
                if tech.get("entry_low") and tech["entry_low"] > 0:
                    p["entry_price"]  = tech["entry_low"]
                    p["target_price"] = tech["take_profit"][0] if tech.get("take_profit") else p.get("target_price")
                    p["stop_loss"]    = tech["stop_loss"]
                print(f"    {p['code']} nx={p['nx']} ma={p['ma_align']} fib={p['fib_zone']}")
            except Exception as _e:
                print(f"    {p['code']} five-dim error: {_e}")
                p.setdefault("nx", "neutral")
                p.setdefault("ma_align", "unknown")
                p.setdefault("fib_zone", "unknown")
                p.setdefault("weekly_dir", p.get("industry", ""))
                p.setdefault("capital_dir", "中性")
                p.setdefault("rr", None)
                p.setdefault("model_hits", [])
                p.setdefault("signal_type", "✅ 买入")
    else:
        for p in picks:
            p.setdefault("nx", "neutral")
            p.setdefault("ma_align", "unknown")
            p.setdefault("fib_zone", "unknown")
            p.setdefault("weekly_dir", p.get("industry", ""))
            p.setdefault("capital_dir", "中性")
            p.setdefault("rr", None)
            p.setdefault("model_hits", [])
            p.setdefault("signal_type", "✅ 买入")

    print(f"\n  ── 信号产出: {len(picks)} 条 ──")
    for i, p in enumerate(picks[:10]):
        reasons_str = ' | '.join(p['reasons'][:2])
        entry = p.get("entry_price", "-")
        target = p.get("target_price", "-")
        price_str = f"  entry={entry}" if not isinstance(entry, str) else ""
        print(f"  {i+1}. [{p['tier']}] {p['code']} {p['name']} "
              f"catalyst={p['catalyst_score']:.1f}  "
              f"{reasons_str}{price_str}")

    # ── Step 3.5: V8.0 event_refs 注入 + 当日 code 去重 ──────────
    # (a) 从 event_narrative_latest.json 建立 code→event_rank 索引
    import json as _json, pathlib as _pl
    _nar_path = _pl.Path(__file__).parent.parent.parent / "data" / "event_narrative_latest.json"
    _code_to_event_ranks: dict = {}
    try:
        _nar = _json.loads(_nar_path.read_text(encoding="utf-8"))
        for _ev in _nar.get("events", []):
            _rank = _ev.get("rank", 99)
            for _sm in (_ev.get("stock_mapping") or []):
                _c = str(_sm.get("code", "")).replace("sh", "").replace("sz", "")
                _code_to_event_ranks.setdefault(_c, []).append(_rank)
    except Exception:
        pass
    # (b) 注入 event_refs 字段
    for p in picks:
        _bare = p["code"].replace("sh", "").replace("sz", "").replace(".", "")
        p["event_refs"] = sorted(set(_code_to_event_ranks.get(_bare, [])))

    # (c) 当日同 code 去重：只保留 catalyst_score 最高那条
    _seen_codes: dict = {}
    for p in picks:
        _c = p["code"]
        if _c not in _seen_codes or p["catalyst_score"] > _seen_codes[_c]["catalyst_score"]:
            _seen_codes[_c] = p
    picks_deduped = list(_seen_codes.values())
    picks_deduped.sort(key=lambda p: -p["catalyst_score"])
    _dedup_removed = len(picks) - len(picks_deduped)
    if _dedup_removed:
        print(f"  ℹ️  当日 code 去重: 移除 {_dedup_removed} 条重复（保留最高分）")
    picks = picks_deduped

    # ── Step 4: 写入 signal ──────────────────────────
    if args.dry_run:
        print(f"\n  [dry-run] 跳过 {len(picks)} 条信号写入")
    else:
        n = emit_signals(picks)
        print(f"\n  ✅ 写入 {n} 条信号到 upstream_signals.jsonl")


if __name__ == "__main__":
    main()
