"""
modules/pool.py — Layer 4: 票池 + 缠论生命周期标签

缠论四阶段（简化日线MA版）：

  生·进入  — MA5 刚从 MA10 下方穿越（近5日内金叉），价格在 MA20 上方
             适合：开始建仓观察
  住·持有  — MA5 > MA10 > MA20，收盘 > MA20（完整多头排列）
             适合：续持 / 择机加仓
  坏·注意  — MA5 < MA10，或收盘跌破 MA20（但收盘仍在 MA60 上方）
             适合：警惕，准备止损 / 减仓
  灭·出局  — 收盘 < MA60，或 MA5 < MA10 < MA20（完整空头排列）
             适合：清仓，移出票池

数据来源：modules/scanner.py::_get_kline（前复权日K）
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from core.trader_mcp import mcp_call

POOL_FILE = Path(__file__).parent.parent / "data" / "watchlist.json"

# 生命周期顺序（用于排序显示）
LIFECYCLE_ORDER = {
    "生·进入": 0,
    "住·持有": 1,
    "坏·注意": 2,
    "灭·出局": 3,
    "未知":    4,
}


def _get_kline(code: str, end_date: str, days: int = 80) -> tuple[str, list[dict]]:
    """获取日K线（同 scanner.py 实现，返回 (name, bars)）。"""
    start = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=days + 30)).strftime("%Y-%m-%d")
    data  = mcp_call("market_quote", "get_kline", {
        "keyword":            code,
        "start_date":         start,
        "end_date":           end_date,
        "kline_type":         1,
        "reinstatement_type": 2,
    })
    raw = data if isinstance(data, list) else data.get("list", [])
    name = raw[0].get("quote_name", "") if raw else ""
    bars = sorted([
        {
            "date":   b.get("trade_date", ""),
            "close":  float(b.get("close_price") or b.get("close") or 0),
            "volume": float(b.get("trade_lots") or b.get("volume") or 0),
        }
        for b in raw
    ], key=lambda x: x["date"])
    return name, bars


def _ma(values: list[float], n: int) -> list[Optional[float]]:
    result: list[Optional[float]] = []
    for i in range(len(values)):
        if i < n - 1:
            result.append(None)
        else:
            result.append(sum(values[i - n + 1: i + 1]) / n)
    return result


# ── 持久化 ───────────────────────────────────────────────

def load_pool() -> dict:
    if POOL_FILE.exists():
        with open(POOL_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"last_updated": "", "stocks": []}


def save_pool(pool: dict) -> None:
    with open(POOL_FILE, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)


# ── 核心：生命周期判断 ────────────────────────────────────

def assess_lifecycle(bars: list[dict]) -> str:
    """
    基于日线 K 线判断缠论阶段。
    - >= 60 条：完整四段（含MA60）
    - 10–59 条：降级判断（用MA5/10/20）
    - < 10 条：返回"未知"（数据不足）
    """
    if len(bars) < 10:
        return "未知"

    closes   = [b["close"] for b in bars]
    ma5_all  = _ma(closes, 5)
    ma10_all = _ma(closes, 10)
    ma20_all = _ma(closes, 20)
    ma60_all = _ma(closes, 60)

    ma5   = ma5_all[-1]
    ma10  = ma10_all[-1]
    ma20  = ma20_all[-1]
    ma60  = ma60_all[-1]
    close = closes[-1]

    if ma5 is None or ma10 is None:
        return "未知"

    # 灭·出局（需要MA60或MA20）
    if ma60 is not None and close < ma60:
        return "灭·出局"
    if ma20 is not None and ma5 < ma10 < ma20:
        return "灭·出局"

    # 坏·注意
    if ma5 < ma10:
        return "坏·注意"
    if ma20 is not None and close < ma20:
        return "坏·注意"

    # 住·持有（MA5>MA10>MA20 完整多头排列）
    if ma20 is not None and ma5 > ma10 > ma20:
        return "住·持有"

    # 生·进入 — MA5 近期刚从 MA10 下方穿越（近5日金叉）
    recently_below = False
    for i in range(-6, -1):
        v5  = ma5_all[i]
        v10 = ma10_all[i]
        if v5 is not None and v10 is not None and v5 < v10:
            recently_below = True
            break
    if recently_below and ma5 >= ma10:
        return "生·进入"

    # 其他：MA5>MA10 但多头排列不完整（如新股/MA20数据不足）
    if ma5 >= ma10:
        return "生·进入"

    return "坏·注意"


# ── entry_price 工具 ─────────────────────────────────────────

def _get_entry_price(code: str, date: str) -> Optional[float]:
    """查询指定日期的收盘价作为入选价。若当日无数据则向前找最近的交易日收盘价。"""
    try:
        _, bars = _get_kline(code, date, days=10)
        if not bars:
            return None
        found = None
        for b in reversed(bars):
            if b["date"] <= date:
                found = b
                break
        return float(found["close"]) if found else None
    except Exception:
        return None


# ── 票池 CRUD ─────────────────────────────────────────────

def add_to_pool(
    code: str,
    name: str,
    date: str,
    reason: str = "",
    lifecycle: str = "",
    notes: str = "",
) -> dict:
    """
    将股票加入观察池。若已存在则更新 reason/notes，不重复添加。
    返回操作后的股票记录。
    """
    pool = load_pool()
    # 检查是否已存在
    for s in pool["stocks"]:
        if s["code"] == code:
            if reason:
                s["add_reason"] = reason
            if notes:
                s["notes"] = notes
            save_pool(pool)
            return s

    entry_price = _get_entry_price(code, date)
    entry: dict = {
        "code": code,
        "name": name,
        "added_date": date,
        "add_reason": reason,
        "lifecycle": lifecycle or "生·进入",
        "lifecycle_updated": date,
        "notes": notes,
        "entry_price": entry_price,
        "entry_price_date": date,
        "entry_price_source": "mcp_kline",
    }
    pool["stocks"].append(entry)
    pool["last_updated"] = date
    save_pool(pool)
    ep_str = f"{entry_price:.2f}" if entry_price else "未获取"
    print(f"  [pool] 加入票池: {code} {name}  ({lifecycle or '生·进入'})  入选价:{ep_str}")
    return entry


def remove_from_pool(code: str, reason: str = "") -> bool:
    """从票池移除股票，返回是否找到并移除。"""
    pool = load_pool()
    before = len(pool["stocks"])
    pool["stocks"] = [s for s in pool["stocks"] if s["code"] != code]
    removed = len(pool["stocks"]) < before
    if removed:
        save_pool(pool)
        print(f"  [pool] 移出票池: {code}  {reason}")
    return removed


def update_lifecycle(code: str, lifecycle: str, date: str) -> bool:
    """手动更新某只股票的生命周期阶段。"""
    pool = load_pool()
    for s in pool["stocks"]:
        if s["code"] == code:
            old = s["lifecycle"]
            s["lifecycle"] = lifecycle
            s["lifecycle_updated"] = date
            save_pool(pool)
            if old != lifecycle:
                print(f"  [pool] {code} 阶段更新: {old} → {lifecycle}")
            return True
    return False


# ── 批量刷新生命周期 ──────────────────────────────────────

def refresh_lifecycles(date: str, verbose: bool = True, auto_remove_days: int = 2) -> dict:
    """
    拉取票池中所有股票的最新K线，重新计算生命周期阶段。
    灭·出局持续 auto_remove_days 天后自动移除。
    返回 {code: new_lifecycle}，自动移除的条目值为 "已移除"。
    """
    pool = load_pool()
    if not pool["stocks"]:
        return {}

    if verbose:
        print(f"  [pool] 刷新 {len(pool['stocks'])} 只票池股票的生命周期...")

    changes: dict[str, str] = {}
    to_remove: list[str] = []

    for s in pool["stocks"]:
        code = s["code"]
        try:
            _, bars = _get_kline(code, date, days=80)
            new_lc = assess_lifecycle(bars)
        except Exception:
            new_lc = s.get("lifecycle", "未知")

        old_lc = s.get("lifecycle", "未知")

        # 追踪首次进入灭·出局的日期
        if new_lc == "灭·出局":
            if old_lc != "灭·出局":
                s["exit_since"] = date          # 刚进入，记录首日
            # 检查是否已持续 auto_remove_days 天
            exit_since = s.get("exit_since", date)
            try:
                d0 = datetime.strptime(exit_since, "%Y-%m-%d")
                d1 = datetime.strptime(date, "%Y-%m-%d")
                if (d1 - d0).days >= auto_remove_days:
                    to_remove.append(code)
            except ValueError:
                pass
        else:
            s.pop("exit_since", None)           # 脱离灭阶段，清除计时

        s["lifecycle"] = new_lc
        s["lifecycle_updated"] = date

        if old_lc != new_lc:
            changes[code] = new_lc
            if verbose:
                print(f"    {code} {s['name']}: {old_lc} → {new_lc}")

    # 自动移除持续灭·出局的股票（但跳过仍在持仓中的）
    if to_remove:
        # 检查 positions.json 持仓情况
        positions_codes: set[str] = set()
        positions_file = Path(__file__).parent.parent / "data" / "positions.json"
        if positions_file.exists():
            with open(positions_file, encoding="utf-8") as f:
                pos_data = json.load(f)
            positions_codes = {h["code"] for h in pos_data.get("holdings", [])}

        for code in to_remove:
            name = next((s["name"] for s in pool["stocks"] if s["code"] == code), code)
            if code in positions_codes:
                if verbose:
                    print(f"    [保留] {code} {name}  灭·出局≥{auto_remove_days}日但仍在持仓，不移出票池")
                continue
            pool["stocks"] = [s for s in pool["stocks"] if s["code"] != code]
            changes[code] = "已移除"
            if verbose:
                print(f"    [自动移除] {code} {name}  灭·出局 ≥{auto_remove_days}日")

    pool["last_updated"] = date
    # 顺带执行超时清理（90日），不额外拉网络，纯日期判断
    cap_removed = _enforce_capacity(pool, date, max_size=50, timeout_days=90)
    for code in cap_removed:
        changes[code] = "已移除"
    save_pool(pool)
    return changes


# ── 查询 ────────────────────────────────────────────────

def get_pool_summary() -> dict:
    """返回票池摘要，按生命周期阶段分组。"""
    pool = load_pool()
    stocks = pool.get("stocks", [])

    by_stage: dict[str, list[dict]] = {k: [] for k in LIFECYCLE_ORDER}
    for s in stocks:
        lc = s.get("lifecycle", "未知")
        by_stage.setdefault(lc, []).append(s)

    return {
        "total": len(stocks),
        "last_updated": pool.get("last_updated", ""),
        "by_stage": by_stage,
        "stocks": sorted(stocks, key=lambda x: LIFECYCLE_ORDER.get(x.get("lifecycle", "未知"), 99)),
    }


# ── 容量 + 超时维护 ──────────────────────────────────────

def _enforce_capacity(pool: dict, date: str, max_size: int = 50, timeout_days: int = 90) -> list[str]:
    """
    1. 移除超过 timeout_days 未更新的股票（生命周期非住·持有）
    2. 若仍超 max_size，按优先级淘汰：灭·出局 → 坏·注意 → 最早入池
    返回被移除的 code 列表。
    """
    removed: list[str] = []
    today = datetime.strptime(date, "%Y-%m-%d")

    # 超时移除（住·持有豁免）
    survivors = []
    for s in pool["stocks"]:
        lc = s.get("lifecycle", "未知")
        if lc == "住·持有":
            survivors.append(s)
            continue
        updated = s.get("lifecycle_updated") or s.get("added_date", "")
        try:
            delta = (today - datetime.strptime(updated, "%Y-%m-%d")).days
        except ValueError:
            delta = 0
        if delta >= timeout_days:
            removed.append(s["code"])
            print(f"  [pool] 超时移除({delta}日): {s['code']} {s['name']}")
        else:
            survivors.append(s)
    pool["stocks"] = survivors

    # 容量裁剪
    if len(pool["stocks"]) > max_size:
        priority = {"灭·出局": 0, "坏·注意": 1, "未知": 2, "生·进入": 3, "住·持有": 4}
        pool["stocks"].sort(key=lambda s: (
            priority.get(s.get("lifecycle", "未知"), 2),
            s.get("added_date", ""),
        ))
        excess = pool["stocks"][max_size:]
        pool["stocks"] = pool["stocks"][:max_size]
        for s in excess:
            removed.append(s["code"])
            print(f"  [pool] 容量裁剪: {s['code']} {s['name']} ({s.get('lifecycle','')})")

    return removed


# ── 综合入池（三源自动导入）────────────────────────────────

def composite_inflow(
    scan_result: Optional[dict],
    sector_themes: Optional[list[dict]],
    date: str,
    sector_score_threshold: int = 6,
    auto_assess: bool = True,
    max_size: int = 50,
    timeout_days: int = 90,
    upstream_signals: Optional[dict] = None,  # V3.9.4: cycleradar 上游信号
) -> dict:
    """
    三源自动入池：
      1. 扫描命中（scan_result.hits）
      2. 板块龙头（sector_themes，score >= sector_score_threshold，且 code 非空）
      3. （微信主题由外部预处理后以 scan_result 形式传入）
      4. V3.9.4: cycleradar 上游个股信号（upstream_signals）

    同时执行容量控制和超时清理。
    返回 {"added": int, "removed": int, "capacity_removed": list[str]}
    """
    pool = load_pool()
    existing = {s["code"] for s in pool["stocks"]}
    added = 0

    # ── 源1：扫描命中 ──
    for model_name, stocks in (scan_result or {}).get("hits", {}).items():
        for s in stocks:
            code = s["code"]
            if code in existing or not code:
                continue
            lc = "生·进入"
            if auto_assess:
                try:
                    _, bars = _get_kline(code, date, days=80)
                    lc = assess_lifecycle(bars)
                except Exception:
                    pass
            add_to_pool(code, s["name"], date, reason=model_name,
                        lifecycle=lc, notes=s.get("note", ""))
            existing.add(code)
            added += 1

    # ── 源2：板块龙头 ──
    for theme in (sector_themes or []):
        if theme.get("score", 0) < sector_score_threshold:
            continue
        for ldr in theme.get("leaders", []):
            code = ldr.get("code", "")
            name = ldr.get("name", "")
            if not code or code in existing:
                continue
            lc = "生·进入"
            if auto_assess:
                try:
                    _, bars = _get_kline(code, date, days=80)
                    lc = assess_lifecycle(bars)
                except Exception:
                    pass
            reason = f"板块龙头·{theme['name']}({theme['score']}分)"
            add_to_pool(code, name, date, reason=reason, lifecycle=lc)
            existing.add(code)
            added += 1

    # ── 源4（V3.9.4）：cycleradar 上游个股信号 ──
    if upstream_signals:
        for stk in upstream_signals.get("stocks", []):
            code = stk.get("code", "")
            name = stk.get("name", "")
            logic = stk.get("logic", "")
            confidence = stk.get("confidence", "low")
            if not code or code in existing:
                continue
            lc = "生·进入"
            if auto_assess:
                try:
                    _, bars = _get_kline(code, date, days=80)
                    lc = assess_lifecycle(bars)
                except Exception:
                    pass
            reason = f"cycleradar 上游·{confidence}·{logic[:40]}" if logic else f"cycleradar 上游·{confidence}"
            add_to_pool(code, name, date, reason=reason, lifecycle=lc)
            existing.add(code)
            added += 1

    # ── 容量 + 超时维护 ──
    pool = load_pool()           # reload after add_to_pool writes
    cap_removed = _enforce_capacity(pool, date, max_size=max_size, timeout_days=timeout_days)
    if cap_removed:
        save_pool(pool)

    print(f"  [pool] 综合入池完成: 新增 {added} 只，清理 {len(cap_removed)} 只，当前 {len(pool['stocks'])} 只")
    return {"added": added, "removed": len(cap_removed), "capacity_removed": cap_removed}


# ═══════════════════════════════════════════════════════
# V3.9.4: 上游信号（cycleradar → trader 文件契约对接）
# ═══════════════════════════════════════════════════════

UPSTREAM_CONTRACT_FILE = Path(__file__).parent.parent / "data" / "upstream_signals.jsonl"


def load_upstream_signals(today: str, max_age_days: int = 5) -> dict:
    """加载 cycleradar 上游信号契约文件，返回仍新鲜的信号。

    新鲜度判定：date + decay_days >= today。

    Returns:
        {"signals": [dict, ...],   # 全部新鲜信号
         "sectors": set[str],      # 涉及的行业集合（用于共振匹配）
         "stocks": list[dict]}     # 涉及的个股列表 [{code, name, logic, confidence}]
    """
    import datetime as _dt

    result: dict = {"signals": [], "sectors": set(), "stocks": []}
    if not UPSTREAM_CONTRACT_FILE.exists():
        return result

    try:
        today_dt = _dt.date.fromisoformat(today)
    except (ValueError, TypeError):
        return result

    try:
        with open(UPSTREAM_CONTRACT_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return result

    seen_codes = set()
    for line in lines:
        try:
            sig = json.loads(line)
        except json.JSONDecodeError:
            continue

        # 新鲜度过滤
        sig_date_str = sig.get("date", "")
        decay = sig.get("decay_days", 3)
        try:
            sig_date = _dt.date.fromisoformat(sig_date_str)
            expires = sig_date + _dt.timedelta(days=decay)
            if today_dt > expires:
                continue
        except (ValueError, TypeError):
            continue  # 无法解析日期的不新鲜，跳过

        result["signals"].append(sig)

        # 行业收集（用于共振匹配）
        for sec in sig.get("sectors", []):
            if sec:
                result["sectors"].add(sec)

        # 个股收集（去重）
        for stk in sig.get("stocks", []):
            code = stk.get("code", "")
            if code and code not in seen_codes:
                seen_codes.add(code)
                result["stocks"].append({
                    "code": code,
                    "name": stk.get("name", ""),
                    "logic": stk.get("logic", ""),
                    "confidence": sig.get("confidence", "low"),
                })

    return result


# ── 从扫描结果批量导入 ────────────────────────────────────

def import_from_scan(scan_result: dict, date: str, auto_assess: bool = True) -> int:
    """
    将扫描命中结果导入票池（仅加入尚未在池中的股票）。
    auto_assess=True 时拉取K线计算初始生命周期。
    返回新增数量。
    """
    pool = load_pool()
    existing = {s["code"] for s in pool["stocks"]}
    added = 0

    for model_name, stocks in scan_result.get("hits", {}).items():
        for s in stocks:
            code = s["code"]
            if code in existing:
                continue
            name = s["name"]
            lifecycle = "生·进入"
            if auto_assess:
                try:
                    _, bars = _get_kline(code, date, days=80)
                    lifecycle = assess_lifecycle(bars)
                except Exception:
                    pass
            add_to_pool(code, name, date, reason=model_name,
                        lifecycle=lifecycle, notes=s.get("note", ""))
            existing.add(code)
            added += 1

    return added


# ── 主入口（命令行查看/刷新票池）────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="票池管理")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list",    help="显示当前票池")
    sub.add_parser("refresh", help="刷新生命周期阶段")

    add_p = sub.add_parser("add", help="手动加入股票")
    add_p.add_argument("code")
    add_p.add_argument("name")
    add_p.add_argument("--reason", default="手动")
    add_p.add_argument("--notes",  default="")

    rm_p = sub.add_parser("remove", help="移出股票")
    rm_p.add_argument("code")

    args = parser.parse_args()

    if args.cmd == "refresh":
        changes = refresh_lifecycles(args.date)
        removed = sum(1 for v in changes.values() if v == "已移除")
        print(f"  变更 {len(changes)} 只（含自动移除 {removed} 只）")
    elif args.cmd == "add":
        add_to_pool(args.code, args.name, args.date, args.reason, notes=args.notes)
    elif args.cmd == "remove":
        remove_from_pool(args.code)
    else:
        # 默认：list
        summary = get_pool_summary()
        if not summary["total"]:
            print("票池为空。使用 `python3 modules/pool.py add <code> <name>` 添加。")
        else:
            print(f"\n票池  {summary['total']} 只  (更新: {summary['last_updated']})")
            print("-" * 50)
            for lc, stocks in summary["by_stage"].items():
                if not stocks:
                    continue
                print(f"\n【{lc}】{len(stocks)} 只")
                for s in stocks:
                    print(f"  {s['code']} {s['name']:<8}  加入: {s['added_date']}  理由: {s['add_reason']}")
                    if s.get("notes"):
                        print(f"          {s['notes']}")
            print()
