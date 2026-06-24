#!/usr/bin/env python3
"""Merge wanjun_signals.jsonl into upstream_signals.jsonl with schema transform.
Run AFTER wanjun_screener.py --jsonl --output data/wanjun_signals.jsonl
"""
import json, os, sys
from datetime import datetime

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")

def main():
    wanjun_path = os.path.join(DATA, "wanjun_signals.jsonl")
    upstream_path = os.path.join(DATA, "upstream_signals.jsonl")
    
    if not os.path.exists(wanjun_path):
        print("[merge_wanjun] no wanjun_signals.jsonl, skip")
        return 0
    
    # Read wanjun
    wanjun = []
    with open(wanjun_path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    wanjun.append(json.loads(line))
                except:
                    pass
    
    if not wanjun:
        print("[merge_wanjun] empty wanjun file, skip")
        return 0
    
    # Read existing upstream, build id set
    existing_ids = set()
    lines = []
    if os.path.exists(upstream_path):
        with open(upstream_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    existing_ids.add(d.get("signal_id", ""))
                    lines.append(line)
                except:
                    lines.append(line)
    
    # Transform and dedup
    today = datetime.now().strftime("%Y-%m-%d")
    new_count = 0
    
    for w in wanjun:
        models_str = "_".join(["M{}".format(m) for m in sorted(w.get("model_ids", []))])
        signal_id = "wanjun_models-{}-{}-{}".format(today, w.get("symbol", "?"), models_str)
        
        if signal_id in existing_ids:
            continue
        
        res = w.get("resonance", 1)
        reasons = ["模型{}: {}".format(mid, name) for mid, name in zip(
            w.get("model_ids", []), w.get("model_names", []))]
        if res > 1:
            reasons.insert(0, "多模型共振({})".format(res))
        
        close_price = w.get("close", 0)
        entry = {
            "signal_id": signal_id,
            "timestamp": w.get("gen_time", datetime.now().isoformat()),
            "strategy": "wanjun_models",
            "asset": w.get("symbol", ""),
            "asset_type": "stock",
            "direction": "long",
            "confidence": min(0.65 + res * 0.1, 0.95),
            "expiry": "{}T15:00:00".format(
                (datetime.now().replace(hour=15, minute=0, second=0, microsecond=0)).strftime("%Y-%m-%d")
                if False else datetime.now().strftime("%Y-%m-%d")),
            "metadata": {
                "stock_name": w.get("name", ""),
                "tier": "积极操作",
                "model_ids": w.get("model_ids", []),
                "model_names": w.get("model_names", []),
                "resonance": res,
                "close": close_price,
                "change_pct": w.get("change_pct", 0),
                "gap_up_pct": w.get("gap_up_pct", 0),
                "volume_ratio": w.get("volume_ratio", 0),
                "temperature": w.get("temperature", 70),
                "reasons": reasons,
                "entry_price": close_price,
                "target_price": round(close_price * 1.1, 2) if close_price else None,
                "stop_loss": round(close_price * 0.95, 2) if close_price else None
            }
        }
        lines.append(json.dumps(entry, ensure_ascii=False))
        new_count += 1
    
    with open(upstream_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    
    print("[merge_wanjun] merged {} signals into upstream (total {})".format(new_count, len(lines)))
    return 0

if __name__ == "__main__":
    sys.exit(main())
