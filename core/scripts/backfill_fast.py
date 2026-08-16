#!/usr/bin/env python3
"""
scripts/backfill_fast.py — CycleRadar V7.6 快速历史回补

策略：按股票维度拉取（而非按日期），每只股票一次腾讯接口调用返回 N 天历史，
按日期分拆写入 Parquet。4720 只 × 1 次 = 4720 次调用，覆盖 2 年。

对比 backfill_market.py（按日遍历）：
  旧策略: 520天 × 4720只 = 245万次调用 → 预计 40+ 小时
  新策略: 4720只 × 1次  =   4720次调用 → 预计 2-3 小时

用法：
    python3 scripts/backfill_fast.py --start 2024-07-07 --end 2026-07-03
    python3 scripts/backfill_fast.py --start 2024-07-07 --end 2026-07-03 --dry-run
    python3 scripts/backfill_fast.py --start 2024-07-07 --end 2026-07-03 --max-symbols 50
    python3 scripts/backfill_fast.py --start 2024-07-07 --end 2026-07-03 --force
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
import requests

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.datalake import (
    DatalakeConfig,
    _ensure_dirs,
    _load_universe,
    _partition_path,
    _partition_dir,
    _manifest_path,
    _checkpoint_path,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backfill_fast")

TENCENT_KL_URL   = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
TENCENT_DELAY    = 0.02   # 20ms 每只，腾讯不严格限流
BATCH_WRITE_SIZE = 200    # 每积累 200 只再做 DataFrame 转换（内存友好）

PARQUET_SCHEMA = pa.schema([
    pa.field("symbol",    pa.string()),
    pa.field("date",      pa.string()),
    pa.field("open",      pa.float64()),
    pa.field("high",      pa.float64()),
    pa.field("low",       pa.float64()),
    pa.field("close",     pa.float64()),
    pa.field("volume",    pa.float64()),
    pa.field("amount",    pa.float64()),
    pa.field("pct_chg",   pa.float64()),
    pa.field("ma5",       pa.float64()),
    pa.field("ma10",      pa.float64()),
    pa.field("ma20",      pa.float64()),
    pa.field("ma60",      pa.float64()),
    pa.field("source",    pa.string()),
    pa.field("source_detail", pa.string()),
    pa.field("quality_flag",  pa.string()),
    pa.field("schema_version", pa.string()),
])


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CycleRadar V7.6 快速历史回补（按股票维度）")
    p.add_argument("--start",        required=True,  help="开始日期 YYYY-MM-DD")
    p.add_argument("--end",          required=True,  help="结束日期 YYYY-MM-DD")
    p.add_argument("--force",        action="store_true", help="覆盖已存在的 Parquet 分区")
    p.add_argument("--dry-run",      action="store_true", help="只打印计划，不写文件")
    p.add_argument("--max-symbols",  type=int, default=None, help="调试用：限制 universe 大小")
    p.add_argument("--delay",        type=float, default=TENCENT_DELAY, help=f"每只请求间隔秒 (default: {TENCENT_DELAY})")
    p.add_argument("--symbols-file", default=None,
                   help="直接指定 symbol 列表文件（每行一个6位代码），跳过新浪/wanjun universe 加载。"
                        "新浪被限速时使用 data/symbols_universe.txt。")
    return p.parse_args()


def _to_wanjun_sym(sym: str) -> str:
    if sym.startswith(('0', '3', '2')):
        return 'sz' + sym
    elif sym.startswith(('6', '5', '9')):
        return 'sh' + sym
    return 'sz' + sym


def _trading_days_in_range(start: date, end: date) -> List[str]:
    days, cur = [], start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur.isoformat())
        cur += timedelta(days=1)
    return days


def fetch_symbol_history(
    sym: str, start: date, end: date, session: requests.Session, delay: float
) -> List[dict]:
    """
    腾讯 qfqday 接口：一次调用拿一只股票的全部历史日线。
    返回 [{"symbol","date","open","high","low","close","volume"}, ...] 过滤到 [start, end]。
    """
    wsym = _to_wanjun_sym(sym)
    exchange = wsym[:2]
    code     = wsym[2:]
    # days 参数：腾讯接口是"往前取 N 天"，从今天往前算到 start，加 90 天缓冲
    from datetime import date as _date
    delta_days = (_date.today() - start).days + 90
    param = f"{wsym},day,,,{delta_days},qfq"
    try:
        resp = session.get(TENCENT_KL_URL, params={
            "_var": "kline_dayqfq",
            "param": param,
        }, timeout=6)
        resp.raise_for_status()
        text = resp.text
        if not text.startswith("kline_dayqfq="):
            return []
        payload = json.loads(text[len("kline_dayqfq="):])
        if payload.get("code", -1) != 0:
            return []
        qfq_arr = payload.get("data", {}).get(wsym, {}).get("qfqday") or \
                  payload.get("data", {}).get(f"{exchange}{code}", {}).get("qfqday")
        if not qfq_arr:
            return []

        start_s, end_s = start.isoformat(), end.isoformat()
        rows = []
        for row in qfq_arr:
            if len(row) < 6:
                continue
            d = row[0]
            if d < start_s or d > end_s:
                continue
            rows.append({
                "symbol": sym,
                "date":   d,
                "open":   float(row[1]),
                "close":  float(row[2]),
                "high":   float(row[3]),
                "low":    float(row[4]),
                "volume": float(row[5]),   # 腾讯返回单位：股
            })
        return rows
    except Exception as e:
        logger.debug("fetch_symbol_history %s failed: %s", sym, e)
        return []
    finally:
        if delay > 0:
            time.sleep(delay)


def _compute_ma(values: List[Optional[float]], n: int) -> List[Optional[float]]:
    out = []
    for i in range(len(values)):
        window = [v for v in values[max(0, i - n + 1):i + 1] if v is not None]
        out.append(round(sum(window) / len(window), 4) if len(window) == n else None)
    return out


def write_date_partition(
    trade_date: str,
    rows: List[dict],
    cfg: DatalakeConfig,
    force: bool,
) -> int:
    """
    把一天的 rows 写成 Parquet 分区。返回写入行数，0 表示跳过。
    """
    td = date.fromisoformat(trade_date)
    out_path = _partition_path(td, cfg)

    if not force and out_path.exists():
        return -1  # already exists, skip

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.parent / f"_tmp_{trade_date}.parquet"

    df = pd.DataFrame(rows)
    if df.empty:
        return 0

    df["date"] = trade_date
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.sort_values("symbol").reset_index(drop=True)

    # MA 计算（单日 ingest 时 lookback 不足，填 None）
    for col in ["ma5", "ma10", "ma20", "ma60"]:
        df[col] = None

    df["amount"]         = None
    df["pct_chg"]        = None
    df["source"]         = "wanjun"
    df["source_detail"]  = "tencent_qfqday"
    df["quality_flag"]   = "ok"
    df["schema_version"] = "daily_bar_v1"

    # 确保列顺序与 schema 一致
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
        "trade_date":    trade_date,
        "status":        "success",
        "rows_written":  rows_written,
        "primary_source": "wanjun",
        "mcp_coverage":  0.0,
        "wanjun_coverage": 1.0,
        "ok_rows":       rows_written,
        "warn_rows":     0,
        "bad_rows":      0,
        "script":        "backfill_fast",
        "finished_at":   _now_iso(),
    }
    with open(_manifest_path(cfg), "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _now_iso() -> str:
    from datetime import datetime
    return datetime.utcnow().isoformat()


def main():
    args   = parse_args()
    start  = date.fromisoformat(args.start)
    end    = date.fromisoformat(args.end)
    cfg    = DatalakeConfig()
    _ensure_dirs(cfg)

    # Universe
    logger.info("Loading universe...")
    if args.symbols_file:
        with open(args.symbols_file) as f:
            symbols = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
        logger.info("Loaded %d symbols from %s", len(symbols), args.symbols_file)
    else:
        symbols = _load_universe(cfg)

    # 过滤掉非6位纯数字（防止 pool.json dict 对象混入）
    symbols = [s for s in symbols if isinstance(s, str) and len(s) == 6 and s.isdigit()]
    # 过滤港股（5位数字 — 实际已被上面过滤掉，再兜一次）
    symbols = [s for s in symbols if not (len(s) == 5 and s.isdigit())]
    if args.max_symbols:
        symbols = symbols[:args.max_symbols]
        logger.info("DEBUG: capped at %d symbols", len(symbols))

    target_days = _trading_days_in_range(start, end)

    # 过滤已存在分区（除非 --force）
    if not args.force:
        existing = {d for d in target_days if _partition_path(date.fromisoformat(d), cfg).exists()}
        skip_days = existing
        need_days = [d for d in target_days if d not in existing]
    else:
        skip_days = set()
        need_days = target_days

    logger.info("Universe: %d symbols | Target: %d days | Skip(exist): %d | Need: %d",
                len(symbols), len(target_days), len(skip_days), len(need_days))

    if not need_days:
        logger.info("All dates already ingested. Use --force to overwrite.")
        return

    if args.dry_run:
        logger.info("[DRY-RUN] Would fetch %d symbols × %d days", len(symbols), len(need_days))
        logger.info("[DRY-RUN] Estimated time: %.0f seconds (~%.1f min) at %.0fms/symbol",
                    len(symbols) * args.delay,
                    len(symbols) * args.delay / 60,
                    args.delay * 1000)
        return

    need_set = set(need_days)

    # 按日期分桶：date_str → list of row dicts
    buckets: Dict[str, List[dict]] = defaultdict(list)
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})

    failed_syms = []
    written_days = skipped_days = empty_days = 0
    t0 = time.time()

    FLUSH_EVERY = 500  # 每 500 只 flush 一次，防止 OOM

    def _flush_buckets():
        nonlocal written_days, skipped_days, empty_days
        flushed = 0
        for trade_date in sorted(buckets.keys()):
            date_rows = buckets[trade_date]
            if not date_rows:
                empty_days += 1
                continue
            n = write_date_partition(trade_date, date_rows, cfg, force=args.force)
            if n == -1:
                skipped_days += 1
            elif n == 0:
                empty_days += 1
            else:
                written_days += 1
                flushed += 1
                append_manifest(trade_date, n, cfg)
        if flushed:
            logger.info("  Flushed %d date partitions (total written: %d)", flushed, written_days)
        buckets.clear()

    for i, sym in enumerate(symbols):
        rows = fetch_symbol_history(sym, start, end, session, args.delay)
        if not rows:
            failed_syms.append(sym)
        else:
            for row in rows:
                d = row["date"]
                if d in need_set:
                    buckets[d].append(row)

        if (i + 1) % 200 == 0:
            elapsed = time.time() - t0
            eta = elapsed / (i + 1) * (len(symbols) - i - 1)
            logger.info("Fetched %d/%d | %.0fs elapsed | ETA %.0fs | %d failed | %d dates buffered",
                        i + 1, len(symbols), elapsed, eta, len(failed_syms), len(buckets))

        # 每 FLUSH_EVERY 只写一次，释放内存
        if (i + 1) % FLUSH_EVERY == 0:
            _flush_buckets()

    logger.info("Fetch complete: %d/%d symbols OK, %d failed | %.0fs",
                len(symbols) - len(failed_syms), len(symbols), len(failed_syms), time.time() - t0)

    # 最终 flush 剩余
    if buckets:
        _flush_buckets()

    # Checkpoint update
    ckpt_p = _checkpoint_path(cfg)
    try:
        checkpoint = json.loads(ckpt_p.read_text()) if ckpt_p.exists() else {}
    except Exception:
        checkpoint = {}
    completed = checkpoint.setdefault("completed_dates", {})
    for d in need_days:
        td = date.fromisoformat(d)
        if _partition_path(td, cfg).exists():
            completed[d] = {"status": "success", "script": "backfill_fast",
                            "finished_at": _now_iso()}
    ckpt_p.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=False))

    logger.info("=== Done: %d written | %d skipped | %d empty | %d sym-failures ===",
                written_days, skipped_days, empty_days, len(failed_syms))
    if failed_syms:
        logger.warning("Failed symbols (%d): %s%s",
                       len(failed_syms),
                       ", ".join(failed_syms[:20]),
                       " ..." if len(failed_syms) > 20 else "")


if __name__ == "__main__":
    main()
