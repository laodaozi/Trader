#!/usr/bin/env python3.9
"""
calc_30d_winrate.py — 30 日胜率回溯计算

职责：
  1. 从 snapshots/ 找 30 天前的 alpha 快照
  2. 从 OHLC 缓存取快照日收盘价 + 最新收盘价
  3. 比较 direction（long/short）vs 实际涨跌幅
  4. 计算胜率，写入 positions.json 的 daily_pnl_history

运行方式：
  python3.9 core/scripts/calc_30d_winrate.py
  python3.9 core/scripts/calc_30d_winrate.py --dry-run    # 只打印，不写

Cron（收盘后，在 snapshot_alpha.sh 之后）：
  42 15 * * 1-5  cd /opt/cycleradar-trader && /usr/bin/python3.9 core/scripts/calc_30d_winrate.py >> data/logs/winrate_calc.log 2>&1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── 路径 ─────────────────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).resolve().parent.parent.parent
DATA_DIR     = BASE_DIR / "data"
SNAP_DIR     = Path("/opt/trader/output/snapshots")
OHLC_DIR     = DATA_DIR / "ohlc_cache"
POS_FILE     = DATA_DIR / "positions.json"

LOOKBACK_DAYS  = 30
LOOKBACK_RANGE = 5   # 快照日期容差（前后各 ±5 天）


# ── 工具 ─────────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def find_snapshot(target_date: datetime) -> Optional[Path]:
    """找到最接近 target_date 的快照文件"""
    if not SNAP_DIR.exists():
        return None
    best, best_diff = None, 9999
    for f in SNAP_DIR.glob("alpha_*.json"):
        try:
            date_str = f.stem.replace("alpha_", "")  # alpha_20260525.json → 20260525
            snap_dt = datetime.strptime(date_str, "%Y%m%d")
            diff = abs((snap_dt - target_date).days)
            if diff <= LOOKBACK_RANGE and diff < best_diff:
                best, best_diff = f, diff
        except ValueError:
            continue
    return best


def get_ohlc_close(code: str, target_date: str) -> Optional[float]:
    """从 OHLC 缓存获取指定日期的收盘价"""
    cache_path = OHLC_DIR / f"{code}.json"
    if not cache_path.exists():
        return None
    try:
        d = json.loads(cache_path.read_text(encoding="utf-8"))
        rows = d.get("rows", [])
        for row in rows:
            if row.get("date") == target_date:
                return float(row.get("close", 0))
    except (json.JSONDecodeError, KeyError, ValueError):
        return None
    return None


def get_latest_ohlc(code: str) -> Optional[tuple[str, float]]:
    """从 OHLC 缓存获取最新日期的收盘价 → (date, close)"""
    cache_path = OHLC_DIR / f"{code}.json"
    if not cache_path.exists():
        return None
    try:
        d = json.loads(cache_path.read_text(encoding="utf-8"))
        rows = d.get("rows", [])
        if not rows:
            return None
        latest = rows[-1]
        return latest.get("date"), float(latest.get("close", 0))
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def load_snap_signals(snap_path: Path) -> list[dict]:
    """加载快照中的 signal 列表"""
    try:
        d = json.loads(snap_path.read_text(encoding="utf-8"))
        signals = d.get("signals", d) if isinstance(d, dict) else d
        if isinstance(signals, dict):
            signals = signals.get("signals", signals.get("entries", []))
        if isinstance(signals, list):
            return signals
    except (json.JSONDecodeError, AttributeError):
        pass
    return []


def is_win(direction: str, entry_price: float, current_price: float) -> Optional[bool]:
    """判断方向 vs 实际涨跌是否一致"""
    if entry_price <= 0 or current_price <= 0:
        return None
    actual_return = (current_price - entry_price) / entry_price
    direction = (direction or "").lower().strip()
    if direction in ("long", "buy", "看多", "bullish", "up"):
        return actual_return > 0
    elif direction in ("short", "sell", "看空", "bearish", "down"):
        return actual_return < 0
    return None


def load_positions() -> dict:
    """加载 positions.json"""
    if POS_FILE.exists():
        try:
            return json.loads(POS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def save_positions(data: dict) -> None:
    """原子写入 positions.json"""
    tmp = POS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, POS_FILE)


# ── 主逻辑 ───────────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> None:
    today = datetime.now()
    target_date = today - timedelta(days=LOOKBACK_DAYS)
    log(f"=== 30 日胜率计算 ({today.strftime('%Y-%m-%d')}) ===")
    log(f"查找快照：target={target_date.strftime('%Y-%m-%d')} ±{LOOKBACK_RANGE}d")

    snap = find_snapshot(target_date)
    if not snap:
        log(f"❌ 未找到快照 → 跳过（可能 30 天前无交易日数据）")
        return

    snap_date_str = snap.stem.replace("alpha_", "")
    # 转 OHLC 日期格式：20260525 → 2026-05-25
    snap_date_fmt = f"{snap_date_str[:4]}-{snap_date_str[4:6]}-{snap_date_str[6:8]}"
    log(f"✓ 快照：{snap.name} (日期 {snap_date_str} → {snap_date_fmt})")

    signals = load_snap_signals(snap)
    log(f"✓ 快照 {len(signals)} 条信号")

    if not signals:
        log("快照为空 → 跳过")
        return

    # 逐信号比价
    wins = 0
    losses = 0
    no_data = 0
    results: list[dict] = []

    for sig in signals:
        stock_raw = sig.get("stock", "")
        if isinstance(stock_raw, dict):
            code = str(stock_raw.get("code", "")).strip()
        else:
            code = str(stock_raw).strip()
        direction = sig.get("direction", "")
        entry_price = float(sig.get("entry_price", 0) or 0)

        if not code:
            no_data += 1
            continue

        # 策略 1：OHLC close at snapshot date
        ohlc_close_snap = get_ohlc_close(code, snap_date_fmt)
        ref_price = ohlc_close_snap if ohlc_close_snap else entry_price

        # 取最新收盘价
        latest = get_latest_ohlc(code)
        if latest is None or ref_price <= 0:
            no_data += 1
            continue

        latest_date, latest_close = latest
        result = is_win(direction, ref_price, latest_close)

        if result is None:
            no_data += 1
            results.append({"code": code, "direction": direction, "win": None,
                            "entry": ref_price, "curr": latest_close, "reason": "invalid_price"})
        elif result:
            wins += 1
            results.append({"code": code, "direction": direction, "win": True,
                            "entry": ref_price, "curr": latest_close,
                            "return": round((latest_close - ref_price) / ref_price, 4)})
        else:
            losses += 1
            results.append({"code": code, "direction": direction, "win": False,
                            "entry": ref_price, "curr": latest_close,
                            "return": round((latest_close - ref_price) / ref_price, 4)})

    total_valid = wins + losses
    win_rate = round(wins / total_valid, 4) if total_valid > 0 else 0.0

    log(f"结果：{wins}W / {losses}L / {no_data}? | 有效 {total_valid} | 胜率 {win_rate*100:.1f}%")

    # 写入 positions.json → daily_pnl_history
    entry = {
        "date": today.strftime("%Y-%m-%d"),
        "source": "alpha_30d_backtest",
        "snapshot_date": snap_date_str,
        "total_signals": len(signals),
        "valid_signals": total_valid,
        "wins": wins,
        "losses": losses,
        "no_data": no_data,
        "win_rate": win_rate,
    }

    if dry_run:
        log(f"[DRY-RUN] 将写入：{json.dumps(entry, ensure_ascii=False)}")
        log(f"[DRY-RUN] 结果详情（前 5 条）：")
        for r in results[:5]:
            print(f"    {r}")
        return

    pos = load_positions()
    history = pos.get("daily_pnl_history", [])
    if not isinstance(history, list):
        history = []

    # 避免重复（同一天已有一条则替换）
    history = [e for e in history if e.get("date") != entry["date"]]
    history.append(entry)
    pos["daily_pnl_history"] = history

    save_positions(pos)
    log(f"✓ 已写入 positions.json → daily_pnl_history[{len(history)}]")

    # 打印本次 Top 5 收益 / Bottom 5 亏损
    wins_list = [r for r in results if r.get("win")]
    print("\n   📈 Top 5 收益：")
    for r in sorted(wins_list, key=lambda x: x.get("return", 0), reverse=True)[:5]:
        print(f"      {r['code']} {r['direction']} +{r['return']*100:.1f}%")

    losses_list = [r for r in results if r.get("win") is False]
    print("\n   📉 Bottom 5 亏损：")
    for r in sorted(losses_list, key=lambda x: x.get("return", 0))[:5]:
        print(f"      {r['code']} {r['direction']} {r['return']*100:.1f}%")


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="30日胜率回溯计算")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
