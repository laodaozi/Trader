"""
tracer.py — 信号链路事件追踪器

每次关键节点调用 trace()，输出到 data/traces/trace_YYYYMMDD.jsonl。
设计原则：零依赖（仅标准库）、不抛异常、不影响主业务。

用法：
    from core.utils.tracer import trace, new_run_id

    run_id = new_run_id()
    t0 = time.time()
    # ... 业务逻辑 ...
    trace("SignalCandidateEvent",
          input={"code": "000001", "models_hit": ["jxgz"]},
          output={"signal_id": "scanner-20260723-000001-jxgz", "confidence": 0.55},
          status="success",
          run_id=run_id,
          latency_ms=int((time.time() - t0) * 1000))

trace 文件路径：data/traces/trace_YYYYMMDD.jsonl
每行一条 JSON，字段：
    run_id       str   本次运行标识（格式 YYYYMMDD-XXXX）
    event_type   str   事件类型名（见 events.py）
    timestamp    str   ISO 8601
    status       str   "success" | "failed" | "skipped"
    input        dict  输入摘要（不放大字段，保持可读）
    output       dict  输出摘要
    latency_ms   int?  耗时毫秒（可选）
    error        str?  失败时的错误信息（可选）
"""
from __future__ import annotations

import json
import os
import random
import string
import time
from pathlib import Path

# trace 文件存放目录，相对于项目根（可通过环境变量覆盖）
_DEFAULT_TRACE_DIR = Path(__file__).parent.parent.parent / "data" / "traces"
TRACE_DIR = Path(os.environ.get("CR_TRACE_DIR", _DEFAULT_TRACE_DIR))


def new_run_id() -> str:
    """生成 8 位 run_id，格式 YYYYMMDD-XXXX（4位随机字母数字）。"""
    date_part = time.strftime("%Y%m%d")
    rand_part = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{date_part}-{rand_part}"


def trace(
    event_type: str,
    *,
    input: dict | None = None,
    output: dict | None = None,
    status: str = "success",
    run_id: str | None = None,
    latency_ms: int | None = None,
    error: str | None = None,
    **extra,
) -> None:
    """写一条 trace 记录到当日 jsonl 文件。

    永不抛异常——trace 失败只打印警告，不阻断主业务。

    Args:
        event_type: 事件类型名，如 "SignalCandidateEvent"
        input:      输入摘要 dict
        output:     输出摘要 dict
        status:     "success" | "failed" | "skipped"
        run_id:     本次运行标识，建议用 new_run_id() 在入口处生成并传递
        latency_ms: 耗时毫秒
        error:      失败时的错误信息
        **extra:    其他自定义字段
    """
    try:
        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        date_str = time.strftime("%Y%m%d")
        record: dict = {
            "run_id": run_id or new_run_id(),
            "event_type": event_type,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "status": status,
            "input": input or {},
            "output": output or {},
        }
        if latency_ms is not None:
            record["latency_ms"] = latency_ms
        if error is not None:
            record["error"] = error
        if extra:
            record.update(extra)

        trace_file = TRACE_DIR / f"trace_{date_str}.jsonl"
        with open(trace_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        # trace 失败不能影响主业务
        print(f"[tracer] ⚠ 写入 trace 失败（{event_type}）: {exc}")


if __name__ == "__main__":
    # 自检：用临时目录隔离，不污染真实 data/traces/
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        # 覆盖模块级 TRACE_DIR，指向临时目录
        TRACE_DIR = Path(tmp)

        rid = new_run_id()
        assert len(rid) == 13, f"run_id 长度应为13，实际 {len(rid)}: {rid}"
        assert rid[8] == "-", f"run_id 格式应为 YYYYMMDD-XXXX: {rid}"

        trace("ScannerRunStarted",
              input={"date": "2026-07-23", "candidates": 28},
              output={"hits": 3},
              run_id=rid, latency_ms=4210)

        trace("SignalCandidateEvent",
              input={"code": "000001", "models_hit": ["jxgz", "rzq"]},
              output={"signal_id": "scanner-20260723-000001-jxgz", "confidence": 0.75},
              run_id=rid)

        trace("ScannerRunFailed",
              input={"date": "2026-07-23"},
              output={},
              status="failed",
              error="ConnectionError: timeout",
              run_id=rid)

        date_str = time.strftime("%Y%m%d")
        lines = (Path(tmp) / f"trace_{date_str}.jsonl").read_text().strip().split("\n")
        assert len(lines) == 3, f"期望 3 行，实际 {len(lines)}"

        r0 = json.loads(lines[0])
        assert r0["event_type"] == "ScannerRunStarted"
        assert r0["latency_ms"] == 4210
        assert r0["run_id"] == rid

        r2 = json.loads(lines[2])
        assert r2["status"] == "failed"
        assert "error" in r2

        print(f"✓ tracer 自检全部通过（3 条记录，run_id={rid}）")
        for line in lines:
            print(" ", line)
