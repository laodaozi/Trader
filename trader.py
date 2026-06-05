"""
trader.py — 交易员 主入口

每日运行，输出：
  1. 市场择时（温度 + 阶段 + 仓位建议）
  2. 选股扫描（11模型量化命中）
  3. 账户状态（持仓 P&L + 止损检查 + 健康状态）

用法：
  python trader.py --date 2026-05-17
  python trader.py --date 2026-05-17 --timing-only
  python trader.py --date 2026-05-17 --skip-scan    # 跳过选股扫描
   python trader.py --date 2026-05-17 --dry-run      # 不调用 MCP，使用模拟数据
   python trader.py --date 2026-05-17 --strategy     # 生成自选池策略页（与日报独立）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

# 确保 modules/ 可 import
sys.path.insert(0, str(Path(__file__).parent))

from modules.timing import get_timing, mcp_call
from modules.scanner import scan as run_scan
from modules.sectors import get_active_sectors
from modules.pool import get_pool_summary, refresh_lifecycles, import_from_scan, composite_inflow, load_upstream_signals
from modules.signals import batch_analyze, print_signal_report
from modules.report import generate as generate_report
from modules.diagnose import diagnose as run_diagnose
from modules.strategy import run as run_strategy
from modules.account import (
    get_positions,
    daily_health_check,
    get_holdings_summary,
)

DATA_DIR   = Path(__file__).parent / "data"
OUTPUT_DIR = Path(__file__).parent / "output" / "daily"


# ── 获取持仓最新价 ─────────────────────────────────────

def fetch_current_prices(holdings: list[dict]) -> dict[str, float]:
    """批量获取持仓股最新价（通过 get_snapshot）。"""
    prices = {}
    for h in holdings:
        code = h["code"]
        try:
            data = mcp_call("market_quote", "get_snapshot", {"keyword": code})
            if isinstance(data, list) and data:
                item = data[0]
            elif isinstance(data, dict):
                item = data
            else:
                item = {}
            price = (
                item.get("current_price")
                or item.get("price")
                or item.get("close")
                or item.get("last_price")
            )
            if price:
                prices[code] = float(price)
            else:
                print(f"  ⚠ {code} 快照无价格字段: {list(item.keys())[:8]}")
        except Exception as e:
            print(f"  ⚠ {code} 价格获取失败: {e}")
    return prices


# ── 格式化输出 ────────────────────────────────────────

_PHASE_EMOJI = {
    "即将见顶": "🔴",
    "警惕下杀": "🔴",
    "冲刺":    "🟢",
    "回调":    "🟡",
    "反弹":    "🟡",
    "下跌":    "🔴",
    "试盘":    "🟡",
    "探底":    "🔴",
    "震荡休整": "⚪",
    "反抽":    "⚪",
}


def print_timing_report(timing: dict):
    temp  = timing["temperature"]
    phase = timing["phase"]
    emoji = _PHASE_EMOJI.get(phase, "⚪")
    pos   = int(timing["recommended_position"] * 100)
    idx   = timing["index_direction"]

    print()
    print("=" * 55)
    print(f"  📊 市场择时  {timing['date']}")
    print("=" * 55)
    print(f"  温度计   : {temp}°C")
    print(f"  市场阶段 : {emoji} {phase}")
    print(f"  指数方向 : {idx}")
    print(f"  建议仓位 : {pos}%  [{int(timing['position_min']*100)}% ~ {int(timing['position_max']*100)}%]")
    print(f"  研判     : {timing['message']}")
    print("=" * 55)


def print_account_report(health: dict, holdings: list[dict], prices: dict[str, float]):
    state = health["account_state"]
    state_icon = {"良性": "🟢", "WARNING": "🟡", "EXIT": "🔴"}.get(state, "⚪")

    print()
    print("=" * 55)
    print(f"  💼 账户状态")
    print("=" * 55)
    print(f"  健康状态 : {state_icon} {state}")
    print(f"  仓位比例 : {health['position_ratio']*100:.1f}%")

    pnl = health["daily_pnl"]
    pct = health["daily_pnl_pct"]
    sign = "+" if pnl >= 0 else ""
    print(f"  当日浮盈 : {sign}{pnl:,.0f} 元  ({sign}{pct:.2f}%)")

    loss_days = health["consecutive_loss_days"]
    if loss_days > 0:
        print(f"  连续亏损 : {loss_days} 日")

    if holdings:
        print()
        print(f"  持仓明细 ({len(holdings)} 只):")
        for h in holdings:
            code  = h["code"]
            price = prices.get(code)
            cost_price = h["entry_price"]
            stop  = h["stop_loss"]
            if price:
                pnl_h = (price - cost_price) / cost_price * 100
                flag  = " 🔴止损!" if price <= stop else ""
                print(f"    {code} {h['name']}  "
                      f"成本 {cost_price:.2f} → 现价 {price:.2f}  "
                      f"({'+' if pnl_h>=0 else ''}{pnl_h:.1f}%)  "
                      f"止损 {stop}{flag}")
            else:
                print(f"    {code} {h['name']}  成本 {cost_price:.2f}  止损 {stop}  [价格未取到]")

    if health["messages"]:
        print()
        print("  ⚡ 告警:")
        for m in health["messages"]:
            print(f"    {m}")

    print("=" * 55)
    print("  仅供参考，不构成投资建议")
    print("=" * 55)

def print_scan_report(result: dict):
    total = result["total_hits"]
    print()
    print("=" * 55)
    print(f"  🔍 选股扫描  {result['date']}")
    print("=" * 55)
    print(f"  候选池 {result['candidate_count']} 只 | 命中 {total} 只")

    for model_name, stocks in result["hits"].items():
        if not stocks:
            continue
        label = stocks[0].get("label", "")
        print(f"\n  【{model_name}】{len(stocks)} 只  {label}")
        # 最多显示5只，按原顺序
        show = stocks[:5]
        for s in show:
            reasons = s.get("reasons", [])
            chg_str = next((r for r in reasons if r.startswith("涨幅")), "")
            net_str = next((r for r in reasons if "净买入" in r), "")
            hot_str = "热点" if any("热点" in r for r in reasons) else ""
            # fallback: for models without 涨幅/净买入, show first 2 reasons
            if not chg_str and not net_str:
                flags = "  ".join(r for r in reasons[:2])
            else:
                flags = "  ".join(x for x in [chg_str, net_str, hot_str] if x)
            print(f"    {s['code']} {s['name']}  {flags}")
        if len(stocks) > 5:
            print(f"    … 另有 {len(stocks)-5} 只，运行 scanner.py 查看完整列表")

    print()
    print("=" * 55)

def print_pool_report(date: str, do_refresh: bool = True):
    if do_refresh:
        refresh_lifecycles(date, verbose=False)
    summary = get_pool_summary()
    if not summary["total"]:
        return

    _LC_ICON = {"生·进入": "🟡", "住·持有": "🟢", "坏·注意": "🟠", "灭·出局": "🔴"}
    print()
    print("=" * 55)
    print(f"  🗂  观察票池  {summary['total']} 只")
    print("=" * 55)
    for lc, stocks in summary["by_stage"].items():
        if not stocks:
            continue
        icon = _LC_ICON.get(lc, "⚪")
        print(f"  {icon} {lc}  ({len(stocks)}只)")
        for s in stocks:
            print(f"    {s['code']} {s['name']:<8}  {s['add_reason']}")
    print("=" * 55)


def run(date: str, timing_only: bool = False, skip_scan: bool = False,
        dry_run: bool = False, run_strategy_flag: bool = False):
    print(f"\n🤖 交易员启动  {date}")

    if dry_run:
        print("  [dry-run 模式：跳过 MCP 调用，使用模拟数据]")
        timing = {
            "date": date,
            "temperature": 55.0,
            "phase": "冲刺",
            "index_direction": "上涨",
            "allow_trade": True,
            "recommended_position": 0.65,
            "position_min": 0.5,
            "position_max": 0.8,
            "message": "市场处于冲刺阶段，适宜加仓买入 [dry-run]",
            "raw": {},
        }
    else:
        timing = get_timing(date, verbose=True)

    print_timing_report(timing)

    # 自选池策略页（独立页面，可与 timing-only 等组合使用）
    if run_strategy_flag:
        print("\n[strategy] 自选池策略分析（约需 2-3 分钟）...")
        try:
            temp = int(timing.get("temperature", 0))
            pos  = int(timing.get("recommended_position", 0) * 100)
            run_strategy(date, market_temp=temp, recom_position=pos, verbose=True)
        except Exception as e:
            print(f"  ⚠ 策略分析失败: {e}")

    if timing_only:
        return

    # 选股扫描
    scan_result = None
    if not skip_scan and not dry_run:
        print("\n[scanner] 启动量化扫描（约需 2-3 分钟）...")
        try:
            scan_result = run_scan(date, verbose=True)
            print_scan_report(scan_result)
        except Exception as e:
            print(f"  ⚠ 扫描失败: {e}")

    # 主线识别
    sector_themes: list = []
    if not dry_run:
        try:
            sectors_data  = get_active_sectors(date, top_n=5)
            sector_themes = sectors_data.get("themes", [])
        except Exception as e:
            print(f"  ⚠ 主线识别失败: {e}")

    # 综合入池（扫描命中 + 板块龙头 + V3.9.4 cycleradar 上游，含容量/超时清理）
    if not dry_run and (scan_result or sector_themes):
        try:
            upstream = load_upstream_signals(date)  # V3.9.4: 读上游信号契约
            inflow = composite_inflow(scan_result, sector_themes, date,
                                      upstream_signals=upstream)
            if inflow["added"]:
                print(f"  [pool] 综合入池 +{inflow['added']} 只")
        except Exception as e:
            print(f"  ⚠ 综合入池失败: {e}")

    # 票池 + 买卖点信号
    print_pool_report(date)
    pool_summary = get_pool_summary()

    # 账户数据（在信号分析前读取，供仓位计算使用）
    data     = get_positions()
    holdings = data.get("holdings", [])
    total_capital = data.get("meta", {}).get("total_capital", 0)

    sig_results: list = []
    if pool_summary["total"] and not dry_run:
        pool_codes = [s["code"] for s in pool_summary["stocks"]
                      if s.get("lifecycle") not in ("灭·出局",)]
        if pool_codes:
            print("\n[signals] 分析票池买卖点...")
            try:
                sig_results = batch_analyze(pool_codes, date,
                                            total_capital=total_capital,
                                            filter_status=None)
                print_signal_report(sig_results)
            except Exception as e:
                print(f"  ⚠ 信号分析失败: {e}")

    # 账户检查
    health   = None
    prices: dict = {}

    if not holdings:
        print("\n  ℹ  暂无持仓，跳过账户检查")
    else:
        if dry_run:
            prices = {h["code"]: h["entry_price"] * 1.02 for h in holdings}
        else:
            print("\n[account] 获取持仓最新价格...")
            prices = fetch_current_prices(holdings)

        health = daily_health_check(
            date_str=date,
            current_prices=prices,
            recommended_position=timing["recommended_position"],
        )
        print_account_report(health, holdings, prices)

    _save_snapshot(date, timing, health, scan_result)

    # 深度诊断（票池中生·进入/住·持有的股票）
    diag_results: list = []
    if not dry_run and pool_summary["total"]:
        diag_codes = [
            s["code"] for s in pool_summary["stocks"]
            if s.get("lifecycle") in ("生·进入", "住·持有")
        ]
        if diag_codes:
            print(f"\n[diagnose] 深度诊断 {len(diag_codes)} 只...")
            try:
                lc_map = {s["code"]: s.get("lifecycle", "未知") for s in pool_summary["stocks"]}
                diag_results = run_diagnose(
                    diag_codes, date,
                    signals=sig_results,
                    daily_lifecycles=lc_map,
                    verbose=True,
                )
            except Exception as e:
                print(f"  ⚠ 深度诊断失败: {e}")

    # HTML 日报
    try:
        html_path = generate_report(
            date, timing, pool_summary, sig_results,
            scan_result, health, holdings, prices,
            sector_themes=sector_themes,
            diag_results=diag_results,
        )
        print(f"  🌐 HTML报告: {html_path}")
    except Exception as e:
        print(f"  ⚠ HTML报告生成失败: {e}")


def _save_snapshot(date: str, timing: dict, health, scan_result):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot: dict = {"date": date, "timing": timing}
    if health:
        snapshot["health"] = health
    if scan_result:
        sr = dict(scan_result)
        sr.pop("trending_sectors", None)
        snapshot["scan"] = sr
    snapshot_path = OUTPUT_DIR / f"trader_{date}.json"
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  💾 快照已保存: {snapshot_path}")


def main():
    parser = argparse.ArgumentParser(description="交易员 — 每日决策助手")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"),
                        help="交易日期 YYYY-MM-DD（默认今日）")
    parser.add_argument("--timing-only", action="store_true",
                        help="仅输出择时结果，不检查持仓")
    parser.add_argument("--skip-scan", action="store_true",
                        help="跳过选股扫描（快速模式）")
    parser.add_argument("--dry-run", action="store_true",
                        help="不调用 MCP，使用模拟数据验证流程")
    parser.add_argument("--strategy", action="store_true",
                        help="生成自选池策略页（40只诊断+评分+排名HTML）")
    args = parser.parse_args()
    run(args.date, timing_only=args.timing_only, skip_scan=args.skip_scan,
        dry_run=args.dry_run, run_strategy_flag=args.strategy)



if __name__ == "__main__":
    main()
