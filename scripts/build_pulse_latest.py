#!/usr/bin/env python3.9
"""build_pulse_latest.py — synthesize timing + scanner + alpha into unified pulse"""
import json, os, sys
from datetime import datetime

DATA = "/opt/cycleradar-trader/data"
PULSE_PATH = os.path.join(DATA, "pulse_latest.json")

def load_json(path):
    if not os.path.exists(path): return None
    with open(path) as f: return json.load(f)

def load_jsonl_last(path, n=20):
    if not os.path.exists(path): return []
    lines = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try: lines.append(json.loads(line))
                except: pass
    return lines[-n:]

def build_verdict(phase, temperature, bull_ct, bear_ct, neutral_ct, has_sector):
    if phase == "冲刺" and temperature > 70 and bull_ct > bear_ct + 2:
        return "满仓 · 做多"
    elif phase == "冲刺" and temperature > 50 and bull_ct >= bear_ct:
        return "重仓 · 看多"
    elif phase == "防御" or temperature < 40:
        return "轻仓 · 防守"
    elif abs(bull_ct - bear_ct) <= 1 and bull_ct < 2:
        return "半仓 · 观望"
    elif has_sector:
        return "调仓 · 轮动"
    elif phase in ("冲刺", "切换") and bull_ct > bear_ct:
        return "偏多 · 积极"
    elif bear_ct > bull_ct:
        return "轻仓 · 防守"
    else:
        return "观望 · 等信号"

def main():
    timing = load_json(os.path.join(DATA, "timing_history.json"))
    narrative = load_json(os.path.join(DATA, "event_narrative_latest.json"))
    rotation = load_json(os.path.join(DATA, "rotation_snapshot.json"))
    scanner_lines = load_jsonl_last(os.path.join(DATA, "upstream_signals.jsonl"), 30)

    # --- timing ---
    timing_list = timing.get("history", [timing]) if isinstance(timing, dict) else timing
    if isinstance(timing_list, list) and timing_list:
        r = timing_list[-1]
    else:
        r = timing if isinstance(timing, dict) else {}
    phase = r.get("phase", "未知")
    temperature = r.get("temperature", 50)
    timing_date = r.get("date", "")
    timing_index = r.get("index_direction", "")

    # --- scanner ---
    seen = set()
    scanner_unique = []
    for s in reversed(scanner_lines):
        key = s.get("strategy", "")
        if key and key not in seen:
            seen.add(key)
            scanner_unique.append({
                "model": key,
                "direction": s.get("direction", "?"),
                "confidence": s.get("confidence", 0),
            })

    bull_ct = sum(1 for s in scanner_unique if s["direction"] == "long")
    bear_ct = sum(1 for s in scanner_unique if s["direction"] == "short")
    neutral_ct = sum(1 for s in scanner_unique if s["direction"] == "neutral")

    # --- sector ---
    sector_dir = (rotation or {}).get("direction", "")
    sector_conf = (rotation or {}).get("confidence", 0)

    # --- alpha ---
    alpha_signals = []
    if narrative:
        raw = narrative.get("alpha_signals", [])
        for s in raw[:10]:
            m = s.get("metadata", {})
            alpha_signals.append({
                "stock": m.get("stock_name", s.get("asset", "?")),
                "code": s.get("asset", "?"),
                "tier": m.get("tier", ""),
                "confidence": s.get("confidence", 0),
                "reasons": m.get("reasons", [])[:3],
            })

    # --- verdict ---
    verdict = build_verdict(phase, temperature, bull_ct, bear_ct, neutral_ct, bool(sector_dir))

    pulse = {
        "generated_at": datetime.now().isoformat(),
        "verdict": verdict,
        "timing": {
            "date": timing_date,
            "phase": phase,
            "temperature": temperature,
            "index_direction": timing_index,
        },
        "sector": {
            "direction": sector_dir,
            "confidence": sector_conf,
        },
        "scanner": {
            "models": len(scanner_unique),
            "bullish": bull_ct,
            "bearish": bear_ct,
            "neutral": neutral_ct,
            "details": scanner_unique,
        },
        "alpha": {
            "count": len(alpha_signals),
            "top_picks": alpha_signals,
        },
    }

    os.makedirs(DATA, exist_ok=True)
    with open(PULSE_PATH, "w") as f:
        json.dump(pulse, f, ensure_ascii=False, indent=2)
    print(f"Pulse → {verdict} | timing={phase} t={temperature} | bull={bull_ct} bear={bear_ct} | alpha={len(alpha_signals)}")

if __name__ == "__main__":
    main()
