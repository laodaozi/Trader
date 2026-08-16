#!/usr/bin/env python3
"""
add_tier_to_watchlist.py — 为已有 watchlist_signals.json 追加 tier/score 字段
- 读 watchlist_signals.json（已有 nx_signal/status 等 NX 分析结果）
- 读 upstream_signals.jsonl（信号总线）
- 对每条记录调用 _compute_composite_score
- 写回 watchlist_signals.json
"""

import json
import sys
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / "data"
INPUT_FILE = DATA_DIR / "watchlist_signals.json"
UPSTREAM_FILE = DATA_DIR / "upstream_signals.jsonl"

SCORE_WEIGHTS = {
    "stock_agent": 0.35,
    "wanjun_models": 0.25,
    "nx_technical": 0.25,
    "report_agent": 0.10,
    "rotation_factor": 0.03,
    "ma_signals": 0.02,
}


def load_upstream_signals(path: Path) -> list:
    signals = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    signals.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return signals


def compute_composite_score(code: str, upstream_signals: list, nx_result: dict) -> dict:
    score_parts = {}
    signal_sources = []
    resonance_count = 0

    # 1. stock_agent
    sa_signals = [s for s in upstream_signals if s.get("strategy") == "stock_agent" and s.get("asset") == code]
    if sa_signals:
        best = max(sa_signals, key=lambda s: s.get("metadata", {}).get("catalyst_score", 0))
        catalyst = best.get("metadata", {}).get("catalyst_score") or 0
        score_parts["stock_agent"] = catalyst
        signal_sources.append(f"AI选股({catalyst}分)")
        resonance_count += 1

    # 2. wanjun_models
    sc_signals = [s for s in upstream_signals if s.get("strategy") == "wanjun_models" and s.get("asset") == code]
    if sc_signals:
        best = max(sc_signals, key=lambda s: s.get("metadata", {}).get("resonance", 0))
        meta = best.get("metadata", {})
        resonance = meta.get("resonance", 1)
        cwr = meta.get("calibrated_win_rate") or 0.5
        scanner_score = min(100, resonance * cwr * 100)
        score_parts["wanjun_models"] = scanner_score
        signal_sources.append(f"{resonance}模型共振(胜率{cwr:.0%})")
        resonance_count += 1

    # 3. report_agent
    ra_signals = [s for s in upstream_signals if s.get("strategy") == "report_agent" and s.get("asset") == code]
    if ra_signals:
        best = max(ra_signals, key=lambda s: s.get("confidence", 0))
        event_score = (best.get("confidence") or 0.5) * 100
        score_parts["report_agent"] = event_score
        signal_sources.append(f"事件驱动(置信{event_score:.0f})")
        resonance_count += 1

    # 4. rotation_factor
    rot_signals = [s for s in upstream_signals if s.get("strategy") == "rotation_factor"]
    if rot_signals:
        sector_hint = nx_result.get("sector", "") or nx_result.get("industry", "")
        for rs in rot_signals:
            rs_sector = rs.get("asset", "")
            if sector_hint and rs_sector in str(sector_hint):
                score_parts["rotation_factor"] = (rs.get("confidence") or 0.5) * 100
                signal_sources.append(f"行业轮动({rs_sector})")
                resonance_count += 1
                break

    # 5. ma_signals
    ma_list = [s for s in upstream_signals if s.get("strategy") == "ma_signals" and s.get("asset") == code]
    if ma_list:
        best = max(ma_list, key=lambda s: s.get("confidence", 0))
        score_parts["ma_signals"] = (best.get("confidence") or 0.4) * 100
        signal_sources.append("并购重组信号")
        resonance_count += 1

    # 6. nx_technical
    nx_signal = nx_result.get("nx_signal", "")
    status = nx_result.get("status", "观望")
    if nx_signal == "buy":
        nx_score = 75 if status == "可介入" else 55
    elif nx_signal == "rising":
        nx_score = 45
    elif nx_signal == "sell":
        nx_score = 15
    else:
        nx_score = 25  # neutral / unknown
    score_parts["nx_technical"] = nx_score

    # weighted sum
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

    # Tier
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
    if not INPUT_FILE.exists():
        print(f"ERROR: {INPUT_FILE} not found")
        sys.exit(1)
    if not UPSTREAM_FILE.exists():
        print(f"ERROR: {UPSTREAM_FILE} not found")
        sys.exit(1)

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    upstream = load_upstream_signals(UPSTREAM_FILE)
    print(f"上游信号: {len(upstream)} 条")
    print(f"自选池: {data['count']} 票")

    signals = data.get("signals", [])
    updated = 0

    for item in signals:
        code = item.get("code", "")
        name = item.get("name", "")

        # NX analysis result (already in current watchlist_signals.json)
        nx_result = {
            "nx_signal": item.get("nx_signal", ""),
            "status": item.get("status", "观望"),
            "ma_alignment": item.get("ma_alignment", ""),
        }

        scoring = compute_composite_score(code, upstream, nx_result)
        item["score"] = scoring["score"]
        item["tier"] = scoring["tier"]
        item["resonance_count"] = scoring["resonance_count"]
        item["signal_sources"] = scoring["signal_sources"]

        updated += 1
        print(f"  {code} {name} → {scoring['tier']}({scoring['score']}分) | 共振: {scoring['resonance_count']}")

    data["sig"] = "tier computed"
    data["tier_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(INPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n完成: {updated} 票已追加 tier")


if __name__ == "__main__":
    main()
