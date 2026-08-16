"""
modules/min_backtest.py — 最小回测框架 v1.0

v1.0 改造：
  - 数据源切 datalake.get_history()（本地 Parquet，毫秒级，无 MCP 消耗）
  - 新增 batch_backtest()：14 模型 × N 只股票，输出胜率表
  - 结果写入 data/backtest_winrate.json，供 confidence 模块读取
  - 保留单股单模型 CLI 模式（向后兼容）

用法:
    # 单股单模型（向后兼容）
    python3 modules/min_backtest.py \\
        --code 300750 --model zxji \\
        --start 2024-06-01 --end 2026-06-01

    # 批量回测（14模型 × tracker自选股）
    python3 modules/min_backtest.py --batch \\
        --start 2024-07-07 --end 2026-07-03

    # 批量回测 + 自定义股票列表
    python3 modules/min_backtest.py --batch \\
        --symbols 000001 300750 600519 \\
        --start 2024-07-07 --end 2026-07-03

输出:
    - 交易明细 (date, entry, exit, return)
    - 胜率 / 平均收益率 / 盈亏比 / 最大回撤
    - data/backtest_winrate.json（批量模式）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.scanner import (
    _model_htji, _model_zsji, _model_xsqk,
    _model_zxji, _model_bdxy, _model_rzq,
    _model_sldb, _model_ztht, _model_gwzl, _model_jxgz,
    _model_hydx, _model_nsdyy, _model_cqft,
)

# 模型函数注册表（14 模型，qkxl 需龙虎榜暂不支持回测）
_MODEL_REGISTRY = {
    "htji":  (_model_htji,  "回调狙击"),
    "zxji":  (_model_zxji,  "中线狙击"),
    "zsji":  (_model_zsji,  "主升狙击"),
    "xsqk":  (_model_xsqk,  "向上缺口"),
    "bdxy":  (_model_bdxy,  "波段雄鹰"),
    "rzq":   (_model_rzq,   "弱转强"),
    "sldb":  (_model_sldb,  "缩量地板"),
    "ztht":  (_model_ztht,  "涨停回踩"),
    "gwzl":  (_model_gwzl,  "高位整理"),
    "jxgz":  (_model_jxgz,  "均线共振"),
    "hydx":  (_model_hydx,  "好运低吸"),
    "nsdyy": (_model_nsdyy, "牛市第一阳"),
    "cqft":  (_model_cqft,  "超强反弹"),
}

# 各模型最少需要的历史 K 线数量（用于 backtest 的 idx < min_bars 门控）
_MODEL_MIN_BARS = {
    "zsji": 270, "zxji": 70, "htji": 60, "sldb": 70,
    "gwzl": 35,  "xsqk": 10, "bdxy": 25, "rzq":  25,
    "ztht": 20,  "jxgz": 25, "hydx": 30, "nsdyy": 30, "cqft": 15,
}
_DEFAULT_MIN_BARS = 60

WINRATE_FILE = Path(__file__).parent.parent / "data" / "backtest_winrate.json"


# ── 数据获取：datalake 优先，MCP 兜底 ────────────────────────

def _bars_from_datalake(
    code: str, start_date: str, end_date: str,
    lookback_days: int = 400,
) -> list[dict]:
    """从本地 datalake 读取 K 线（主路径）。"""
    try:
        from core.datalake import get_history, DatalakeConfig
        cfg = DatalakeConfig()
        # 向前多拉 lookback_days 供 MA 计算
        fetch_start = (
            datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=lookback_days)
        ).date()
        fetch_end = datetime.strptime(end_date, "%Y-%m-%d").date()

        df = get_history(
            symbols=[code],
            start_date=fetch_start,
            end_date=fetch_end,
            fields=["symbol", "date", "open", "high", "low", "close", "volume", "pct_chg"],
            config=cfg,
        )
        if df.empty:
            return []

        bars = []
        for _, row in df.iterrows():
            d = str(row["date"])
            bars.append({
                "date":   d,
                "open":   float(row["open"]  or 0),
                "high":   float(row["high"]  or 0),
                "low":    float(row["low"]   or 0),
                "close":  float(row["close"] or 0),
                "volume": float(row["volume"] or 0),
                "chg":    float(row["pct_chg"] or 0) / 100,  # 归一化为小数
            })
        return sorted(bars, key=lambda x: x["date"])
    except Exception as e:
        return []


def _bars_from_mcp(code: str, start_date: str, end_date: str) -> list[dict]:
    """从 MCP 拉取 K 线（兜底路径，datalake 无数据时使用）。"""
    try:
        from core.trader_mcp import mcp_call
        fetch_start = (
            datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=400)
        ).strftime("%Y-%m-%d")
        fetch_end = datetime.strptime(end_date, "%Y-%m-%d")

        all_bars: list[dict] = []
        chunk_start_str = fetch_start
        while True:
            chunk_start = datetime.strptime(chunk_start_str, "%Y-%m-%d")
            chunk_end   = min(chunk_start + timedelta(days=365), fetch_end)
            chunk_end_str = chunk_end.strftime("%Y-%m-%d")
            data = mcp_call("market_quote", "get_kline", {
                "keyword": code, "start_date": chunk_start_str,
                "end_date": chunk_end_str, "kline_type": 1, "reinstatement_type": 2,
            })
            raw = data if isinstance(data, list) else data.get("list", [])
            for b in raw:
                all_bars.append({
                    "date":   b.get("trade_date", ""),
                    "open":   float(b.get("open_price") or 0),
                    "high":   float(b.get("high_price") or 0),
                    "low":    float(b.get("low_price")  or 0),
                    "close":  float(b.get("close_price") or 0),
                    "volume": float(b.get("trade_lots") or 0),
                    "chg":    float(b.get("price_change_rate") or 0),
                })
            if chunk_end >= fetch_end:
                break
            chunk_start_str = (chunk_end + timedelta(days=1)).strftime("%Y-%m-%d")

        seen, unique = set(), []
        for b in sorted(all_bars, key=lambda x: x["date"]):
            if b["date"] not in seen:
                seen.add(b["date"])
                unique.append(b)
        return unique
    except Exception:
        return []


def _fetch_kline(code: str, start_date: str, end_date: str) -> list[dict]:
    """datalake 优先（数据需 ≥300 条），不足则降级到 MCP。"""
    bars = _bars_from_datalake(code, start_date, end_date)
    if len(bars) >= 300:
        return bars
    if bars:
        print(f"    [datalake 仅 {len(bars)} 条，不足300，降级 MCP]")
    return _bars_from_mcp(code, start_date, end_date)


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
        kline:      完整 K 线序列（按日期升序，需覆盖 start_date 之前足够历史）
        model_key:  模型键名（如 "zxji"）
        stock_name: 股票名称
        stock_code: 股票代码
        start_date: 回测起始日 YYYY-MM-DD
        end_date:   回测结束日 YYYY-MM-DD
        hold_days:  持有天数
        stop_loss:  止损线（负数，如 -0.08 = -8%）
        take_profit:止盈线（正数，如 0.15 = +15%）
        verbose:    是否打印进度
    """
    if model_key not in _MODEL_REGISTRY:
        raise ValueError(f"未知模型: {model_key}。支持: {list(_MODEL_REGISTRY.keys())}")

    model_func, model_name = _MODEL_REGISTRY[model_key]
    min_bars = _MODEL_MIN_BARS.get(model_key, _DEFAULT_MIN_BARS)

    date_index  = {b["date"]: i for i, b in enumerate(kline)}
    sorted_dates = sorted(date_index.keys())
    test_dates  = [d for d in sorted_dates if start_date <= d <= end_date]

    if not test_dates:
        return _empty_result()

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

    for i, dt in enumerate(test_dates):
        if verbose and (i + 1) % 50 == 0:
            print(f"  [{i+1}/{len(test_dates)}] {dt} | 信号{signals_count} 交易{len(trades)}")

        idx = date_index[dt]
        if idx < min_bars:
            continue

        history = kline[:idx + 1]
        try:
            result  = model_func(stock_code, stock_name, history)
        except (IndexError, ZeroDivisionError):
            continue
        if result is None:
            continue

        signals_count += 1
        if idx + 1 >= len(kline):
            skipped_no_entry += 1
            continue

        next_bar = kline[idx + 1]
        entry_price = next_bar["open"]
        if entry_price <= 0:
            skipped_no_entry += 1
            continue

        exit_idx    = min(idx + 2 + hold_days, len(kline))
        exit_price  = entry_price
        exit_date   = next_bar["date"]
        exit_reason = "持有到期"

        for j in range(idx + 2, exit_idx):
            bar = kline[j]
            if bar["low"] <= entry_price * (1 + stop_loss):
                exit_price  = entry_price * (1 + stop_loss)
                exit_date   = bar["date"]
                exit_reason = f"止损 {stop_loss*100:+.0f}%"
                break
            if bar["high"] >= entry_price * (1 + take_profit):
                exit_price  = entry_price * (1 + take_profit)
                exit_date   = bar["date"]
                exit_reason = f"止盈 {take_profit*100:+.0f}%"
                break

        if exit_reason == "持有到期":
            last_bar    = kline[exit_idx - 1]
            exit_price  = last_bar["close"]
            exit_date   = last_bar["date"]

        trade_return = (exit_price - entry_price) / entry_price
        trades.append({
            "signal_date": dt,
            "entry_date":  next_bar["date"],
            "entry_price": round(entry_price, 2),
            "exit_date":   exit_date,
            "exit_price":  round(exit_price, 2),
            "return_pct":  round(trade_return * 100, 2),
            "exit_reason": exit_reason,
        })

    if not trades:
        if verbose:
            print(f"\n  ⚠️ 无交易记录（信号 {signals_count} 个）")
        return _empty_result()

    returns = [t["return_pct"] for t in trades]
    wins    = [r for r in returns if r > 0]
    losses  = [r for r in returns if r <= 0]

    win_rate      = len(wins) / len(returns)
    avg_win       = sum(wins)   / len(wins)   if wins   else 0.0
    avg_loss      = sum(losses) / len(losses) if losses else 0.0
    # 无亏损时封顶 99.0（与批量聚合层一致）——绝不写 inf，否则 JSON 非法、JS 端 JSON.parse 崩溃
    profit_factor = abs(sum(wins) / sum(losses)) if sum(losses) != 0 else 99.0
    avg_return    = sum(returns) / len(returns)
    total_return  = sum(returns)

    cumulative = peak = max_drawdown = 0.0
    for r in returns:
        cumulative += r
        peak        = max(peak, cumulative)
        max_drawdown = min(max_drawdown, cumulative - peak)

    by_reason: dict[str, list[float]] = {}
    for t in trades:
        by_reason.setdefault(t["exit_reason"], []).append(t["return_pct"])

    result = {
        "trades":           trades,
        "win_rate":         round(win_rate * 100, 1),
        "avg_return":       round(avg_return, 2),
        "avg_win":          round(avg_win, 2),
        "avg_loss":         round(avg_loss, 2),
        "profit_factor":    round(profit_factor, 2),
        "max_drawdown":     round(max_drawdown, 2),
        "total_return":     round(total_return, 2),
        "total_signals":    signals_count,
        "total_trades":     len(trades),
        "skipped_no_entry": skipped_no_entry,
        "by_reason":        {k: {"count": len(v), "avg_return": round(sum(v)/len(v), 2)}
                             for k, v in by_reason.items()},
    }

    if verbose:
        _print_report(result, stock_name, stock_code, model_name, start_date, end_date, hold_days)

    return result


