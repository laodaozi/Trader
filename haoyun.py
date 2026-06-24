"""
modules/haoyun.py — 好运哥仓位纪律调节器

基于好运哥交易体系的仓位控制规则，叠加在 market timing 仓位建议之上：

  条件                            → 仓位调节
  ─────────────────────────────────────────────────
  大盘月涨幅 < -5%                → 仓位 × 0  (空仓)
  连续3日亏损                     → 仓位 × 0  (强制清仓)
  连续2日亏损                     → 仓位 × 0.5, max 0.3
  周阴线 > 8% (即跌幅 > 8%)      → 仓位 × 0.3
  账户市值创历史新高              → 仓位 × 1.2, cap 1.0
  默认                            → 原仓位不变

最终仓位 = max(0, min(1, 调节后))

数据来源：
  - MCP get_kline("上证指数") → 月涨幅/周跌幅
  - account.get_positions()  → 连续亏损天数/历史市值
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ensure modules/ importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.mcp import mcp_call

# ── 指数数据获取 ──────────────────────────────────────────

def _get_index_kline(date: str, lookback_days: int = 45) -> list[dict]:
    """获取上证指数日K线，返回按日期升序的 bars 列表。"""
    start = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=lookback_days + 5)).strftime("%Y-%m-%d")
    try:
        data = mcp_call("market_quote", "get_kline", {
            "keyword": "上证指数",
            "start_date": start,
            "end_date": date,
            "kline_type": 1,
            "reinstatement_type": 1,
        })
        raw = data if isinstance(data, list) else data.get("list", [])
        bars = []
        for b in raw:
            bars.append({
                "date": b.get("trade_date", ""),
                "close": float(b.get("close_price") or b.get("close") or 0),
                "open": float(b.get("open_price") or b.get("open") or 0),
                "high": float(b.get("high_price") or b.get("high") or 0),
                "low": float(b.get("low_price") or b.get("low") or 0),
            })
        return sorted(bars, key=lambda x: x["date"])
    except Exception:
        return []


def _get_monthly_index_change(date: str) -> float:
    """
    上证指数月涨幅（约22个交易日），小数形式。
    正数表示上涨，负数表示下跌。获取失败返回 0。
    """
    bars = _get_index_kline(date, lookback_days=45)
    if len(bars) < 23:
        return 0.0
    today_close   = bars[-1]["close"]
    month_ago_bar = bars[-23]  # ~22 trading days
    if today_close <= 0 or month_ago_bar["close"] <= 0:
        return 0.0
    return (today_close - month_ago_bar["close"]) / month_ago_bar["close"]


def _get_weekly_index_change(date: str) -> float:
    """
    上证指数本周涨跌幅（约5个交易日），小数形式。
    负数表示下跌。获取失败返回 0。
    """
    bars = _get_index_kline(date, lookback_days=12)
    if len(bars) < 6:
        return 0.0
    today_close = bars[-1]["close"]
    week_ago = bars[-6]["close"]
    if today_close <= 0 or week_ago <= 0:
        return 0.0
    return (today_close - week_ago) / week_ago


# ── 账户状态读取 ──────────────────────────────────────────

def _get_account_status() -> dict:
    """从 positions.json 读取账户状态。"""
    try:
        from modules.account import get_positions
        data = get_positions()
        history = data.get("daily_pnl_history", [])
        capital = data["meta"]["total_capital"]
        # 注意：consecutive_loss_days 来自 account.py 的健康检查，由 daily_health_check() 写入
        # 在 trader.py 流程中 timing.py 先于 account.py 运行，此时还未经当日健康检查
        # 因此需要自己计算连续亏损天数
        loss_days = 0
        for h in reversed(history):
            if h.get("pnl", 0) < 0:
                loss_days += 1
            else:
                break
        return {
            "consecutive_loss_days": loss_days,
            "daily_pnl_history": history,
            "total_capital": capital,
            "position_ratio": data["meta"].get("position_ratio", 0),
        }
    except Exception:
        return {
            "consecutive_loss_days": 0,
            "daily_pnl_history": [],
            "total_capital": 0,
            "position_ratio": 0,
        }


def _is_account_ath(date: str) -> tuple[bool, float]:
    """
    检查账户是否处于历史最高市值水平。
    返回 (is_ath, current_value)。
    市值 = 总资金 + 最近一日 P&L（如有）。
    """
    status = _get_account_status()
    capital = status["total_capital"]
    history = status["daily_pnl_history"]
    today_pnl = history[-1]["pnl"] if history else 0.0
    current_value = capital + today_pnl

    if not history:
        return False, current_value

    # 用每日 P&L 累积来估算历史每日市值
    peak = capital  # 初始资金就是初始峰值
    running_pnl = 0.0
    for h in history:
        running_pnl += h.get("pnl", 0)
        peak = max(peak, capital + running_pnl)

    # 当前市值 >= 历史峰值的 99.5%（容忍浮点误差）
    is_ath = current_value >= peak * 0.995
    return is_ath, current_value


# ── 核心调节器 ────────────────────────────────────────────

def adjust_position(
    date: str,
    original_position: float,
    verbose: bool = True,
) -> tuple[float, list[str]]:
    """
    好运哥仓位调节器。

    参数:
      date: 当日日期 YYYY-MM-DD
      original_position: 市场温度计给出的建议仓位 (0-1)
      verbose: 是否打印调节日志

    返回:
      (adjusted_position, flags)
        adjusted_position: 调节后的最终仓位 (0-1)
        flags: 每项调节的原因列表
    """
    pos = original_position
    flags: list[str] = []

    # ── 规则 0: 空仓时跳过 ──
    if pos <= 0:
        return 0.0, ["原仓位为0，无需调节"]

    # ── 规则 1: 大盘月跌幅 > 5% → 空仓 ──
    monthly_chg = _get_monthly_index_change(date)
    if monthly_chg < -0.05:
        old = pos
        pos = 0.0
        flags.append(f"🔴 大盘月跌幅 {monthly_chg*100:.1f}% < -5%，强制空仓（原{old:.0%}→{pos:.0%}）")
        if verbose:
            print(f"  [haoyun] 大盘月跌幅 {monthly_chg*100:.1f}% → 强制空仓")
        return pos, flags

    # ── 规则 2: 连续亏损天数 ──
    status = _get_account_status()
    loss_days = status["consecutive_loss_days"]

    if loss_days >= 3:
        old = pos
        pos = 0.0
        flags.append(f"🔴 连续{loss_days}日亏损，强制清仓（原{old:.0%}→{pos:.0%}）")
        if verbose:
            print(f"  [haoyun] 连续{loss_days}日亏损 → 强制清仓")
        return pos, flags

    if loss_days >= 2:
        pos = min(pos * 0.5, 0.3)
        flags.append(f"🟡 连续{loss_days}日亏损，仓位降至 {pos:.0%}（×0.5, max 30%）")
        if verbose:
            print(f"  [haoyun] 连续{loss_days}日亏损 → 仓位降至 {pos:.0%}")

    # ── 规则 3: 周阴线 > 8% → 仓位 × 0.3 ──
    weekly_chg = _get_weekly_index_change(date)
    if weekly_chg < -0.08:
        pos = pos * 0.3
        flags.append(f"🔴 周跌幅 {weekly_chg*100:.1f}% < -8%，仓位降至 {pos:.0%}（×0.3）")
        if verbose:
            print(f"  [haoyun] 周跌幅 {weekly_chg*100:.1f}% → 仓位降至 {pos:.0%}")

    # ── 规则 4: 账户市值新高 → 仓位 × 1.2, cap 1.0 ──
    is_ath, current_value = _is_account_ath(date)
    if is_ath and current_value > 0 and pos > 0:
        pos = min(pos * 1.2, 1.0)
        flags.append(f"🟢 账户市值新高，仓位提升至 {pos:.0%}（×1.2, cap 100%）")
        if verbose:
            print(f"  [haoyun] 账户市值新高 → 仓位提升至 {pos:.0%}")

    # ── 最终封顶 ──
    pos = max(0.0, min(1.0, pos))

    # ── 大盘/账户正常：原仓位不变 ──
    if not flags:
        flags.append(f"✅ 无触发条件，维持原仓位 {pos:.0%}")

    if verbose:
        print(f"  [haoyun] 最终仓位: {pos:.0%} | 触发 {len(flags)-1} 项调节" if len(flags) > 1 else f"  [haoyun] 最终仓位: {pos:.0%} | {flags[0]}")

    return pos, flags


# ── 独立运行（调试用）─────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="好运哥仓位调节器")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--position", type=float, default=0.65,
                        help="市场温度计建议仓位 (0-1), 默认 0.65")
    args = parser.parse_args()

    adjusted, flags = adjust_position(args.date, args.position, verbose=True)
    print(f"\n原仓位: {args.position:.0%} → 好运哥调节后: {adjusted:.0%}")
    for f in flags:
        print(f"  {f}")
