"""Event and rotation signal helpers for CycleRadar scoring."""

from __future__ import annotations


def _as_float(value: object, default: float = 0.0) -> float:
    """Convert numeric fields from scan/ledger payloads without leaking errors."""
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: object, default: int) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _scan_score(item: dict) -> float:
    return _as_float(item.get("score_auto", item.get("score", 0.0)))


def _ledger_score(item: dict) -> float:
    return _as_float(item.get("score", item.get("score_auto", 0.0)))


def detect_rotation_signals(
    ledger: dict,
    date_str: str,
    scan_results: list[dict],
) -> list[dict]:
    """Detect top-10 industry rotation versus the previous ledger week."""
    current_top10 = scan_results[:10]
    current_map = {item.get("name"): item for item in scan_results if item.get("name")}
    current_top10_names = {item.get("name") for item in current_top10 if item.get("name")}

    weeks = sorted(ledger.get("weeks", []), key=lambda week: week.get("date", ""))
    prev_week = None
    for week in weeks:
        if week.get("date", "") < date_str:
            prev_week = week
        else:
            break

    if not prev_week:
        return []

    prev_top10 = prev_week.get("top10", [])[:10]
    prev_map = {item.get("name"): item for item in prev_top10 if item.get("name")}
    prev_top10_names = set(prev_map)

    signals: list[dict] = []

    for index, current in enumerate(current_top10, start=1):
        name = current.get("name")
        if not name or name in prev_top10_names:
            continue
        signals.append({
            "type": "new_entry",
            "name": name,
            "industry": name,
            "detail": f"{name} 新进入 TOP10（#{_as_int(current.get('rank'), index)}，{current.get('stage', '')}）",
            "rank": _as_int(current.get("rank"), index),
            "prev_rank": None,
            "score": _scan_score(current),
            "prev_score": None,
            "date": date_str,
            "stage": current.get("stage", ""),
        })

    for index, previous in enumerate(prev_top10, start=1):
        name = previous.get("name")
        if not name or name in current_top10_names:
            continue
        current = current_map.get(name, {})
        prev_rank = _as_int(previous.get("rank"), index)
        signals.append({
            "type": "exit",
            "name": name,
            "industry": name,
            "detail": f"{name} 跌出 TOP10（前 #{prev_rank}）",
            "rank": prev_rank,
            "prev_rank": prev_rank,
            "score": _scan_score(current) if current else 0.0,
            "prev_score": _ledger_score(previous),
            "date": date_str,
            "stage": current.get("stage", previous.get("stage", "")),
        })

    for index, current in enumerate(current_top10, start=1):
        name = current.get("name")
        if not name or name not in prev_map:
            continue
        previous = prev_map[name]
        current_rank = _as_int(current.get("rank"), index)
        prev_rank = _as_int(previous.get("rank"), current_rank)
        rank_delta = prev_rank - current_rank

        if rank_delta >= 5:
            signal_type = "rank_up"
        elif rank_delta <= -5:
            signal_type = "rank_down"
        else:
            continue

        direction = "↑" if signal_type == "rank_up" else "↓"
        delta = abs(rank_delta)
        signals.append({
            "type": signal_type,
            "name": name,
            "industry": name,
            "detail": f"{name} #{prev_rank}→#{current_rank}（{direction}{delta}）",
            "rank": current_rank,
            "prev_rank": prev_rank,
            "score": _scan_score(current),
            "prev_score": _ledger_score(previous),
            "date": date_str,
            "stage": current.get("stage", ""),
        })

    return signals


def compute_multi_period_heat(industry_or_ledger = None, ledger_or_scans = None, **kwargs) -> dict:
    """Stub retained for score.py import compatibility."""
    return {"weekly_hot": [], "trending_up": [], "cooling_down": [], "heat": 0.0, "trend": "stable"}


def detect_exit_warnings(industry: str = None, ledger: dict = None, scan_results: list = None, **kwargs) -> list:
    """Stub retained for score.py import compatibility."""
    return []


# ── V4.2 stub: daily.py 直接导入但未实现 ──────────────────
def detect_institutional_anomalies(inst_data: dict, scan_results: list) -> list:
    """机构资金异动检测 (stub: 返回空)."""
    return []


def detect_block_trade_signals(block_data: dict, scan_results: list) -> list:
    """大宗交易信号检测 (stub: 返回空)."""
    return []


def prioritize_events(news_events: list, money_signals: list,
                      rotation_sigs: list, exit_warns: list) -> list:
    """事件优先级重排为 L0/L1/L2 (stub: 返回空)."""
    return []
