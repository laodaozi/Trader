"""
core/datalake.py — CycleRadar V7.6 市场数据资产层（融合自 ~/交易员，B1 并入平台）

It knows:
- where daily Parquet files live (data/lake/market/daily/yyyy=YYYY/date=YYYY-MM-DD/part.parquet)
- the canonical daily_bar_v1 schema
- how to ingest one trading day from MCP primary source and wanjun fallback
- how to query historical bars through DuckDB
- how to validate freshness, coverage, and source quality

It does not know:
- how to rank stocks / run selection models / call LLMs
- how to generate trading contracts / decide position sizing
- how to interpret strategy performance

All callers must treat symbol+date as the primary key.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# ── MCP 参数常量（铁律：禁止散落硬编码）─────────────────────
MCP_KLINE_TOOL       = "get_kline"
MCP_KLINE_SERVICE    = "market_quote"
MCP_KLINE_TYPE       = 1          # 日线，不是 count
MCP_REINSTATEMENT    = 2          # 前复权，不是 fq_type
# keyword: 股票代码，不是 code

SCHEMA_VERSION = "daily_bar_v1"

# ── 固定 ETF 白名单（纳入 universe）────────────────────────
ETF_WHITELIST = ["512880", "512480", "510300", "510500", "159915", "588000"]

# ── 港股资产类型标识 ─────────────────────────────────────
def _is_hk_equity(symbol: str) -> bool:
    """港股 symbol 通常为 5 位数字且以 0-9 开头，无沪深前缀。"""
    return bool(symbol) and len(symbol) == 5 and symbol.isdigit()


# ────────────────────────────────────────────────────────────
# 配置
# ────────────────────────────────────────────────────────────

@dataclass
class DatalakeConfig:
    root_dir:                   str   = "data/lake/market"
    daily_dir:                  str   = "data/lake/market/daily"
    manifest_dir:               str   = "data/lake/market/manifest"
    checkpoint_dir:             str   = "data/lake/market/checkpoints"
    tmp_dir:                    str   = "data/lake/market/tmp"
    schema_version:             str   = SCHEMA_VERSION
    mcp_timeout_seconds:        int   = 20
    mcp_retry:                  int   = 1
    mcp_daily_primary_threshold: float = 0.80
    mcp_daily_ok_threshold:     float = 0.95
    wanjun_min_coverage:        float = 0.90
    close_warn_threshold:       float = 0.005
    close_bad_threshold:        float = 0.02
    volume_warn_threshold:      float = 0.20


# ────────────────────────────────────────────────────────────
# 结果数据类
# ────────────────────────────────────────────────────────────

@dataclass
class IngestResult:
    trade_date:      date
    universe_size:   int
    rows_written:    int
    primary_source:  str
    mcp_coverage:    float
    wanjun_coverage: float
    ok_rows:         int
    warn_rows:       int
    bad_rows:        int
    missing_symbols: List[str]
    output_path:     str
    manifest_path:   str
    error:           Optional[str] = None
    status:          str = "success"   # success | failed | skipped


@dataclass
class QualitySummary:
    trade_date:      date
    primary_source:  str
    universe_size:   int
    rows_written:    int
    mcp_coverage:    float
    wanjun_coverage: float
    bad_rows:        int
    warn_rows:       int
    is_usable:       bool


_DEFAULT_CFG = DatalakeConfig()

# ────────────────────────────────────────────────────────────
# 路径工具
# ────────────────────────────────────────────────────────────

def _partition_dir(trade_date: date, cfg: DatalakeConfig) -> Path:
    return Path(cfg.daily_dir) / f"yyyy={trade_date.year}" / f"date={trade_date.isoformat()}"

def _partition_path(trade_date: date, cfg: DatalakeConfig) -> Path:
    return _partition_dir(trade_date, cfg) / "part.parquet"

def _tmp_partition_path(trade_date: date, run_id: str, cfg: DatalakeConfig) -> Path:
    return (Path(cfg.tmp_dir) / "daily"
            / f"yyyy={trade_date.year}"
            / f"date={trade_date.isoformat()}"
            / f"run_id={run_id}" / "part.parquet")

def _manifest_path(cfg: DatalakeConfig) -> Path:
    return Path(cfg.manifest_dir) / "datalake_runs.jsonl"

def _checkpoint_path(cfg: DatalakeConfig) -> Path:
    return Path(cfg.checkpoint_dir) / "backfill_market_v1.json"

def _ensure_dirs(cfg: DatalakeConfig) -> None:
    for d in [cfg.daily_dir, cfg.manifest_dir, cfg.checkpoint_dir, cfg.tmp_dir]:
        Path(d).mkdir(parents=True, exist_ok=True)


# ────────────────────────────────────────────────────────────
# DuckDB 查询层
# ────────────────────────────────────────────────────────────

def _get_duckdb_conn(cfg: DatalakeConfig):
    """获取 DuckDB 连接，注册 daily_bars 视图（不用 hive_partitioning，date 列由 Parquet 自身携带）。"""
    try:
        import duckdb
    except ImportError:
        raise RuntimeError("duckdb 未安装，请执行: pip install duckdb pyarrow")
    con = duckdb.connect()
    glob_pattern = str(Path(cfg.daily_dir) / "**" / "*.parquet")
    # hive_partitioning=false: date 列由 Parquet 文件自身携带，避免与路径推断的字典列冲突
    con.execute(f"""
        CREATE OR REPLACE VIEW daily_bars AS
        SELECT * FROM read_parquet('{glob_pattern}', hive_partitioning = false)
    """)
    return con


def query(
    sql: str,
    params: Optional[Sequence[Any]] = None,
    config: Optional[DatalakeConfig] = None,
) -> pd.DataFrame:
    """
    Execute a DuckDB SQL query against the daily data lake.

    The daily table is exposed as view `daily_bars`.
    Default callers should filter quality_flag != 'bad'.
    symbol must be parameterized — do NOT concatenate into SQL string.
    """
    cfg = config or _DEFAULT_CFG
    con = _get_duckdb_conn(cfg)
    try:
        if params:
            return con.execute(sql, list(params)).df()
        return con.execute(sql).df()
    finally:
        con.close()


def _available_columns(cfg: DatalakeConfig) -> set:
    """
    检测 daily_bars 视图实际存在的列（用于列容错）。
    旧 schema 分区可能缺 turnover 等可选列，硬查会导致整表读不出。
    检测失败时返回空 set，调用方退回原字段交由 DuckDB 处理。
    """
    con = _get_duckdb_conn(cfg)
    try:
        df = con.execute("DESCRIBE daily_bars").df()
        return set(df["column_name"].tolist())
    except Exception as e:
        logger.warning("datalake: 列检测失败（%s），退回默认字段", e)
        return set()
    finally:
        con.close()


def get_history(
    symbols: Sequence[str],
    start_date: date,
    end_date: date,
    fields: Optional[Sequence[str]] = None,
    include_bad: bool = False,
    config: Optional[DatalakeConfig] = None,
) -> pd.DataFrame:
    """
    Return historical daily bars for symbols between start_date and end_date.

    Returns DataFrame sorted by (symbol, date).
    Excludes quality_flag='bad' by default.
    Does NOT fill missing/halt days.
    """
    cfg = config or _DEFAULT_CFG
    default_fields = [
        "symbol", "date", "open", "high", "low", "close",
        "volume", "amount", "turnover", "pct_chg",
        "ma5", "ma10", "ma20", "ma60", "source", "quality_flag",
    ]
    requested = list(fields or default_fields)
    # 列容错：对可选列缺失的历史/旧 schema 分区，仅选实际存在的列
    # （交易员早期 ingest 的分区无 turnover 列，硬查会导致整表读不出）
    actual_cols = _available_columns(cfg)
    if actual_cols:
        missing = [c for c in requested if c not in actual_cols]
        selected_fields = [c for c in requested if c in actual_cols]
        if missing:
            logger.warning(
                "datalake get_history: 分区缺失列 %s，已跳过（可选列容错）", missing
            )
        if not selected_fields:
            selected_fields = requested  # 兜底：检测异常时退回原字段，交给 DuckDB 报错
    else:
        selected_fields = requested
    selected = ", ".join(selected_fields)
    # quality_flag 缺失时不加 bad_filter，避免引用不存在的列
    has_qflag = (not actual_cols) or ("quality_flag" in actual_cols)
    bad_filter = "" if (include_bad or not has_qflag) else "AND quality_flag != 'bad'"
    placeholders = ", ".join(["?" for _ in symbols])
    sql = f"""
        SELECT {selected}
        FROM daily_bars
        WHERE symbol IN ({placeholders})
          AND date BETWEEN ? AND ?
          {bad_filter}
        ORDER BY symbol, date
    """
    params = list(symbols) + [start_date.isoformat(), end_date.isoformat()]
    return query(sql, params, cfg)


def get_latest_available_date(
    config: Optional[DatalakeConfig] = None,
) -> Optional[date]:
    """Return latest date with a successful ingest manifest."""
    cfg = config or _DEFAULT_CFG
    mp = _manifest_path(cfg)
    if not mp.exists():
        return None
    latest = None
    with open(mp) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("status") == "success" and rec.get("trade_date"):
                    d = date.fromisoformat(rec["trade_date"])
                    if latest is None or d > latest:
                        latest = d
            except (json.JSONDecodeError, ValueError):
                continue
    return latest


def load_daily_partition(
    trade_date: date,
    config: Optional[DatalakeConfig] = None,
    include_bad: bool = False,
) -> pd.DataFrame:
    """Load one date partition directly via DuckDB."""
    cfg = config or _DEFAULT_CFG
    p = _partition_path(trade_date, cfg)
    if not p.exists():
        return pd.DataFrame()
    bad_filter = "" if include_bad else "WHERE quality_flag != 'bad'"
    try:
        import duckdb
        con = duckdb.connect()
        df = con.execute(f"SELECT * FROM read_parquet('{p}') {bad_filter}").df()
        con.close()
        return df
    except Exception as e:
        logger.warning("load_daily_partition failed for %s: %s", trade_date, e)
        return pd.DataFrame()


def check_freshness(
    expected_trade_date: date,
    max_lag_days: int = 1,
    config: Optional[DatalakeConfig] = None,
) -> QualitySummary:
    """Validate whether the data lake is fresh enough for downstream jobs."""
    cfg = config or _DEFAULT_CFG
    latest = get_latest_available_date(cfg)
    if latest is None:
        return QualitySummary(
            trade_date=expected_trade_date, primary_source="none",
            universe_size=0, rows_written=0,
            mcp_coverage=0.0, wanjun_coverage=0.0,
            bad_rows=0, warn_rows=0, is_usable=False,
        )
    lag = (expected_trade_date - latest).days
    # Read latest manifest entry for the latest date
    mp = _manifest_path(cfg)
    rec: Dict[str, Any] = {}
    with open(mp) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if r.get("status") == "success" and r.get("trade_date") == latest.isoformat():
                    rec = r
            except json.JSONDecodeError:
                continue
    rows_written  = rec.get("rows_written", 0)
    universe_size = rec.get("universe_size", 1)
    bad_rows      = rec.get("bad_rows", 0)
    warn_rows     = rec.get("warn_rows", 0)
    coverage_ok   = rows_written / max(universe_size, 1) >= 0.90
    bad_ok        = bad_rows / max(rows_written, 1) <= 0.01
    is_usable     = lag <= max_lag_days and coverage_ok and bad_ok and rec.get("is_usable", True)
    return QualitySummary(
        trade_date=latest,
        primary_source=rec.get("primary_source", "unknown"),
        universe_size=universe_size,
        rows_written=rows_written,
        mcp_coverage=rec.get("mcp_coverage", 0.0),
        wanjun_coverage=rec.get("wanjun_coverage", 0.0),
        bad_rows=bad_rows,
        warn_rows=warn_rows,
        is_usable=is_usable,
    )

# ────────────────────────────────────────────────────────────
# Schema 与指标计算
# ────────────────────────────────────────────────────────────

_SCHEMA_DTYPES = {
    "schema_version": str,
    "symbol":         str,
    "source":         str,
    "source_detail":  str,
    "quality_flag":   str,
}

_REQUIRED_COLS = [
    "schema_version", "symbol", "date", "source",
    "source_detail", "quality_flag", "ingested_at", "updated_at",
]

_NULLABLE_COLS = [
    "open", "high", "low", "close", "volume", "amount",
    "turnover", "pct_chg", "ma5", "ma10", "ma20", "ma60",
]

_PRICE_COLS  = ["open", "high", "low", "close", "ma5", "ma10", "ma20", "ma60"]
_FLOAT4_COLS = ["turnover", "pct_chg"]


def compute_moving_averages(
    bars: pd.DataFrame,
    windows: Sequence[int] = (5, 10, 20, 60),
) -> pd.DataFrame:
    """
    Compute MA columns per symbol using close price, sorted by (symbol, date).
    Requires enough lookback rows before the target dates.
    Returns DataFrame with added ma{n} columns.
    """
    bars = bars.copy().sort_values(["symbol", "date"])
    for w in windows:
        col = f"ma{w}"
        bars[col] = (
            bars.groupby("symbol")["close"]
            .transform(lambda s: s.rolling(w, min_periods=w).mean())
        )
    return bars


def normalize_daily_schema(
    rows: pd.DataFrame,
    config: Optional[DatalakeConfig] = None,
) -> pd.DataFrame:
    """
    Enforce daily_bar_v1 columns, types, null conventions, and primary key uniqueness.
    - pct_chg stored as percent value (2.35 = 2.35%), NOT decimal (0.0235)
    - volume in shares (not lots)
    - price precision 4dp, amount 2dp, turnover/pct_chg 4dp
    """
    cfg = config or _DEFAULT_CFG
    df = rows.copy()

    # schema_version
    df["schema_version"] = cfg.schema_version

    # date → isoformat string for storage, then convert
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"]).dt.date

    # Numeric coercion
    for col in _PRICE_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(4)
    for col in _FLOAT4_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(4)
    if "amount" in df.columns:
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").round(2)
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
        df["volume"] = df["volume"].where(df["volume"].notna(), other=pd.NA)
        # volume must be int64 (shares); coerce floats
        df["volume"] = df["volume"].astype("Int64")

    # pct_chg: ensure stored as percent value (e.g. 2.35, not 0.0235)
    # wanjun returns decimal (0.0235); MCP returns decimal fraction per chg field
    # normalisation is done during row building, not here — this is a safety check
    if "pct_chg" in df.columns:
        # If max abs value < 0.5 AND not all NaN, likely still in decimal form → *100
        non_null = df["pct_chg"].dropna()
        if len(non_null) > 0 and non_null.abs().max() < 0.5:
            logger.warning("pct_chg appears to be decimal fraction, converting ×100")
            df["pct_chg"] = (df["pct_chg"] * 100).round(4)

    # timestamps
    now_ts = datetime.utcnow()
    if "ingested_at" not in df.columns:
        df["ingested_at"] = now_ts
    if "updated_at" not in df.columns:
        df["updated_at"] = now_ts

    # Defaults for required string cols
    for col in ["source", "quality_flag"]:
        if col not in df.columns:
            df[col] = "unknown"
    if "source_detail" not in df.columns:
        df["source_detail"] = "{}"

    # Drop duplicate primary keys — keep last (most recent source wins)
    df = df.drop_duplicates(subset=["symbol", "date"], keep="last")

    # Column ordering
    ordered = [
        "schema_version", "symbol", "date",
        "open", "high", "low", "close", "volume", "amount", "turnover", "pct_chg",
        "ma5", "ma10", "ma20", "ma60",
        "source", "source_detail", "quality_flag", "ingested_at", "updated_at",
    ]
    for col in ordered:
        if col not in df.columns:
            df[col] = None
    return df[ordered]


# ────────────────────────────────────────────────────────────
# MCP 数据获取
# ────────────────────────────────────────────────────────────

def _fetch_mcp_single(
    symbol: str, trade_date: date, lookback_days: int = 80
) -> Optional[Dict[str, Any]]:
    """
    Fetch one symbol's kline from MCP for a single trading day.
    Returns normalised dict or None on failure.
    MCP params: keyword (NOT code), kline_type=1 (NOT count), reinstatement_type=2 (NOT fq_type)
    """
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from core.trader_mcp import mcp_call
    except ImportError:
        logger.error("core.trader_mcp not found")
        return None

    start = (trade_date - timedelta(days=lookback_days + 30)).isoformat()
    end   = trade_date.isoformat()
    data  = mcp_call(MCP_KLINE_SERVICE, MCP_KLINE_TOOL, {
        "keyword":            symbol,
        "start_date":         start,
        "end_date":           end,
        "kline_type":         MCP_KLINE_TYPE,
        "reinstatement_type": MCP_REINSTATEMENT,
    })
    if not data:
        return None

    raw_bars = data if isinstance(data, list) else data.get("list", [])
    if not raw_bars:
        return None

    # Find the bar for trade_date
    target = trade_date.isoformat()
    bar = None
    for b in raw_bars:
        if b.get("trade_date", "") == target:
            bar = b
            break
    if bar is None:
        return None

    # MCP volume field: trade_lots = lots (手), need ×100 → shares
    # But scanner.py L84 uses trade_lots directly as "volume" without ×100.
    # We align with existing usage: trade_lots × 100 → shares per design decision
    raw_vol = float(bar.get("trade_lots") or bar.get("volume") or 0)
    volume_shares = int(raw_vol * 100) if raw_vol > 0 else None

    pct_chg_raw = float(bar.get("price_change_rate") or 0)
    # MCP price_change_rate: decimal form (0.05 = 5%) → convert to percent value (5.0)
    pct_chg = round(pct_chg_raw * 100, 4)

    return {
        "symbol":  symbol,
        "date":    target,
        "open":    float(bar.get("open_price")  or bar.get("open")  or 0) or None,
        "high":    float(bar.get("high_price")  or bar.get("high")  or 0) or None,
        "low":     float(bar.get("low_price")   or bar.get("low")   or 0) or None,
        "close":   float(bar.get("close_price") or bar.get("close") or 0) or None,
        "volume":  volume_shares,
        "amount":  None,  # MCP get_kline does not return amount reliably
        "turnover": None,
        "pct_chg": pct_chg if pct_chg_raw != 0 else None,
        "_source": "mcp",
        "_volume_unit_raw": "lot",
    }


def _fetch_mcp_batch(
    symbols: Sequence[str], trade_date: date, lookback_days: int = 80,
    sleep_every: int = 20, sleep_sec: float = 1.0,
) -> Tuple[Dict[str, Dict], List[str]]:
    """
    Fetch MCP klines for multiple symbols.
    Returns (successful_dict, failed_list).
    """
    success: Dict[str, Dict] = {}
    failed:  List[str]       = []
    for i, sym in enumerate(symbols):
        if i > 0 and i % sleep_every == 0:
            time.sleep(sleep_sec)
        row = _fetch_mcp_single(sym, trade_date, lookback_days)
        if row:
            success[sym] = row
        else:
            failed.append(sym)
    return success, failed

# ────────────────────────────────────────────────────────────
# wanjun 数据获取（封装调用 wanjun_screener.py）
# ────────────────────────────────────────────────────────────

def _fetch_wanjun_hist(
    symbols: Sequence[str], trade_date: date, lookback_days: int = 80,
) -> Dict[str, Dict]:
    """
    Fetch historical daily bars from wanjun (Tencent qfqday).
    Returns dict: symbol → normalised bar dict for trade_date.
    wanjun symbol format: 'sz000001' or '000001' (no prefix).
    """
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from scripts.wanjun_screener import fetch_hist_batch, _parse_tencent_symbol
    except ImportError:
        logger.warning("wanjun_screener not importable, skipping wanjun path")
        return {}

    # Convert plain codes to wanjun format (sz/sh prefix)
    def _to_wanjun_sym(sym: str) -> str:
        if re.match(r'^[a-zA-Z]{2}\d', sym):
            return sym  # already prefixed
        if sym.startswith(('0', '3', '2')):
            return 'sz' + sym
        elif sym.startswith(('6', '5', '9')):
            return 'sh' + sym
        return 'sz' + sym  # fallback

    import re
    wanjun_syms = [_to_wanjun_sym(s) for s in symbols if not _is_hk_equity(s)]
    hist_dict = fetch_hist_batch(wanjun_syms, days=lookback_days + 30)

    result: Dict[str, Dict] = {}
    target = trade_date.isoformat()

    for wsym, df in hist_dict.items():
        if df is None or df.empty:
            continue
        # Reverse-map wanjun symbol to plain code
        plain_code = wsym[2:] if re.match(r'^[a-zA-Z]{2}\d', wsym) else wsym
        # Find row for trade_date
        if "date" not in df.columns:
            continue
        df["date"] = pd.to_datetime(df["date"]).dt.date.astype(str)
        row_df = df[df["date"] == target]
        if row_df.empty:
            continue
        row = row_df.iloc[0]

        # wanjun volume: shares (腾讯接口返回的是股，非手)
        vol = row.get("volume", row.get("vol"))
        volume_shares = int(float(vol)) if vol is not None and not pd.isna(vol) else None

        # wanjun change_pct: wanjun_screener divides by 100 → decimal
        # We need percent value → ×100 back
        chg = row.get("change_pct", row.get("chg", row.get("pct_chg")))
        if chg is not None and not pd.isna(chg):
            chg_float = float(chg)
            # If abs < 0.5 it's decimal form → ×100
            if abs(chg_float) < 0.5:
                pct_chg = round(chg_float * 100, 4)
            else:
                pct_chg = round(chg_float, 4)
        else:
            pct_chg = None

        result[plain_code] = {
            "symbol":  plain_code,
            "date":    target,
            "open":    float(row.get("open", 0)) or None,
            "high":    float(row.get("high", 0)) or None,
            "low":     float(row.get("low", 0))  or None,
            "close":   float(row.get("close", row.get("trade", 0))) or None,
            "volume":  volume_shares,
            "amount":  float(row.get("amount", 0)) or None,
            "turnover": float(row.get("turnover_rate", row.get("turnover", 0))) or None,
            "pct_chg": pct_chg,
            "_source": "wanjun",
            "_volume_unit_raw": "share",
        }
    return result


# ────────────────────────────────────────────────────────────
# 双源合并与质量标注
# ────────────────────────────────────────────────────────────

def _merge_rows(
    mcp_rows:    Dict[str, Dict],
    wanjun_rows: Dict[str, Dict],
    all_symbols: Sequence[str],
    mcp_coverage:    float,
    wanjun_coverage: float,
    cfg: DatalakeConfig,
) -> List[Dict]:
    """
    Merge MCP primary and wanjun fallback rows per design spec.
    Assigns source, source_detail, quality_flag per row.
    """
    merged = []
    use_wanjun_primary = mcp_coverage < cfg.mcp_daily_primary_threshold

    for sym in all_symbols:
        mcp_row    = mcp_rows.get(sym)
        wanjun_row = wanjun_rows.get(sym)

        if use_wanjun_primary:
            # MCP coverage < 80%: wanjun is primary
            if wanjun_row:
                row = _build_row(sym, wanjun_row, None, "wanjun",
                                 "mcp_coverage_below_threshold", cfg)
            else:
                continue  # missing from both
        else:
            # MCP is primary
            if mcp_row and wanjun_row:
                row = _build_row(sym, mcp_row, wanjun_row,
                                 "mcp+wanjun" if mcp_coverage >= cfg.mcp_daily_ok_threshold else "mcp+wanjun",
                                 None, cfg)
            elif mcp_row:
                row = _build_row(sym, mcp_row, None, "mcp", None, cfg)
            elif wanjun_row:
                row = _build_row(sym, wanjun_row, None, "wanjun",
                                 "mcp_missing", cfg)
            else:
                continue  # missing from both

        if row:
            merged.append(row)
    return merged


def _build_row(
    symbol:          str,
    primary:         Dict,
    secondary:       Optional[Dict],
    source:          str,
    fallback_reason: Optional[str],
    cfg:             DatalakeConfig,
) -> Dict:
    """Build one normalised row with source_detail and quality_flag."""
    row = {k: v for k, v in primary.items() if not k.startswith("_")}
    row["symbol"] = symbol
    row["source"] = source

    checks: Dict[str, Any] = {}
    field_source: Dict[str, str] = {
        f: source.split("+")[0] for f in
        ["open", "high", "low", "close", "volume", "amount", "turnover", "pct_chg"]
    }
    quality_flag = "ok"

    if secondary:
        # Cross-validate close
        mc = primary.get("close")
        wc = secondary.get("close")
        if mc and wc and mc != 0:
            close_pct_diff = abs(mc - wc) / abs(mc)
            checks["close_pct_diff"] = round(close_pct_diff, 6)
            if close_pct_diff > cfg.close_bad_threshold:
                quality_flag = "bad"
            elif close_pct_diff > cfg.close_warn_threshold:
                quality_flag = "warn"

        # Cross-validate volume
        mv = primary.get("volume")
        wv = secondary.get("volume")
        if mv and wv and mv != 0:
            vol_pct_diff = abs(mv - wv) / abs(mv)
            checks["volume_pct_diff"] = round(vol_pct_diff, 4)
            if vol_pct_diff > cfg.volume_warn_threshold and quality_flag == "ok":
                quality_flag = "warn"

        # Fill missing fields from secondary
        for fld in ["amount", "turnover"]:
            if row.get(fld) is None and secondary.get(fld) is not None:
                row[fld] = secondary[fld]
                field_source[fld] = "wanjun"

    # pct_chg computed if missing
    if row.get("pct_chg") is None:
        field_source["pct_chg"] = "computed"

    # ETF / HK tagging
    is_hk = _is_hk_equity(symbol)
    asset_class = "hk_equity" if is_hk else "cn_equity"

    source_detail = {
        "primary": source.split("+")[0],
        "fallback": "wanjun" if secondary else None,
        "fallback_reason": fallback_reason,
        "field_source": field_source,
        "checks": checks,
        "raw": {
            "mcp_tool": f"{MCP_KLINE_SERVICE}/{MCP_KLINE_TOOL}",
            "mcp_reinstatement_type": MCP_REINSTATEMENT,
            "wanjun_source": "tencent_qfqday" if secondary or source == "wanjun" else None,
            "volume_unit_raw": primary.get("_volume_unit_raw", "unknown"),
        },
        "asset_class": asset_class,
    }

    row["source_detail"] = json.dumps(source_detail, ensure_ascii=False)
    row["quality_flag"]  = quality_flag
    return row


# ────────────────────────────────────────────────────────────
# Universe 加载
# ────────────────────────────────────────────────────────────

def _load_universe(cfg: DatalakeConfig) -> List[str]:
    """
    Build daily ingest universe:
      1. wanjun spot full A-share (via fetch_spot_data if available)
      2. tracker active symbols
      3. ETF whitelist
    HK equities excluded from universe (best-effort only, not in coverage denominator).
    """
    import re  # needed for exchange-prefix stripping below
    symbols: set = set(ETF_WHITELIST)

    # Tracker active symbols
    try:
        tracker_path = Path("data") / "trader_tracker.jsonl"
        if tracker_path.exists():
            with open(tracker_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        code = rec.get("code") or rec.get("symbol")
                        if code and not _is_hk_equity(str(code)):
                            symbols.add(str(code))
                    except json.JSONDecodeError:
                        continue
    except Exception as e:
        logger.warning("tracker_log read failed: %s", e)

    # Watchlist symbols
    try:
        wl_path = Path("data") / "pool.json"
        if not wl_path.exists():
            wl_path = Path("data") / "watchlist.json"
        if wl_path.exists():
            with open(wl_path) as f:
                wl = json.load(f)
            if isinstance(wl, dict):
                stocks = wl.get("stocks", wl)
                for code in stocks:
                    if not _is_hk_equity(str(code)):
                        symbols.add(str(code))
            elif isinstance(wl, list):
                for item in wl:
                    code = item.get("code") or item.get("symbol") if isinstance(item, dict) else str(item)
                    if code and not _is_hk_equity(str(code)):
                        symbols.add(str(code))
    except Exception as e:
        logger.warning("watchlist read failed: %s", e)

    # Attempt wanjun spot for full market
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from scripts.wanjun_screener import fetch_spot_data
        spot_df = fetch_spot_data()
        if not spot_df.empty:
            code_col = "code" if "code" in spot_df.columns else "symbol"
            for sym in spot_df[code_col].dropna():
                sym_str = str(sym)
                # Strip exchange prefix if present
                if re.match(r'^[a-zA-Z]{2}\d', sym_str):
                    sym_str = sym_str[2:]
                if sym_str and not _is_hk_equity(sym_str):
                    symbols.add(sym_str)
    except Exception as e:
        logger.warning("wanjun fetch_spot_data failed (using tracker+ETF universe): %s", e)

    import re
    return sorted(symbols)


# ────────────────────────────────────────────────────────────
# 幂等写入
# ────────────────────────────────────────────────────────────

def write_daily_partition(
    trade_date: date,
    rows: pd.DataFrame,
    result: IngestResult,
    config: Optional[DatalakeConfig] = None,
) -> str:
    """
    Write Parquet via tmp → validate → atomic replace target partition.
    Returns final output path string.
    Raises RuntimeError if atomic replace fails (caller should log and abort).
    """
    cfg = config or _DEFAULT_CFG
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    tmp_p  = _tmp_partition_path(trade_date, run_id, cfg)
    tgt_p  = _partition_path(trade_date, cfg)
    bak_p  = tgt_p.parent / f"part.parquet.bak-{run_id}"

    tmp_p.parent.mkdir(parents=True, exist_ok=True)
    tgt_p.parent.mkdir(parents=True, exist_ok=True)

    # Ensure date column is string for Parquet compatibility with DuckDB hive partitioning
    rows = rows.copy()
    if "date" in rows.columns:
        rows["date"] = rows["date"].apply(
            lambda d: d.isoformat() if hasattr(d, "isoformat") else str(d)
        )

    # Write tmp
    rows.to_parquet(str(tmp_p), index=False, engine="pyarrow")

    # Validate tmp using pandas (avoid pyarrow schema merge issues)
    df_check = pd.read_parquet(str(tmp_p), columns=["symbol", "date"])
    assert len(df_check) == len(rows), f"row count mismatch: {len(df_check)} vs {len(rows)}"
    if df_check.duplicated().any():
        tmp_p.unlink(missing_ok=True)
        raise ValueError("Duplicate primary keys in Parquet output")

    # Atomic replace
    if tgt_p.exists():
        tgt_p.rename(bak_p)
    try:
        tmp_p.rename(tgt_p)
        if bak_p.exists():
            bak_p.unlink()
    except Exception as e:
        # Restore backup
        if bak_p.exists():
            bak_p.rename(tgt_p)
        raise RuntimeError(f"Atomic replace failed: {e}") from e

    # Cleanup tmp parent if empty
    try:
        tmp_p.parent.rmdir()
    except OSError:
        pass

    return str(tgt_p)


# ────────────────────────────────────────────────────────────
# Manifest 写入
# ────────────────────────────────────────────────────────────

def _write_manifest(result: IngestResult, cfg: DatalakeConfig) -> str:
    """Append one line to datalake_runs.jsonl. Returns manifest path."""
    mp = _manifest_path(cfg)
    mp.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "run_id":        datetime.utcnow().strftime("%Y%m%dT%H%M%S"),
        "schema_version": cfg.schema_version,
        "task":          "ingest_daily",
        "status":        result.status,
        "trade_date":    result.trade_date.isoformat(),
        "universe_size": result.universe_size,
        "rows_written":  result.rows_written,
        "primary_source": result.primary_source,
        "mcp_coverage":  result.mcp_coverage,
        "wanjun_coverage": result.wanjun_coverage,
        "ok_rows":       result.ok_rows,
        "warn_rows":     result.warn_rows,
        "bad_rows":      result.bad_rows,
        "missing_symbols": result.missing_symbols[:20],  # cap to avoid giant lines
        "output_path":   result.output_path,
        "is_usable":     result.status == "success" and result.bad_rows == 0,
        "error":         result.error,
        "finished_at":   datetime.utcnow().isoformat(),
    }
    with open(mp, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return str(mp)


# ────────────────────────────────────────────────────────────
# 主入口：ingest_daily
# ────────────────────────────────────────────────────────────

def ingest_daily(
    trade_date: date,
    symbols: Optional[Sequence[str]] = None,
    config: Optional[DatalakeConfig] = None,
    force: bool = False,
    mcp_symbols: Optional[Sequence[str]] = None,
) -> IngestResult:
    """
    Ingest one trading day into the local Parquet data lake.

    If force=False and the date partition already exists with a successful
    manifest entry, returns a skipped IngestResult without rewriting.
    If force=True, rebuilds and atomically replaces the date partition.

    symbols: if None, loads universe dynamically via _load_universe().
    mcp_symbols: if provided, only these symbols are fetched from MCP (whitelist).
                 All other symbols go directly to wanjun.
                 If None (default), original behaviour (full MCP batch for all symbols).
                 Pass an empty list [] to skip MCP entirely (wanjun-only mode).
    """
    cfg = config or _DEFAULT_CFG
    _ensure_dirs(cfg)
    import re

    # Skip if already ingested
    if not force and _partition_path(trade_date, cfg).exists():
        existing = get_latest_available_date(cfg)
        if existing == trade_date:
            logger.info("ingest_daily: %s already ingested, skipping", trade_date)
            return IngestResult(
                trade_date=trade_date, universe_size=0, rows_written=0,
                primary_source="skipped", mcp_coverage=0.0, wanjun_coverage=0.0,
                ok_rows=0, warn_rows=0, bad_rows=0, missing_symbols=[],
                output_path=str(_partition_path(trade_date, cfg)),
                manifest_path=str(_manifest_path(cfg)),
                status="skipped",
            )

    # Load universe
    all_symbols = list(symbols) if symbols else _load_universe(cfg)
    a_share_symbols = [s for s in all_symbols if not _is_hk_equity(s)]
    universe_size = len(a_share_symbols)
    logger.info("ingest_daily: %s | universe=%d symbols", trade_date, universe_size)

    # Fetch MCP (primary, optionally limited to a whitelist)
    if mcp_symbols is None:
        # Original behaviour: full MCP batch for all A-share symbols
        mcp_fetch_list = a_share_symbols
    else:
        # Whitelist mode: only fetch the provided symbols from MCP
        mcp_fetch_set = set(mcp_symbols)
        mcp_fetch_list = [s for s in a_share_symbols if s in mcp_fetch_set]

    if mcp_fetch_list:
        logger.info("Fetching MCP klines (%d symbols)...", len(mcp_fetch_list))
        mcp_rows, mcp_failed = _fetch_mcp_batch(
            mcp_fetch_list, trade_date,
            sleep_every=20, sleep_sec=1.0,
        )
    else:
        logger.info("MCP fetch skipped (mcp_symbols=[]).")
        mcp_rows, mcp_failed = {}, []

    mcp_coverage = len(mcp_rows) / max(universe_size, 1)
    logger.info("MCP coverage: %.1f%% (%d/%d)", mcp_coverage * 100, len(mcp_rows), universe_size)

    # Fetch wanjun (fallback/validation)
    logger.info("Fetching wanjun klines...")
    wanjun_rows = _fetch_wanjun_hist(a_share_symbols, trade_date)
    wanjun_coverage = len(wanjun_rows) / max(universe_size, 1)
    logger.info("wanjun coverage: %.1f%% (%d/%d)", wanjun_coverage * 100, len(wanjun_rows), universe_size)

    # Abort if wanjun coverage too low (can't even validate)
    if wanjun_coverage < cfg.wanjun_min_coverage and mcp_coverage < cfg.mcp_daily_ok_threshold:
        msg = (f"wanjun coverage {wanjun_coverage:.2f} < {cfg.wanjun_min_coverage} "
               f"and MCP coverage {mcp_coverage:.2f} < {cfg.mcp_daily_ok_threshold}")
        logger.error("ingest_daily ABORT: %s", msg)
        result = IngestResult(
            trade_date=trade_date, universe_size=universe_size,
            rows_written=0, primary_source="none",
            mcp_coverage=mcp_coverage, wanjun_coverage=wanjun_coverage,
            ok_rows=0, warn_rows=0, bad_rows=0,
            missing_symbols=list(set(a_share_symbols) - set(mcp_rows) - set(wanjun_rows)),
            output_path="", manifest_path="", error=msg, status="failed",
        )
        _write_manifest(result, cfg)
        return result

    # Determine primary source
    primary_source = "wanjun" if mcp_coverage < cfg.mcp_daily_primary_threshold else "mcp"

    # Merge rows
    merged_list = _merge_rows(
        mcp_rows, wanjun_rows, a_share_symbols,
        mcp_coverage, wanjun_coverage, cfg,
    )

    if not merged_list:
        msg = "No rows produced after merge"
        result = IngestResult(
            trade_date=trade_date, universe_size=universe_size,
            rows_written=0, primary_source=primary_source,
            mcp_coverage=mcp_coverage, wanjun_coverage=wanjun_coverage,
            ok_rows=0, warn_rows=0, bad_rows=0,
            missing_symbols=a_share_symbols, output_path="", manifest_path="",
            error=msg, status="failed",
        )
        _write_manifest(result, cfg)
        return result

    # Build DataFrame
    df = pd.DataFrame(merged_list)
    df["date"] = pd.to_datetime(df["date"]).dt.date

    # Compute MAs (requires enough lookback; use whatever we have)
    # For single-day ingest we won't have enough lookback from this day alone.
    # MA computation is deferred to backfill which loads full history.
    for col in ["ma5", "ma10", "ma20", "ma60"]:
        if col not in df.columns:
            df[col] = None

    # Normalize schema
    df = normalize_daily_schema(df, cfg)
    now_ts = datetime.utcnow()
    df["ingested_at"] = now_ts
    df["updated_at"]  = now_ts

    # Quality stats
    ok_rows   = int((df["quality_flag"] == "ok").sum())
    warn_rows = int((df["quality_flag"] == "warn").sum())
    bad_rows  = int((df["quality_flag"] == "bad").sum())
    missing   = sorted(set(a_share_symbols) - set(df["symbol"].tolist()))

    # Write partition
    try:
        out_path = write_daily_partition(trade_date, df, None, cfg)
    except Exception as e:
        msg = str(e)
        result = IngestResult(
            trade_date=trade_date, universe_size=universe_size,
            rows_written=0, primary_source=primary_source,
            mcp_coverage=mcp_coverage, wanjun_coverage=wanjun_coverage,
            ok_rows=ok_rows, warn_rows=warn_rows, bad_rows=bad_rows,
            missing_symbols=missing, output_path="", manifest_path="",
            error=msg, status="failed",
        )
        _write_manifest(result, cfg)
        return result

    result = IngestResult(
        trade_date=trade_date, universe_size=universe_size,
        rows_written=len(df), primary_source=primary_source,
        mcp_coverage=mcp_coverage, wanjun_coverage=wanjun_coverage,
        ok_rows=ok_rows, warn_rows=warn_rows, bad_rows=bad_rows,
        missing_symbols=missing, output_path=out_path,
        manifest_path=str(_manifest_path(cfg)),
        status="success",
    )
    _write_manifest(result, cfg)
    logger.info("ingest_daily DONE: %s | rows=%d ok=%d warn=%d bad=%d",
                trade_date, len(df), ok_rows, warn_rows, bad_rows)
    return result


# ────────────────────────────────────────────────────────────
# backfill（多日）
# ────────────────────────────────────────────────────────────

def backfill(
    start_date: date,
    end_date: date,
    symbols: Optional[Sequence[str]] = None,
    config: Optional[DatalakeConfig] = None,
    batch_days: int = 20,
    force: bool = False,
    sleep_seconds: float = 0.5,
) -> List[IngestResult]:
    """
    Backfill multiple trading days. Checkpoint-aware: completed dates are skipped.
    """
    cfg = config or _DEFAULT_CFG
    _ensure_dirs(cfg)

    # Load checkpoint
    ckpt_p = _checkpoint_path(cfg)
    checkpoint: Dict[str, Any] = {"completed_dates": {}, "failed_dates": {}}
    if ckpt_p.exists():
        try:
            checkpoint = json.loads(ckpt_p.read_text())
        except Exception:
            pass

    # Generate trading days (skip weekends; non-trading days detected by empty data)
    all_dates = []
    cur = start_date
    while cur <= end_date:
        if cur.weekday() < 5:  # Mon-Fri
            all_dates.append(cur)
        cur += timedelta(days=1)

    results = []
    completed = checkpoint.get("completed_dates", {})
    failed    = checkpoint.get("failed_dates", {})

    sym_list = list(symbols) if symbols else None

    for i, td in enumerate(all_dates):
        td_str = td.isoformat()

        # Skip if checkpoint says done and partition exists
        if not force and td_str in completed:
            if _partition_path(td, cfg).exists():
                logger.debug("backfill: skip %s (checkpoint ok)", td_str)
                continue

        logger.info("backfill: ingesting %s (%d/%d)", td_str, i + 1, len(all_dates))
        result = ingest_daily(td, sym_list, cfg, force=force)
        results.append(result)

        if result.status == "success":
            completed[td_str] = {
                "status": "success",
                "rows_written": result.rows_written,
                "primary_source": result.primary_source,
                "mcp_coverage": result.mcp_coverage,
                "wanjun_coverage": result.wanjun_coverage,
                "finished_at": datetime.utcnow().isoformat(),
            }
            failed.pop(td_str, None)
        elif result.status == "skipped":
            completed[td_str] = {"status": "skipped", "finished_at": datetime.utcnow().isoformat()}
        else:
            failed[td_str] = {
                "status": "failed",
                "reason": result.error or "unknown",
                "finished_at": datetime.utcnow().isoformat(),
            }

        # Save checkpoint after every day
        checkpoint["completed_dates"] = completed
        checkpoint["failed_dates"]    = failed
        ckpt_p.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=False))

        if sleep_seconds > 0 and i < len(all_dates) - 1:
            time.sleep(sleep_seconds)

    return results

