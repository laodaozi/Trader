#!/usr/bin/env python3
"""
update_watchlist_signals.py — 每日预计算自选池信号缓存
- 读取 watchlist.json（含 entry_price）
- 调用 signals.analyze() 逐票分析
- 读取 upstream_signals.jsonl 计算多模型综合评分
- 写入 data/watchlist_signals.json（供 /m/api/watchlist 快读）
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "signals"))
from core.pool_manager import load_pool
from core.signals_nx import analyze
from upstream_signals import read_active_signals

DATA_DIR = Path(__file__).parent.parent.parent / "data"
OUTPUT_FILE = DATA_DIR / "watchlist_signals.json"

# ── 评分权重 ────────────────────────────────────────────────
SCORE_WEIGHTS = {
    "stock_agent": 0.35,      # catalyst_score 0-100
    "wanjun_models": 0.25,    # scanner 共振 × 胜率
    "nx_technical": 0.25,     # NX 买点技术信号
    "report_agent": 0.10,     # 事件驱动 LLM 信号
    "rotation_factor": 0.03,  # 行业轮动共振
    "ma_signals": 0.02,       # 并购重组
}


def compute_pnl(close: float, entry_price: Optional[float]) -> Optional[float]:
    if entry_price and entry_price > 0:
        return round((close - entry_price) / entry_price * 100, 2)
    return None


def _compute_composite_score(code: str, upstream_signals: list[dict], nx_result: dict) -> dict:
    """从上游信号总线 + NX 技术分析计算综合评分。

    Returns:
        {"score": int, "tier": str, "resonance_count": int, "signal_sources": [str]}
    """
    score_parts = {}
    signal_sources = []
    resonance_count = 0

    # 1. stock_agent catalyst_score (0-100)
    sa_signals = [s for s in upstream_signals if s.get("strategy") == "stock_agent" and s.get("asset") == code]
    if sa_signals:
        best = max(sa_signals, key=lambda s: s.get("metadata", {}).get("catalyst_score", 0))
        catalyst = best.get("metadata", {}).get("catalyst_score", 0)
        score_parts["stock_agent"] = catalyst
        signal_sources.append(f"AI选股({catalyst}分)")
        resonance_count += 1

    # 2. scanner (wanjun_models) resonance × calibrated_win_rate
    sc_signals = [s for s in upstream_signals if s.get("strategy") == "wanjun_models" and s.get("asset") == code]
    if sc_signals:
        best = max(sc_signals, key=lambda s: s.get("metadata", {}).get("resonance", 0))
        meta = best.get("metadata", {})
        resonance = meta.get("resonance", 1)
        cwr = meta.get("calibrated_win_rate", 0.5)
        scanner_score = min(100, resonance * cwr * 100)
        score_parts["wanjun_models"] = scanner_score
        signal_sources.append(f"{resonance}模型共振(胜率{cwr:.0%})")
        resonance_count += 1

    # 3. report_agent event-driven
    ra_signals = [s for s in upstream_signals if s.get("strategy") == "report_agent" and s.get("asset") == code]
    if ra_signals:
        best = max(ra_signals, key=lambda s: s.get("confidence", 0))
        event_score = best.get("confidence", 0.5) * 100
        score_parts["report_agent"] = event_score
        signal_sources.append(f"事件驱动(置信{event_score:.0f})")
        resonance_count += 1

    # 4. rotation_factor — check if stock's sector is in rotation
    rot_signals = [s for s in upstream_signals if s.get("strategy") == "rotation_factor"]
    if rot_signals:
        # Check if any rotation signal matches this stock's sector (via nx_result or metadata)
        sector_hint = nx_result.get("sector", "")
        for rs in rot_signals:
            rs_sector = rs.get("asset", "")
            if sector_hint and rs_sector in sector_hint:
                score_parts["rotation_factor"] = rs.get("confidence", 0.5) * 100
                signal_sources.append(f"行业轮动({rs_sector})")
                resonance_count += 1
                break

    # 5. ma_signals
    ma_signals_list = [s for s in upstream_signals if s.get("strategy") == "ma_signals" and s.get("asset") == code]
    if ma_signals_list:
        best = max(ma_signals_list, key=lambda s: s.get("confidence", 0))
        score_parts["ma_signals"] = best.get("confidence", 0.4) * 100
        signal_sources.append("并购重组信号")
        resonance_count += 1

    # ── 6. NX 技术信号 ──
    nx_signal = nx_result.get("nx_signal", "")
    status = nx_result.get("status", "观望")
    nx_score = 0
    if nx_signal == "buy":
        nx_score = 75 if status == "可介入" else 55
    elif nx_signal == "rising":
        nx_score = 45
    elif nx_signal == "sell":
        nx_score = 15
    score_parts["nx_technical"] = nx_score

    # ── 加权综合 ──
    if not score_parts:
        return {"score": 0, "tier": "无信号", "resonance_count": 0, "signal_sources": []}

    weighted_sum = 0
    weight_sum = 0
    for source, score_val in score_parts.items():
        w = SCORE_WEIGHTS.get(source, 0.1)
        weighted_sum += score_val * w
        weight_sum += w

    if weight_sum == 0:
        return {"score": 0, "tier": "无信号", "resonance_count": 0, "signal_sources": []}

    final_score = round(weighted_sum / weight_sum)

    # ── Tier 分级 ──
    if final_score >= 70 and resonance_count >= 2:
        tier = "T1·强推"
    elif final_score >= 50:
        tier = "T2·关注"
    elif final_score >= 30:
        tier = "T3·观察"
    else:
        tier = "T4·冷门"

    return {
        "score": final_score,
        "tier": tier,
        "resonance_count": resonance_count,
        "signal_sources": signal_sources,
    }


def main():
    date = os.environ.get("WATCHLIST_DATE") or datetime.now().strftime("%Y-%m-%d")
    print(f"=== update_watchlist_signals  {date} ===")

    pool = load_pool()
    stocks = pool.get("stocks", [])

    # ── 预读上游信号，避免每只股票重复 IO ──
    active_signals = read_active_signals()
    print(f"  上游信号: {len(active_signals)} 条活跃")

    results = []
    ok = 0

    for i, s in enumerate(stocks):
        code = s["code"]
        name = s.get("name", "")
        print(f"  [{i+1}/{len(stocks)}] {code} {name} ", end="", flush=True)

        try:
            r = analyze(code, date)
        except Exception as e:
            print(f"✗ {e}")
            continue

        close = r.get("close", 0)
        entry_price = s.get("entry_price")
        pnl = compute_pnl(close, entry_price)

        # ── 多模型综合评分 ──
        scoring = _compute_composite_score(code, active_signals, r)

        results.append({
            "code": code,
            "name": name,
            "added_date": s.get("added_date", ""),
            "close": close,
            "entry_price": entry_price,
            "pnl_pct": pnl,
            "lifecycle": s.get("lifecycle", "未知"),
            "nx_signal": r.get("nx_signal", ""),
            "ma_alignment": r.get("ma_alignment", ""),
            "fib_zone": r.get("fib_zone", ""),
            "status": r.get("status", "观望"),
            "entry_zone": r.get("entry_zone", []),
            "stop_loss": r.get("stop_loss", 0),
            "take_profit": r.get("take_profit", []),
            "signal_basis": r.get("signal_basis", []),
            # ── V8.3 多模型综合评分 ──
            "score": scoring["score"],
            "tier": scoring["tier"],
            "resonance_count": scoring["resonance_count"],
            "signal_sources": scoring["signal_sources"],
        })
        ok += 1
        print(f"→ {scoring['tier']}({scoring['score']}分) | P&L:{pnl}%")

        time.sleep(0.15)

    output = {
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "date": date,
        "count": len(results),
        "signals": results,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n输出: {OUTPUT_FILE}  ({ok} 票)")


if __name__ == "__main__":
    main()
