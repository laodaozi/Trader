"""
api_bridge.py — Node.js ↔ Python 桥接：加载传导图谱，trace 事件，输出 JSON
被 mobile.js 通过 execSync 调用。

用法：
  python3 core/graph/api_bridge.py T1_nvda_earnings
  python3 core/graph/api_bridge.py --list        # 列出所有可用事件
  python3 core/graph/api_bridge.py --top-paths 3 # 跨所有事件取 top-N 最强传导路径
  python3 core/graph/api_bridge.py                # 默认 trace 第一个 Tier-1 事件
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_BASE))

from core.graph.transmission_graph import TransmissionGraph
from core.graph.event_evolution import trace_transmission, signal_to_narrative


def _load_event_list(graph: TransmissionGraph) -> list[dict]:
    """从图谱节点中提取事件列表。"""
    events = []
    for node_id, node in graph.nodes.items():
        if node["type"] == "event":
            events.append({
                "id": node_id.replace("event:", ""),
                "name": node["name"],
                "tier": node.get("tier", 2),
                "category": node.get("category", ""),
                "direction": node.get("direction", "long"),
            })
    return sorted(events, key=lambda e: (e["tier"], e["id"]))


def _top_transmission_paths(graph: TransmissionGraph, n: int = 3) -> dict:
    """跨所有事件运行 BFS，返回 top-N 最强传导路径摘要。

    评分：confidence × (1/depth)，优先高置信度 + 短路径。
    只取 medium+ 信号（confidence >= 0.4），去重（同目标 stock 只保留最强一条）。
    """
    events = _load_event_list(graph)
    all_signals = []

    for ev in events:
        event_id = ev["id"]
        signals = trace_transmission(graph, event_id, depth=2)
        for s in signals:
            if s["confidence"] < 0.4:  # 只出 medium+
                continue
            all_signals.append({
                "event_id": event_id,
                "event_name": s.get("event_name", ev["name"]),
                "event_tier": ev["tier"],
                "direction": s["direction"],
                "target_name": s["target"]["name"],
                "target_code": s["target"].get("code", ""),
                "target_type": s["target"]["type"],
                "path": s["transmission"]["path"],
                "path_names": s["transmission"]["path_names"],
                "depth": s["transmission"]["depth"],
                "confidence": s["confidence"],
                "confidence_level": s["confidence_level"],
                # 加权评分：confidence / depth，depth=1（直连）不被稀释
                "score": round(s["confidence"] / max(s["transmission"]["depth"], 1), 4),
            })

    # 去重：同一 stock 只保留 score 最高的路径
    seen_stocks = {}
    for sig in all_signals:
        code = sig["target_code"]
        if code not in seen_stocks or sig["score"] > seen_stocks[code]["score"]:
            seen_stocks[code] = sig

    # 取 top N
    top = sorted(seen_stocks.values(), key=lambda x: (-x["score"], x["depth"]))[:n]

    return {
        "top_paths": top,
        "total_paths": len(seen_stocks),
        "total_events_scanned": len(events),
    }


def _build_live_graph() -> TransmissionGraph:
    """优先从今日 transmission_signals.jsonl 动态构建图谱。
    
    策略：
    1. 尝试 build_from_signals()（读取真实日频信号）
    2. 若信号文件不存在/过期 → 自动回退 build_from_library()
    3. 不读静态 transmission_graph.json（可能过期）
    """
    return TransmissionGraph.build_from_signals()


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""

    graph = _build_live_graph()

    if cmd == "--list":
        events = _load_event_list(graph)
        print(json.dumps({"events": events}, ensure_ascii=False))
        return

    # ── V8.3: --top-paths N 模式 — 跨所有事件取最强传导路径 ──
    if cmd == "--top-paths":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
        print(json.dumps(_top_transmission_paths(graph, n), ensure_ascii=False))
        return

    # 选择事件：指定 ID > 默认第一个 Tier-1
    event_id = cmd if cmd else _load_event_list(graph)[0]["id"]

    # 检查事件是否存在
    full_id = f"event:{event_id}"
    if full_id not in graph.nodes:
        print(json.dumps({"error": f"事件 {event_id} 不存在"}, ensure_ascii=False))
        return

    event_node = graph.nodes[full_id]

    # Trace 传导信号
    signals = trace_transmission(graph, event_id, depth=2)

    # 转叙事文本
    narrative = signal_to_narrative(graph, signals)

    # 组装输出
    output = {
        "event": {
            "id": event_id,
            "name": event_node["name"],
            "tier": event_node.get("tier", 1),
            "category": event_node.get("category", ""),
            "direction": event_node.get("direction", "long"),
        },
        "graph_stats": graph.summary(),
        "narrative": narrative,
        "signals": sorted(
            [s for s in signals if s["confidence"] >= 0.4],  # 只出 MEDIUM+
            key=lambda s: -s["confidence"],
        ),
        "signal_counts": {
            "high": sum(1 for s in signals if s["confidence"] >= 0.8),
            "medium": sum(1 for s in signals if 0.4 <= s["confidence"] < 0.8),
            "low": sum(1 for s in signals if s["confidence"] < 0.4),
        },
        "available_events": _load_event_list(graph),
    }

    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
