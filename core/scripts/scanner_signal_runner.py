#!/usr/bin/env python3.9
"""
scanner_signal_runner.py — ECS cron 入口：扫描 14 模型 → 共振 → 写入信号流

执行流程：
  1. 调用 core/scanner.py 的 scan() — 14 模型对全量股票池扫描
  2. 调用 scanner_adapter.emit_scanner_signals() — 共振计信 → write_signal
  3. 输出摘要到 stdout / 日志文件

用法：
  python3.9 core/scripts/scanner_signal_runner.py [--date YYYY-MM-DD] [--dry-run]

部署：
  ECS crontab: 40 15 * * 1-5 ... scanner_signal_runner.py
  路径：/opt/cycleradar-trader/core/scripts/scanner_signal_runner.py

依赖：
  PYTHONPATH 必须包含 /opt/cycleradar-trader/core:/opt/cycleradar-trader/core/signals:/opt/cycleradar-trader/core/signals/adapters
  CYCLERADAR_DATA_DIR 必须指向 /opt/cycleradar-trader/data
"""
from __future__ import annotations
import sys
import os
import argparse
import logging
import time
import traceback
from datetime import datetime, date


# ── 路径注入（ECS 环境需此模块自身定位，本地 dev 也兼容）──
# 从 scripts/ 找到 core/ 的上级目录
_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BASE_DIR = os.path.dirname(_CORE_DIR)

# 确保项目根和 core/ 在 sys.path 中
# （scanner.py 用 `from core.xxx` 导入，需要 _BASE_DIR 在 path）
for d in [_BASE_DIR, _CORE_DIR]:
    if d not in sys.path:
        sys.path.insert(0, d)

# 确保 signals/ 适配器目录可导入
_SIGNALS_DIR = os.path.join(_CORE_DIR, "signals")
_ADAPTERS_DIR = os.path.join(_SIGNALS_DIR, "adapters")
for d in [_SIGNALS_DIR, _ADAPTERS_DIR]:
    if d not in sys.path:
        sys.path.insert(0, d)

