#!/usr/bin/env python3.9
"""
scripts/backfill_market.py — CycleRadar V7.6 历史数据回补

将历史日线数据批量写入本地 Parquet 数据资产层。
支持断点续传（checkpoint）、幂等（重跑不重复）、MCP 限流。

用法：
    python3.9 scripts/backfill_market.py --start 2024-07-01 --end 2026-07-06
    python3.9 scripts/backfill_market.py --start 2024-07-01 --end 2026-07-06 --dry-run
    python3.9 scripts/backfill_market.py --start 2024-07-01 --end 2026-07-06 --force
    python3.9 scripts/backfill_market.py --start 2024-07-01 --end 2026-07-06 --max-symbols 30
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

# Make sure modules/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.datalake import (
    DatalakeConfig,
    IngestResult,
    _ensure_dirs,
    _load_universe,
    _checkpoint_path,
    _partition_path,
    _write_manifest,
    ingest_daily,
    get_latest_available_date,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backfill_market")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CycleRadar V7.6 历史数据回补")
    p.add_argument("--start",       required=True,  help="开始日期 YYYY-MM-DD")
    p.add_argument("--end",         required=True,  help="结束日期 YYYY-MM-DD")
    p.add_argument("--universe",    default="all",  choices=["all", "tracker", "file"],
                   help="universe 来源 (default: all)")
    p.add_argument("--symbols-file", default=None,  help="universe=file 时的 symbol 列表文件（每行一个）")
    p.add_argument("--batch-days",  type=int, default=20, help="每批交易日数 (default: 20)")
    p.add_argument("--sleep",       type=float, default=0.5, help="每日间隔秒 (default: 0.5)")
    p.add_argument("--force",       action="store_true", help="强制覆盖已有成功分区")
    p.add_argument("--dry-run",     action="store_true", help="只打印计划，不写文件")
    p.add_argument("--max-symbols", type=int, default=None, help="调试用：限制 universe 大小")
    p.add_argument("--wanjun-only", action="store_true",
                   help="跳过全量 MCP，仅用 wanjun 作主数据源（tracker 自选股除外）。"
                        "回补 2 年历史时推荐使用，速度提升 ~10×。")
    p.add_argument("--mcp-whitelist-file", default=None,
                   help="MCP 精确调用的 symbol 白名单文件（每行一个）。"
                        "--wanjun-only 时若不指定，自动读取 tracker 自选池。")
    return p.parse_args()


def load_universe_from_file(path: str) -> list:
    with open(path) as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def load_tracker_symbols() -> list:
    """读取自选池（pool.json）中的 symbol 列表，作为 MCP 精确调用白名单。"""
    import re as _re
    import json as _json
    pool_path = Path(__file__).parent.parent / "data" / "pool.json"
    if not pool_path.exists():
        logger.warning("pool.json not found, MCP whitelist = []")
        return []
    try:
        d = _json.loads(pool_path.read_text())
        stocks = d.get("stocks", [])
        syms = []
        for s in stocks:
            code = s.get("code") or s.get("symbol") or ""
            code_str = str(code).strip().zfill(6)
            if _re.match(r'^\d{6}$', code_str):
                syms.append(code_str)
        syms = list(dict.fromkeys(syms))
        logger.info("pool.json: %d symbols → MCP whitelist", len(syms))
        return syms
    except Exception as e:
        logger.warning("Could not read pool.json (%s), MCP whitelist = []", e)
        return []


def generate_trading_days(start: date, end: date) -> list:
    """Generate Mon-Fri dates between start and end inclusive."""
    days = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    return days


def main() -> None:
    args = parse_args()
    start_date = date.fromisoformat(args.start)
    end_date   = date.fromisoformat(args.end)
    cfg = DatalakeConfig()
    _ensure_dirs(cfg)

    # Load universe
    logger.info("Loading universe (mode=%s)...", args.universe)
    if args.universe == "file":
        if not args.symbols_file:
            logger.error("--symbols-file required when --universe=file")
            sys.exit(1)
        symbols = load_universe_from_file(args.symbols_file)
    elif args.universe == "tracker":
        symbols = _load_universe(cfg)  # includes tracker + ETF
    else:  # all
        symbols = _load_universe(cfg)

    if args.max_symbols:
        symbols = symbols[:args.max_symbols]
        logger.info("DEBUG mode: universe capped at %d symbols", len(symbols))

    logger.info("Universe size: %d symbols", len(symbols))

    # Determine MCP whitelist for --wanjun-only mode
    mcp_symbols = None  # None = original full-MCP behaviour
    if args.wanjun_only:
        if args.mcp_whitelist_file:
            mcp_symbols = load_universe_from_file(args.mcp_whitelist_file)
            logger.info("--wanjun-only: MCP whitelist from file = %d symbols", len(mcp_symbols))
        else:
            mcp_symbols = load_tracker_symbols()
            logger.info("--wanjun-only: MCP whitelist = tracker watchlist (%d symbols)", len(mcp_symbols))

    # Generate trading days
    all_days = generate_trading_days(start_date, end_date)
    logger.info("Trading days to process: %d (%s → %s)",
                len(all_days), start_date, end_date)

    if args.dry_run:
        logger.info("[DRY-RUN] Would process %d trading days × %d symbols",
                    len(all_days), len(symbols))
        for i, td in enumerate(all_days[:5]):
            logger.info("  %d. %s", i + 1, td)
        if len(all_days) > 5:
            logger.info("  ... and %d more", len(all_days) - 5)
        return

    # Load checkpoint
    ckpt_p = _checkpoint_path(cfg)
    checkpoint: dict = {"completed_dates": {}, "failed_dates": {}}
    if ckpt_p.exists():
        try:
            checkpoint = json.loads(ckpt_p.read_text())
            done_count = len(checkpoint.get("completed_dates", {}))
            fail_count = len(checkpoint.get("failed_dates", {}))
            logger.info("Checkpoint loaded: %d done, %d failed previously", done_count, fail_count)
        except Exception as e:
            logger.warning("Checkpoint read failed (%s), starting fresh", e)

    completed = checkpoint.get("completed_dates", {})
    failed    = checkpoint.get("failed_dates", {})

    # Process in batches
    success_count = 0
    skip_count    = 0
    fail_count    = 0

    for batch_start in range(0, len(all_days), args.batch_days):
        batch = all_days[batch_start:batch_start + args.batch_days]
        logger.info("--- Batch %d/%d: %s → %s ---",
                    batch_start // args.batch_days + 1,
                    (len(all_days) + args.batch_days - 1) // args.batch_days,
                    batch[0], batch[-1])

        for td in batch:
            td_str = td.isoformat()

            # Skip if checkpoint says done and partition exists
            if not args.force and td_str in completed:
                if _partition_path(td, cfg).exists():
                    logger.debug("SKIP %s (checkpoint ok + parquet exists)", td_str)
                    skip_count += 1
                    continue
                else:
                    logger.info("RERUN %s (checkpoint ok but parquet missing)", td_str)

            logger.info("INGEST %s", td_str)
            result = ingest_daily(td, symbols, cfg, force=args.force,
                                  mcp_symbols=mcp_symbols)

            if result.status == "success":
                success_count += 1
                completed[td_str] = {
                    "status":        "success",
                    "rows_written":  result.rows_written,
                    "primary_source": result.primary_source,
                    "mcp_coverage":  result.mcp_coverage,
                    "wanjun_coverage": result.wanjun_coverage,
                    "finished_at":   _now_iso(),
                }
                failed.pop(td_str, None)
                logger.info("  OK rows=%d mcp_cov=%.1f%% wanjun_cov=%.1f%%",
                            result.rows_written,
                            result.mcp_coverage * 100,
                            result.wanjun_coverage * 100)
            elif result.status == "skipped":
                skip_count += 1
                completed[td_str] = {"status": "skipped", "finished_at": _now_iso()}
                logger.debug("  SKIP (already ingested)")
            else:
                fail_count += 1
                failed[td_str] = {
                    "status":      "failed",
                    "reason":      result.error or "unknown",
                    "finished_at": _now_iso(),
                }
                logger.warning("  FAIL: %s", result.error)

            # Save checkpoint after every day
            checkpoint["completed_dates"] = completed
            checkpoint["failed_dates"]    = failed
            ckpt_p.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=False))

            if args.sleep > 0:
                time.sleep(args.sleep)

    logger.info("=== Backfill complete: success=%d skip=%d fail=%d ===",
                success_count, skip_count, fail_count)
    latest = get_latest_available_date(cfg)
    logger.info("Latest available date in lake: %s", latest)

    if fail_count > 0:
        logger.warning("%d days failed. Re-run without --force to retry only failed days.", fail_count)
        sys.exit(1)


def _now_iso() -> str:
    from datetime import datetime
    return datetime.utcnow().isoformat()


if __name__ == "__main__":
    main()
