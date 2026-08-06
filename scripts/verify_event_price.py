"""
verify_event_price.py — 事件驱动信号价格验证

验证4个近期真实案例（2026-07）在信源触发后的实际价格反应
信源日期 → T+1/T+5/T+10/T+20 收益率

用法：
    python scripts/verify_event_price.py
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BASE))

from core.stock_analysis import _get_ohlc_from_tencent_direct

# ── 4个近期案例定义 ────────────────────────────────────────────────────────────
CASES = [
    {
        "name": "T1 高端PCB供需缺口",
        "signal_date": "2026-07-22",
        "stocks": [
            ("沪电股份", "002463"),
            ("深南电路", "002916"),
            ("生益科技", "600183"),
        ],
        "event": "高端PCB价格涨超300%，订单锁定至2027",
        "graph_event_id": None,  # 无匹配 graph 事件
    },
    {
        "name": "T2 源杰科技业绩预增13倍",
        "signal_date": "2026-07-21",
        "stocks": [
            ("源杰科技", "688498"),
            ("仕佳光子", "688313"),
            ("光迅科技", "002281"),
        ],
        "event": "净利预增1197-1305%，光芯片龙头",
        "graph_event_id": None,  # 无匹配 graph 事件
    },
    {
        "name": "T3 可再生能源十五五规划",
        "signal_date": "2026-07-23",
        "stocks": [
            ("宁德时代", "300750"),
            ("阳光电源", "300274"),
            ("金风科技", "002202"),
            ("隆基绿能", "601012"),
        ],
        "event": "发改委+能源局印发十五五规划，总投资超5万亿",
        "graph_event_id": "T3_policy_catalyst",  # → 图谱 T3 policy 触发节点
    },
    {
        "name": "T4 五洲医疗重组复牌",
        "signal_date": "2026-07-22",
        "stocks": [
            ("五洲医疗", "430418"),
            ("旋智科技", "688520"),
        ],
        "event": "跨界电机控制芯片，复牌一字涨停",
        "graph_event_id": None,  # 无匹配 graph 事件
    },
]

WINDOWS = [1, 3, 5, 10, 20]   # T+N 交易日


def get_price_on_or_after(ohlc: list[dict], date_str: str) -> tuple[float, str] | None:
    """返回 date_str 当日或之后第一个交易日的收盘价 + 实际日期"""
    for row in ohlc:
        if row["date"] >= date_str:
            return row["close"], row["date"]
    return None


def get_price_at_offset(ohlc: list[dict], base_date: str, offset: int) -> tuple[float, str] | None:
    """返回 base_date 之后第 offset 个交易日的收盘价"""
    idx = None
    for i, row in enumerate(ohlc):
        if row["date"] >= base_date:
            idx = i
            break
    if idx is None:
        return None
    target_idx = idx + offset
    if target_idx >= len(ohlc):
        return None
    row = ohlc[target_idx]
    return row["close"], row["date"]


def verify_case(case: dict) -> dict:
    signal_date = case["signal_date"]
    start_dt = datetime.strptime(signal_date, "%Y-%m-%d") - timedelta(days=5)
    end_dt = datetime.strptime(signal_date, "%Y-%m-%d") + timedelta(days=40)

    results = []
    for name, code in case["stocks"]:
        time.sleep(0.3)  # 避免频控
        ohlc = _get_ohlc_from_tencent_direct(code, start_dt, end_dt)
        if not ohlc:
            results.append({"stock": name, "code": code, "error": "无数据"})
            continue

        base = get_price_on_or_after(ohlc, signal_date)
        if not base:
            results.append({"stock": name, "code": code, "error": "信源日无交易数据"})
            continue

        base_price, base_actual_date = base
        row = {"stock": name, "code": code, "base_date": base_actual_date, "base_price": base_price}

        for w in WINDOWS:
            pt = get_price_at_offset(ohlc, base_actual_date, w)
            if pt:
                price, date = pt
                pct = (price - base_price) / base_price * 100
                row[f"T+{w}"] = f"{pct:+.1f}% ({date})"
            else:
                row[f"T+{w}"] = "N/A"

        results.append(row)

    return {"case": case["name"], "event": case["event"], "signal_date": signal_date, "stocks": results}


def print_report(all_results: list[dict]):
    print("\n" + "═" * 70)
    print("CycleRadar 事件驱动信号 — 价格验证报告")
    print(f"验证日期: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("═" * 70)

    for r in all_results:
        print(f"\n{'─' * 70}")
        print(f"📌 {r['case']}")
        print(f"   信源日期: {r['signal_date']}  |  {r['event']}")
        print(f"{'─' * 70}")
        header = f"{'标的':<10} {'代码':<8} {'基准价':>8} " + " ".join(f"{'T+'+str(w):>14}" for w in WINDOWS)
        print(header)
        print("-" * len(header))
        for s in r["stocks"]:
            if "error" in s:
                print(f"{s['stock']:<10} {s['code']:<8}  ⚠ {s['error']}")
                continue
            row_str = f"{s['stock']:<10} {s['code']:<8} {s['base_price']:>8.2f}"
            for w in WINDOWS:
                val = s.get(f"T+{w}", "N/A")
                row_str += f" {val:>14}"
            print(row_str)

    print("\n" + "═" * 70)
    print("说明：T+N = 信源触发后第N个交易日收益率（相对信源当日收盘价）")
    print("波段目标：2-4周（T+10~T+20）持续正收益 = 信号有效")
    print("═" * 70 + "\n")


def run_reinforce(all_results: list[dict], verify_window: int = 5):
    """价格验证完成后，调用 reinforce_from_verification 更新传导图谱边权重。

    Args:
        all_results: verify_case 返回的验证结果列表
        verify_window: 使用 T+N 窗口做验证（默认 T+5）
    """
    from core.graph.transmission_graph import TransmissionGraph
    from core.graph.event_evolution import reinforce_from_verification

    # 构建 market_data: {code: return_pct}
    market_data = {}
    for r in all_results:
        for s in r.get("stocks", []):
            key = f"T+{verify_window}"
            val = s.get(key, "N/A")
            if val == "N/A":
                continue
            try:
                pct = float(val.split("%")[0])
                market_data[s["code"]] = pct
            except (ValueError, IndexError):
                continue

    graph = TransmissionGraph.load()
    for r in all_results:
        event_id = r.get("graph_event_id")
        if not event_id:
            continue

        signal_date = datetime.strptime(r["signal_date"], "%Y-%m-%d")
        days_elapsed = (datetime.now() - signal_date).days

        print(f"\n[强化] {r['case']} → graph:{event_id} | 距今 {days_elapsed} 天 | 验证窗口 T+{verify_window}")
        try:
            reinforce_from_verification(graph, event_id, days_elapsed, market_data)
            print(f"  ✅ 边权重已更新 ({len(market_data)} 只标的验证数据)")
        except Exception as e:
            print(f"  ⚠ 强化失败: {e}")


if __name__ == "__main__":
    all_results = []
    for case in CASES:
        print(f"[验证] {case['name']} ...")
        result = verify_case(case)
        all_results.append(result)

    print_report(all_results)

    # V8.3: 验证完成后自动强化图谱
    print("\n── 传导图谱强化（reinforce_from_verification）──")
    run_reinforce(all_results)
