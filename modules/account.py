"""
modules/account.py — Layer 5: 持仓记录 + 账户健康检查

好运哥规则：
  - 连续2日亏损 → WARNING（减少操作）
  - 连续3日亏损 → EXIT（建议清仓）
  - 持仓比例 > timing.recommended_position × 1.1 → 超仓警告
  - 止损检查：任意持仓跌破止损位 → 立即告警

持久化：~/交易员/data/positions.json + data/trade_log.json
"""
from __future__ import annotations

import json
import os
from datetime import datetime, date
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).parent.parent / "data"
POSITIONS_FILE = DATA_DIR / "positions.json"
TRADE_LOG_FILE = DATA_DIR / "trade_log.json"

# 初始账户配置（若 config.json 不存在则使用默认值）
_DEFAULT_CAPITAL = 2_000_000  # 200万，可在 config.json 中覆盖


# ── I/O 工具 ─────────────────────────────────────────

def _load_positions() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if POSITIONS_FILE.exists():
        with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return _empty_positions()


def _save_positions(data: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_trade_log() -> list:
    if TRADE_LOG_FILE.exists():
        with open(TRADE_LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_trade_log(log: list):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(TRADE_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def _empty_positions() -> dict:
    return {
        "meta": {
            "total_capital": _DEFAULT_CAPITAL,
            "available_cash": _DEFAULT_CAPITAL,
            "position_ratio": 0.0,
            "last_updated": str(date.today()),
        },
        "holdings": [],
        "account_state": "良性",
        "consecutive_loss_days": 0,
        "daily_pnl_history": [],
    }


# ── 交易成本 ───────────────────────────────────────────

# A股费率（2026年现行）
#   佣金：0.025%（万一五），最低5元，买卖双向
#   印花税：0.05%（万五，千一减半），卖出单向
#   过户费：0.001%（十万分之一），买卖双向

def _calc_trade_costs(buy_price: float, sell_price: float, quantity: int) -> float:
    """计算完整双边交易成本（佣金+印花税+过户费）。"""
    buy_amt  = buy_price * quantity
    sell_amt = sell_price * quantity
    buy_comm  = max(buy_amt * 0.00025, 5.0)
    sell_comm = max(sell_amt * 0.00025, 5.0)
    stamp     = sell_amt * 0.0005
    transfer  = (buy_amt + sell_amt) * 0.00001
    return round(buy_comm + sell_comm + stamp + transfer, 2)


def _calc_buy_costs(buy_price: float, quantity: int) -> float:
    """仅计算买入端成本（买卖一次交易可用的买入部分）。"""
    buy_amt = buy_price * quantity
    return round(max(buy_amt * 0.00025, 5.0) + buy_amt * 0.00001, 2)


# ── 持仓 CRUD ─────────────────────────────────────────

def get_positions() -> dict:
    return _load_positions()


def add_holding(
    code: str,
    name: str,
    entry_date: str,
    entry_price: float,
    quantity: int,
    stop_loss: float,
    model_tag: str = "",
    lifecycle: str = "生·建仓",
    note: str = "",
) -> dict:
    """添加一笔持仓。"""
    data = _load_positions()
    cost = round(entry_price * quantity, 2)

    # 检查是否已有同代码持仓（摊平）
    for h in data["holdings"]:
        if h["code"] == code:
            old_cost = h["cost"]
            old_qty  = h["quantity"]
            new_qty  = old_qty + quantity
            new_cost = old_cost + cost
            h["quantity"] = new_qty
            h["cost"] = new_cost
            h["entry_price"] = round(new_cost / new_qty, 3)
            h["stop_loss"] = stop_loss
            h["lifecycle"] = lifecycle
            if note:
                h["note"] = note
            _recalc_meta(data)
            _save_positions(data)
            _append_trade_log("buy", code, name, entry_date, entry_price, quantity, cost, model_tag, note)
            return data

    data["holdings"].append({
        "code": code,
        "name": name,
        "entry_date": entry_date,
        "entry_price": entry_price,
        "quantity": quantity,
        "cost": cost,
        "stop_loss": stop_loss,
        "model_tag": model_tag,
        "lifecycle": lifecycle,
        "note": note,
    })
    _recalc_meta(data)
    _save_positions(data)
    _append_trade_log("buy", code, name, entry_date, entry_price, quantity, cost, model_tag, note)
    return data


def remove_holding(
    code: str,
    exit_date: str,
    exit_price: float,
    quantity: Optional[int] = None,
    note: str = "",
) -> dict:
    """卖出/减仓一笔持仓。quantity=None 表示全仓卖出。"""
    data = _load_positions()
    for i, h in enumerate(data["holdings"]):
        if h["code"] != code:
            continue
        qty = quantity or h["quantity"]
        trade_cost = _calc_trade_costs(h["entry_price"], exit_price, qty)
        pnl = round((exit_price - h["entry_price"]) * qty - trade_cost, 2)
        _append_trade_log("sell", code, h["name"], exit_date, exit_price, qty,
                          round(exit_price * qty, 2), h.get("model_tag", ""), note, pnl=pnl)
        if qty >= h["quantity"]:
            data["holdings"].pop(i)
        else:
            h["quantity"] -= qty
            h["cost"] = round(h["entry_price"] * h["quantity"], 2)
        _recalc_meta(data)
        _save_positions(data)
        return data
    raise ValueError(f"持仓中未找到 {code}")


def update_stop_loss(code: str, new_stop: float) -> dict:
    data = _load_positions()
    for h in data["holdings"]:
        if h["code"] == code:
            h["stop_loss"] = new_stop
            _save_positions(data)
            return data
    raise ValueError(f"持仓中未找到 {code}")


def update_lifecycle(code: str, lifecycle: str) -> dict:
    data = _load_positions()
    for h in data["holdings"]:
        if h["code"] == code:
            h["lifecycle"] = lifecycle
            _save_positions(data)
            return data
    raise ValueError(f"持仓中未找到 {code}")


def _recalc_meta(data: dict):
    total_cost = sum(h["cost"] for h in data["holdings"])
    capital = data["meta"]["total_capital"]
    data["meta"]["position_ratio"] = round(total_cost / max(capital, 1), 3)
    data["meta"]["available_cash"] = max(capital - total_cost, 0)
    data["meta"]["last_updated"] = str(date.today())


def _append_trade_log(
    action: str, code: str, name: str, trade_date: str,
    price: float, quantity: int, amount: float,
    model_tag: str, note: str, pnl: float = 0.0
):
    log = _load_trade_log()
    log.append({
        "action": action,
        "code": code,
        "name": name,
        "date": trade_date,
        "price": price,
        "quantity": quantity,
        "amount": amount,
        "model_tag": model_tag,
        "pnl": pnl,
        "note": note,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    _save_trade_log(log)


# ── 每日健康检查 ─────────────────────────────────────

def daily_health_check(
    date_str: str,
    current_prices: dict[str, float],
    recommended_position: float,
) -> dict:
    """
    每日健康检查。

    参数:
      date_str: 当日日期 YYYY-MM-DD
      current_prices: {code: latest_price}，用于计算当日 P&L 和止损检查
      recommended_position: timing 模块给出的建议仓位（0-1）

    返回:
    {
      "account_state": "良性" | "WARNING" | "EXIT",
      "daily_pnl": 总浮盈亏（元），
      "daily_pnl_pct": 总浮盈亏（%），
      "stop_loss_alerts": [{code, name, entry_price, stop_loss, current_price}],
      "overweight_alert": bool,
      "consecutive_loss_days": int,
      "messages": [str],
    }
    """
    data = _load_positions()
    messages = []
    alerts = []

    # 计算当日持仓浮盈亏
    total_cost   = 0.0
    total_market = 0.0
    stop_alerts  = []

    for h in data["holdings"]:
        code  = h["code"]
        price = current_prices.get(code)
        qty   = h["quantity"]
        cost  = h["cost"]
        total_cost += cost

        if price is None:
            messages.append(f"⚠ {code} {h['name']} 未获取到最新价格")
            continue

        market_val = price * qty
        total_market += market_val

        # 止损检查
        if price <= h["stop_loss"]:
            stop_alerts.append({
                "code": code,
                "name": h["name"],
                "entry_price": h["entry_price"],
                "stop_loss": h["stop_loss"],
                "current_price": price,
                "loss_pct": round((price - h["entry_price"]) / h["entry_price"] * 100, 2),
            })

    daily_pnl = round(total_market - total_cost, 2) if total_market > 0 else 0.0
    # 扣除已发生的买入端交易成本（佣金+过户费）
    if data["holdings"]:
        buy_cost_total = sum(
            _calc_buy_costs(h["entry_price"], h["quantity"])
            for h in data["holdings"]
        )
        daily_pnl = round(daily_pnl - buy_cost_total, 2)
    capital = data["meta"]["total_capital"]
    daily_pnl_pct = round(daily_pnl / capital * 100, 2) if capital > 0 else 0.0

    # 连续亏损计数
    history = data.get("daily_pnl_history", [])
    history.append({"date": date_str, "pnl": daily_pnl, "pnl_pct": daily_pnl_pct})
    # 保留最近30天
    history = history[-30:]
    data["daily_pnl_history"] = history

    loss_days = 0
    for h in reversed(history[:-1]):  # 不含今天
        if h["pnl"] < 0:
            loss_days += 1
        else:
            break
    data["consecutive_loss_days"] = loss_days

    # 账户状态判断（好运哥规则）
    if loss_days >= 3:
        state = "EXIT"
        messages.append(f"🔴 连续{loss_days}日亏损，建议清仓观望，重置账户状态")
    elif loss_days >= 2:
        state = "WARNING"
        messages.append(f"🟡 连续{loss_days}日亏损，减少操作，控制风险")
    else:
        state = "良性"

    data["account_state"] = state

    # 超仓检查
    overweight = data["meta"]["position_ratio"] > recommended_position * 1.1
    if overweight:
        messages.append(
            f"⚠ 当前仓位 {data['meta']['position_ratio']*100:.0f}% "
            f"超过建议仓位 {recommended_position*100:.0f}% × 1.1，请减仓"
        )

    # 止损告警信息
    for sa in stop_alerts:
        messages.append(
            f"🔴 止损触碰！{sa['code']} {sa['name']} "
            f"当前价 {sa['current_price']} ≤ 止损位 {sa['stop_loss']} "
            f"（亏损 {sa['loss_pct']}%）"
        )

    _save_positions(data)

    return {
        "account_state": state,
        "daily_pnl": daily_pnl,
        "daily_pnl_pct": daily_pnl_pct,
        "total_cost": total_cost,
        "total_market": total_market,
        "stop_loss_alerts": stop_alerts,
        "overweight_alert": overweight,
        "consecutive_loss_days": loss_days,
        "position_ratio": data["meta"]["position_ratio"],
        "messages": messages,
    }


def get_holdings_summary() -> list[dict]:
    """返回持仓简表（含持仓成本、止损位、周期阶段）。"""
    data = _load_positions()
    return data.get("holdings", [])


def set_total_capital(capital: float) -> dict:
    """更新账户总资金。"""
    data = _load_positions()
    data["meta"]["total_capital"] = capital
    _recalc_meta(data)
    _save_positions(data)
    return data


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--show", action="store_true", help="显示当前持仓")
    parser.add_argument("--set-capital", type=float, help="设置总资金")
    args = parser.parse_args()

    if args.set_capital:
        result = set_total_capital(args.set_capital)
        print(f"总资金已更新为: {result['meta']['total_capital']:,.0f} 元")
    elif args.show:
        data = get_positions()
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        data = get_positions()
        meta = data["meta"]
        holdings = data["holdings"]
        print(f"账户状态: {data['account_state']}")
        print(f"总资金: {meta['total_capital']:,.0f} 元")
        print(f"可用现金: {meta['available_cash']:,.0f} 元")
        print(f"仓位比例: {meta['position_ratio']*100:.1f}%")
        print(f"持仓数: {len(holdings)} 只")
        if holdings:
            print("\n持仓明细:")
            for h in holdings:
                print(f"  {h['code']} {h['name']} | 成本 {h['entry_price']} × {h['quantity']} "
                      f"= {h['cost']:,.0f} | 止损 {h['stop_loss']} | {h['lifecycle']}")
