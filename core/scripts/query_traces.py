#!/usr/bin/env python3
"""
query_traces.py — trace 日志查询工具

用法：
  # 看今天所有事件
  python3 core/scripts/query_traces.py

  # 按事件类型过滤
  python3 core/scripts/query_traces.py --type SignalCandidateEvent

  # 按日期
  python3 core/scripts/query_traces.py --date 2026-07-23

  # 按 run_id 看完整链路
  python3 core/scripts/query_traces.py --run-id 20260723-a3f2

  # 只看失败事件
  python3 core/scripts/query_traces.py --status failed

  # 摘要统计
  python3 core/scripts/query_traces.py --summary
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(os.environ.get("CYCLERADAR_DATA_DIR",
                               Path(__file__).parent.parent.parent / "data"))
TRACES_DIR = DATA_DIR / "traces"


def load_traces(date_str: str) -> list[dict]:
    """读取指定日期的 trace 文件，返回记录列表。"""
    path = TRACES_DIR / f"trace_{date_str}.jsonl"
    if not path.exists():
        return []
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


def fmt_record(r: dict) -> str:
    """单条记录的格式化输出。"""
    ts = r.get("timestamp", "")[-8:]          # 只取时间部分 HH:MM:SS
    run_id = r.get("run_id", "")[-4:]          # 只取后 4 位
    event = r.get("event_type", "")
    status = r.get("status", "success")
    latency = r.get("latency_ms")
    latency_str = f" {latency}ms" if latency else ""

    status_icon = "✓" if status == "success" else ("⚠" if status in ("skipped", "dry_run") else "✗")

    # 关键字段摘要
    out = r.get("output", {})
    inp = r.get("input", {})
    detail = ""
    if event == "SignalCandidateEvent":
        code = inp.get("code", "")
        name = inp.get("name", "")
        conf = out.get("confidence", "")
        res = out.get("resonance", "")
        detail = f"{code} {name} conf={conf} res={res}"
    elif event in ("ScannerRunCompleted", "ScannerRunStarted"):
        detail = f"written={out.get('written', '')} candidates={inp.get('candidates', '')}"
    elif event == "ContractWrittenEvent":
        detail = f"{inp.get('filename', '')} count={out.get('signal_count', '')}"
    elif event == "TrackerCloseEvent":
        detail = f"{inp.get('code', '')} → {out.get('verdict', '')}"
    elif r.get("error"):
        detail = f"error={r['error'][:60]}"

    return f"[{ts}] {status_icon} run={run_id} {event:<32} {detail}{latency_str}"


def main():
    parser = argparse.ArgumentParser(description="查询 trace 日志")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"),
                        help="日期 YYYY-MM-DD 或 YYYYMMDD（默认今天）")
    parser.add_argument("--type", dest="event_type", default="",
                        help="事件类型过滤，如 SignalCandidateEvent")
    parser.add_argument("--run-id", default="",
                        help="按 run_id 过滤（支持前缀匹配）")
    parser.add_argument("--status", default="",
                        help="按 status 过滤：success / failed / skipped / dry_run")
    parser.add_argument("--summary", action="store_true",
                        help="只输出统计摘要，不输出每条记录")
    args = parser.parse_args()

    # 统一日期格式为 YYYYMMDD
    date_str = args.date.replace("-", "")

    records = load_traces(date_str)
    if not records:
        print(f"[query_traces] 没有找到 data/traces/trace_{date_str}.jsonl")
        return

    # 过滤
    if args.event_type:
        records = [r for r in records if r.get("event_type") == args.event_type]
    if args.run_id:
        records = [r for r in records if r.get("run_id", "").startswith(args.run_id)]
    if args.status:
        records = [r for r in records if r.get("status") == args.status]

    if args.summary:
        total = len(records)
        by_type = Counter(r.get("event_type") for r in records)
        by_status = Counter(r.get("status", "success") for r in records)
        run_ids = sorted({r.get("run_id", "") for r in records})

        print(f"\n📊 Trace 摘要 | {date_str[:4]}-{date_str[4:6]}-{date_str[6:]}")
        print(f"   总记录数: {total}")
        print(f"   run_id 列表: {', '.join(run_ids) if run_ids else '(无)'}")
        print(f"\n   事件类型分布:")
        for evt, cnt in by_type.most_common():
            print(f"     {evt:<36} {cnt}")
        print(f"\n   状态分布:")
        for st, cnt in by_status.most_common():
            icon = "✓" if st == "success" else ("⚠" if st in ("skipped", "dry_run") else "✗")
            print(f"     {icon} {st:<12} {cnt}")
        return

    # 逐条输出
    print(f"\n📋 Trace 记录 | {date_str[:4]}-{date_str[4:6]}-{date_str[6:]} | {len(records)} 条\n")
    for r in records:
        print(fmt_record(r))


if __name__ == "__main__":
    main()
