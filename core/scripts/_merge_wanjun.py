import json, os

base = "/opt/cycleradar-trader/data"
today = "2026-06-21"

with open(os.path.join(base, "wanjun_signals.jsonl")) as f:
    wanjun = [json.loads(l) for l in f if l.strip()]

print("Read {} wanjun entries".format(len(wanjun)))

# Read existing upstream
existing_ids = set()
lines = []
upstream_path = os.path.join(base, "upstream_signals.jsonl")
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

new_count = 0
for w in wanjun:
    models_str = "_".join(["M{}".format(m) for m in sorted(w["model_ids"])])
    signal_id = "wanjun_models-{}-{}-{}".format(today, w["symbol"], models_str)
    
    if signal_id in existing_ids:
        continue
    
    reasons = ["模型{}: {}".format(mid, name) for mid, name in zip(w["model_ids"], w["model_names"])]
    if w["resonance"] > 1:
        reasons.insert(0, "多模型共振({}个)".format(w["resonance"]))
    
    res = w["resonance"]
    entry = {
        "signal_id": signal_id,
        "timestamp": w["gen_time"],
        "strategy": "wanjun_models",
        "asset": w["symbol"],
        "asset_type": "stock",
        "direction": "long",
        "confidence": min(0.65 + res * 0.1, 0.95),
        "expiry": "2026-06-28T15:00:00",
        "metadata": {
            "stock_name": w["name"],
            "tier": "积极操作",
            "model_ids": w["model_ids"],
            "model_names": w["model_names"],
            "resonance": res,
            "close": w["close"],
            "change_pct": w["change_pct"],
            "gap_up_pct": w["gap_up_pct"],
            "volume_ratio": w["volume_ratio"],
            "temperature": w.get("temperature", 70),
            "reasons": reasons,
            "entry_price": w["close"],
            "target_price": round(w["close"] * 1.1, 2),
            "stop_loss": round(w["close"] * 0.95, 2)
        }
    }
    lines.append(json.dumps(entry, ensure_ascii=False))
    new_count += 1

with open(upstream_path, "w") as f:
    f.write("\n".join(lines) + "\n")

print("Appended {} wanjun signals. Total: {} entries".format(new_count, len(lines)))
