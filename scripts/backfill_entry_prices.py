#!/usr/bin/env python3
"""
backfill_entry_prices.py — 批量回填 pool.json 中所有股票的入选价
- 通过 MCP K线查询 each stock's added_date 收盘价
- 写入 entry_price / entry_price_date / entry_price_source
- 进度条 + 异常处理（单票失败不中断）
"""

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from modules.pool import load_pool, save_pool, _get_kline

POOL_FILE = Path(__file__).parent.parent / "data" / "pool.json"
POOL_BACKUP = POOL_FILE.with_suffix(".json.bak.prices")


def get_entry_price(code: str, date_str: str):
    """查询 added_date 附近收盘价，取 <= date 的最后一条 K 线收盘价。"""
    try:
        _, bars = _get_kline(code, date_str, days=10)
        if not bars:
            return None, None
        found = None
        for b in reversed(bars):
            if b["date"] <= date_str:
                found = b
                break
        return (float(found["close"]), found["date"]) if found else (None, None)
    except Exception as e:
        print(f"    ⚠ {code} K线查询失败: {e}")
        return None, None


def main():
    print("=== backfill_entry_prices ===")

    # 备份
    with open(POOL_FILE, encoding="utf-8") as f:
        orig = f.read()
    with open(POOL_BACKUP, "w", encoding="utf-8") as f:
        f.write(orig)
    print(f"备份: {POOL_BACKUP}")

    pool = load_pool()
    stocks = pool.get("stocks", [])
    total = len(stocks)
    print(f"票池 {total} 只股票")

    filled = 0
    missing = 0
    errors = 0

    for i, s in enumerate(stocks):
        code = s["code"]
        name = s.get("name", "")
        added_date = s.get("added_date", "")

        if s.get("entry_price") is not None:
            print(f"  [{i+1}/{total}] {code} {name} 已有入选价，跳过")
            filled += 1
            continue

        print(f"  [{i+1}/{total}] {code} {name} added={added_date} ", end="", flush=True)

        price, actual_date = get_entry_price(code, added_date)
        if price is not None:
            s["entry_price"] = price
            s["entry_price_date"] = actual_date or added_date
            s["entry_price_source"] = "mcp_kline"
            print(f"→ {price:.2f} ({actual_date})")
            filled += 1
        else:
            s["entry_price"] = None
            s["entry_price_date"] = added_date
            s["entry_price_source"] = "unavailable"
            print("→ 无数据")
            missing += 1

        time.sleep(0.15)  # MCP 限流

    pool["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_pool(pool)

    print(f"\n结果: {filled} 已填 / {missing} 无数据 / {errors} 失败 / {total} 总数")
    print("完成 ✓")


if __name__ == "__main__":
    main()
