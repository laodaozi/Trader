#!/usr/bin/env python3
"""
scripts/backfill_akshare.py — CycleRadar V7.6 历史回补（akshare 数据源）

腾讯接口被 JS challenge 封锁时的替代方案。
akshare stock_zh_a_hist 直接返回复权日线，无浏览器依赖。

用法：
    python3 scripts/backfill_akshare.py --start 2024-01-01 --end 2026-07-07 \\
        --symbols-file /tmp/symbols_missing.txt
    python3 scripts/backfill_akshare.py --start 2024-01-01 --end 2026-07-07 \\
        --symbols-file /tmp/symbols_missing.txt --dry-run
    python3 scripts/backfill_akshare.py --start 2024-01-01 --end 2026-07-07 \\
        --symbols-file /tmp/symbols_missing.txt --max-symbols 20
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.datalake import (
    DatalakeConfig,
    _ensure_dirs,
    _partition_path,
    _manifest_path,
    _checkpoint_path,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backfill_akshare")

BATCH_SIZE   = 50    # 每 50 只写一次 Parquet（积累后 merge）
DEFAULT_DELAY = 0.3  # akshare 限流宽松，但别太快

PARQUET_SCHEMA = pa.schema([
    pa.field("symbol",        pa.string()),
    pa.field("date",          pa.string()),
    pa.field("open",          pa.float64()),
    pa.field("high",          pa.float64()),
    pa.field("low",           pa.float64()),
    pa.field("close",         pa.float64()),
    pa.field("volume",        pa.float64()),
    pa.field("amount",        pa.float64()),
    pa.field("pct_chg",       pa.float64()),
    pa.field("ma5",           pa.float64()),
    pa.field("ma10",          pa.float64()),
    pa.field("ma20",          pa.float64()),
    pa.field("ma60",          pa.float64()),
    pa.field("source",        pa.string()),
    pa.field("source_detail", pa.string()),
    pa.field("quality_flag",  pa.string()),
    pa.field("schema_version", pa.string()),
])


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CycleRadar V7.6 历史回补（akshare 数据源）")
    p.add_argument("--start",        required=True, help="开始日期 YYYY-MM-DD")
    p.add_argument("--end",          required=True, help="结束日期 YYYY-MM-DD")
    p.add_argument("--force",        action="store_true", help="覆盖已存在分区")
    p.add_argument("--dry-run",      action="store_true", help="只打印计划，不写文件")
    p.add_argument("--max-symbols",  type=int, default=None, help="调试用：限制数量")
    p.add_argument("--delay",        type=float, default=DEFAULT_DELAY,
                   help=f"每只请求间隔秒 (default: {DEFAULT_DELAY})")
    p.add_argument("--symbols-file", default=None,
                   help="symbol 列表文件（每行一个6位代码）；不指定则读 data/symbols_universe.txt")
    return p.parse_args()


def fetch_symbol_history_ak(
    sym: str, start: date, end: date, delay: float
) -> List[dict]:
    """
    akshare stock_zh_a_hist 前复权日线。
    返回 [{"symbol","date","open","high","low","close","volume","amount","pct_chg"}, ...]
    """
    import akshare as ak
    try:
        df = ak.stock_zh_a_hist(
            symbol=sym,
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust="qfq",
        )
        if df.empty:
            return []

        # akshare 列名是中文
        col_map = {
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low",
            "成交量": "volume",  # 单位：手 → 股 ×100
            "成交额": "amount",
            "涨跌幅": "pct_chg",
        }
        df = df.rename(columns=col_map)
        df["symbol"] = sym
        df["date"]   = df["date"].astype(str)
        # 成交量单位换算（akshare 返回"手"，1手=100股）
        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce") * 100

        rows = []
        start_s, end_s = start.isoformat(), end.isoformat()
        for _, row in df.iterrows():
            d = str(row["date"])
            if d < start_s or d > end_s:
                continue
            rows.append({
                "symbol":  sym,
                "date":    d,
                "open":    float(row.get("open")   or 0),
                "high":    float(row.get("high")   or 0),
                "low":     float(row.get("low")    or 0),
                "close":   float(row.get("close")  or 0),
                "volume":  float(row.get("volume") or 0),
                "amount":  float(row.get("amount") or 0),
                "pct_chg": float(row.get("pct_chg") or 0),
            })
        return rows
    except Exception as e:
        logger.debug("fetch_symbol_history_ak %s failed: %s", sym, e)
        return []
    finally:
        if delay > 0:
            time.sleep(delay)


def _now_iso() -> str:
    from datetime import datetime
    return datetime.utcnow().isoformat()


def _trading_days_in_range(start: date, end: date) -> List[str]:
    days, cur = [], start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur.isoformat())
        cur += timedelta(days=1)
    return days


def write_date_partition(
    trade_date: str,
    rows: List[dict],
    cfg: DatalakeConfig,
    force: bool,
) -> int:
    """
    把一天的 rows 写成 Parquet 分区。
    若分区已存在（非 force），则 merge 已有数据（追加新股票）。
    返回最终写入总行数，-1 表示完全跳过。
    """
    td = date.fromisoformat(trade_date)
    out_path = _partition_path(td, cfg)

    if not force and out_path.exists():
        # merge：读已有数据，过滤掉本次新股票，再追加
        try:
            existing_df = pd.read_parquet(out_path)
            existing_syms = set(existing_df["symbol"].tolist())
            new_rows = [r for r in rows if r["symbol"] not in existing_syms]
            if not new_rows:
                return -1  # 所有股票已存在，跳过
            merged_df = pd.concat([existing_df, pd.DataFrame(new_rows)], ignore_index=True)
            rows = merged_df.to_dict("records")
        except Exception:
            pass  # 读失败就直接覆盖

    if not rows:
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.parent / f"_tmp_{trade_date}.parquet"

    df = pd.DataFrame(rows)
    df["date"] = trade_date
    for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            df[col] = None

    df = df.sort_values("symbol").reset_index(drop=True)

    for col in ["ma5", "ma10", "ma20", "ma60"]:
        df[col] = None

    df["source"]         = "akshare"
    df["source_detail"]  = "stock_zh_a_hist_qfq"
    df["quality_flag"]   = "ok"
    df["schema_version"] = "daily_bar_v1"

    cols = [f.name for f in PARQUET_SCHEMA]
    for c in cols:
        if c not in df.columns:
            df[c] = None
    df = df[cols]

    table = pa.Table.from_pandas(df, schema=PARQUET_SCHEMA, safe=False)
    pq.write_table(table, tmp_path, compression="snappy")
    tmp_path.rename(out_path)
    return len(df)


def append_manifest(trade_date: str, rows_written: int, cfg: DatalakeConfig):
    entry = {
        "trade_date":      trade_date,
        "status":          "success",
        "rows_written":    rows_written,
        "primary_source":  "akshare",
        "mcp_coverage":    0.0,
        "wanjun_coverage": 1.0,
        "ok_rows":         rows_written,
        "warn_rows":       0,
        "bad_rows":        0,
        "script":          "backfill_akshare",
        "finished_at":     _now_iso(),
    }
    with open(_manifest_path(cfg), "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main():
    args  = parse_args()
    start = date.fromisoformat(args.start)
    end   = date.fromisoformat(args.end)
    cfg   = DatalakeConfig()
    _ensure_dirs(cfg)

    # Universe
    logger.info("Loading universe...")
    if args.symbols_file:
        with open(args.symbols_file) as f:
            symbols = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
        logger.info("Loaded %d symbols from %s", len(symbols), args.symbols_file)
    else:
        src = Path("data/symbols_universe.txt")
        if src.exists():
            symbols = src.read_text().splitlines()
            logger.info("Loaded %d symbols from data/symbols_universe.txt", len(symbols))
        else:
            logger.error("No symbols file specified and data/symbols_universe.txt missing")
            sys.exit(1)

    symbols = [s for s in symbols if isinstance(s, str) and len(s) == 6 and s.isdigit()]
    if args.max_symbols:
        symbols = symbols[:args.max_symbols]
        logger.info("DEBUG: capped at %d symbols", len(symbols))

    target_days = _trading_days_in_range(start, end)

    # 注意：不能靠分区存在与否判断是否需要 fetch——
    # 分区可能已存在但只有 000xxx 数据，需要 merge。
    # 只有 --force=False 且分区已有当前 sym 时才跳过单只股票（在 write_date_partition 内处理）。
    # 所以这里始终 fetch 所有 symbols，写入时 merge。
    logger.info("Universe: %d symbols | Target: %d days", len(symbols), len(target_days))

    if args.dry_run:
        est = len(symbols) * args.delay
        logger.info("[DRY-RUN] Would fetch %d symbols | ETA %.0fs (~%.1f min) at %.0fms/sym",
                    len(symbols), est, est / 60, args.delay * 1000)
        return

    need_set = set(target_days)
    buckets: Dict[str, List[dict]] = defaultdict(list)
    failed_syms: List[str] = []
    t0 = time.time()

    for i, sym in enumerate(symbols):
        rows = fetch_symbol_history_ak(sym, start, end, args.delay)
        if not rows:
            failed_syms.append(sym)
        else:
            for row in rows:
                d = row["date"]
                if d in need_set:
                    buckets[d].append(row)

        if (i + 1) % BATCH_SIZE == 0:
            elapsed = time.time() - t0
            eta = elapsed / (i + 1) * (len(symbols) - i - 1)
            logger.info("Fetched %d/%d | %.0fs elapsed | ETA %.0fs | %d failed | %d dates buffered",
                        i + 1, len(symbols), elapsed, eta, len(failed_syms), len(buckets))

            # 批量写入（减少内存占用）
            written = 0
            for trade_date in sorted(buckets.keys()):
                n = write_date_partition(trade_date, buckets[trade_date], cfg, args.force)
                if n > 0:
                    written += 1
                    append_manifest(trade_date, n, cfg)
            if written:
                logger.info("  Wrote/merged %d date partitions", written)
            buckets.clear()

    # 剩余 buckets
    if buckets:
        for trade_date in sorted(buckets.keys()):
            n = write_date_partition(trade_date, buckets[trade_date], cfg, args.force)
            if n > 0:
                append_manifest(trade_date, n, cfg)
        buckets.clear()

    logger.info("Fetch complete: %d/%d OK, %d failed | %.0fs",
                len(symbols) - len(failed_syms), len(symbols), len(failed_syms), time.time() - t0)

    # Checkpoint
    ckpt_p = _checkpoint_path(cfg)
    try:
        checkpoint = json.loads(ckpt_p.read_text()) if ckpt_p.exists() else {}
    except Exception:
        checkpoint = {}
    completed = checkpoint.setdefault("completed_dates", {})
    for d in target_days:
        td = date.fromisoformat(d)
        if _partition_path(td, cfg).exists():
            completed[d] = {"status": "success", "script": "backfill_akshare",
                            "finished_at": _now_iso()}
    ckpt_p.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=False))

    logger.info("=== Done | %d sym-failures ===", len(failed_syms))
    if failed_syms:
        logger.warning("Failed (%d): %s%s",
                       len(failed_syms),
                       ", ".join(failed_syms[:20]),
                       " ..." if len(failed_syms) > 20 else "")


if __name__ == "__main__":
    main()
