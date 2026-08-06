"""
transmission_graph.py — 金融因果传导图谱

轻量级有向图（adjacency list），不需要 networkx。
Node types: event, sector, stock, factor
Edge types: AFFECTS, CONTAINS, UPSTREAM, DOWNSTREAM, CORRELATES

三级管线：
  1. build_from_library() — 从 event_library.json 构建初始图
  2. bfs_from_event()    — 事件触发时 BFS 遍历传导链
  3. reinforce_edge()    — 盘后收益率验证 → 边权重自进化

用法：
  python -m core.graph.transmission_graph --build     # 构建图
  python -m core.graph.transmission_graph --evolve T1  # 测试演化
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Optional

_BASE = Path(__file__).resolve().parent.parent.parent
LIBRARY_PATH = _BASE / "data" / "event_library.json"
GRAPH_PATH = _BASE / "data" / "transmission_graph.json"

# ── 置信度映射 ─────────────────────────────────────────────────────────────────
CONFIDENCE_MAP = {
    "very_high": 1.0,
    "high": 0.85,
    "medium_high": 0.70,
    "medium": 0.55,
    "low": 0.35,
}

# ── 申万行业上下游链（已验证的核心传导关系）──────────────────────────────────
# 格式：sector → {upstream: [...], downstream: [...]}
SECTOR_CHAINS: dict[str, dict[str, list[str]]] = {
    "半导体": {
        "upstream": ["半导体设备", "半导体材料"],
        "downstream": ["消费电子", "通信设备"],
    },
    "通信设备": {
        "upstream": ["电子元器件", "光纤光缆"],
        "downstream": ["电信运营"],
    },
    "光学光电子": {
        "upstream": ["电子元器件"],
        "downstream": ["通信设备", "消费电子"],
    },
    "电池": {
        "upstream": ["能源金属", "电力设备"],
        "downstream": ["乘用车"],
    },
    "能源金属": {
        "upstream": [],
        "downstream": ["电池"],
    },
    "煤炭": {
        "upstream": [],
        "downstream": ["电力", "化工"],
    },
    "石油石化": {
        "upstream": [],
        "downstream": ["化工", "化纤"],
    },
    "工业金属": {
        "upstream": [],
        "downstream": ["建筑装饰", "机械设备", "电力设备"],
    },
    "养殖业": {
        "upstream": ["饲料"],
        "downstream": ["食品饮料"],
    },
    "饲料": {
        "upstream": [],
        "downstream": ["养殖业"],
    },
    "乘用车": {
        "upstream": ["汽车零部件"],
        "downstream": ["汽车销售"],
    },
    "汽车零部件": {
        "upstream": ["钢铁"],
        "downstream": ["乘用车"],
    },
    "磷化工": {
        "upstream": [],
        "downstream": ["化肥"],
    },
    "消费电子": {
        "upstream": ["半导体", "光学光电子"],
        "downstream": [],
    },
    "电力设备": {
        "upstream": ["工业金属"],
        "downstream": ["电池", "电力"],
    },
    "建筑装饰": {
        "upstream": ["工业金属"],
        "downstream": ["房地产"],
    },
    "房地产": {
        "upstream": ["建筑装饰"],
        "downstream": ["银行"],
    },
    "银行": {
        "upstream": [],
        "downstream": [],
    },
    "机械设备": {
        "upstream": ["工业金属"],
        "downstream": [],
    },
}

# Sector names that may appear in event_library but lack explicit chain data
# → treated as terminal nodes (no further BFS expansion)
_TERMINAL_SECTORS = {"航空装备", "氟化工", "工业硅", "黄磷", "电信运营", "化纤",
                     "化肥", "食品饮料", "汽车销售", "钢铁", "半导体设备", "半导体材料",
                     "光纤光缆", "电子元器件", "电力", "化工"}


class TransmissionGraph:
    """轻量有向图：因果传导链的存储与查询。"""

    def __init__(self):
        self.nodes: dict[str, dict] = {}         # {node_id: {type, name, ...}}
        self.edges: list[dict] = []               # [{from, to, type, weight, ...}]
        self.adj_out: dict[str, list[int]] = {}   # {from_node: [edge_index]}

    # ── 构建 ───────────────────────────────────────────────────────────────────

    def add_node(self, node_id: str, node_type: str, **attrs):
        """添加节点（幂等：已存在则合并 attrs）。"""
        existing = self.nodes.get(node_id)
        if existing:
            existing.update(attrs)
        else:
            self.nodes[node_id] = {"type": node_type, **attrs}
        return self

    def add_edge(self, from_id: str, to_id: str, edge_type: str,
                 weight: float = 1.0, lag_hours: int = 24,
                 hit_count: int = 0, miss_count: int = 0):
        """添加有向边（幂等：同 from/to/type 不重复添加）。"""
        # 幂等检查
        for idx in self.adj_out.get(from_id, []):
            e = self.edges[idx]
            if e["to"] == to_id and e["type"] == edge_type:
                return self

        # 确保两端节点存在
        if from_id not in self.nodes:
            self.add_node(from_id, "unknown")
        if to_id not in self.nodes:
            self.add_node(to_id, "unknown")

        idx = len(self.edges)
        self.edges.append({
            "from": from_id, "to": to_id, "type": edge_type,
            "weight": weight, "lag_hours": lag_hours,
            "hit_count": hit_count, "miss_count": miss_count,
        })
        self.adj_out.setdefault(from_id, []).append(idx)
        return self

    def get_hit_rate(self, from_id: str, to_id: str) -> float:
        """查询某条边的历史命中率。无历史 → 返回边权重作为先验。"""
        for idx in self.adj_out.get(from_id, []):
            e = self.edges[idx]
            if e["to"] == to_id:
                total = e["hit_count"] + e["miss_count"]
                return e["hit_count"] / total if total > 0 else e["weight"]
        return 0.0

    # ── 查询 ───────────────────────────────────────────────────────────────────

    def bfs_from_event(self, event_id: str, depth: int = 2) -> list[dict]:
        """从事件节点出发，BFS depth 层，返回所有受影响路径。

        返回格式：
          [{path: [node_id, ...], transmission_type: "direct"|"indirect",
            confidence: float, direction: "long"|"short"}]
        """
        if event_id not in self.nodes:
            return []

        results: list[dict] = []
        visited: set[str] = {event_id}
        # queue: (current_node, path_so_far, depth, transmission_type)
        queue: deque[tuple[str, list[str], int, str]] = deque()
        queue.append((event_id, [event_id], 0, "root"))

        while queue:
            current, path, d, ttype = queue.popleft()
            if d >= depth:
                continue

            for edge_idx in self.adj_out.get(current, []):
                edge = self.edges[edge_idx]
                neighbor = edge["to"]
                if neighbor in visited:
                    continue
                visited.add(neighbor)

                new_path = path + [neighbor]
                new_type = "direct" if edge["type"] in ("AFFECTS", "CONTAINS") else "indirect"
                # 如果 neighbor 是 stock 节点，或 depth 用尽 → 终点
                neighbor_type = self.nodes.get(neighbor, {}).get("type", "")
                is_terminal = (neighbor_type == "stock" or d + 1 >= depth)

                # 计算路径置信度
                path_confidence = self._path_confidence(new_path)

                results.append({
                    "path": new_path,
                    "terminal_node": neighbor,
                    "terminal_type": neighbor_type,
                    "transmission_type": new_type,
                    "confidence": round(path_confidence, 3),
                    "direction": self.nodes.get(event_id, {}).get("direction", "long"),
                    "is_terminal": is_terminal,
                })

                # stock 是终点，不继续展开
                if neighbor_type != "stock":
                    queue.append((neighbor, new_path, d + 1, new_type))

        return results

    def _path_confidence(self, path: list[str]) -> float:
        """路径置信度 = 各边权重连乘（含 hit_rate 微调）。"""
        conf = 1.0
        for i in range(len(path) - 1):
            from_id, to_id = path[i], path[i + 1]
            hr = self.get_hit_rate(from_id, to_id)
            conf *= hr
        return conf

    # ── 进化 ───────────────────────────────────────────────────────────────────

    # ── 边类型权重上限（渐进式，防止过拟合）────────────────────────────────
    _CEILINGS: dict[str, float] = {
        "AFFECTS":    2.0,   # event → sector
        "CONTAINS":   0.6,   # sector → stock
        "UPSTREAM":   1.5,   # sector → upstream sector
        "DOWNSTREAM": 1.5,   # sector → downstream sector
    }

    def reinforce_edge(self, from_id: str, to_id: str, delta: float = 0.05):
        """预测方向与市场一致 → 加强传导边权重（type-aware ceiling）。"""
        for idx in self.adj_out.get(from_id, []):
            e = self.edges[idx]
            if e["to"] == to_id:
                e["hit_count"] = e.get("hit_count", 0) + 1
                ceiling = self._CEILINGS.get(e.get("type", ""), 2.0)
                e["weight"] = min(ceiling, e["weight"] + delta)
                return

    def weaken_edge(self, from_id: str, to_id: str, delta: float = 0.10):
        """预测方向与市场不一致 → 削弱传导边权重。"""
        for idx in self.adj_out.get(from_id, []):
            e = self.edges[idx]
            if e["to"] == to_id:
                e["miss_count"] = e.get("miss_count", 0) + 1
                e["weight"] = max(0.1, e["weight"] - delta)
                return

    def _clamp_to_ceilings(self):
        """将所有边裁剪到类型特定的上限内（幂等操作）。"""
        for e in self.edges:
            ceiling = self._CEILINGS.get(e.get("type", ""), 2.0)
            if e["weight"] > ceiling:
                e["weight"] = ceiling

    def decay_all_edges(self, factor: float = 0.995):
        """每日衰减所有边权重（仅在 hit/miss > 0 时生效，避免冷启动边被压到 0）。
        
        衰减公式：weight = weight * factor
        
        适用场景：cron 每日 reinforce 后调用，防止权重因重复命中而无限膨胀。
        """
        decayed = 0
        for e in self.edges:
            if e.get("hit_count", 0) > 0 or e.get("miss_count", 0) > 0:
                old_w = e["weight"]
                e["weight"] = round(old_w * factor, 4)
                decayed += 1
        # Enforce type-specific ceilings (also fixes pre-existing over-ceiling weights)
        self._clamp_to_ceilings()
        return decayed

    # ── 序列化 ─────────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "_meta": {"version": "1.0", "node_count": len(self.nodes), "edge_count": len(self.edges)},
        }

    def save(self, path: Path | None = None):
        p = path or GRAPH_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: Path | None = None) -> "TransmissionGraph":
        p = path or GRAPH_PATH
        g = cls()
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        g.nodes = data.get("nodes", {})
        g.edges = data.get("edges", [])
        # 重建 adjacency index
        for idx, edge in enumerate(g.edges):
            g.adj_out.setdefault(edge["from"], []).append(idx)
        return g

    # ── 工厂方法 ───────────────────────────────────────────────────────────────

    @classmethod
    def build_from_library(cls, library_path: Path | None = None) -> "TransmissionGraph":
        """从 event_library.json 构建传导图谱。

        三步：
          1. 为每个事件创建 event 节点 + AFFECTS edges → beneficiary sectors
          2. 为每个 sector 创建 stock 节点 + CONTAINS edges
          3. 添加 sector 间 UPSTREAM/DOWNSTREAM edges（行业链）
        """
        g = cls()
        lib_path = library_path or LIBRARY_PATH

        with open(lib_path, "r", encoding="utf-8") as f:
            lib = json.load(f)

        events = lib.get("events", [])

        for evt in events:
            eid = evt["id"]
            ename = evt.get("name", eid)
            confidence = CONFIDENCE_MAP.get(evt.get("confidence", "medium"), 0.55)
            direction = evt.get("direction", "long")

            # 1. 事件节点
            g.add_node(f"event:{eid}", "event", name=ename,
                       tier=evt.get("tier", 2), category=evt.get("category", ""),
                       direction=direction)

            beneficiary = evt.get("beneficiary", {})

            # 处理 sectors / sectors_map 两种格式
            sector_list: list[str] = []
            sectors_map = beneficiary.get("sectors_map", {})
            if sectors_map:
                for policy_key, sectors in sectors_map.items():
                    if isinstance(sectors, list):
                        sector_list.extend(sectors)
            else:
                sector_list = beneficiary.get("sectors", [])

            # 去重
            sector_list = list(dict.fromkeys(sector_list))

            # 2. 事件 → 板块（AFFECTS edges）
            window = evt.get("window", {})
            total_days = window.get("total_days", 5)
            best_entry = window.get("best_entry", "")

            for sec in sector_list:
                sec_id = f"sector:{sec}"
                g.add_node(sec_id, "sector", name=sec)
                g.add_edge(f"event:{eid}", sec_id, "AFFECTS",
                           weight=confidence, lag_hours=0)

            # 3. 板块 → 个股（CONTAINS edges）
            key_stocks = beneficiary.get("key_stocks", [])
            stock_weight = 1.0 / max(len(key_stocks), 1)

            for ks in key_stocks:
                # 格式："中际旭创300308" → 名称 + 代码
                stock_name = ks
                stock_code = ""
                # 简单解析：末尾 6 位数字是代码
                import re
                m = re.search(r"(\d{6})$", ks)
                if m:
                    stock_code = m.group(1)
                    stock_name = ks[:m.start()]
                stock_id = f"stock:{stock_code}" if stock_code else f"stock:{stock_name}"

                g.add_node(stock_id, "stock", name=stock_name, code=stock_code)
                # 个股属于哪个板块？取第一个匹配的板块
                for sec in sector_list[:1]:
                    g.add_edge(f"sector:{sec}", stock_id, "CONTAINS",
                               weight=stock_weight)

            # 4. 板块间上下游（UPSTREAM / DOWNSTREAM edges）
            for sec in sector_list:
                chains = SECTOR_CHAINS.get(sec, {})
                upstream = chains.get("upstream", [])
                downstream = chains.get("downstream", [])

                for up in upstream:
                    up_id = f"sector:{up}"
                    g.add_node(up_id, "sector", name=up)
                    g.add_edge(f"sector:{sec}", up_id, "UPSTREAM",
                               weight=0.6, lag_hours=72)  # 上游传导有延迟
                for down in downstream:
                    down_id = f"sector:{down}"
                    g.add_node(down_id, "sector", name=down)
                    g.add_edge(f"sector:{sec}", down_id, "DOWNSTREAM",
                               weight=0.7, lag_hours=48)  # 下游传导略快

        return g

    # ── 统计 ───────────────────────────────────────────────────────────────────

    def summary(self) -> str:
        n_event = sum(1 for n in self.nodes.values() if n["type"] == "event")
        n_sector = sum(1 for n in self.nodes.values() if n["type"] == "sector")
        n_stock = sum(1 for n in self.nodes.values() if n["type"] == "stock")
        n_affects = sum(1 for e in self.edges if e["type"] == "AFFECTS")
        n_contains = sum(1 for e in self.edges if e["type"] == "CONTAINS")
        n_up = sum(1 for e in self.edges if e["type"] == "UPSTREAM")
        n_down = sum(1 for e in self.edges if e["type"] == "DOWNSTREAM")
        return (
            f"TransmissionGraph: {len(self.nodes)} 节点 ({n_event}事件/{n_sector}板块/{n_stock}个股), "
            f"{len(self.edges)} 边 (AFFECTS:{n_affects} CONTAINS:{n_contains} "
            f"UPSTREAM:{n_up} DOWNSTREAM:{n_down})"
        )


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    import sys

    if len(sys.argv) < 2:
        print("用法: python -m core.graph.transmission_graph --build | --evolve <event_id>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "--build":
        print("[transmission_graph] 从 event_library.json 构建传导图谱...")
        g = TransmissionGraph.build_from_library()
        g.save()
        print(g.summary())
        print(f"[transmission_graph] 已保存 → {GRAPH_PATH}")

    elif cmd == "--evolve":
        if len(sys.argv) < 3:
            print("用法: --evolve <event_id> (如 T1_nvda_earnings)")
            sys.exit(1)
        event_id = sys.argv[2]

        g = TransmissionGraph.load()
        if f"event:{event_id}" not in g.nodes:
            print(f"[transmission_graph] 事件 '{event_id}' 不在图谱中。先运行 --build。")
            sys.exit(1)

        results = g.bfs_from_event(f"event:{event_id}", depth=2)
        print(f"\n{'='*60}")
        print(f"📡 事件传导: {g.nodes[f'event:{event_id}']['name']}")
        print(f"{'='*60}")

        # 分组：direct first, then indirect
        direct = [r for r in results if r["transmission_type"] == "direct"]
        indirect = [r for r in results if r["transmission_type"] == "indirect"]

        def _conf_bar(conf: float) -> str:
            if conf >= 0.8:
                return "★★★"
            elif conf >= 0.6:
                return "★★☆"
            elif conf >= 0.4:
                return "★☆☆"
            return " ✗ "

        print(f"\n🔵 直接传导（{len(direct)} 条）:")
        for r in direct:
            node = g.nodes.get(r["terminal_node"], {})
            name = node.get("name", r["terminal_node"])
            bar = _conf_bar(r["confidence"])
            print(f"  {bar} {name:12s} 置信度 {r['confidence']:.0%}  "
                  f"类型: {r['terminal_type']}  {'[终点]' if r['is_terminal'] else ''}")

        print(f"\n🟡 间接传导（{len(indirect)} 条）:")
        for r in indirect:
            node = g.nodes.get(r["terminal_node"], {})
            name = node.get("name", r["terminal_node"])
            bar = _conf_bar(r["confidence"])
            path_str = " → ".join(g.nodes.get(n, {}).get("name", n) for n in r["path"])
            print(f"  {bar} {name:12s} 置信度 {r['confidence']:.0%}  "
                  f"路径: {path_str}")

    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
