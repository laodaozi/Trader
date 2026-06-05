"""
modules/min_backtest.py — 最小回测框架 v0.1

单模型 + 单股票 walk-forward 回测。
信号日 T → 次日 T+1 开盘买入 → N 日持有/止盈止损 → 统计胜率和盈亏比。

用法:
    python3 modules/min_backtest.py \
        --code 300750 --model zxji \
        --start 2024-06-01 --end 2026-06-01

输出:
    - 交易明细 (date, entry, exit, return)
    - 胜率 / 平均收益率 / 盈亏比 / 最大回撤
    - 按持有天数分组的统计
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.mcp import mcp_call
from modules.scanner import (
    _model_htji, _model_zsji, _model_xsqk,
    _model_zxji, _model_bdxy, _model_rzq,
    _model_sldb, _model_ztht, _model_gwzl, _model_jxgz,
    _model_qkxl,
)

# 模型函数注册表
_MODEL_REGISTRY = {
    "htji": _model_htji,
    "zxji": _model_zxji,
    "zsji": _model_zsji,
    "xsqk": _model_xsqk,
    "bdxy": _model_bdxy,
    "rzq":  _model_rzq,
    "sldb": _model_sldb,
    "ztht": _model_ztht,
    "gwzl": _model_gwzl,
    "jxgz": _model_jxgz,
    # qkxl 需要龙虎榜数据，暂不支持回测
}

# 各模型最少需要的历史 K 线数量
_MODEL_MIN_BARS = {
    "zsji": 270,
    "zxji": 70,
    "htji": 60,
    "sldb": 70,
    "gwzl": 35,
    "xsqk": 3,
    "bdxy": 25,
    "rzq":  25,
    "ztht": 20,
    "jxgz": 25,
}
_DEFAULT_MIN_BARS = 60

# ── K 线获取 ──────────────────────────────────────────────

def _fetch_kline(code: str, start_date: str, end_date: str) -> list[dict]:
    """
    拉取个股日K线，返回按日期升序排列的 bars。
    自动分块处理 API 单次 ≤1 年的限制。
    字段：date, open, high, low, close, volume, chg
    """
    # 多拉一年缓冲（供 MA 计算），但实际拉取时分块
    fetch_start = (datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
    fetch_end = datetime.strptime(end_date, "%Y-%m-%d")

    all_bars: list[dict] = []
    chunk_start_str = fetch_start
    while True:
        chunk_start = datetime.strptime(chunk_start_str, "%Y-%m-%d")
        chunk_end   = min(chunk_start + timedelta(days=365), fetch_end)
        chunk_end_str = chunk_end.strftime("%Y-%m-%d")

        data = mcp_call("market_quote", "get_kline", {
            "keyword":           code,
            "start_date":        chunk_start_str,
            "end_date":          chunk_end_str,
            "kline_type":        1,
            "reinstatement_type": 2,  # 前复权
        })
        raw_bars = data if isinstance(data, list) else data.get("list", [])
        for b in raw_bars:
            all_bars.append({
                "date":   b.get("trade_date", ""),
                "open":   float(b.get("open_price") or b.get("open") or 0),
                "high":   float(b.get("high_price") or b.get("high") or 0),
                "low":    float(b.get("low_price")  or b.get("low")  or 0),
                "close":  float(b.get("close_price") or b.get("close") or 0),
                "volume": float(b.get("trade_lots") or b.get("volume") or 0),
                "chg":    float(b.get("price_change_rate") or 0),
            })

        if chunk_end >= fetch_end:
            break
        chunk_start_str = (chunk_end + timedelta(days=1)).strftime("%Y-%m-%d")

    # 去重 + 排序
    seen = set()
    unique = []
    for b in sorted(all_bars, key=lambda x: x["date"]):
        if b["date"] not in seen:
            seen.add(b["date"])
            unique.append(b)
    return unique


# ── 回测引擎 ──────────────────────────────────────────────

def backtest(
    kline: list[dict],
    model_key: str,
    stock_name: str,
    stock_code: str,
    start_date: str,
    end_date: str,
    hold_days: int = 10,
    stop_loss: float = -0.08,
    take_profit: float = 0.15,
    verbose: bool = True,
) -> dict:
    """
    单模型 walk-forward 回测。

    参数:
        kline:     完整 K 线序列（按日期升序，需覆盖 start_date 之前足够历史）
        model_key: 模型键名（如 "zxji"）
        stock_name:股票名称
        stock_code:股票代码
        start_date:回测起始日 YYYY-MM-DD
        end_date:  回测结束日 YYYY-MM-DD
        hold_days: 持有天数
        stop_loss: 止损线（负数，如 -0.08 = -8%）
        take_profit: 止盈线（正数，如 0.15 = +15%）
        verbose:   是否打印进度

    返回:
        {
            "trades": [{date, entry_price, exit_price, exit_date, return, exit_reason}],
            "win_rate": 0.60,
            "avg_return": 0.032,
            "profit_factor": 1.8,
            "max_drawdown": -0.12,
            "total_signals": 20,
            "total_trades": 18,
        }
    """
    model_func = _MODEL_REGISTRY.get(model_key)
    if model_func is None:
        raise ValueError(f"未知模型: {model_key}。支持: {list(_MODEL_REGISTRY.keys())}")
    if model_key == "qkxl":
        raise ValueError("钱坤寻龙(qkxl)需要龙虎榜席位数据，暂不支持回测。")

    min_bars = _MODEL_MIN_BARS.get(model_key, _DEFAULT_MIN_BARS)

    # 按日期建立索引
    date_index = {b["date"]: i for i, b in enumerate(kline)}
    sorted_dates = sorted(date_index.keys())

    # 过滤出回测区间内的所有交易日
    test_dates = [d for d in sorted_dates if start_date <= d <= end_date]
    if not test_dates:
        print("❌ 回测区间内无交易日数据")
        return _empty_result()

    model_name = {
        "htji": "回调狙击", "zxji": "中线狙击", "zsji": "主升狙击",
        "xsqk": "向上缺口", "bdxy": "波段雄鹰", "rzq": "弱转强",
        "sldb": "缩量地板", "ztht": "涨停回踩", "gwzl": "高位整理",
        "jxgz": "均线共振",
    }.get(model_key, model_key)

    if verbose:
        print(f"\n╔══════════════════════════════════════╗")
        print(f"║  回测: {stock_name}({stock_code}) × {model_name}")
        print(f"║  区间: {start_date} → {end_date}")
        print(f"║  持有: {hold_days}天 | 止损: {stop_loss*100:+.0f}% | 止盈: {take_profit*100:+.0f}%")
        print(f"║  总K线: {len(kline)} | 回测日: {len(test_dates)}")
        print(f"╚══════════════════════════════════════╝")

    trades: list[dict] = []
    signals_count = 0
    skipped_no_entry = 0

    for i, date in enumerate(test_dates):
        # 日期间进度
        if verbose and (i + 1) % 50 == 0:
            print(f"  [{i+1}/{len(test_dates)}] {date} | 信号{signals_count} 交易{len(trades)}")

        idx = date_index[date]

        # 确保有足够历史 bars
        if idx < min_bars:
            continue

        # 切片：kline[0:idx+1] 为截至当日的所有 K 线
        history = kline[:idx + 1]

        # 运行模型
        result = model_func(stock_code, stock_name, history)
        if result is None:
            continue

        signals_count += 1

        # 需要次日 K 线来执行买入
        if idx + 1 >= len(kline):
            skipped_no_entry += 1
            continue

        # 模拟交易：T+1 开盘买入
        next_bar = kline[idx + 1]
        entry_price = next_bar["open"]
        if entry_price <= 0:
            skipped_no_entry += 1
            continue

        # 计算退出：持有 hold_days 天，或触发止盈/止损
        exit_idx = min(idx + 2 + hold_days, len(kline))  # T+1 买入后第一天是 idx+2
        exit_price = entry_price
        exit_date = next_bar["date"]
        exit_reason = "持有到期"

        for j in range(idx + 2, exit_idx):
            bar = kline[j]
            # 检查止损
            if bar["low"] <= entry_price * (1 + stop_loss):
                exit_price = entry_price * (1 + stop_loss)
                exit_date = bar["date"]
                exit_reason = f"止损 {stop_loss*100:+.0f}%"
                break
            # 检查止盈
            if bar["high"] >= entry_price * (1 + take_profit):
                exit_price = entry_price * (1 + take_profit)
                exit_date = bar["date"]
                exit_reason = f"止盈 {take_profit*100:+.0f}%"
                break

        # 正常持有到期
        if exit_reason == "持有到期":
            last_bar = kline[exit_idx - 1]
            exit_price = last_bar["close"]
            exit_date = last_bar["date"]

        trade_return = (exit_price - entry_price) / entry_price

        trades.append({
            "signal_date": date,
            "entry_date":  next_bar["date"],
            "entry_price": round(entry_price, 2),
            "exit_date":   exit_date,
            "exit_price":  round(exit_price, 2),
            "return_pct":  round(trade_return * 100, 2),
            "exit_reason": exit_reason,
        })

    # ── 统计 ──
    if not trades:
        if verbose:
            print(f"\n  ⚠️ 无交易记录（信号 {signals_count} 个，无买入机会 {skipped_no_entry} 次）")
        return _empty_result()

    returns = [t["return_pct"] for t in trades]
    wins   = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    win_rate = len(wins) / len(returns) if returns else 0.0

    avg_win  = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    profit_factor = abs(sum(wins) / sum(losses)) if sum(losses) != 0 else float("inf")

    avg_return = sum(returns) / len(returns) if returns else 0.0
    total_return = sum(returns)

    # 最大回撤（累计收益序列）
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for r in returns:
        cumulative += r
        peak = max(peak, cumulative)
        drawdown = cumulative - peak
        max_drawdown = min(max_drawdown, drawdown)

    # 按退出原因分组
    by_reason: dict[str, list[float]] = {}
    for t in trades:
        reason = t["exit_reason"]
        by_reason.setdefault(reason, []).append(t["return_pct"])

    result = {
        "trades":          trades,
        "win_rate":        round(win_rate * 100, 1),
        "avg_return":      round(avg_return, 2),
        "avg_win":         round(avg_win, 2),
        "avg_loss":        round(avg_loss, 2),
        "profit_factor":   round(profit_factor, 2),
        "max_drawdown":    round(max_drawdown, 2),
        "total_return":    round(total_return, 2),
        "total_signals":   signals_count,
        "total_trades":    len(trades),
        "skipped_no_entry": skipped_no_entry,
        "by_reason":       {k: {"count": len(v), "avg_return": round(sum(v)/len(v), 2)} for k, v in by_reason.items()},
    }

    if verbose:
        _print_report(result, stock_name, stock_code, model_name, start_date, end_date, hold_days)

    return result


def _empty_result() -> dict:
    return {
        "trades": [],
        "win_rate": 0.0, "avg_return": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
        "profit_factor": 0.0, "max_drawdown": 0.0, "total_return": 0.0,
        "total_signals": 0, "total_trades": 0, "skipped_no_entry": 0, "by_reason": {},
    }


# ── 报告 ──────────────────────────────────────────────────

def _print_report(
    result: dict,
    stock_name: str,
    stock_code: str,
    model_name: str,
    start_date: str,
    end_date: str,
    hold_days: int,
):
    """打印回测报告。"""
    print(f"\n{'='*64}")
    print(f"  回测报告: {stock_name}({stock_code}) × {model_name}")
    print(f"  区间: {start_date} → {end_date}  |  持有: {hold_days}天")
    print(f"{'='*64}")
    print(f"  信号数: {result['total_signals']:>5}    交易数: {result['total_trades']:>5}")
    if result['skipped_no_entry']:
        print(f"  跳过(无次日K线): {result['skipped_no_entry']}")
    print(f"  {'-'*40}")
    print(f"  胜率:        {result['win_rate']:>6.1f}%")
    print(f"  平均收益:    {result['avg_return']:>+7.2f}%")
    print(f"  平均盈利:    {result['avg_win']:>+7.2f}%")
    print(f"  平均亏损:    {result['avg_loss']:>+7.2f}%")
    print(f"  盈亏比:      {result['profit_factor']:>7.2f}")
    print(f"  最大回撤:    {result['max_drawdown']:>+7.2f}%")
    print(f"  累计收益:    {result['total_return']:>+7.2f}%")
    print(f"  {'-'*40}")
    print(f"  退出方式分布:")
    for reason, stats in sorted(result["by_reason"].items()):
        bar = "█" * min(int(abs(stats["avg_return"]) * 4), 30)
        print(f"    {reason:12s}  {stats['count']:>3}笔  avg {stats['avg_return']:+6.2f}%  {bar}")

    # 最近 10 笔交易明细
    if result["trades"]:
        print(f"\n  最近 10 笔交易:")
        print(f"    {'信号日':>12s} {'入场日':>12s} {'入场价':>8s} {'离场日':>12s} {'离场价':>8s} {'收益':>7s} {'退出方式'}")
        for t in result["trades"][-10:]:
            print(f"    {t['signal_date']:>12s} {t['entry_date']:>12s} {t['entry_price']:>8.2f} "
                  f"{t['exit_date']:>12s} {t['exit_price']:>8.2f} {t['return_pct']:+6.2f}% {t['exit_reason']}")

    print(f"\n{'='*64}")
    print(f"  ⚠️  历史回测不代表未来表现，仅供参考")
    print(f"{'='*64}")


# ── CLI ────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="最小回测框架 — 单模型 + 单股票")
    parser.add_argument("--code",   required=True, help="股票代码（如 300750）")
    parser.add_argument("--model",  required=True, choices=list(_MODEL_REGISTRY.keys()),
                        help="模型键名")
    parser.add_argument("--start",  required=True, help="回测起始日 YYYY-MM-DD")
    parser.add_argument("--end",    default=datetime.now().strftime("%Y-%m-%d"),
                        help="回测结束日（默认今天）")
    parser.add_argument("--hold",   type=int, default=10, help="持有天数（默认10）")
    parser.add_argument("--sl",     type=float, default=-0.08, help="止损线（默认-0.08）")
    parser.add_argument("--tp",     type=float, default=0.15, help="止盈线（默认0.15）")
    args = parser.parse_args()

    # 拉取数据
    print(f"⏳ 拉取 {args.code} K线数据...")
    kline = _fetch_kline(args.code, args.start, args.end)
    if not kline:
        print(f"❌ 无法获取 {args.code} 的 K 线数据")
        sys.exit(1)

    stock_name = "unknown"
    try:
        # 从市场报价获取名称
        quote = mcp_call("market_quote", "get_stock_quote", {"codes": [args.code]})
        if quote and isinstance(quote, list) and len(quote) > 0:
            stock_name = quote[0].get("name") or quote[0].get("stock_name") or args.code
    except Exception:
        stock_name = args.code

    print(f"   ✅ {stock_name}({args.code}) 共 {len(kline)} 条日K ({kline[0]['date']} ~ {kline[-1]['date']})")

    # 运行回测
    result = backtest(
        kline=kline,
        model_key=args.model,
        stock_name=stock_name,
        stock_code=args.code,
        start_date=args.start,
        end_date=args.end,
        hold_days=args.hold,
        stop_loss=args.sl,
        take_profit=args.tp,
        verbose=True,
    )
