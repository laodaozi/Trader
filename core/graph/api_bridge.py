"""
api_bridge.py — Node.js ↔ Python 桥接：加载传导图谱，trace 事件，输出 JSON
被 mobile.js 通过 execSync 调用。

用法：
  python3 core/graph/api_bridge.py T1_nvda_earnings
  python3 core/graph/api_bridge.py --list        # 列出所有可用事件
  python3 core/graph/api_bridge.py                # 默认 trace 第一个 Tier-1 事件
"""


import json
from typing import List

import sys
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_BASE))

from core.graph.transmission_graph import TransmissionGraph
from core.graph.event_evolution import trace_transmission, signal_to_narrative


def _load_event_list(graph: TransmissionGraph) -> List[dict]:
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


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""

    graph = TransmissionGraph.load()

    if cmd == "--list":
        events = _load_event_list(graph)
        print(json.dumps({"events": events}, ensure_ascii=False))
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