# ── 批量回测 ────────────────────────────────────────────────

def batch_backtest(
    symbols: list[str],
    start_date: str,
    end_date: str,
    hold_days: int = 10,
    stop_loss: float = -0.08,
    take_profit: float = 0.15,
    min_trades: int = 5,
    save: bool = True,
) -> dict:
    """
    14 模型 × N 只股票批量回测，输出胜率表。

    参数:
        symbols:    股票代码列表
        start_date: 回测起始日
        end_date:   回测结束日
        min_trades: 最少交易次数（不足则不统计胜率）
        save:       是否写入 data/backtest_winrate.json

    返回:
        {
          "updated_at": "...",
          "start_date": "...", "end_date": "...",
          "models": {
            "zxji": {"win_rate": 58.3, "avg_return": 1.2, "total_trades": 120,
                     "profit_factor": 1.8, "sample_size": 120},
            ...
          },
          "by_symbol": {
            "300750": {"zxji": {...}, ...},
            ...
          }
        }
    """
    print(f"\n[batch_backtest] {len(symbols)} 只 × {len(_MODEL_REGISTRY)} 模型")
    print(f"  区间: {start_date} → {end_date} | 持有: {hold_days}天")

    # 聚合容器
    model_agg: dict[str, list[dict]] = {k: [] for k in _MODEL_REGISTRY}
    by_symbol: dict[str, dict] = {}

    for sym_i, code in enumerate(symbols):
        print(f"\n[{sym_i+1}/{len(symbols)}] {code} 拉取K线...", end=" ", flush=True)
        kline = _fetch_kline(code, start_date, end_date)
        if not kline:
            print("无数据，跳过")
            continue
        print(f"{len(kline)} 条")

        by_symbol[code] = {}
        for model_key in _MODEL_REGISTRY:
            res = backtest(
                kline=kline,
                model_key=model_key,
                stock_name=code,
                stock_code=code,
                start_date=start_date,
                end_date=end_date,
                hold_days=hold_days,
                stop_loss=stop_loss,
                take_profit=take_profit,
                verbose=False,
            )
            if res["total_trades"] >= min_trades:
                model_agg[model_key].extend(res["trades"])
                by_symbol[code][model_key] = {
                    "win_rate":      res["win_rate"],
                    "avg_return":    res["avg_return"],
                    "total_trades":  res["total_trades"],
                    "profit_factor": res["profit_factor"],
                }

    # 汇总每个模型的全局胜率
    models_summary: dict[str, dict] = {}
    for model_key, trades in model_agg.items():
        _, model_name = _MODEL_REGISTRY[model_key]
        if not trades:
            models_summary[model_key] = {
                "name": model_name, "win_rate": 0.0, "avg_return": 0.0,
                "profit_factor": 0.0, "sample_size": 0,
            }
            continue
        returns = [t["return_pct"] for t in trades]
        wins    = [r for r in returns if r > 0]
        losses  = [r for r in returns if r <= 0]
        win_rate = len(wins) / len(returns) * 100
        avg_ret  = sum(returns) / len(returns)
        pf       = abs(sum(wins) / sum(losses)) if sum(losses) != 0 else 99.0
        models_summary[model_key] = {
            "name":          model_name,
            "win_rate":      round(win_rate, 1),
            "avg_return":    round(avg_ret, 2),
            "profit_factor": round(pf, 2),
            "sample_size":   len(trades),
        }

    output = {
        "updated_at":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "start_date":  start_date,
        "end_date":    end_date,
        "hold_days":   hold_days,
        "stop_loss":   stop_loss,
        "take_profit": take_profit,
        "symbols_count": len(symbols),
        "models":      models_summary,
        "by_symbol":   by_symbol,
    }

    if save:
        WINRATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        WINRATE_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
        print(f"\n[batch_backtest] 胜率表已写入 {WINRATE_FILE}")

    # 打印汇总
    print(f"\n{'='*60}")
    print(f"  批量回测胜率表  |  {start_date} → {end_date}")
    print(f"{'='*60}")
    print(f"  {'模型':<12} {'名称':<10} {'胜率':>7} {'均收益':>8} {'盈亏比':>7} {'样本':>6}")
    print(f"  {'-'*56}")
    for k, v in sorted(models_summary.items(), key=lambda x: -x[1]["win_rate"]):
        flag = "✅" if v["win_rate"] >= 55 else ("⚠️" if v["win_rate"] >= 45 else "❌")
        print(f"  {flag} {k:<10} {v['name']:<10} {v['win_rate']:>6.1f}% "
              f"{v['avg_return']:>+7.2f}% {v['profit_factor']:>6.2f}x {v['sample_size']:>5}")
    print(f"{'='*60}")

    return output


