#!/usr/bin/env python3.9
"""
tracker_closer.py — CycleRadar Tracker OHLC 闭环

职责：
  读取 tracker_log.jsonl 中 result=NODATA 的记录，
  通过 AKShare 拉取对应股票的历史 K 线，
  比价判断是否命中止盈/止损/超时，
  原子写回 tracker_log.jsonl（atomic rewrite via .tmp + os.replace）。

运行方式：
  /usr/bin/python3.9 core/scripts/tracker_closer.py
  /usr/bin/python3.9 core/scripts/tracker_closer.py --dry-run   # 只打印，不写

Cron（收盘后运行）：
  0 16 * * 1-5  cd /opt/cycleradar-trader && /usr/bin/python3.9 core/scripts/tracker_closer.py >> data/logs/tracker_closer.log 2>&1
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── 路径 ─────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).resolve().parent.parent.parent  # /opt/cycleradar-trader
DATA_DIR    = BASE_DIR / "data"
TRACKER_FILE = DATA_DIR / "tracker_log.jsonl"
OHLC_DIR    = DATA_DIR / "ohlc_cache"
LOG_DIR     = DATA_DIR / "logs"

# ── 结果常量 ──────────────────────────────────────────────────────────────────
RESULT_WIN    = "WIN"
RESULT_LOSE   = "LOSE"
RESULT_HOLD   = "HOLD"    # 窗口内未触发，仍在观察期
RESULT_EXPIRE = "EXPIRE"  # 观察期已过但未触发（无数据判断不到）
RESULT_NODATA = "NODATA"


# ── OHLC 获取（优先 cache，次选腾讯直连）────────────────────────────────────
# 复用 core/stock_analysis.py 的腾讯 API：ECS 上 AKShare(东方财富) 被限，腾讯可用

# 把 core/ 加入 sys.path 以便 import stock_analysis
sys.path.insert(0, str(BASE_DIR / "core"))

def _load_ohlc_cache_fresh(code: str, max_age_hours: int = 23) -> Optional[list[dict]]:
    """从本地缓存读取 OHLC rows；超过 max_age_hours 视为过期"""
    p = OHLC_DIR / f"{code}.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        rows = d.get("rows", [])
        if not rows:
            return None
        fetched_at = d.get("fetched_at", "")
        if fetched_at:
            fa = datetime.fromisoformat(fetched_at)
            age_h = (datetime.now() - fa).total_seconds() / 3600
            if age_h > max_age_hours:
                return None  # 过期，需要刷新
        return rows
    except Exception:
        return None


def _fetch_ohlc_tencent(code: str, start_date: str) -> Optional[list[dict]]:
    """直连腾讯行情 API 拉历史 K 线（复用 stock_analysis 的实现）"""
    try:
        import requests
        start_dt = datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=10)
        end_dt   = datetime.now()

        tx_code = ("sh" if code.startswith(("6", "9")) else "sz") + code
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
                    break
                stock_data = data.get("data", {}).get(tx_code, {})
                klines = stock_data.get("qfqday") or stock_data.get("day") or []
                if not klines:
                    if attempt < 2:
                        import time; time.sleep(1.5)
                        continue
                    break
                rows = []
                for row in klines:
                    # [日期, 开盘, 收盘, 最高, 最低, 成交量]
                    rows.append({
                        "date":  str(row[0]),
                        "open":  float(row[1]),
                        "close": float(row[2]),
                        "high":  float(row[3]),
                        "low":   float(row[4]),
                    })
                # 写缓存
                OHLC_DIR.mkdir(parents=True, exist_ok=True)
                cache_path = OHLC_DIR / f"{code}.json"
                tmp_path   = cache_path.with_suffix(".json.tmp")
                payload = {
                    "code": code, "source": "tencent",
                    "fetched_at": datetime.now().isoformat(timespec="seconds"),
                    "rows": rows,
                }
                tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp_path, cache_path)
                return rows
            except Exception as e:
                if attempt < 2:
                    import time; time.sleep(1.5)
                else:
                    raise
        return None
    except Exception as e:
        print(f"  [WARN] {code} 腾讯 API 拉取失败: {e}", file=sys.stderr)
        return None


def get_ohlc(code: str, start_date: str) -> Optional[list[dict]]:
    """取 OHLC 数据：优先 fresh cache，过期或无则腾讯直连"""
    rows = _load_ohlc_cache_fresh(code)
    if rows is None:
        rows = _fetch_ohlc_tencent(code, start_date)
    return rows


# ── 核心比价逻辑 ──────────────────────────────────────────────────────────────

def close_record(rec: dict, rows: list[dict]) -> dict:
    """
    对单条 tracker 记录做比价，返回更新后的 rec（不修改原对象）。

    规则：
      - 观察窗口：从 track_date 开始，共 horizon 个交易日
      - 命中止盈：窗口内任意一日 high >= targets[0]  → WIN
      - 命中止损：窗口内任意一日 low  <= stop         → LOSE
      - 同一天同时触发：优先 WIN（当天 high 先于 low 算法无法区分，保守取 WIN）
      - 窗口结束未触发：
          - 今天 < 窗口结束日期 → HOLD（继续等待）
          - 今天 >= 窗口结束日期 → EXPIRE
    """
    r = dict(rec)

    track_date_str = r.get("track_date", "")
    horizon        = int(r.get("horizon", 5))
    entry          = float(r.get("entry", 0) or 0)
    stop           = float(r.get("stop",  0) or 0)
    targets        = r.get("targets", [])
    target1        = float(targets[0]) if targets else None

    if not track_date_str or not target1 or not stop:
        return r  # 数据不完整，保留 NODATA

    # 按日期过滤：只取 >= track_date 的 K 线
    try:
        track_date = datetime.strptime(track_date_str, "%Y-%m-%d")
    except ValueError:
        return r

    window_rows = [
        row for row in rows
        if row.get("date", "") >= track_date_str
    ]
    # 只取前 horizon 根（交易日数）
    window_rows = window_rows[:horizon]

    if not window_rows:
        # 还没有数据（比如刚发出信号，当天就跑）
        r["result"] = RESULT_HOLD
        return r

    today_str = datetime.now().strftime("%Y-%m-%d")
    window_end_str = ""
    if len(window_rows) >= horizon:
        window_end_str = window_rows[-1]["date"]

    # 逐日扫描
    hit_target_day = None
    hit_stop_day   = None
    max_high = 0.0
    max_dd   = 0.0

    for i, row in enumerate(window_rows):
        high  = float(row.get("high",  0) or 0)
        low   = float(row.get("low",   0) or 0)
        close = float(row.get("close", 0) or 0)

        if entry > 0:
            day_return = (close - entry) / entry
            max_high   = max(max_high, (high - entry) / entry)
            drawdown   = (low - entry) / entry
            max_dd     = min(max_dd, drawdown)

        # 止盈检查（优先）
        if target1 and high >= target1 and hit_target_day is None:
            hit_target_day = i + 1  # 第几个交易日命中

        # 止损检查
        if stop and low <= stop and hit_stop_day is None:
            hit_stop_day = i + 1

    # 判断结果
    if hit_target_day is not None and (hit_stop_day is None or hit_target_day <= hit_stop_day):
        # 命中止盈（或止盈止损同日，优先 WIN）
        last_close = float(window_rows[hit_target_day - 1].get("close", entry))
        r["result"]         = RESULT_WIN
        r["hit_target"]     = True
        r["hit_stop"]       = False
        r["days_to_target"] = hit_target_day
        r["days_to_stop"]   = None
        r["n_bars"]         = len(window_rows)
        r["final_return"]   = round((target1 - entry) / entry, 4) if entry > 0 else 0
        r["max_return"]     = round(max_high, 4)
        r["max_dd"]         = round(max_dd, 4)

    elif hit_stop_day is not None:
        # 命中止损
        r["result"]         = RESULT_LOSE
        r["hit_target"]     = False
        r["hit_stop"]       = True
        r["days_to_target"] = None
        r["days_to_stop"]   = hit_stop_day
        r["n_bars"]         = len(window_rows)
        r["final_return"]   = round((stop - entry) / entry, 4) if entry > 0 else 0
        r["max_return"]     = round(max_high, 4)
        r["max_dd"]         = round(max_dd, 4)

    else:
        # 未触发
        r["n_bars"]     = len(window_rows)
        r["max_return"] = round(max_high, 4)
        r["max_dd"]     = round(max_dd, 4)
        if window_end_str and today_str > window_end_str:
            # 窗口已满但未触发 → EXPIRE
            last_close = float(window_rows[-1].get("close", entry)) if window_rows else entry
            r["result"]       = RESULT_EXPIRE
            r["hit_target"]   = False
            r["hit_stop"]     = False
            r["final_return"] = round((last_close - entry) / entry, 4) if entry > 0 else 0
        else:
            # 窗口未满 → 继续观察
            r["result"]     = RESULT_HOLD
            r["hit_target"] = None
            r["hit_stop"]   = False

    return r


# ── 文件读写（atomic rewrite）────────────────────────────────────────────────

def load_tracker() -> list[dict]:
    if not TRACKER_FILE.exists():
        return []
    records = []
    with open(TRACKER_FILE, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"  [WARN] 第 {ln} 行解析失败，跳过: {e}", file=sys.stderr)
    return records


def save_tracker_atomic(records: list[dict]) -> None:
    """先写 .tmp，再 os.replace（POSIX 原子操作）。写前备份 .bak。"""
    tmp_path = TRACKER_FILE.with_suffix(".jsonl.tmp")
    bak_path = TRACKER_FILE.with_suffix(".jsonl.bak")

    # 写临时文件
    with open(tmp_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # 备份当前文件（覆盖旧备份，只保留 1 份）
    if TRACKER_FILE.exists():
        shutil.copy2(TRACKER_FILE, bak_path)

    # 原子替换
    os.replace(tmp_path, TRACKER_FILE)


# ── 主流程 ────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[tracker_closer] {now_str} {'DRY-RUN ' if dry_run else ''}开始")

    records = load_tracker()
    print(f"  读取 {len(records)} 条 tracker 记录")

    # 按 code 分组，只对 NODATA 和 HOLD 的记录处理
    to_close = [r for r in records if r.get("result") in (RESULT_NODATA, RESULT_HOLD)]
    already_done = [r for r in records if r.get("result") not in (RESULT_NODATA, RESULT_HOLD)]
    print(f"  待处理: {len(to_close)} 条 (NODATA/HOLD) | 已完成: {len(already_done)} 条")

    if not to_close:
        print("  无待处理记录，退出。")
        return

    # 按 code 分批拉 OHLC（每只股票只拉一次）
    codes_needed = sorted(set(r["code"] for r in to_close))
    print(f"  需要拉取 OHLC 的标的: {len(codes_needed)} 只 → {codes_needed}")

    ohlc_map: dict[str, list[dict]] = {}
    for code in codes_needed:
        # 找到该 code 最早的 signal_date 作为 OHLC 起始点
        earliest = min(
            r["signal_date"] for r in to_close if r["code"] == code
        )
        rows = get_ohlc(code, earliest)
        if rows:
            ohlc_map[code] = rows
            print(f"    {code}: {len(rows)} 条 K 线 (from {rows[0]['date']} to {rows[-1]['date']})")
        else:
            print(f"    {code}: ❌ 无法获取 OHLC 数据，保留 NODATA")

    # 逐条比价
    updated_records: dict[tuple, dict] = {}  # key=(code, signal_date, horizon)
    stats = {"win": 0, "lose": 0, "hold": 0, "expire": 0, "nodata": 0, "skip": 0}

    for rec in to_close:
        key = (rec["code"], rec["signal_date"], rec["horizon"])
        rows = ohlc_map.get(rec["code"])
        if rows is None:
            updated_records[key] = rec  # 保留 NODATA
            stats["nodata"] += 1
            continue
        closed = close_record(rec, rows)
        updated_records[key] = closed
        result = closed.get("result", "?")
        stats[result.lower() if result.lower() in stats else "skip"] += 1

    # 合并：已完成的 + 本次更新的
    final_records = list(already_done)
    for rec in to_close:
        key = (rec["code"], rec["signal_date"], rec["horizon"])
        final_records.append(updated_records.get(key, rec))

    # 按 signal_date + code + horizon 排序，保持文件整洁
    final_records.sort(key=lambda r: (r.get("signal_date", ""), r.get("code", ""), r.get("horizon", 0)))

    # 打印统计
    print(f"\n  比价结果：")
    print(f"    WIN   : {stats['win']}")
    print(f"    LOSE  : {stats['lose']}")
    print(f"    HOLD  : {stats['hold']}  (窗口未满，继续观察)")
    print(f"    EXPIRE: {stats['expire']}  (窗口已过，未触发)")
    print(f"    NODATA: {stats['nodata']}  (无 OHLC 数据)")

    if dry_run:
        print("\n  [DRY-RUN] 不写入文件。")
        # dry-run 时打印几条样本
        sample_changed = [
            updated_records[k] for k in updated_records
            if updated_records[k].get("result") != RESULT_NODATA
        ][:5]
        for r in sample_changed:
            print(f"    {r['code']} {r['name']} {r['signal_date']} h{r['horizon']} → {r['result']}"
                  f" (hit_target={r.get('hit_target')}, days={r.get('days_to_target') or r.get('days_to_stop')},"
                  f" final_return={r.get('final_return',0):.1%})")
        return

    # 原子写回
    save_tracker_atomic(final_records)
    print(f"\n  ✅ 写回完成：{len(final_records)} 条 → {TRACKER_FILE}")
    print(f"     备份：{TRACKER_FILE.with_suffix('.jsonl.bak')}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="tracker_closer — OHLC 比价闭环")
    parser.add_argument("--dry-run", action="store_true", help="只打印结果，不写文件")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
