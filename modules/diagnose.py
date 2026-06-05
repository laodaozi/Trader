"""
modules/diagnose.py — 深度诊断

对票池股票做多周期综合评估，输出是否值得操作的结论。

诊断维度：
  1. 周线方向   — 周K MA5/MA10 走向（上升/震荡/下降）
  2. 日线阶段   — 缠论生命周期（来自 pool.py assess_lifecycle）
  3. 主力资金   — 近3日资金净流入（正/负/中性）
  4. 风险回报   — R:R = (目标1 - 入场) / (入场 - 止损) ≥ 2 才推荐
  5. 策略分类   — 趋势 / 反弹 / 突破 / 低吸

输出：
  diagnose(codes, date, signals) → list[DiagResult]
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from modules.mcp import mcp_call


# ── 数据拉取 ──────────────────────────────────────────────

def _get_weekly_kline(code: str, end_date: str, bars: int = 26) -> list[dict]:
    """周K，取最近 bars 根（约半年）。"""
    start = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=bars * 7 + 30)).strftime("%Y-%m-%d")
    data = mcp_call("market_quote", "get_kline", {
        "keyword":            code,
        "start_date":         start,
        "end_date":           end_date,
        "kline_type":         2,   # 周K
        "reinstatement_type": 2,
    })
    raw = data if isinstance(data, list) else data.get("list", [])
    result = sorted([
        {
            "date":  b.get("trade_date", ""),
            "close": float(b.get("close_price") or b.get("close") or 0),
        }
        for b in raw
    ], key=lambda x: x["date"])
    return result[-bars:]


def _get_capital_flow(code: str, end_date: str) -> dict:
    """近3日主力资金净流入（万元）。"""
    start = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=10)).strftime("%Y-%m-%d")
    data = mcp_call("market_quote", "get_net_flow_list", {
        "keyword":    code,
        "start_date": start,
        "end_date":   end_date,
    })
    rows = data if isinstance(data, list) else data.get("list", [])
    rows = sorted(rows, key=lambda x: x.get("trade_date", ""), reverse=True)[:3]
    if not rows:
        return {"net_3d": 0.0, "direction": "无数据"}
    total = 0.0
    for r in rows:
        val = r.get("main_net_inflow") or r.get("net_inflow") or r.get("major_net") or 0
        total += float(val)
    direction = "净流入" if total > 0 else "净流出" if total < 0 else "中性"
    return {"net_3d": total, "direction": direction}


# ── 分析函数 ──────────────────────────────────────────────

def _ma(values: list[float], n: int) -> list[Optional[float]]:
    result: list[Optional[float]] = []
    for i in range(len(values)):
        result.append(None if i < n - 1 else sum(values[i - n + 1: i + 1]) / n)
    return result


def _weekly_direction(weekly_bars: list[dict]) -> str:
    if len(weekly_bars) < 10:
        return "数据不足"
    closes = [b["close"] for b in weekly_bars]
    ma5  = _ma(closes, 5)[-1]
    ma10 = _ma(closes, 10)[-1]
    if ma5 is None or ma10 is None:
        return "数据不足"
    if ma5 > ma10 * 1.005:
        return "上升"
    if ma5 < ma10 * 0.995:
        return "下降"
    return "震荡"


def _strategy_type(daily_lifecycle: str, weekly_direction: str) -> str:
    if weekly_direction == "上升":
        if daily_lifecycle in ("生·进入", "住·持有"):
            return "趋势"
        if daily_lifecycle == "坏·注意":
            return "反弹"
    if weekly_direction == "震荡":
        if daily_lifecycle == "生·进入":
            return "突破"
        return "低吸"
    if weekly_direction == "下降":
        return "观望"
    return "低吸"


def _rr(sig: dict) -> Optional[float]:
    """R:R = (目标1 - 入场中值) / (入场中值 - 止损)."""
    entry = sig.get("entry_zone", [0, 0])
    tp    = sig.get("take_profit", [])
    sl    = sig.get("stop_loss", 0)
    if not tp or not sl or sl <= 0:
        return None
    mid   = (entry[0] + entry[1]) / 2 if entry[1] > entry[0] else entry[0]
    risk  = mid - sl
    if risk <= 0:
        return None
    return round((tp[0] - mid) / risk, 2)


def _verdict(
    weekly_dir: str,
    daily_lc: str,
    capital_dir: str,
    rr: Optional[float],
) -> str:
    if daily_lc == "灭·出局":
        return "回避"
    if weekly_dir == "下降" and daily_lc in ("坏·注意", "灭·出局"):
        return "回避"
    if rr is not None and rr < 2.0:
        return "R:R不足"
    if capital_dir == "净流出" and weekly_dir != "上升":
        return "观望"
    if weekly_dir == "上升" and daily_lc in ("生·进入", "住·持有") and capital_dir != "净流出":
        return "可介入"
    if rr is not None and rr >= 2.0:
        return "可介入"
    return "观望"


# ── 主入口 ───────────────────────────────────────────────

def diagnose(
    codes: list[str],
    date: str,
    signals: Optional[list[dict]] = None,
    daily_lifecycles: Optional[dict[str, str]] = None,
    verbose: bool = True,
) -> list[dict]:
    """
    批量诊断。

    signals: signals.py 的输出列表（含 entry_zone / stop_loss / take_profit）
    daily_lifecycles: {code: lifecycle}，已有则不重新计算（从 pool.py 传入）
    返回 list[dict]，每项包含所有诊断字段。
    """
    sig_by_code = {s["code"]: s for s in (signals or [])}
    lc_by_code  = daily_lifecycles or {}

    results = []
    for code in codes:
        if verbose:
            print(f"  [diagnose] {code} ...", end=" ", flush=True)

        # 周线方向
        try:
            weekly_bars = _get_weekly_kline(code, date)
            weekly_dir  = _weekly_direction(weekly_bars)
        except Exception:
            weekly_bars, weekly_dir = [], "数据不足"

        # 日线阶段（优先从外部传入，否则跳过，caller 应从 pool 获取）
        daily_lc = lc_by_code.get(code, "未知")

        # 资金流向
        try:
            flow = _get_capital_flow(code, date)
        except Exception:
            flow = {"net_3d": 0.0, "direction": "无数据"}

        # R:R
        sig = sig_by_code.get(code, {})
        rr  = _rr(sig)

        # 策略分类 & 综合结论
        strategy = _strategy_type(daily_lc, weekly_dir)
        verdict  = _verdict(weekly_dir, daily_lc, flow["direction"], rr)

        rec = {
            "code":          code,
            "name":          sig.get("name", ""),
            "weekly_dir":    weekly_dir,
            "daily_lc":      daily_lc,
            "capital_dir":   flow["direction"],
            "capital_net3d": flow["net_3d"],
            "rr":            rr,
            "strategy":      strategy,
            "verdict":       verdict,
            "entry_zone":    sig.get("entry_zone", [0, 0]),
            "stop_loss":     sig.get("stop_loss", 0),
            "take_profit":   sig.get("take_profit", []),
        }
        results.append(rec)

        if verbose:
            print(f"{verdict}  周{weekly_dir}/日{daily_lc}  资金{flow['direction']}  R:R={rr}")

    return results


# ── 命令行 ────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    from modules.pool import load_pool

    parser = argparse.ArgumentParser(description="深度诊断")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--codes", nargs="+", help="指定代码列表，默认使用票池")
    args = parser.parse_args()

    if args.codes:
        codes = args.codes
        lc_map: dict = {}
    else:
        pool = load_pool()
        codes = [s["code"] for s in pool["stocks"]]
        lc_map = {s["code"]: s.get("lifecycle", "未知") for s in pool["stocks"]}

    if not codes:
        print("无股票可诊断（票池为空或未指定代码）")
    else:
        results = diagnose(codes, args.date, daily_lifecycles=lc_map)
        print(f"\n{'代码':<8} {'名称':<8} {'周线':^4} {'日线':^6} {'资金':^5} {'R:R':^5} {'策略':^4} {'结论'}")
        print("-" * 70)
        for r in results:
            rr_str = f"{r['rr']:.1f}" if r["rr"] is not None else "—"
            print(
                f"{r['code']:<8} {r['name']:<8} {r['weekly_dir']:^4} "
                f"{r['daily_lc']:^8} {r['capital_dir']:^5} {rr_str:^5} "
                f"{r['strategy']:^4} {r['verdict']}"
            )
