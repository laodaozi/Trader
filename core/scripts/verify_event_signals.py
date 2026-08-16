#!/usr/bin/env python3
"""
verify_event_signals.py — 事件标的 N 日回查验证
================================================
读取 data/hot_enrichment.json 里每条事件的 tickers，
用 enriched_at 日期之后 3/5/10 交易日的实际价格判断预测对错，
写入 data/event_hit_log.json。

用法：
    python3 core/scripts/verify_event_signals.py          # 全量回查
    python3 core/scripts/verify_event_signals.py --days 90  # 只看最近90天事件

Cron（每日收盘后）：
    30 16 * * 1-5  cd /opt/cycleradar-trader && python3.9 core/scripts/verify_event_signals.py >> data/logs/verify_event.log 2>&1
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── 路径 ──────────────────────────────────────────────────────────
PROJECT_ROOT   = Path(__file__).resolve().parents[2]
ENRICHMENT_FILE = PROJECT_ROOT / "data" / "hot_enrichment.json"
HIT_LOG_FILE    = PROJECT_ROOT / "data" / "event_hit_log.json"
LOG_DIR         = PROJECT_ROOT / "data" / "logs"

# 判定阈值
HIT_THRESHOLD  =  2.0   # 涨超 2% → HIT
MISS_THRESHOLD = -2.0   # 跌超 2% → MISS（方向 long）；涨超 2% → MISS（方向 short，按 reason 判）
HORIZONS       = [3, 5, 10]   # 回查天数


# ── K 线获取（复用 tracker_reflection.py 的降级链）─────────────────

def _strip_exchange(code: str) -> str:
    """'sh688327' / 'sz002049' → '688327' / '002049'"""
    return re.sub(r'^(sh|sz|bj)', '', code.lower())


def _kline_from_akshare(code: str, start_date: str, end_date: str) -> list[dict]:
    """东方财富 akshare 前复权日线，2 次重试。"""
    try:
        import akshare as ak
    except ImportError:
        return []
    start_ak = start_date.replace("-", "")
    end_ak   = end_date.replace("-", "")
    for attempt in range(2):
        try:
            df = ak.stock_zh_a_hist(
                symbol=code, period="daily",
                start_date=start_ak, end_date=end_ak, adjust="qfq",
            )
            if df is not None and not df.empty:
                return [
                    {"date": str(r["日期"]), "close": float(r["收盘"])}
                    for _, r in df.iterrows()
                ]
        except Exception:
            if attempt == 0:
                time.sleep(1.0)
    return []


def _kline_from_tencent(code: str, start_dt: datetime, end_dt: datetime) -> list[dict]:
    """腾讯直连降级（ECS 可用）。"""
    import requests
    tx_code = ("sh" if code.startswith(("6", "9", "5")) else "sz") + code
    url = (
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={tx_code},day,{start_dt.strftime('%Y-%m-%d')},"
        f"{end_dt.strftime('%Y-%m-%d')},640,qfq"
    )
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()
            if data.get("code") != 0:
                raise ValueError(f"API code={data.get('code')}")
            stock_data = data.get("data", {}).get(tx_code, {})
            klines = stock_data.get("qfqday") or stock_data.get("day") or []
            if klines:
                return [{"date": str(k[0]), "close": float(k[2])} for k in klines]
            if attempt < 2:
                time.sleep(1.0 * (2 ** attempt))
        except Exception:
            if attempt < 2:
                time.sleep(1.0 * (2 ** attempt))
    return []


def _get_forward_closes(code: str, base_date: str, max_days: int = 15) -> list[dict]:
    """
    获取 base_date 之后最多 max_days 个日历日的收盘价序列。
    返回 [{"date": "2026-07-15", "close": 12.34}, ...]，按日期升序。
    """
    pure_code = _strip_exchange(code)
    start_dt  = datetime.strptime(base_date, "%Y-%m-%d")
    end_dt    = start_dt + timedelta(days=max_days)

    rows = _kline_from_akshare(pure_code, base_date, end_dt.strftime("%Y-%m-%d"))
    if not rows:
        rows = _kline_from_tencent(pure_code, start_dt, end_dt)

    # 过滤掉 base_date 当天（取事件发出之后的价格）
    rows = [r for r in rows if r["date"] > base_date]
    rows.sort(key=lambda r: r["date"])
    return rows


def _get_base_close(code: str, base_date: str) -> Optional[float]:
    """获取 base_date 当天（或最近前一交易日）的收盘价作为基准价。"""
    pure_code = _strip_exchange(code)
    # 往前取 5 天确保能拿到基准价
    start_dt = datetime.strptime(base_date, "%Y-%m-%d") - timedelta(days=5)
    rows = _kline_from_akshare(pure_code, start_dt.strftime("%Y-%m-%d"), base_date)
    if not rows:
        rows = _kline_from_tencent(pure_code, start_dt, datetime.strptime(base_date, "%Y-%m-%d"))
    rows = [r for r in rows if r["date"] <= base_date]
    rows.sort(key=lambda r: r["date"])
    return rows[-1]["close"] if rows else None


def _verdict(pct: Optional[float], direction: str = "long") -> str:
    """根据涨跌幅和预测方向判断 HIT/MISS/NEUTRAL/PENDING。"""
    if pct is None:
        return "PENDING"
    if direction == "short":
        if pct <= -HIT_THRESHOLD:
            return "HIT"
        if pct >= abs(MISS_THRESHOLD):
            return "MISS"
    else:
        if pct >= HIT_THRESHOLD:
            return "HIT"
        if pct <= MISS_THRESHOLD:
            return "MISS"
    return "NEUTRAL"


def _infer_direction(reason: str) -> str:
    """从 reason 文字粗略推断多空方向。"""
    bearish_keywords = ["偏空", "看空", "减仓", "利空", "风险", "压制", "杀估值", "松动"]
    for kw in bearish_keywords:
        if kw in reason:
            return "short"
    return "long"


# ── 主逻辑 ────────────────────────────────────────────────────────

def verify_all(max_age_days: int = 90) -> dict:
    """
    遍历 hot_enrichment.json，对每条事件的每个 ticker 做 N 日回查。
    只处理 enriched_at 在 max_age_days 内、且距今 ≥ 3 个交易日（有足够数据）的事件。
    """
    if not ENRICHMENT_FILE.exists():
        print("⚠ hot_enrichment.json 不存在，跳过")
        return {}

    raw = json.loads(ENRICHMENT_FILE.read_text(encoding="utf-8"))
    now = datetime.now()
    cutoff_old = now - timedelta(days=max_age_days)
    cutoff_new = now - timedelta(days=3)   # 至少3天前才能验证

    results = []
    processed = skipped_new = skipped_old = skipped_no_ticker = 0

    for ev_hash, ev in raw.items():
        thesis = ev.get("thesis", "")
        # 跳过非市场内容
        if "非市场分析内容" in thesis:
            skipped_no_ticker += 1
            continue

        tickers = ev.get("tickers", [])
        if not tickers:
            skipped_no_ticker += 1
            continue

        enriched_at_str = ev.get("enriched_at", "")
        try:
            enriched_at = datetime.strptime(enriched_at_str[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            skipped_no_ticker += 1
            continue

        # 时间窗口过滤
        if enriched_at < cutoff_old:
            skipped_old += 1
            continue
        if enriched_at > cutoff_new:
            skipped_new += 1
            continue

        base_date = enriched_at.strftime("%Y-%m-%d")
        print(f"  [{ev_hash[:8]}] {ev.get('title','')[:30]}... base={base_date} tickers={len(tickers)}")

        ticker_results = []
        for tk in tickers:
            code = tk.get("code", "")
            name = tk.get("name", "")
            reason = tk.get("reason", "")
            direction = _infer_direction(reason)

            if not code:
                continue

            # 获取基准价
            base_close = _get_base_close(code, base_date)
            if base_close is None or base_close <= 0:
                ticker_results.append({
                    "code": _strip_exchange(code), "name": name,
                    "direction": direction, "base_close": None,
                    "verdicts": {}, "pcts": {}, "status": "NODATA"
                })
                continue

            # 获取前向收盘序列
            forward = _get_forward_closes(code, base_date, max_days=20)
            # 为每个 horizon 找最近的收盘价
            verdicts = {}
            pcts = {}
            for h in HORIZONS:
                # 找第 h 个交易日附近的收盘价（最多允许 +3 天偏差）
                candidates = [r for r in forward if r["date"] > base_date][:h + 2]
                if len(candidates) >= h:
                    close_h = candidates[h - 1]["close"]
                    pct = round((close_h / base_close - 1) * 100, 2)
                    pcts[f"d{h}"] = pct
                    verdicts[f"d{h}"] = _verdict(pct, direction)
                else:
                    pcts[f"d{h}"] = None
                    verdicts[f"d{h}"] = "PENDING"

            ticker_results.append({
                "code": _strip_exchange(code), "name": name,
                "direction": direction, "base_close": round(base_close, 3),
                "verdicts": verdicts, "pcts": pcts, "status": "OK"
            })
            time.sleep(0.1)   # 避免打爆行情接口

        if not ticker_results:
            continue

        # 计算事件级命中率（以 d5 为主）
        d5_verdicts = [t["verdicts"].get("d5") for t in ticker_results if t["verdicts"].get("d5") not in (None, "PENDING")]
        hit_rate_5d = round(d5_verdicts.count("HIT") / len(d5_verdicts), 2) if d5_verdicts else None

        results.append({
            "hash": ev_hash,
            "title": ev.get("title", ""),
            "source": ev.get("source", ""),
            "source_date": ev.get("source_date", ""),
            "enriched_at": enriched_at_str,
            "tickers": ticker_results,
            "hit_rate_5d": hit_rate_5d,
            "verdicts_count": len(d5_verdicts),
        })
        processed += 1

    print(f"\n  处理: {processed}条 | 跳过(太新): {skipped_new} | 跳过(太旧): {skipped_old} | 跳过(无标的): {skipped_no_ticker}")

    # ── 汇总统计 ──
    all_d5 = []
    by_source: dict[str, list[str]] = {}
    for ev in results:
        src = ev["source"]
        for tk in ev["tickers"]:
            v = tk["verdicts"].get("d5")
            if v and v not in ("PENDING", "NODATA"):
                all_d5.append(v)
                by_source.setdefault(src, []).append(v)

    def _hit_rate(lst: list[str]) -> Optional[float]:
        if not lst:
            return None
        return round(lst.count("HIT") / len(lst), 2)

    summary = {
        "overall_hit_rate_5d": _hit_rate(all_d5),
        "total_verdicts_5d": len(all_d5),
        "hits_5d": all_d5.count("HIT"),
        "misses_5d": all_d5.count("MISS"),
        "neutral_5d": all_d5.count("NEUTRAL"),
        "by_source": {src: _hit_rate(lst) for src, lst in by_source.items()},
    }

    output = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "max_age_days": max_age_days,
        "total_events": len(results),
        "summary": summary,
        "events": sorted(results, key=lambda e: e["enriched_at"], reverse=True),
    }

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    HIT_LOG_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  ✓ 写入 {HIT_LOG_FILE} ({len(results)} 条事件)")
    print(f"  整体命中率(5日): {summary['overall_hit_rate_5d']} ({summary['hits_5d']}/{summary['total_verdicts_5d']})")
    return output


def main():
    parser = argparse.ArgumentParser(description="事件标的 N 日回查验证")
    parser.add_argument("--days", type=int, default=90, help="只验证最近 N 天内的事件（默认90）")
    args = parser.parse_args()

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] verify_event_signals 启动 (max_age={args.days}天)")
    verify_all(max_age_days=args.days)
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 完成")


if __name__ == "__main__":
    main()
