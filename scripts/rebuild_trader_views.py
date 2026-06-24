#!/usr/bin/env python3
"""Rebuild trader_strategy/trader_tracker JSONL from upstream_signals.

This is a recovery bridge for the mobile trader diagnostic tabs.
Processes stock_agent AND wanjun_models signals.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path


def _data_dir() -> Path:
    env_dir = os.environ.get("CYCLERADAR_DATA_DIR")
    if env_dir:
        return Path(env_dir)
    script_dir = Path(__file__).resolve().parent
    return script_dir.parent / "data"


def _signal_date(signal: dict) -> str:
    signal_id = str(signal.get("signal_id", ""))
    parts = signal_id.split("-")
    # STOCK_AGENT-YYYYMMDD-xxxxxx → YYYY-MM-DD
    if len(parts) >= 3 and parts[0] == "STOCK_AGENT" and len(parts[1]) == 8:
        return f"{parts[1][:4]}-{parts[1][4:6]}-{parts[1][6:8]}"
    # wanjun_models-YYYY-MM-DD-xxxxxx-Mx → YYYY-MM-DD
    if len(parts) >= 2 and parts[0] == "wanjun_models" and len(parts[1]) == 10:
        return parts[1]
    timestamp = str(signal.get("timestamp", ""))
    return timestamp[:10] if len(timestamp) >= 10 else datetime.now().strftime("%Y-%m-%d")


def _read_latest_signals(path: Path) -> list[dict]:
    """Read latest stock_agent AND wanjun_models signals, dedup by signal_id."""
    latest_by_id: dict[str, dict] = {}
    if not path.exists():
        return []

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            signal = json.loads(line)
        except json.JSONDecodeError:
            continue
        strategy = signal.get("strategy", "")
        if strategy not in ("stock_agent", "wanjun_models"):
            continue
        signal_id = signal.get("signal_id")
        if not signal_id:
            continue
        existing = latest_by_id.get(signal_id)
        if not existing or str(signal.get("timestamp", "")) >= str(existing.get("timestamp", "")):
            latest_by_id[signal_id] = signal

    return sorted(latest_by_id.values(), key=lambda s: (_signal_date(s), s.get("asset", "")))


def _signal_type(signal: dict) -> str:
    metadata = signal.get("metadata") or {}
    tier = metadata.get("tier") or ""
    strategy = signal.get("strategy", "")
    
    if strategy == "wanjun_models":
        resonance = metadata.get("resonance", 1)
        if resonance >= 3:
            return "🔥 进攻"
        if resonance >= 2:
            return "✅ 买入"
        return "🕐 观察"
    
    if tier == "强推":
        return "🔥 进攻"
    if tier == "关注":
        return "✅ 买入"
    return "🕐 观察"


def _capital_dir(metadata: dict) -> str:
    breakdown = metadata.get("catalyst_breakdown") or {}
    fund_score = breakdown.get("fund")
    if fund_score == 20:
        return "流入"
    if fund_score == 0:
        return "流出"
    return "中性"


def _strategy_record(signal: dict) -> dict:
    metadata = signal.get("metadata") or {}
    date = _signal_date(signal)
    strategy = signal.get("strategy", "")
    code = str(signal.get("asset", ""))
    name = metadata.get("stock_name") or code

    if strategy == "wanjun_models":
        # wanjun-specific mapping
        model_names = metadata.get("model_names") or []
        model_ids = metadata.get("model_ids") or []
        reasons = metadata.get("reasons") or [f"模型{m}:" for m in model_ids]
        score = int(float(metadata.get("resonance", 1)) * 25)
        entry = metadata.get("entry_price") or metadata.get("close")
        stop = metadata.get("stop_loss")
        target = metadata.get("target_price")
        return {
            "date": date,
            "code": code,
            "name": name,
            "nx": "unknown",
            "ma_align": "unknown",
            "fib_zone": "unknown",
            "weekly_dir": "",
            "capital_dir": "中性",
            "rr": None,
            "model_hits": reasons,
            "signal_type": _signal_type(signal),
            "strategy": "wanjun_models",
            "source": "wanjun_models",
            "score": score,
            "entry_low": entry,
            "entry_high": entry,
            "stop_loss": stop,
            "take_profit": [target] if target else [],
            "error": None,
        }

    # stock_agent mapping (original)
    tier = metadata.get("tier") or "关注"
    score = metadata.get("catalyst_score") or int(float(signal.get("confidence", 0)) * 100)
    entry = metadata.get("entry_price")
    target = metadata.get("target_price")
    stop = metadata.get("stop_loss")

    return {
        "date": date,
        "code": code,
        "name": name,
        "nx": "unknown",
        "ma_align": "unknown",
        "fib_zone": "unknown",
        "weekly_dir": metadata.get("industry") or "未知",
        "capital_dir": _capital_dir(metadata),
        "rr": None,
        "model_hits": metadata.get("reasons") or [],
        "signal_type": _signal_type(signal),
        "strategy": "stock_agent",
        "source": "stock_agent",
        "score": score,
        "entry_low": entry,
        "entry_high": entry,
        "stop_loss": stop,
        "take_profit": [target] if target else [],
        "error": None,
    }


def _tracker_records(strategy_record: dict) -> list[dict]:
    records = []
    for horizon in (5, 10, 20):
        records.append({
            "code": strategy_record["code"],
            "name": strategy_record["name"],
            "signal_date": strategy_record["date"],
            "horizon": horizon,
            "entry": strategy_record.get("entry_low"),
            "stop": strategy_record.get("stop_loss"),
            "targets": strategy_record.get("take_profit") or [],
            "result": "PENDING",
            "max_return": None,
            "max_dd": None,
            "final_return": None,
            "hit_target": None,
            "hit_stop": None,
            "days_to_target": None,
            "days_to_stop": None,
            "n_bars": 0,
            "track_date": datetime.now().strftime("%Y-%m-%d"),
            "signal_type": strategy_record.get("signal_type"),
            "strategy": strategy_record.get("strategy"),
            "score": strategy_record.get("score"),
        })
    return records


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def main() -> int:
    data_dir = _data_dir()
    upstream_path = data_dir / "upstream_signals.jsonl"
    signals = _read_latest_signals(upstream_path)
    if not signals and "--allow-empty" not in sys.argv:
        print(f"no signals found in {upstream_path}; refusing to overwrite trader views", file=sys.stderr)
        return 2

    strategy_rows = [_strategy_record(signal) for signal in signals]
    tracker_rows = [record for row in strategy_rows for record in _tracker_records(row)]

    _write_jsonl(data_dir / "trader_strategy.jsonl", strategy_rows)
    _write_jsonl(data_dir / "trader_tracker.jsonl", tracker_rows)

    dates = sorted({row["date"] for row in strategy_rows}, reverse=True)
    strategies = {}
    for row in strategy_rows:
        s = row.get("strategy", "?")
        strategies[s] = strategies.get(s, 0) + 1
    print(f"rebuilt trader_strategy rows={len(strategy_rows)} strategies={strategies} dates={dates[:5]}")
    print(f"rebuilt trader_tracker rows={len(tracker_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
