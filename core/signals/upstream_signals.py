"""
upstream_signals.py — CycleRadar 分支 C：信号合约写入/读取工具

职责：
  各上游策略模块通过 write_signal() 按 signals_contract.json 输出信号，
  统一追加到 data/upstream_signals.jsonl；下游消费者通过 read_signals() /
  read_latest_signals() 读取，替代硬编码 import。

设计原则（对齐 PLAN 风险缓解）：
  - 零第三方依赖：内置轻量校验，不强依赖 jsonschema（若已安装则做完整 schema 校验）
  - 追加写（append-only），单行一条信号，崩溃安全
  - 时间窗口读取按 timestamp 过滤，自动跳过损坏行

用法：
  from upstream_signals import write_signal, read_signals, read_latest_signals

  write_signal({
      "signal_id": str(uuid.uuid4()),
      "timestamp": "2026-06-05T16:30:00+08:00",
      "strategy": "commodity_radar",
      "asset": "原油",
      "asset_type": "commodity",
      "direction": "short",
      "confidence": 0.85,
      "expiry": "2026-06-08T16:30:00+08:00",
      "metadata": {"commodity_type": "原油", "price_5d_pct": -1.32},
  })
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

# ── 路径常量（统一项目目录定位）──────────────────────
# 本文件现位于 core/signals/ 下：
#   - signals_contract.json 与本文件同目录
#   - 数据目录默认在项目根下的 data/（core/signals/ 上两级），
#     可用环境变量 CYCLERADAR_DATA_DIR 覆盖。
SIGNALS_DIR = Path(__file__).resolve().parent
CONTRACT_PATH = SIGNALS_DIR / "signals_contract.json"
_DEFAULT_DATA_DIR = SIGNALS_DIR.parent.parent / "data"
DATA_DIR = Path(os.environ.get("CYCLERADAR_DATA_DIR", _DEFAULT_DATA_DIR))
SIGNALS_FILE = DATA_DIR / "upstream_signals.jsonl"

# ── V4.3: 写入端去重 ── 模块加载时预读全部 signal_id，避免重复追加
_SEEN_IDS: set[str] = set()

def _load_seen_ids() -> None:
    """从 upstream_signals.jsonl 读取全部 signal_id 到 _SEEN_IDS，
    用于 write_signal 去重。跳过损坏行。"""
    if not SIGNALS_FILE.exists():
        return
    try:
        with open(SIGNALS_FILE, "r", encoding="utf-8") as f:
            for ln, raw in enumerate(f, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    sig = json.loads(raw)
                    sid = sig.get("signal_id")
                    if sid:
                        _SEEN_IDS.add(sid)
                except json.JSONDecodeError:
                    pass  # 损坏行不影响去重
    except OSError as e:
        print(f"  ⚠ _load_seen_ids OSError: {e}")

_load_seen_ids()

# ── 合约常量（与 signals_contract.json 保持同步）────────
REQUIRED_FIELDS = (
    "signal_id", "timestamp", "strategy", "asset",
    "asset_type", "direction", "confidence", "expiry",
)
DIRECTION_ENUM = frozenset({"long", "short", "neutral"})
ASSET_TYPE_ENUM = frozenset({"stock", "etf", "index", "commodity", "sector"})

# 历史/本地词汇 → 统一 direction 归一表（write_signal 自动归一）
_DIRECTION_NORMALIZE = {
    "多": "long", "看多": "long", "利好": "long", "buy": "long", "long": "long",
    "空": "short", "看空": "short", "利空": "short", "sell": "short", "short": "short",
    "观望": "neutral", "中性": "neutral", "neutral": "neutral",
    "1": "long", "-1": "short", "0": "neutral",
}
# 字符串置信度 → 浮点归一表
_CONFIDENCE_NORMALIZE = {"high": 0.85, "medium": 0.55, "low": 0.3}


class SignalValidationError(ValueError):
    """信号不符合 signals_contract.json 时抛出。"""


# ── 校验 ────────────────────────────────────────────────

def _load_jsonschema_validator():
    """若环境装了 jsonschema，返回校验函数；否则返回 None（降级到基础校验）。"""
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return None
    contract_path = Path(__file__).parent / "signals_contract.json"
    if not contract_path.exists():
        return None
    schema = json.loads(contract_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(schema)
    return validator


def normalize_direction(value: Any) -> str:
    """把任意本地方向词汇归一到 long/short/neutral。"""
    if value is None:
        return "neutral"
    key = str(value).strip()
    if key in _DIRECTION_NORMALIZE:
        return _DIRECTION_NORMALIZE[key]
    if key in DIRECTION_ENUM:
        return key
    raise SignalValidationError(f"无法识别的 direction: {value!r}")


def normalize_confidence(value: Any) -> float:
    """把字符串/数值置信度归一到 [0,1] float。"""
    if isinstance(value, str) and value in _CONFIDENCE_NORMALIZE:
        return _CONFIDENCE_NORMALIZE[value]
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise SignalValidationError(f"无法识别的 confidence: {value!r}")
    if not (0.0 <= f <= 1.0):
        raise SignalValidationError(f"confidence 越界 (需 0~1): {f}")
    return f


def _basic_validate(signal: dict[str, Any]) -> None:
    """基础校验：required 字段 / direction 枚举 / confidence 值域 / asset_type 枚举。"""
    if not isinstance(signal, dict):
        raise SignalValidationError("signal 必须是 dict")

    missing = [k for k in REQUIRED_FIELDS if k not in signal or signal[k] in (None, "")]
    if missing:
        raise SignalValidationError(f"缺少必填字段: {missing}")

    if signal["direction"] not in DIRECTION_ENUM:
        raise SignalValidationError(
            f"direction 必须是 {sorted(DIRECTION_ENUM)}，得到 {signal['direction']!r}"
        )

    if signal["asset_type"] not in ASSET_TYPE_ENUM:
        raise SignalValidationError(
            f"asset_type 必须是 {sorted(ASSET_TYPE_ENUM)}，得到 {signal['asset_type']!r}"
        )

    conf = signal["confidence"]
    if not isinstance(conf, (int, float)) or isinstance(conf, bool):
        raise SignalValidationError(f"confidence 必须是数值，得到 {type(conf).__name__}")
    if not (0.0 <= float(conf) <= 1.0):
        raise SignalValidationError(f"confidence 必须在 0~1，得到 {conf}")

    # 时间字段可解析性检查
    for tf in ("timestamp", "expiry"):
        if not _parse_iso(signal[tf]):
            raise SignalValidationError(f"{tf} 非合法 ISO8601: {signal[tf]!r}")


def validate_signal(signal: dict[str, Any]) -> None:
    """完整校验：优先用 jsonschema（draft-07），降级到基础校验。"""
    _basic_validate(signal)
    validator = _load_jsonschema_validator()
    if validator is not None:
        errors = sorted(validator.iter_errors(signal), key=lambda e: e.path)
        if errors:
            msgs = "; ".join(e.message for e in errors[:5])
            raise SignalValidationError(f"schema 校验失败: {msgs}")


# ── 时间工具 ────────────────────────────────────────────

def _parse_iso(value: Any) -> Optional[datetime]:
    """宽松解析 ISO8601；失败返回 None。兼容末尾 Z。"""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    s = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _to_naive(dt: datetime) -> datetime:
    """去掉时区，统一为 naive 以便跨 aware/naive 比较。"""
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


# ── 写入 ────────────────────────────────────────────────

def write_signal(signal_dict: dict[str, Any], *, normalize: bool = True, skip_dup: bool = True) -> dict[str, Any]:
    """校验单条信号并追加到 data/upstream_signals.jsonl。

    Args:
        signal_dict: 待写入信号（符合 signals_contract.json）。
        normalize: True 时自动归一 direction/confidence 本地词汇（默认开）。
        skip_dup: True 时若 signal_id 已存在于 JSONL 中则跳过写入（默认开）。
                  设为 False 可强制追加（用于 intentional update 场景）。

    Returns:
        实际写入的（归一后）信号 dict。若 skip_dup 命中且跳过，返回原信号。

    Raises:
        SignalValidationError: 校验失败时抛出，不写入。
    """
    signal = dict(signal_dict)  # 浅拷贝，避免污染调用方

    if normalize:
        if "direction" in signal:
            signal["direction"] = normalize_direction(signal["direction"])
        if "confidence" in signal:
            signal["confidence"] = normalize_confidence(signal["confidence"])

    signal.setdefault("metadata", {})

    validate_signal(signal)

    sid = signal.get("signal_id")
    if skip_dup and sid and sid in _SEEN_IDS:
        return signal  # 不写入，但仍返回信号供调用方使用

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps(signal, ensure_ascii=False)
    with open(SIGNALS_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

    if sid:
        _SEEN_IDS.add(sid)
    return signal


def write_signals(signals: Iterable[dict[str, Any]], *, normalize: bool = True, skip_dup: bool = True) -> int:
    """批量写入，返回成功条数（含 dup-skipped）。单条失败不影响其余（打印告警）。"""
    n = 0
    for sig in signals:
        try:
            write_signal(sig, normalize=normalize, skip_dup=skip_dup)
            n += 1
        except SignalValidationError as e:
            print(f"  ⚠ 跳过非法信号 ({sig.get('signal_id', '?')}): {e}")
    return n


# ── 读取 ────────────────────────────────────────────────

def _iter_raw_signals() -> Iterable[dict[str, Any]]:
    """逐行读取 JSONL，自动跳过空行/损坏行。"""
    if not SIGNALS_FILE.exists():
        return
    with open(SIGNALS_FILE, "r", encoding="utf-8") as f:
        for ln, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                print(f"  ⚠ 第 {ln} 行 JSON 解析失败，已跳过")


def read_signals(start_time: Any = None, end_time: Any = None) -> list[dict[str, Any]]:
    """按时间窗口读取信号（按 timestamp 过滤）。

    Args:
        start_time: 起始时间（含），ISO8601 字符串或 datetime。None 表示不限下界。
        end_time:   截止时间（含），ISO8601 字符串或 datetime。None 表示不限上界。

    Returns:
        按 timestamp 升序排列的信号列表。
    """
    start = _to_naive(_parse_iso(start_time)) if start_time else None
    end = _to_naive(_parse_iso(end_time)) if end_time else None

    out: list[tuple[datetime, dict]] = []
    for sig in _iter_raw_signals():
        ts = _parse_iso(sig.get("timestamp"))
        if ts is None:
            continue
        ts_n = _to_naive(ts)
        if start is not None and ts_n < start:
            continue
        if end is not None and ts_n > end:
            continue
        out.append((ts_n, sig))

    out.sort(key=lambda x: x[0])
    return [s for _, s in out]


def read_latest_signals(strategy: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    """读取最新 N 条信号（按 timestamp 倒序）。

    Args:
        strategy: 仅返回该策略的信号；None 表示全部策略。
        limit:    最多返回条数。

    Returns:
        按 timestamp 降序（最新在前）的信号列表，最多 limit 条。
    """
    rows: list[tuple[datetime, dict]] = []
    for sig in _iter_raw_signals():
        if strategy is not None and sig.get("strategy") != strategy:
            continue
        ts = _parse_iso(sig.get("timestamp"))
        if ts is None:
            continue
        rows.append((_to_naive(ts), sig))

    rows.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in rows[:limit]]


def read_active_signals(now: Any = None, strategy: Optional[str] = None) -> list[dict[str, Any]]:
    """读取未过期信号（expiry >= now）。下游消费者的推荐入口。"""
    ref = _to_naive(_parse_iso(now)) if now else datetime.now()
    out = []
    for sig in _iter_raw_signals():
        if strategy is not None and sig.get("strategy") != strategy:
            continue
        exp = _parse_iso(sig.get("expiry"))
        if exp is None or _to_naive(exp) >= ref:
            out.append(sig)
    return out


# ── 自检 CLI ────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import uuid

    parser = argparse.ArgumentParser(description="upstream_signals 自检 / 查询")
    parser.add_argument("--selftest", action="store_true", help="写入一条样例并回读验证")
    parser.add_argument("--latest", type=int, metavar="N", help="打印最新 N 条信号")
    parser.add_argument("--strategy", type=str, default=None, help="按策略过滤")
    args = parser.parse_args()

    if args.selftest:
        sample = {
            "signal_id": str(uuid.uuid4()),
            "timestamp": datetime.now().isoformat(),
            "strategy": "commodity_radar",
            "asset": "原油",
            "asset_type": "commodity",
            "direction": "空",          # 故意用本地词汇，测试归一
            "confidence": "high",        # 故意用字符串，测试归一
            "expiry": datetime.now().replace(year=datetime.now().year + 1).isoformat(),
            "metadata": {"commodity_type": "原油", "price_5d_pct": -1.32},
        }
        written = write_signal(sample)
        print(f"✓ 写入成功: direction={written['direction']} confidence={written['confidence']}")
        back = read_latest_signals(strategy="commodity_radar", limit=1)
        print(f"✓ 回读 {len(back)} 条，最新 signal_id={back[0]['signal_id'] if back else 'N/A'}")

    if args.latest:
        for s in read_latest_signals(strategy=args.strategy, limit=args.latest):
            print(f"  [{s.get('timestamp')}] {s.get('strategy')} {s.get('asset')} "
                  f"{s.get('direction')} ({s.get('confidence')})")
