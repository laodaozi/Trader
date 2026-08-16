#!/usr/bin/env python3.9
"""
reinforce_from_verify.py — verify → graph 桥接脚本

读取 event_hit_log.json 的 ticker 级判决，按行业聚合，反馈到传导图谱权重。

流程：
  1. 从图 CONTAINS 边构建 stock→sector 反向索引
  2. 读取 event_hit_log.json
  3. checkpoint 过滤：只处理上次 reinforce 之后的新事件
  4. 按 sector 聚合 HIT/MISS 计数
  5. 对每个 sector，调整 event_type→sector 的边权重：
     - sector hit_rate > 0.5 → 加强
     - sector hit_rate < 0.5 → 削弱
  6. 保存图谱 + checkpoint

用法：
  PYTHONPATH=/opt/cycleradar-trader python3.9 core/graph/reinforce_from_verify.py
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path("/opt/cycleradar-trader")
DATA = BASE / "data"
CHECKPOINT = DATA / "reinforce_checkpoint.json"


def load_checkpoint() -> str | None:
    """加载上次 reinforce 处理到的最新 source_date。返回 ISO 日期字符串或 None。"""
    if CHECKPOINT.exists():
        try:
            cp = json.loads(CHECKPOINT.read_text())
            return cp.get("last_source_date")
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def save_checkpoint(source_date: str):
    """保存 checkpoint。"""
    CHECKPOINT.write_text(json.dumps({
        "last_source_date": source_date,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2))


def build_stock_sector_index(graph) -> dict[str, set[str]]:
    """从 CONTAINS 边构建 stock_code → {sector, ...} 反向索引。"""
    index: dict[str, set[str]] = defaultdict(set)
    for e in graph.edges:
        if e.get("type") == "CONTAINS":
            src = e.get("from", "")
            dst = e.get("to", "")
            if src.startswith("sector:") and dst.startswith("stock:"):
                code = dst.replace("stock:", "")
                sector = src.replace("sector:", "")
                index[code].add(sector)
    return index


def main():
    # ── 1. Load graph ──
    from core.graph.transmission_graph import TransmissionGraph

    g = TransmissionGraph.load()
    stock_to_sectors = build_stock_sector_index(g)
    if not stock_to_sectors:
        print("[reinforce] graph has no CONTAINS edges — nothing to bridge")
        return

    # ── 2. Read event_hit_log ──
    hit_log = DATA / "event_hit_log.json"
    if not hit_log.exists():
        print("[reinforce] event_hit_log.json not found — verify hasn't run yet")
        return

    with open(hit_log) as f:
        log = json.load(f)

    events = log.get("events", [])
    if not events:
        print("[reinforce] event_hit_log has no events")
        return

    # ── 3. Determine which events are new (checkpoint-based) ──
    last_check = load_checkpoint()
    last_date = None
    max_new_date = None

    if last_check:
        try:
            last_date = datetime.strptime(last_check, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            last_date = None

    # ── 4. Aggregate per-sector verdicts (only new events) ──
    sector_hits = Counter()
    sector_misses = Counter()
    sector_pending = Counter()

    today = datetime.now().date()
    cutoff = today - timedelta(days=90)

    processed = 0
    skipped = 0

    for event in events:
        sd = event.get("source_date", "")
        try:
            evt_date = datetime.strptime(sd, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            evt_date = None

        if evt_date and evt_date < cutoff:
            continue

        # Checkpoint: skip events already processed in previous runs
        if last_date and evt_date and evt_date <= last_date:
            skipped += 1
            continue

        processed += 1
        if max_new_date is None or (evt_date and evt_date > max_new_date):
            max_new_date = evt_date

        for ticker in event.get("tickers", []):
            code = ticker.get("code", "")
            sectors = stock_to_sectors.get(code)
            if not sectors:
                continue

            verdicts = ticker.get("verdicts", {})
            verdict = verdicts.get("d5", verdicts.get("d3", "PENDING"))

            for sector in sectors:
                if verdict == "HIT":
                    sector_hits[sector] += 1
                elif verdict == "MISS":
                    sector_misses[sector] += 1
                else:
                    sector_pending[sector] += 1

    # ── 5. Adjust graph edges ──
    all_sectors = set(list(sector_hits.keys()) + list(sector_misses.keys()))
    adjusted = 0
    report_lines = []

    for sector in sorted(all_sectors):
        hits = sector_hits.get(sector, 0)
        misses = sector_misses.get(sector, 0)
        total = hits + misses

        if total < 3:
            continue

        hit_rate = hits / total

        sector_id = f"sector:{sector}"
        for e in g.edges:
            src = e.get("from", "")
            dst = e.get("to", "")
            if dst == sector_id and src.startswith("event:"):
                old_w = e.get("weight", 0)
                if hit_rate > 0.5:
                    delta = min(0.025 + 0.025 * (hit_rate - 0.5), 0.05)
                    g.reinforce_edge(src, sector_id, delta=round(delta, 4))
                else:
                    delta = min(0.025 + 0.025 * (0.5 - hit_rate), 0.05)
                    g.weaken_edge(src, sector_id, delta=round(delta, 4))
                new_w = e.get("weight", old_w)
                adjusted += 1

        report_lines.append(
            f"  {sector}: hit_rate={hit_rate:.1%} ({hits}H/{misses}M/{total}T)"
        )

    # ── 6. Save graph + checkpoint ──
    g.save()

    if max_new_date:
        save_checkpoint(max_new_date.strftime("%Y-%m-%d"))

    ts = datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] reinforce_from_verify: {adjusted} edges across {len(report_lines)} sectors "
          f"(processed={processed}, skipped={skipped})")
    for line in report_lines:
        print(line)


if __name__ == "__main__":
    main()
