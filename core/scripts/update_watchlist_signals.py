#!/usr/bin/env python3
"""
update_watchlist_signals.py — 每日预计算自选池信号缓存
- 读取 pool.json（含 entry_price）
- 调用 signals.analyze() 逐票分析
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
from core.pool_manager import load_pool
from core.signals_nx import analyze

DATA_DIR = Path(__file__).parent.parent.parent / "data"
OUTPUT_FILE = DATA_DIR / "watchlist_signals.json"


def compute_pnl(close: float, entry_price: Optional[float]) -> Optional[float]:
    if entry_price and entry_price > 0:
        return round((close - entry_price) / entry_price * 100, 2)
    return None


def main():
    date = datetime.now().strftime("%Y-%m-%d")
    print(f"=== update_watchlist_signals  {date} ===")

    pool = load_pool()
    stocks = pool.get("stocks", [])

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
        })
        ok += 1
        print(f"→ {r['status']} | P&L:{pnl}%")

        time.sleep(0.15)

    output = {
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "date": date,
        "count": len(results),
        "signals": results,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # V6.1: 多路写入（mobile.js _getContractsPath 按优先级查）
    extra_paths = [
        Path(os.path.expanduser("~/交易员/data")),       # Mac 开发
        Path("/opt/trader/output/contracts"),             # ECS 生产 contracts
    ]
    written = [str(OUTPUT_FILE)]
    for p in extra_paths:
        try:
            p.mkdir(parents=True, exist_ok=True)
            dest = p / "watchlist_signals.json"
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(output, f, ensure_ascii=False, indent=2)
            written.append(str(dest))
        except (OSError, PermissionError) as e:
            print(f"  ⚠ 跳过 {p}: {e}")

    print(f"\n输出: {' / '.join(written)}  ({ok} 票)")


if __name__ == "__main__":
    main()