# 确保数据目录存在
DATA_DIR = os.environ.get("CYCLERADAR_DATA_DIR", os.path.join(_BASE_DIR, "data"))
LOG_DIR = os.path.join(DATA_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# V10: 环境配置（模型开关 + 交易风格偏好）落盘文件
ENV_CONFIG_FILE = os.path.join(DATA_DIR, "env-config.json")


def load_enabled_models() -> list:
    """读取 env-config.json 的 models 开关，返回启用的模型 key 列表。

    规则：
      - 配置缺失/不可读 → 返回 []（表示"全开"，scan() 用默认全部模型）
      - 全部开启 → 返回 []（等价全开，避免与未来新增模型脱节）
      - 部分关闭 → 返回仅启用的 key 列表（与 MODEL_FUNCS 交集）
    """
    if not os.path.exists(ENV_CONFIG_FILE):
        return []
    try:
        import json as _json
        with open(ENV_CONFIG_FILE, encoding="utf-8") as _f:
            cfg = _json.load(_f)
        models = cfg.get("models") or {}
        if not isinstance(models, dict) or not models:
            return []
        from scanner import MODEL_FUNCS
        enabled = [k for k, v in models.items() if v and k in MODEL_FUNCS]
        if len(enabled) == len(MODEL_FUNCS):
            return []
        return enabled
    except Exception:
        return []


def setup_logging(date_str: str) -> str:
    """配置日志文件路径，返回 log 文件路径"""
    log_file = os.path.join(
        LOG_DIR,
        f"scanner_signals_cron_{date_str}_{datetime.now().strftime('%H%M%S')}.log"
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_file


def main():
    parser = argparse.ArgumentParser(description="Scanner 14 模型信号写入流水线")
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="扫描日期 YYYY-MM-DD（默认今天）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只预览信号不写入",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=True,
        help="打印每条写入结果",
    )
    args = parser.parse_args()

    scan_date = args.date
    log_file = setup_logging(scan_date.replace("-", ""))

    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info(f"  scanner_signals runner — {scan_date}")
    logger.info(f"  dry_run={args.dry_run}  log={log_file}")
    logger.info("=" * 60)

    t0 = time.time()

    # ── tracer：生成本次 run_id，贯穿整个扫描周期 ──
    try:
        from core.utils.tracer import trace, new_run_id
        from core.utils.events import EVT
        _trace_ok = True
    except ImportError:
        _trace_ok = False
        def trace(*a, **kw): pass  # noqa: E731
        class EVT:  # noqa: E302
            SCANNER_RUN_STARTED = "ScannerRunStarted"
            SCANNER_RUN_COMPLETED = "ScannerRunCompleted"
            SCANNER_RUN_FAILED = "ScannerRunFailed"
        def new_run_id(): return "noop"  # noqa: E731

    run_id = new_run_id()
    trace(EVT.SCANNER_RUN_STARTED,
          input={"date": scan_date, "dry_run": args.dry_run},
          output={},
          run_id=run_id)

    try:
        # ── 1. 执行全量扫描 ──
        enabled_models = load_enabled_models()
        if enabled_models:
            logger.info(f"▶ Step 1: 启动 scanner.scan()（启用模型 {len(enabled_models)} 个: {', '.join(enabled_models)}）")
        else:
            logger.info("▶ Step 1: 启动 scanner.scan()（14 模型全量扫描）")
        from scanner import scan as scan_stocks

        result = scan_stocks(
            date=scan_date,
            models=enabled_models or None,
            verbose=True,
        )

        if not result or not result.get("hits"):
            logger.warning("scanner.scan() 返回空结果或空 hits，停止信号写入")
            logger.warning("可能原因：非交易日 / 数据源不可用 / 无候选股")
            trace(EVT.SCANNER_RUN_COMPLETED,
                  input={"date": scan_date},
                  output={"written": 0, "reason": "empty_hits"},
                  status="skipped",
                  run_id=run_id,
                  latency_ms=int((time.time() - t0) * 1000))
            return 1

        candidates = result.get("candidate_count", 0)
        model_count = len(result.get("hits", {}))
        logger.info(f"扫描完成 — {model_count} 个模型命中, 候选数={candidates}")

        # ── 2. 调 adapter 写入信号 ──
        logger.info("▶ Step 2: emit_scanner_signals → write_signal")
        from scanner_adapter import emit_scanner_signals

        count = emit_scanner_signals(
            result,
            dry_run=args.dry_run,
            verbose=args.verbose,
            run_id=run_id,          # 传入 run_id 供 adapter trace 使用
        )

        elapsed = time.time() - t0
        logger.info(f"✅ scanner_signals 完成 — {count} 条信号, 耗时 {elapsed:.1f}s")

        trace(EVT.SCANNER_RUN_COMPLETED,
              input={"date": scan_date, "candidates": candidates, "models": model_count},
              output={"written": count, "dry_run": args.dry_run},
              run_id=run_id,
              latency_ms=int(elapsed * 1000))

        # ── 3. 摘要输出 ──
        print(f"\n📊 scanner_signals 摘要 | {scan_date}")
        print(f"   写入: {count} 条信号")
        print(f"   耗时: {elapsed:.1f}s")
        print(f"   run_id: {run_id}")
        if not args.dry_run:
            print(f"   目标: {DATA_DIR}/upstream_signals.jsonl")

        return 0

    except Exception:
        elapsed = time.time() - t0
        err = traceback.format_exc()
        logger.error(f"❌ scanner_signals 失败 (耗时 {elapsed:.1f}s)")
        logger.error(err)
        trace(EVT.SCANNER_RUN_FAILED,
              input={"date": scan_date},
              output={},
              status="failed",
              error=err.strip().splitlines()[-1],  # 只记最后一行
              run_id=run_id,
              latency_ms=int(elapsed * 1000))
        return 1


if __name__ == "__main__":
    sys.exit(main())
