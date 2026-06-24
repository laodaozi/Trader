"""
modules/signals.py — Layer 4: 日线买卖点（轻量三步确认）

Step 1  NX 日线信号 — 典型价 PRINT=(3C+H+L+O)/6，RSI(6)转折点
Step 2  MA 多头排列检查 — MA5>MA10>MA20>MA60 + 价格回踩均线
Step 3  Fibonacci 支撑位 — 近60日高低点 0.382/0.5/0.618 回撤

输出：
   status      可介入 / 观望 / 止损警戒
   expires     信号过期日（+3 交易日，跳过周末）
   entry_zone  [low, high]  建议介入区间
  stop_loss   止损价（MA10下方3% or 入场×0.95，取较高者）
  take_profit [t1, t2]    目标位（Fib延伸 1.382 / 1.618）
  signal_basis  命中信号列表
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from core.trader_mcp import mcp_call


# ── K 线获取 ──────────────────────────────────────────────

def _get_kline(code: str, end_date: str, days: int = 90) -> tuple[str, list[dict]]:
    """返回 (stock_name, bars)，bars 升序，每条含 open/high/low/close/volume/date。"""
    start = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=days + 30)).strftime("%Y-%m-%d")
    data  = mcp_call("market_quote", "get_kline", {
        "keyword":           code,
        "start_date":        start,
        "end_date":          end_date,
        "kline_type":        1,
        "reinstatement_type": 2,
    })
    raw = data if isinstance(data, list) else data.get("list", [])
    name = raw[0].get("quote_name", "") if raw else ""
    bars = sorted([
        {
            "date":   b.get("trade_date", ""),
            "open":   float(b.get("open_price")  or b.get("open")  or 0),
            "high":   float(b.get("high_price")  or b.get("high")  or 0),
            "low":    float(b.get("low_price")   or b.get("low")   or 0),
            "close":  float(b.get("close_price") or b.get("close") or 0),
            "volume": float(b.get("trade_lots")  or b.get("volume") or 0),
        }
        for b in raw
        if b.get("close_price") or b.get("close")
    ], key=lambda x: x["date"])
    return name, bars


# ── 均线 ─────────────────────────────────────────────────

def _ma(values: list[float], n: int) -> list[Optional[float]]:
    result: list[Optional[float]] = []
    for i in range(len(values)):
        result.append(sum(values[i - n + 1: i + 1]) / n if i >= n - 1 else None)
    return result


# ── Step 1: NX 日线信号 ──────────────────────────────────

def _compute_nx(bars: list[dict]) -> str:
    """
    NX 信号：典型价 PRINT = (3×Close + High + Low + Open) / 6
    RSI(PRINT, 6)，取最近 3 个 RSI 值判断转折：
      上升后回落 → "sell"
      下降后回升 → "buy"
      否则 → "neutral"
    """
    if len(bars) < 15:
        return "neutral"

    prints = [(3 * b["close"] + b["high"] + b["low"] + b["open"]) / 6 for b in bars]

    # RSI(6) 计算
    period = 6
    gains, losses = [], []
    for i in range(1, len(prints)):
        diff = prints[i] - prints[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    if len(gains) < period:
        return "neutral"

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsi_series: list[float] = []
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        rsi_series.append(100 - 100 / (1 + rs))

    if len(rsi_series) < 3:
        return "neutral"

    r0, r1, r2 = rsi_series[-3], rsi_series[-2], rsi_series[-1]

    if r1 > r0 and r1 > r2:
        return "sell"
    if r1 < r0 and r1 < r2:
        return "buy"
    # 趋势延续：RSI 连续上升
    if r0 < r1 < r2:
        return "rising"
    return "neutral"


# ── Step 2: MA 排列检查 ──────────────────────────────────

def _check_ma(bars: list[dict]) -> dict:
    """
    返回 {ma5, ma10, ma20, ma60, close, alignment, touch_zone}
    alignment: "bull_full" / "bull_partial" / "bear" / "neutral"
    touch_zone: "ma10" / "ma20" / "ma60" / None  — 价格在哪条均线附近（±3%）
    """
    closes = [b["close"] for b in bars]
    ma5_s  = _ma(closes, 5)
    ma10_s = _ma(closes, 10)
    ma20_s = _ma(closes, 20)
    ma60_s = _ma(closes, 60)

    ma5  = ma5_s[-1]
    ma10 = ma10_s[-1]
    ma20 = ma20_s[-1]
    ma60 = ma60_s[-1]
    close = closes[-1]

    if ma5 is None or ma10 is None:
        return {"alignment": "neutral", "touch_zone": None,
                "ma5": None, "ma10": None, "ma20": None, "ma60": None, "close": close}

    # 排列判断
    if ma60 is not None and ma5 > ma10 > ma20 > ma60:
        alignment = "bull_full"
    elif ma20 is not None and ma5 > ma10 > ma20:
        alignment = "bull_partial"
    elif ma5 < ma10:
        alignment = "bear"
    else:
        alignment = "neutral"

    # 价格靠近哪条均线（±3%）
    touch_zone = None
    for label, ma_val in [("ma10", ma10), ("ma20", ma20), ("ma60", ma60)]:
        if ma_val and abs(close - ma_val) / ma_val <= 0.03:
            touch_zone = label
            break

    return {
        "alignment":  alignment,
        "touch_zone": touch_zone,
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
        "close": close,
    }


# ── Step 3: Fibonacci 支撑位 ─────────────────────────────

def _compute_fibonacci(bars: list[dict], lookback: int = 60) -> dict:
    """
    基于近 lookback 日高低点计算 Fibonacci 回撤和延伸。
    支撑带：0.382 / 0.5 / 0.618 回撤
    目标位：1.382 / 1.618 延伸（相对低点）
    返回 {swing_high, swing_low, supports, targets, current_zone}
    current_zone: "support" / "above_support" / "below_support"
    """
    window = bars[-lookback:] if len(bars) >= lookback else bars
    if len(window) < 10:
        return {"supports": [], "targets": [], "current_zone": "unknown",
                "swing_high": 0, "swing_low": 0}

    highs = [b["high"] for b in window]
    lows  = [b["low"]  for b in window]
    swing_high = max(highs)
    swing_low  = min(lows)
    diff       = swing_high - swing_low
    close      = bars[-1]["close"]

    if diff < 0.001:
        return {"supports": [], "targets": [], "current_zone": "unknown",
                "swing_high": swing_high, "swing_low": swing_low}

    supports = [
        round(swing_high - diff * r, 2)
        for r in (0.382, 0.5, 0.618)
    ]
    targets = [
        round(swing_low + diff * r, 2)
        for r in (1.382, 1.618)
    ]

    # 判断价格是否在支撑带内（±1.5%）
    in_support = any(abs(close - s) / s <= 0.015 for s in supports)
    current_zone = "support" if in_support else (
        "above_support" if close > supports[0] else "below_support"
    )

    return {
        "swing_high":   swing_high,
        "swing_low":    swing_low,
        "supports":     supports,
        "targets":      targets,
        "current_zone": current_zone,
    }


# ── 信号有效期 ─────────────────────────────────────────────

def _signal_expires(signal_date: str) -> str:
    """信号 +3 交易日过期（跳过周末的简单近似）。"""
    dt = datetime.strptime(signal_date, "%Y-%m-%d")
    days_added = 0
    while days_added < 3:
        dt += timedelta(days=1)
        if dt.weekday() < 5:  # Mon-Fri
            days_added += 1
    return dt.strftime("%Y-%m-%d")


# ── 主函数：三步确认综合判断 ─────────────────────────────

def analyze(code: str, date: str, total_capital: float = 0, verbose: bool = False) -> dict:
    """
    对单只股票做三步确认，返回介入报告。

    Parameters
    ----------
    code          : 股票代码
    date          : 分析日期 YYYY-MM-DD
    total_capital : 总资金（元），用于计算建议仓位；0 表示不计算
    verbose       : 是否打印调试信息
    """
    name, bars = _get_kline(code, date, days=90)
    if not name:
        name = code
    if len(bars) < 15:
        return {"code": code, "name": name, "status": "数据不足", "signal_basis": []}

    nx   = _compute_nx(bars)
    ma   = _check_ma(bars)
    fib  = _compute_fibonacci(bars, lookback=60)
    close = bars[-1]["close"]

    signals: list[str] = []

    # NX 信号
    if nx == "buy":
        signals.append("NX买点")
    elif nx == "rising":
        signals.append("NX趋势上升")
    elif nx == "sell":
        signals.append("NX卖点")

    # MA 信号
    if ma["alignment"] in ("bull_full", "bull_partial"):
        signals.append("MA多头排列")
    if ma["touch_zone"] == "ma10":
        signals.append("回踩MA10")
    elif ma["touch_zone"] == "ma20":
        signals.append("回踩MA20")
    elif ma["touch_zone"] == "ma60":
        signals.append("回踩MA60")

    # Fibonacci 信号
    if fib["current_zone"] == "support":
        signals.append(f"Fib支撑带({fib['supports'][1]:.2f})")

    # ── 状态判断 ──────────────────────────────────────────
    has_buy   = nx in ("buy", "rising") or ma["touch_zone"] is not None
    has_bull  = ma["alignment"] in ("bull_full", "bull_partial")
    is_sell   = nx == "sell" and ma["alignment"] == "bear"
    fib_ok    = fib["current_zone"] in ("support", "above_support")

    if is_sell or ma["alignment"] == "bear":
        status = "止损警戒"
    elif has_buy and has_bull and fib_ok:
        status = "可介入"
    elif has_bull and fib_ok:
        status = "观望"
    elif has_buy:
        status = "观望"
    else:
        status = "观望"

    # ── 介入区间 ─────────────────────────────────────────
    entry_low = entry_high = None
    if ma["ma10"] and ma["touch_zone"] == "ma10":
        entry_low  = round(ma["ma10"] * 0.98, 2)
        entry_high = round(ma["ma10"] * 1.02, 2)
    elif ma["ma20"] and ma["touch_zone"] == "ma20":
        entry_low  = round(ma["ma20"] * 0.98, 2)
        entry_high = round(ma["ma20"] * 1.02, 2)
    elif fib["current_zone"] == "support" and fib["supports"]:
        ref = fib["supports"][1]  # 0.5 回撤
        entry_low  = round(ref * 0.985, 2)
        entry_high = round(ref * 1.015, 2)
    else:
        entry_low  = round(close * 0.98, 2)
        entry_high = round(close * 1.01, 2)

    # ── 止损位 ───────────────────────────────────────────
    stop_candidates = [entry_low * 0.95]
    if ma["ma10"]:
        stop_candidates.append(round(ma["ma10"] * 0.97, 2))
    if ma["ma20"]:
        stop_candidates.append(round(ma["ma20"] * 0.97, 2))
    stop_loss = round(max(stop_candidates), 2)

    # ── 目标位 ───────────────────────────────────────────
    take_profit = fib["targets"] if fib["targets"] else [
        round(close * 1.10, 2), round(close * 1.20, 2)
    ]

    # ── 建议仓位 ─────────────────────────────────────────
    position_size = 0
    if total_capital > 0 and status == "可介入":
        risk_per_trade = total_capital * 0.02       # 单笔最大亏损2%总资金
        risk_per_share = max(entry_low - stop_loss, 0.01)
        shares = int(risk_per_trade / risk_per_share / 100) * 100  # 取整百股
        position_size = round(shares * entry_low, 0)

    result = {
        "code":          code,
        "name":          name,
        "date":          date,
        "expires":       _signal_expires(date),
        "status":        status,
        "entry_zone":    [entry_low, entry_high],
        "stop_loss":     stop_loss,
        "take_profit":   take_profit,
        "position_size": int(position_size),
        "signal_basis":  signals,
        "nx_signal":     nx,
        "ma_alignment":  ma["alignment"],
        "fib_zone":      fib["current_zone"],
        "close":         close,
    }

    if verbose:
        _print_signal(result, ma, fib)

    return result


# ── 批量分析 ─────────────────────────────────────────────

def batch_analyze(codes: list[str], date: str, total_capital: float = 0,
                  filter_status: str = "可介入") -> list[dict]:
    """
    批量分析多只股票，返回符合 filter_status 的列表（按信号数量降序）。
    filter_status=None 返回全部。
    """
    results = []
    for code in codes:
        r = analyze(code, date, total_capital=total_capital)
        if filter_status is None or r["status"] == filter_status:
            results.append(r)
    return sorted(results, key=lambda x: len(x["signal_basis"]), reverse=True)


# ── 格式化打印 ───────────────────────────────────────────

def _print_signal(r: dict, ma: dict, fib: dict):
    status_icon = {"可介入": "🟢", "观望": "🟡", "止损警戒": "🔴", "数据不足": "⚫"}.get(r["status"], "⚪")
    print(f"\n  {status_icon} {r['code']} {r['name']}  [{r['status']}]")
    print(f"     收盘: {r['close']:.2f}  MA排列: {r['ma_alignment']}  NX: {r['nx_signal']}  Fib: {r['fib_zone']}")
    print(f"     介入区: {r['entry_zone'][0]:.2f} ~ {r['entry_zone'][1]:.2f}  止损: {r['stop_loss']:.2f}")
    print(f"     目标:  {' / '.join(f'{t:.2f}' for t in r['take_profit'][:2])}")
    print(f"     信号:  {' | '.join(r['signal_basis']) or '无'}")
    if fib.get("supports"):
        sups = " / ".join(f"{s:.2f}" for s in fib["supports"])
        print(f"     Fib支撑: {sups}  (高点:{fib['swing_high']:.2f} 低点:{fib['swing_low']:.2f})")
    if r["position_size"]:
        print(f"     建议仓位: {r['position_size']:,.0f} 元")


def print_signal_report(results: list[dict]):
    print()
    print("=" * 55)
    if not results:
        print("  无符合条件的买卖点信号")
        print("=" * 55)
        return
    print(f"  📈 买卖点信号  {len(results)} 只")
    print("=" * 55)
    for r in results:
        status_icon = {"可介入": "🟢", "观望": "🟡", "止损警戒": "🔴"}.get(r["status"], "⚪")
        sigs = " | ".join(r["signal_basis"]) or "—"
        entry = r["entry_zone"]
        print(f"  {status_icon} {r['code']} {r['name']}  {entry[0]:.2f}~{entry[1]:.2f}  止损:{r['stop_loss']:.2f}")
        print(f"      {sigs}")
    print("=" * 55)


# ── 命令行入口 ───────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="买卖点信号分析")
    parser.add_argument("--date",    default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--codes",   nargs="+", required=True, help="股票代码列表")
    parser.add_argument("--capital", type=float, default=0, help="总资金（元），用于计算仓位")
    parser.add_argument("--all",     action="store_true", help="显示全部结果（不只显示可介入）")
    args = parser.parse_args()

    fs = None if args.all else "可介入"
    results = batch_analyze(args.codes, args.date, total_capital=args.capital, filter_status=fs)

    if not results:
        print(f"  无{'任何' if args.all else '可介入'}信号")
    else:
        for r in results:
            _, bars = _get_kline(r["code"], args.date, days=90)
            ma  = _check_ma(bars) if bars else {}
            fib = _compute_fibonacci(bars) if bars else {}
            _print_signal(r, ma, fib)
