"""
event_evolution.py — 事件演化管线

收到事件触发 → 图遍历 → 结构化信号 → 叙事文本 → 盘后验证

管线三阶段：
  1. trace_transmission()  — event_monitor 检测到事件后调用
  2. signal_to_narrative() — 转为 event-feed 可渲染文本
  3. reinforce_from_verification() — 盘后验证，更新边权重

用法：
  from core.graph.transmission_graph import TransmissionGraph
  from core.graph.event_evolution import trace_transmission, signal_to_narrative

  g = TransmissionGraph.load()
  signals = trace_transmission(g, "T1_nvda_earnings")
  narrative = signal_to_narrative(g, signals)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.graph.transmission_graph import TransmissionGraph, GRAPH_PATH

_BASE = Path(__file__).resolve().parent.parent.parent
SIGNALS_PATH = _BASE / "data" / "transmission_signals.jsonl"


def trace_transmission(
    graph: TransmissionGraph,
    event_id: str,
    content: dict | None = None,
    depth: int = 2,
) -> list[dict]:
    """从事件出发，遍历传导图，生成结构化信号列表。

    Args:
        graph: 预构建的传导图谱
        event_id: 事件 ID（如 "T1_nvda_earnings"）
        content: 事件上下文（触发价格/关键词等，可选）
        depth: BFS 深度（默认 2 层）

    Returns:
        [{"event_id": ..., "signal_id": ..., "path": [...], "confidence": ...}, ...]
    """
    full_id = f"event:{event_id}"
    if full_id not in graph.nodes:
        return []

    event_node = graph.nodes[full_id]
    bfs_results = graph.bfs_from_event(full_id, depth=depth)

    signals = []
    for i, r in enumerate(bfs_results):
        terminal = graph.nodes.get(r["terminal_node"], {})
        terminal_name = terminal.get("name", r["terminal_node"])
        terminal_type = terminal.get("type", "unknown")

        # 只输出可交易的终点：stock 或 sector
        if terminal_type not in ("stock", "sector"):
            continue

        signal_id = f"{event_id}:{datetime.now().strftime('%Y%m%d')}:{terminal_name}"

        # 根据置信度分级
        conf = r["confidence"]
        if conf >= 0.8:
            level = "high"
        elif conf >= 0.6:
            level = "medium"
        elif conf >= 0.4:
            level = "low"
        else:
            level = "noise"

        signal = {
            "event_id": event_id,
            "signal_id": signal_id,
            "triggered_at": datetime.now().isoformat(),
            "event_name": event_node.get("name", event_id),
            "event_category": event_node.get("category", ""),
            "direction": r["direction"],
            "target": {
                "type": terminal_type,
                "name": terminal_name,
                "code": terminal.get("code", ""),
            },
            "transmission": {
                "path": r["path"],
                "path_names": [graph.nodes.get(n, {}).get("name", n) for n in r["path"]],
                "type": r["transmission_type"],
                "depth": len(r["path"]) - 1,
            },
            "confidence": round(conf, 3),
            "confidence_level": level,
            "is_terminal": r["is_terminal"],
        }

        # 只输出 high/medium 信号
        if level in ("high", "medium"):
            signals.append(signal)

    # 按置信度降序
    signals.sort(key=lambda s: s["confidence"], reverse=True)

    return signals


def signal_to_narrative(
    graph: TransmissionGraph,
    signals: list[dict],
) -> str:
    """将结构化信号转为 event-feed 可渲染的文本。

    格式：
      📊 直接传导:
        ★★★ 中际旭创 300308 [命中率 87%，T+1~T+3]
        ★★☆ 天孚通信 300394 [命中率 73%，T+1~T+3]
      📊 间接传导（上游）:
        ★★☆ 光纤光缆 [命中率 60%，T+3~T+10]
        ★☆☆ 电子元器件 [命中率 55%，T+3~T+7]
    """
    if not signals:
        return ""

    direct = [s for s in signals if s["transmission"]["type"] == "direct"]
    indirect = [s for s in signals if s["transmission"]["type"] == "indirect"]

    lines = []

    if direct:
        lines.append("📊 直接传导:")
        for s in direct:
            bar = _conf_icon(s["confidence"])
            t = s["target"]
            lines.append(f"  {bar} {t['name']} {t['code']} [命中率 {s['confidence']:.0%}]")

    if indirect:
        # 按路径分组
        by_upstream = {}
        by_downstream = {}
        for s in indirect:
            path_types = _path_edge_types(graph, s["transmission"]["path"])
            if "UPSTREAM" in path_types:
                by_upstream.setdefault("上游", []).append(s)
            else:
                by_downstream.setdefault("下游", []).append(s)

        if by_upstream.get("上游"):
            lines.append("\n📊 间接传导（上游）:")
            for s in by_upstream["上游"]:
                bar = _conf_icon(s["confidence"])
                t = s["target"]
                lines.append(f"  {bar} {t['name']} [命中率 {s['confidence']:.0%}]")

        if by_downstream.get("下游"):
            lines.append("\n📊 间接传导（下游）:")
            for s in by_downstream["下游"]:
                bar = _conf_icon(s["confidence"])
                t = s["target"]
                lines.append(f"  {bar} {t['name']} [命中率 {s['confidence']:.0%}]")

    return "\n".join(lines)


def reinforce_from_verification(
    graph: TransmissionGraph,
    event_id: str,
    days_elapsed: int,
    market_data: dict[str, float] | None = None,
    signal_path: Path | None = None,
):
    """盘后验证：比较预测方向 vs 实际收益，更新边权重。

    Args:
        graph: 传导图谱（会被原地修改）
        event_id: 事件 ID
        days_elapsed: 距事件触发已过天数
        market_data: {stock_code или sector_name: actual_return_pct}
        signal_path: 信号文件路径（默认从 SIGNALS_PATH 读）
    """
    sp = signal_path or SIGNALS_PATH
    if not sp.exists():
        return

    # 读取该事件的最近信号
    signals = []
    with open(sp, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            s = json.loads(line)
            if s.get("event_id") == event_id:
                signals.append(s)

    if not signals or not market_data:
        return

    for sig in signals:
        target_name = sig["target"]["name"]
        target_code = sig["target"]["code"]
        predicted_dir = sig["direction"]

        # 查找实际收益
        actual_return = market_data.get(target_code, market_data.get(target_name))
        if actual_return is None:
            continue

        # 判断方向一致性
        predicted_right = (predicted_dir == "long" and actual_return > 0) or \
                          (predicted_dir == "short" and actual_return < 0)

        # 更新传导路径上所有边
        path = sig["transmission"]["path"]
        for i in range(len(path) - 1):
            from_id, to_id = path[i], path[i + 1]
            if predicted_right:
                graph.reinforce_edge(from_id, to_id, delta=0.05)
            else:
                graph.weaken_edge(from_id, to_id, delta=0.10)

    graph.save()


def write_signals(signals: list[dict], path: Path | None = None):
    """追加信号到 transmission_signals.jsonl。"""
    p = path or SIGNALS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        for s in signals:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")


# ── helpers ────────────────────────────────────────────────────────────────────

def _conf_icon(conf: float) -> str:
    if conf >= 0.8:
        return "★★★"
    elif conf >= 0.6:
        return "★★☆"
    elif conf >= 0.4:
        return "★☆☆"
    return " ✗ "


def _path_edge_types(graph: TransmissionGraph, path: list[str]) -> set[str]:
    """提取路径中所有边的类型。"""
    types = set()
    for i in range(len(path) - 1):
        from_id, to_id = path[i], path[i + 1]
        for idx in graph.adj_out.get(from_id, []):
            e = graph.edges[idx]
            if e["to"] == to_id:
                types.add(e["type"])
                break
    return types
