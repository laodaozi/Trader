"""
signal_tracker.py — 归集所有信号到统一追踪台账

信源优先级：
  1. 微信信源（领先信号）：杜牛牛/微策神机/数据宝/叙事平权old/台球之门/低吸波段王/财闻私享
  2. 扫描合成信号（alpha_signals / sector_outlook）
  3. 龙虎榜 / 大宗交易（滞后，仅辅助）

输出：底稿/history/signal_tracking_ledger.json
用法：
  python signal_tracker.py                 # 增量归集（跳过已处理日期）
  python signal_tracker.py --rebuild       # 全量重建
  python signal_tracker.py --date 2026-05-15  # 只处理指定日期
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

RAW_DIR = Path("底稿/raw")
HISTORY_DIR = Path("底稿/history")
THESIS_LEDGER = HISTORY_DIR / "thesis_ledger.json"
TRACKING_LEDGER = HISTORY_DIR / "signal_tracking_ledger.json"

# 微信信源名 → 信号类型偏向
SOURCE_ROLES = {
    "叙事平权old": "stock_pick",
    "在下杜牛牛": "stock_pick",
    "微策神机": "stock_pick",
    "数据宝": "stock_pick",
    "台球之门": "cycle_phase",
    "低吸波段王": "cycle_phase",
    "财闻私享": "sector_call",
    "小马白话期权": "volatility",
}

# 归集来源的优先级标签（越低越领先）
SOURCE_TIER = {
    "wechat_deep": 1,
    "thesis_ledger": 1,
    "alpha_signals": 2,
    "sector_outlook": 2,
    "events": 2,
    "wechat_all_stocks": 3,
    "inst_data": 4,
    "block_data": 4,
}


def _load_json(path: Path) -> dict | list | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _sig_id(date: str, source: str, signal_type: str, target: str | list) -> str:
    if isinstance(target, list):
        target = "/".join(str(t) for t in target)
    safe = str(target).replace(" ", "").replace("/", "_")[:20]
    src_safe = source.replace(" ", "")[:12]
    return f"SIG-{date.replace('-','')}-{src_safe}-{safe}"


def _extract_signals_for_date(date_str: str) -> list[dict]:
    signals_path = RAW_DIR / f"{date_str}_signals.json"
    data_path = RAW_DIR / f"{date_str}_daily_data.json"

    signals_file = _load_json(signals_path)
    data_file = _load_json(data_path)

    seen: set[str] = set()
    entries: list[dict] = []

    def _add(source: str, signal_type: str, target: str, target_code: str,
             direction: str, confidence: str, time_horizon: str,
             data_source: str, entry_price: float | None = None,
             sector: str | None = None):
        sig_id = _sig_id(date_str, source, signal_type, target)
        if sig_id in seen:
            return
        seen.add(sig_id)
        entries.append({
            "signal_id": sig_id,
            "date": date_str,
            "source": source,
            "signal_type": signal_type,
            "target": target,
            "target_code": target_code or "",
            "sector": sector or "",
            "direction": direction,
            "confidence": confidence,
            "time_horizon": time_horizon,
            "data_source": data_source,
            "tier": SOURCE_TIER.get(data_source, 5),
            "entry_price": entry_price,
            "verification": {
                "t5_price": None, "t10_price": None,
                "t5_return": None, "t10_return": None,
                "hit_t5": None, "hit_t10": None,
                "verified_at": None,
            },
        })

    # ── 1. 微信深度解析（wechat_deep）优先 ────────────────────────
    if data_file:
        wechat_deep = data_file.get("wechat_deep", {})
        if isinstance(wechat_deep, dict):
            for src_name, src_data in wechat_deep.items():
                if not isinstance(src_data, (dict, list)):
                    continue
                items = src_data if isinstance(src_data, list) else [src_data]
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    t = item.get("thesis") or {}
                    if not isinstance(t, dict):
                        t = {}
                    role = SOURCE_ROLES.get(src_name, "stock_pick")
                    # 个股信号
                    for stock in (t.get("target_stocks") or []):
                        _add(src_name, "stock_pick", stock, "",
                             "long", "medium", t.get("time_horizon", ""),
                             "wechat_deep")
                    # 行业信号（target_industry 可能是字符串或列表）
                    industries = t.get("target_industry") or []
                    if isinstance(industries, str):
                        industries = [industries] if industries else []
                    for industry in industries:
                        _add(src_name, "sector_call", industry, "",
                             "long", "medium", t.get("time_horizon", ""),
                             "wechat_deep", sector=industry)
                    # 情绪/周期信号
                    if role == "cycle_phase":
                        cycle = item.get("cycle_phase", {}) or {}
                        phase = cycle.get("phase", "")
                        trend = cycle.get("trend_call", "")
                        if phase:
                            _add(src_name, "cycle_phase", phase, "",
                                 "neutral", "medium", "",
                                 "wechat_deep")

    # ── 2. thesis_ledger（补充历史 thesis）────────────────────────
    thesis_data = _load_json(THESIS_LEDGER)
    if thesis_data:
        entries_list = thesis_data if isinstance(thesis_data, list) else thesis_data.get("entries", [])
        for entry in entries_list:
            if entry.get("created", "") != date_str:
                continue
            src = entry.get("source", "")
            t = entry.get("thesis") or {}
            for stock in (t.get("target_stocks") or []):
                _add(src, "stock_pick", stock, "",
                     "long", t.get("confidence", "medium"),
                     t.get("time_horizon", ""),
                     "thesis_ledger")
            industries = t.get("target_industry") or []
            if isinstance(industries, str):
                industries = [industries] if industries else []
            for industry in industries:
                _add(src, "sector_call", industry, "",
                     "long", t.get("confidence", "medium"),
                     t.get("time_horizon", ""),
                     "thesis_ledger", sector=industry)

    # ── 3. alpha_signals（合成个股信号）────────────────────────────
    if signals_file:
        for sig in (signals_file.get("alpha_signals") or []):
            stock = sig.get("stock") or {}
            code = stock.get("code", "")
            name = stock.get("name", "")
            # 解析 event_source 拆出微信信源
            raw_source = sig.get("event_source", "alpha_signals")
            source_label = raw_source.split("/")[0].strip() if "/" in raw_source else raw_source
            # 若 source_label 带"微信-"前缀则去掉
            if source_label.startswith("微信-"):
                source_label = source_label[3:]
            _add(source_label, "stock_pick", name or code, code,
                 sig.get("direction", "long"),
                 sig.get("confidence", "medium"),
                 sig.get("time_window", ""),
                 "alpha_signals",
                 entry_price=sig.get("entry_price"),
                 sector=sig.get("sector_context", "").split("·")[0].strip())

        # ── 4. events → stock_impact ─────────────────────────────
        for ev in (signals_file.get("events") or []):
            ev_source = ev.get("source", "events")
            # 解析信源名
            source_labels = [s.strip() for s in ev_source.replace("微信-", "").split("/")]
            for impact in (ev.get("stock_impact") or []):
                code = impact.get("code", "")
                name = impact.get("name", "")
                for lbl in source_labels[:1]:  # 只用第一个信源标签
                    _add(lbl, "stock_pick", name or code, code,
                         "long", "low", "",
                         "events")
            for impact in (ev.get("sector_impact") or []):
                sector = impact.get("sector", "")
                direction_map = {"利好": "long", "利空": "short", "中性": "neutral"}
                direction = direction_map.get(impact.get("direction", "中性"), "neutral")
                for lbl in source_labels[:1]:
                    _add(lbl, "sector_call", sector, "",
                         direction, "low", "",
                         "events", sector=sector)

        # ── 5. sector_outlook ────────────────────────────────────
        for so in (signals_file.get("sector_outlook") or []):
            sector = so.get("sector", "")
            direction_map = {"看多": "long", "看空": "short", "中性": "neutral"}
            direction = direction_map.get(so.get("direction", "中性"), "neutral")
            _add("sector_outlook", "sector_call", sector, "",
                 direction, so.get("confidence", "medium"),
                 so.get("time_horizon", ""),
                 "sector_outlook", sector=sector)

    # ── 6. wechat_all_stocks（微信提及个股，补充）────────────────
    if data_file:
        for item in (data_file.get("wechat_all_stocks") or []):
            if not isinstance(item, dict):
                continue
            src = item.get("source", "wechat")
            code = item.get("code", "")
            name = item.get("name", "")
            _add(src, "stock_pick", name or code, code,
                 "long", "low", "",
                 "wechat_all_stocks")

    return entries


def _get_all_signal_dates() -> list[str]:
    dates = set()
    for f in RAW_DIR.glob("*_signals.json"):
        stem = f.stem  # "2026-05-15_signals"
        date_part = stem.replace("_signals", "")
        try:
            datetime.strptime(date_part, "%Y-%m-%d")
            dates.add(date_part)
        except ValueError:
            pass
    return sorted(dates)


def load_tracking_ledger() -> dict:
    data = _load_json(TRACKING_LEDGER)
    if data and isinstance(data, dict):
        return data
    return {"updated_at": "", "processed_dates": [], "signals": []}


def save_tracking_ledger(ledger: dict) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    ledger["updated_at"] = datetime.now().isoformat()
    with open(TRACKING_LEDGER, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)


def run(dates: list[str] | None = None, rebuild: bool = False) -> None:
    ledger = {} if rebuild else load_tracking_ledger()
    if rebuild:
        ledger = {"updated_at": "", "processed_dates": [], "signals": []}

    processed = set(ledger.get("processed_dates", []))
    existing_ids = {s["signal_id"] for s in ledger.get("signals", [])}

    all_dates = dates or _get_all_signal_dates()
    new_total = 0

    for date_str in all_dates:
        if not rebuild and date_str in processed:
            print(f"  {date_str}: 已处理，跳过")
            continue
        print(f"  {date_str}: 归集中...", end="")
        new_entries = _extract_signals_for_date(date_str)
        added = 0
        for entry in new_entries:
            if entry["signal_id"] not in existing_ids:
                ledger.setdefault("signals", []).append(entry)
                existing_ids.add(entry["signal_id"])
                added += 1
        processed.add(date_str)
        new_total += added
        print(f" +{added} 条（合计 {len(ledger['signals'])}）")

    ledger["processed_dates"] = sorted(processed)
    save_tracking_ledger(ledger)

    # 统计报告
    print(f"\n{'═'*50}")
    print(f"信号追踪台账更新完成：共 {len(ledger['signals'])} 条")
    by_source: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for s in ledger["signals"]:
        by_source[s["source"]] = by_source.get(s["source"], 0) + 1
        by_type[s["signal_type"]] = by_type.get(s["signal_type"], 0) + 1
    print("\n按信源：")
    for src, cnt in sorted(by_source.items(), key=lambda x: -x[1]):
        print(f"  {src}: {cnt}")
    print("\n按类型：")
    for t, cnt in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"  {t}: {cnt}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true", help="全量重建")
    parser.add_argument("--date", help="只处理指定日期 YYYY-MM-DD")
    args = parser.parse_args()

    dates = [args.date] if args.date else None
    run(dates=dates, rebuild=args.rebuild)