def _load_pool_symbols() -> list[str]:
    """从 pool.json 读取自选股代码。"""
    pool_path = Path(__file__).parent.parent / "data" / "pool.json"
    if not pool_path.exists():
        return []
    try:
        d = json.loads(pool_path.read_text())
        return [s["code"] for s in d.get("stocks", []) if s.get("code")]
    except Exception:
        return []


# ── 报告 ──────────────────────────────────────────────────

def _empty_result() -> dict:
    return {
        "trades": [], "win_rate": 0.0, "avg_return": 0.0,
        "avg_win": 0.0, "avg_loss": 0.0, "profit_factor": 0.0,
        "max_drawdown": 0.0, "total_return": 0.0,
        "total_signals": 0, "total_trades": 0, "skipped_no_entry": 0, "by_reason": {},
    }


def _print_report(result, stock_name, stock_code, model_name, start_date, end_date, hold_days):
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
    parser = argparse.ArgumentParser(description="最小回测框架 v1.0")
    parser.add_argument("--batch",   action="store_true", help="批量模式：14模型 × N只")
    parser.add_argument("--code",    help="单股模式：股票代码（如 300750）")
    parser.add_argument("--model",   choices=list(_MODEL_REGISTRY.keys()), help="单股模式：模型键名")
    parser.add_argument("--symbols", nargs="+", help="批量模式：指定股票列表（默认读 pool.json）")
    parser.add_argument("--start",   required=True, help="回测起始日 YYYY-MM-DD")
    parser.add_argument("--end",     default=datetime.now().strftime("%Y-%m-%d"), help="回测结束日")
    parser.add_argument("--hold",    type=int,   default=10,    help="持有天数（默认10）")
    parser.add_argument("--sl",      type=float, default=-0.08, help="止损线（默认-0.08）")
    parser.add_argument("--tp",      type=float, default=0.15,  help="止盈线（默认0.15）")
    parser.add_argument("--min-trades", type=int, default=5, help="批量模式最少交易次数（默认5）")
    args = parser.parse_args()

    if args.batch:
        syms = args.symbols or _load_pool_symbols()
        if not syms:
            print("❌ 批量模式需要 --symbols 或 data/pool.json 中有股票")
            sys.exit(1)
        batch_backtest(
            symbols=syms,
            start_date=args.start,
            end_date=args.end,
            hold_days=args.hold,
            stop_loss=args.sl,
            take_profit=args.tp,
            min_trades=args.min_trades,
        )
    else:
        if not args.code or not args.model:
            print("❌ 单股模式需要 --code 和 --model")
            sys.exit(1)
        print(f"⏳ 拉取 {args.code} K线数据（datalake 优先）...")
        kline = _fetch_kline(args.code, args.start, args.end)
        if not kline:
            print(f"❌ 无法获取 {args.code} 的 K 线数据（datalake 和 MCP 均失败）")
            sys.exit(1)
        print(f"   ✅ 共 {len(kline)} 条日K ({kline[0]['date']} ~ {kline[-1]['date']})")
        backtest(
            kline=kline,
            model_key=args.model,
            stock_name=args.code,
            stock_code=args.code,
            start_date=args.start,
            end_date=args.end,
            hold_days=args.hold,
            stop_loss=args.sl,
            take_profit=args.tp,
            verbose=True,
        )
