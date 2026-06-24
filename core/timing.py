"""
modules/timing.py — Layer 1: 市场温度计 + 8阶段判断 + 仓位建议

数据来源：Finstep MCP
  - get_market_snapshot：大盘涨跌家数、涨停数
  - get_kline：上证指数日K（判断指数方向）
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from core.trader_mcp import mcp_call
from core.haoyun import adjust_position

# ── 持久化历史 ──────────────────────────────────────────
TIMING_HISTORY_FILE = Path(__file__).parent.parent / "data" / "timing_history.json"


def _load_timing_history() -> list[dict]:
    if TIMING_HISTORY_FILE.exists():
        with open(TIMING_HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f).get("history", [])
    return []


def _save_timing_history(result: dict):
    history = _load_timing_history()
    entry = {
        "date":            result["date"],
        "phase":           result["phase"],
        "temperature":     result["temperature"],
        "index_direction": result["index_direction"],
    }
    # Upsert: 同日期覆盖
    existing = {h["date"]: i for i, h in enumerate(history)}
    if result["date"] in existing:
        history[existing[result["date"]]] = entry
    else:
        history.append(entry)
    history.sort(key=lambda x: x["date"])
    TIMING_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TIMING_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump({"history": history[-90:]}, f, ensure_ascii=False, indent=2)


def _load_prev_phase(date: str) -> str | None:
    """读取 date 之前最近一个交易日的阶段（用于 apply_buffer）。"""
    history = _load_timing_history()
    target = datetime.strptime(date, "%Y-%m-%d")
    prev = None
    for h in history:
        h_date = datetime.strptime(h["date"], "%Y-%m-%d")
        if h_date < target:
            prev = h
        else:
            break
    return prev["phase"] if prev else None


# ── 8阶段映射 ────────────────────────────────────────────

_PHASE_MAP = [
    # (temp_min, temp_max, index_up, phase, position_range, description)
    (80, 100, True,  "即将见顶", (0.6, 0.8), "市场处于即将见顶阶段，适宜逢高减仓"),
    (80, 100, False, "警惕下杀", (0.4, 0.6), "市场疯狂结束警惕下杀，适宜分批减仓"),
    (50, 80,  True,  "冲刺",    (0.5, 0.8), "市场处于冲刺阶段，适宜加仓买入"),
    (50, 80,  False, "回调",    (0.3, 0.5), "市场处于回调阶段，适宜分批减仓"),
    (20, 50,  True,  "反弹",    (0.3, 0.5), "市场处于反弹阶段，适宜分批介入"),
    (20, 50,  False, "下跌",    (0.1, 0.3), "市场处于下跌阶段，适宜观望"),
    (0,  20,  True,  "试盘",    (0.2, 0.3), "市场处于试盘阶段，适宜快进快出"),
    (0,  20,  False, "探底",    (0.0, 0.1), "市场处于探底阶段，适宜清仓观望"),
]


def _resolve_phase(temperature: float, index_up: bool) -> dict:
    for t_min, t_max, up, phase, pos_range, desc in _PHASE_MAP:
        if t_min <= temperature <= t_max and up == index_up:
            mid = (pos_range[0] + pos_range[1]) / 2
            return {
                "phase": phase,
                "position_min": pos_range[0],
                "position_max": pos_range[1],
                "recommended_position": round(mid, 2),
                "description": desc,
                "allow_trade": temperature >= 20 or index_up,
            }
    # fallback
    return {
        "phase": "未知",
        "position_min": 0.0,
        "position_max": 0.3,
        "recommended_position": 0.1,
        "description": "无法判断市场阶段",
        "allow_trade": False,
    }


# ── 缓冲词：避免阶段跳变 ──────────────────────────────

# 上升阶段首次下降 → 震荡休整；下降阶段首次上升 → 反抽
_BUFFER_PHASES = {
    ("冲刺",  False): ("震荡休整", (0.4, 0.6), "市场处于震荡休整阶段，适宜高抛低吸"),
    ("即将见顶", False): ("震荡休整", (0.4, 0.6), "市场处于震荡休整阶段，适宜高抛低吸"),
    ("下跌",  True):  ("反抽",    (0.3, 0.5), "市场处于反抽阶段，适宜高抛低吸"),
    ("探底",  True):  ("反抽",    (0.3, 0.5), "市场处于反抽阶段，适宜高抛低吸"),
}


def apply_buffer(prev_phase: str | None, current: dict, index_up: bool) -> dict:
    """若当日方向与昨日阶段相反（首次转向），应用缓冲阶段。"""
    if prev_phase is None:
        return current
    key = (prev_phase, index_up)
    if key in _BUFFER_PHASES:
        phase, pos_range, desc = _BUFFER_PHASES[key]
        mid = (pos_range[0] + pos_range[1]) / 2
        return {
            "phase": phase,
            "position_min": pos_range[0],
            "position_max": pos_range[1],
            "recommended_position": round(mid, 2),
            "description": desc,
            "allow_trade": True,
        }
    return current


# ── 核心计算 ──────────────────────────────────────────

def compute_market_temperature(snapshot: dict) -> float:
    """
    市场温度 0-100
    广度（涨家/总家）× 60 + 涨停强度（min(涨停/100, 1)）× 40
    """
    ud = snapshot.get("up_down_num", snapshot)
    rising   = ud.get("total_up_num") or ud.get("rise_count") or ud.get("rising_count") or 0
    falling  = ud.get("total_down_num") or ud.get("fall_count") or ud.get("falling_count") or 0
    limit_up = ud.get("limit_up_num") or ud.get("limit_rise_count") or ud.get("limit_up_count") or 0
    limit_down = ud.get("limit_down_num") or ud.get("limit_fall_count") or ud.get("limit_down_count") or 0

    total = rising + falling
    breadth = rising / max(total, 1)
    limit_intensity = min(limit_up / 100, 1.0)
    temperature = breadth * 60 + limit_intensity * 40

    # 跌停因子：跌停>50只视为恐慌，温度-15（不低于0）
    if limit_down > 50:
        temperature = max(temperature - 15, 0)

    return round(temperature, 1)


def _get_index_direction(date: str) -> bool:
    """获取上证指数方向：比较近3日收盘价 MA3 与前3日 MA3，返回 True=上涨。"""
    end = date
    start = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=10)).strftime("%Y-%m-%d")
    try:
        data = mcp_call("market_quote", "get_kline", {
            "keyword": "上证指数",
            "start_date": start,
            "end_date": end,
            "kline_type": 1,
            "reinstatement_type": 1,
        })
        bars = data if isinstance(data, list) else data.get("list", [])
        if len(bars) >= 6:
            closes = [float(b.get("close_price") or b.get("close") or 0) for b in bars]
            ma3_current = sum(closes[-3:]) / 3
            ma3_prev    = sum(closes[-6:-3]) / 3
            return ma3_current >= ma3_prev
        if bars:
            bar = bars[-1]
            return bar.get("price_change_rate", 0) >= 0
    except Exception as e:
        print(f"  ⚠ 指数方向获取失败: {e}")
    return True  # fallback: 默认上涨


def get_timing(date: str, prev_phase: str | None = None, verbose: bool = True) -> dict:
    """
    主接口：获取当日择时结果。
    
    若 prev_phase 未显式传入，自动从 data/timing_history.json 读取前日阶段，
    用于 apply_buffer 阻止阶段跳变。结果自动持久化到同一文件。

    返回:
    {
      "date": "2026-05-17",
      "temperature": 62.5,
      "phase": "冲刺",
      "index_direction": "上涨",
      "allow_trade": True,
      "recommended_position": 0.65,
      "position_min": 0.5,
      "position_max": 0.8,
      "message": "市场处于冲刺阶段，适宜加仓买入",
      "raw": { ... }
    }
    """
    if verbose:
        print(f"[timing] 获取市场温度 {date}...")

    # 1. 大盘快照
    try:
        snapshot = mcp_call("market_quote", "get_market_snapshot", {})
    except Exception as e:
        raise RuntimeError(f"大盘快照获取失败: {e}") from e

    # 2. 计算温度
    temperature = compute_market_temperature(snapshot)

    # 3. 指数方向
    index_up = _get_index_direction(date)
    index_dir = "上涨" if index_up else "下跌"

    # 4. 阶段判断
    phase_info = _resolve_phase(temperature, index_up)

    # 5. 缓冲处理（自动读取前日阶段，避免阶段跳变）
    if prev_phase is None:
        prev_phase = _load_prev_phase(date)
    phase_info = apply_buffer(prev_phase, phase_info, index_up)

    result = {
        "date": date,
        "temperature": temperature,
        "phase": phase_info["phase"],
        "index_direction": index_dir,
        "allow_trade": phase_info["allow_trade"],
        "recommended_position": phase_info["recommended_position"],
        "position_min": phase_info["position_min"],
        "position_max": phase_info["position_max"],
        "message": phase_info["description"],
        "raw": {
            "snapshot": snapshot,
        },
    }

    # ── 好运哥仓位纪律叠加 ──
    haoyun_pos, haoyun_flags = adjust_position(
        date, result["recommended_position"], verbose=verbose,
    )
    result["haoyun_position"] = haoyun_pos
    result["haoyun_flags"] = haoyun_flags

    # 持久化到历史（供次日 apply_buffer 读取）
    try:
        _save_timing_history(result)
    except Exception as e:
        if verbose:
            print(f"  ⚠ 保存择时历史失败: {e}")

    if verbose:
        buf_note = f" | 前日阶段: {prev_phase}" if prev_phase else " | 无前日记录(跳过缓冲)"
        print(f"  温度: {temperature}°C | 阶段: {phase_info['phase']}{buf_note} | "
              f"指数: {index_dir} | 建议仓位: {int(phase_info['recommended_position']*100)}%")

    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    args = parser.parse_args()
    result = get_timing(args.date)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
