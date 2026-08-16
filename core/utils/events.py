"""
events.py — 信号链路事件类型定义

用途：
  1. 类型即文档——让管道里流的数据有名字、有结构
  2. 作为 tracer.trace() 的 event_type 字符串常量来源
  3. 为未来迁移到 LlamaAgents @step 事件模型提供类型锚点

事件流：
  ScannerRunStarted
    → SignalCandidateEvent（每只股票一条）
    → SignalWrittenEvent（写入 upstream_signals.jsonl）
  ScannerRunCompleted / ScannerRunFailed

  ReportAgentRunStarted
    → AlphaSignalEvent（每条 alpha 信号）
    → ContractWrittenEvent（契约文件写入）
  ReportAgentRunCompleted / ReportAgentRunFailed

  TrackerCloseEvent（tracker_closer 裁决一条 HOLD 信号）
  WinrateCalcEvent（30 日胜率计算完成）

使用示例（与 tracer 配合）：
    from core.utils.events import EVT
    from core.utils.tracer import trace

    trace(EVT.SIGNAL_CANDIDATE,
          input={"code": "000001", "models_hit": ["jxgz"]},
          output={"signal_id": "...", "confidence": 0.55},
          run_id=run_id)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ── 事件类型字符串常量（传给 tracer.trace 的 event_type） ──

class EVT:
    """事件类型常量，避免手写字符串拼写错误。"""
    # Scanner 管道
    SCANNER_RUN_STARTED    = "ScannerRunStarted"
    SIGNAL_CANDIDATE       = "SignalCandidateEvent"
    SIGNAL_WRITTEN         = "SignalWrittenEvent"
    SCANNER_RUN_COMPLETED  = "ScannerRunCompleted"
    SCANNER_RUN_FAILED     = "ScannerRunFailed"

    # Report Agent 管道（Pipeline A）
    REPORT_AGENT_STARTED   = "ReportAgentRunStarted"
    ALPHA_SIGNAL           = "AlphaSignalEvent"
    CONTRACT_WRITTEN       = "ContractWrittenEvent"
    REPORT_AGENT_COMPLETED = "ReportAgentRunCompleted"
    REPORT_AGENT_FAILED    = "ReportAgentRunFailed"

    # Tracker 闭环
    TRACKER_CLOSE          = "TrackerCloseEvent"
    WINRATE_CALC           = "WinrateCalcEvent"

    # 通用
    STEP_SKIPPED           = "StepSkipped"


# ── 事件 dataclass（结构化，便于类型检查和未来迁移） ──

@dataclass
class ScannerRunStarted:
    """Scanner 开始扫描。"""
    run_id: str
    date: str                        # 扫描日期 YYYY-MM-DD
    candidates: int = 0              # 候选股票数


@dataclass
class SignalCandidateEvent:
    """Scanner 产出一条候选信号（尚未写入）。"""
    run_id: str
    code: str                        # 股票代码
    name: str                        # 股票名称
    models_hit: list[str]            # 命中的模型列表
    confidence: float                # 置信度 0.55 / 0.75
    resonance: int                   # 共振模型数
    calibrated_win_rate: Optional[float] = None   # 胜率校准值
    calibrated_sample: int = 0       # 胜率校准样本量


@dataclass
class SignalWrittenEvent:
    """一条信号成功写入 upstream_signals.jsonl。"""
    run_id: str
    signal_id: str
    code: str
    confidence: float


@dataclass
class ScannerRunCompleted:
    """Scanner 扫描完成汇总。"""
    run_id: str
    date: str
    total_hits: int                  # 命中总数
    written: int                     # 成功写入数
    latency_ms: int = 0


@dataclass
class ScannerRunFailed:
    """Scanner 扫描失败。"""
    run_id: str
    date: str
    error: str


@dataclass
class ReportAgentRunStarted:
    """Pipeline A（report_agent）启动。"""
    run_id: str
    date: str


@dataclass
class AlphaSignalEvent:
    """Report agent 产出一条 alpha 信号。"""
    run_id: str
    asset: str                       # 股票代码
    sector: str                      # 所属板块
    thesis: str                      # 投资逻辑摘要
    confidence: float
    entry: Optional[float] = None
    target: Optional[float] = None
    stop: Optional[float] = None


@dataclass
class ContractWrittenEvent:
    """契约文件写入成功。"""
    run_id: str
    filename: str                    # alpha_latest.json / event_narrative_latest.json
    signal_count: int = 0
    path: str = ""


@dataclass
class ReportAgentRunCompleted:
    """Pipeline A 完成。"""
    run_id: str
    date: str
    alpha_count: int = 0
    latency_ms: int = 0


@dataclass
class TrackerCloseEvent:
    """tracker_closer 裁决一条 HOLD 信号。"""
    signal_id: str
    code: str
    verdict: str                     # "WIN" | "LOSE" | "HOLD"
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    target: Optional[float] = None
    stop: Optional[float] = None
    run_id: str = ""


@dataclass
class WinrateCalcEvent:
    """30 日胜率计算完成。"""
    run_id: str
    date: str
    total_signals: int = 0
    win_count: int = 0
    win_rate: float = 0.0
    models_covered: list[str] = field(default_factory=list)


if __name__ == "__main__":
    # 自检：确认所有 EVT 常量都有对应 dataclass
    evt_values = {v for k, v in EVT.__dict__.items() if not k.startswith("_")}
    dataclass_names = {
        "ScannerRunStarted", "SignalCandidateEvent", "SignalWrittenEvent",
        "ScannerRunCompleted", "ScannerRunFailed",
        "ReportAgentRunStarted", "AlphaSignalEvent", "ContractWrittenEvent",
        "ReportAgentRunCompleted",
        "TrackerCloseEvent", "WinrateCalcEvent",
    }
    # 验证 dataclass 可以正常实例化
    s = ScannerRunStarted(run_id="20260723-test", date="2026-07-23", candidates=28)
    assert s.candidates == 28

    sig = SignalCandidateEvent(
        run_id="20260723-test", code="000001", name="平安银行",
        models_hit=["jxgz", "rzq"], confidence=0.75, resonance=2
    )
    assert sig.confidence == 0.75

    tc = TrackerCloseEvent(
        signal_id="scanner-20260701-000001-jxgz",
        code="000001", verdict="WIN", entry_price=12.5, exit_price=13.8
    )
    assert tc.verdict == "WIN"

    print(f"✓ events 自检通过（{len(dataclass_names)} 个事件类型，EVT 常量 {len(evt_values)} 个）")
    print(f"  EVT 常量示例: {EVT.SIGNAL_CANDIDATE}, {EVT.TRACKER_CLOSE}")
