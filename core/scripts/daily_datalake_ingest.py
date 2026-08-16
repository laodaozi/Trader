#!/usr/bin/env python3.9
"""
scripts/daily_datalake_ingest.py — CycleRadar V7.6 每日盘后数据增量

盘后（15:30 后）自动将当日行情写入本地 Parquet 数据资产层。
由 cron_daily.sh 调用，接在 scanner/daily jobs 之后、calibration 之前。

用法：
    python3.9 scripts/daily_datalake_ingest.py           # --date auto
    python3.9 scripts/daily_datalake_ingest.py --date auto
    python3.9 scripts/daily_datalake_ingest.py --date 2026-07-07
    python3.9 scripts/daily_datalake_ingest.py --date auto --force

退出码：
    0 = success or skipped
    1 = failed (下游校准任务应跳过当日)
    2 = non-trading day or too early (not an error)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.datalake import (
    DatalakeConfig,
    _ensure_dirs,
    _load_universe,
    _manifest_path,
    _partition_path,
    check_freshness,
    get_latest_available_date,
    ingest_daily,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("daily_datalake_ingest")

# 盘后最早执行时间（15:30 CST）
MARKET_CLOSE_HOUR   = 15
MARKET_CLOSE_MINUTE = 30


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CycleRadar V7.6 每日盘后数据增量")
    p.add_argument("--date",       default="auto",
                   help="交易日 YYYY-MM-DD 或 auto（默认）")
    p.add_argument("--force",      action="store_true",
                   help="强制覆盖当日已有分区")
    p.add_argument("--max-lag-days", type=int, default=1,
                   help="freshness 检查最大滞后天数 (default: 1)")
    return p.parse_args()


def _is_weekday(d: date) -> bool:
    return d.weekday() < 5


def _resolve_trade_date(mode: str) -> tuple[date, str]:
    """
    Returns (trade_date, reason).
    reason: 'today' | 'last_trading_day' | 'specified'
    """
    if mode != "auto":
        return date.fromisoformat(mode), "specified"

    now = datetime.now()
    today = now.date()

    # Before 15:30 → don't ingest today
    if now.hour < MARKET_CLOSE_HOUR or (
        now.hour == MARKET_CLOSE_HOUR and now.minute < MARKET_CLOSE_MINUTE
    ):
        # Use last trading day
        td = today - timedelta(days=1)
        while not _is_weekday(td):
            td -= timedelta(days=1)
        return td, "last_trading_day_before_close"

    # After 15:30
    if _is_weekday(today):
        return today, "today"
    else:
        # Weekend → last Fri
        td = today - timedelta(days=1)
        while not _is_weekday(td):
            td -= timedelta(days=1)
        return td, "last_trading_day"


def _write_run_manifest(status: str, trade_date: date, result, cfg: DatalakeConfig) -> None:
    """Write datalake_daily_ingest entry to run_manifest.json for Admin health page."""
    manifest_path = Path("data") / "run_manifest.json"
    try:
        existing = {}
        if manifest_path.exists():
            existing = json.loads(manifest_path.read_text())

        tasks = existing.get("tasks", {})
        tasks["datalake_daily_ingest"] = {
            "status":        status,
            "trade_date":    trade_date.isoformat(),
            "finished_at":   datetime.utcnow().isoformat(),
            "rows_written":  getattr(result, "rows_written", 0),
            "primary_source": getattr(result, "primary_source", ""),
            "mcp_coverage":  getattr(result, "mcp_coverage", 0.0),
            "wanjun_coverage": getattr(result, "wanjun_coverage", 0.0),
            "ok_rows":       getattr(result, "ok_rows", 0),
            "warn_rows":     getattr(result, "warn_rows", 0),
            "bad_rows":      getattr(result, "bad_rows", 0),
            "output_path":   getattr(result, "output_path", ""),
            "is_usable":     status == "success",
            "error":         getattr(result, "error", None),
        }
        existing["tasks"] = tasks
        manifest_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
    except Exception as e:
        logger.warning("run_manifest update failed: %s", e)


def main() -> None:
    args = parse_args()
    cfg  = DatalakeConfig()
    _ensure_dirs(cfg)

    # Resolve trade date
    trade_date, reason = _resolve_trade_date(args.date)
    logger.info("Trade date resolved: %s (%s)", trade_date, reason)

    # Check if already ingested (unless force)
    if not args.force and _partition_path(trade_date, cfg).exists():
        latest = get_latest_available_date(cfg)
        if latest == trade_date:
            logger.info("Already ingested %s, skipping (use --force to override)", trade_date)
            _write_run_manifest("skipped", trade_date, None, cfg)
            sys.exit(0)

    # Load universe
    logger.info("Loading universe...")
    symbols = _load_universe(cfg)
    logger.info("Universe: %d symbols", len(symbols))

    # Ingest
    logger.info("Starting ingest for %s...", trade_date)
    result = ingest_daily(trade_date, symbols, cfg, force=args.force)

    # Write to run_manifest
    _write_run_manifest(result.status, trade_date, result, cfg)

    if result.status == "success":
        logger.info(
            "SUCCESS: %s | rows=%d ok=%d warn=%d bad=%d mcp=%.1f%% wanjun=%.1f%%",
            trade_date, result.rows_written, result.ok_rows, result.warn_rows, result.bad_rows,
            result.mcp_coverage * 100, result.wanjun_coverage * 100,
        )
        # Freshness check
        qs = check_freshness(trade_date, args.max_lag_days, cfg)
        if not qs.is_usable:
            logger.warning("Freshness check WARN: is_usable=False (bad_rows=%d, rows=%d/%d)",
                           qs.bad_rows, qs.rows_written, qs.universe_size)
        else:
            logger.info("Freshness check PASS")
        sys.exit(0)
    elif result.status == "skipped":
        logger.info("Skipped (already up to date)")
        sys.exit(0)
    else:
        logger.error("FAILED: %s", result.error)
        sys.exit(1)


if __name__ == "__main__":
    main()
