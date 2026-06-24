"""
scanner_adapter.py — 将 core/scanner.py 的 scan() 输出转为标准信号格式

职责：
  1. 展平 scanner hits（按模型分组 → 按股票聚合）
  2. 计算共振度（一只股票被几个模型同时命中）
  3. 按共振度设置信度（≥2模型→0.75，单模型→0.55）
  4. 写入 upstream_signals.jsonl（调用 write_signal）

用法：
  在 scanner_signal_runner.py 或其他入口中：
    from core.signals.adapters.scanner_adapter import emit_scanner_signals
    result = scanner.scan("2026-06-22")
    count = emit_scanner_signals(result)
"""
from __future__ import annotations
from datetime import datetime, timedelta
from collections import defaultdict
import sys as _sys
import os as _os

# 确保 signals/ 父目录在 path 中（同时兼容 runner 调用和自检运行）
_sig_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _sig_dir not in _sys.path:
    _sys.path.insert(0, _sig_dir)

from upstream_signals import write_signal


def scanner_hits_to_signals(
    scan_result: dict,
    *,
    expiry_days: int = 1,
) -> list[dict]:
    """将 scanner scan() 输出转换为标准信号列表。

    Args:
        scan_result: scanner.scan() 的返回值，含 hits/date 字段
        expiry_days: 信号过期天数（默认次日收盘）

    Returns:
        标准信号 dict 列表，可直接传给 write_signal()
    """
    hits: dict[str, list[dict]] = scan_result.get("hits", {})
    scan_date: str = scan_result.get("date", datetime.now().strftime("%Y-%m-%d"))
    signal_date: str = scan_date.replace("-", "")

    # ── 1. 按股票 code 聚合：每只股票记录命中模型列表 ──
    stock_models: dict[str, dict] = defaultdict(lambda: {
        "name": "",
        "models": [],
        "reasons": [],
    })

    for model_name, stock_list in hits.items():
        for s in stock_list:
            code = str(s.get("code", ""))
            if not code:
                continue
            entry = stock_models[code]
            entry["name"] = s.get("name", code)
            entry["models"].append(model_name)
            # 收集该模型的命中理由
            for r in s.get("reasons", []):
                if r not in entry["reasons"]:
                    entry["reasons"].append(r)

    # ── 2. 逐只产出信号 ──
    now = datetime.now().astimezone()
    expiry = (now + timedelta(days=expiry_days)).isoformat()

    signals: list[dict] = []
    for code, info in stock_models.items():
        model_count = len(info["models"])
        primary_model = info["models"][0]  # 第一个命中的模型作为主模型
        resonance = model_count

        # 置信度：≥2 模型共振 → 0.75，单模型 → 0.55
        confidence = 0.75 if model_count >= 2 else 0.55

        signal = {
            "signal_id": f"scanner-{signal_date}-{code}-{primary_model[:4]}",
            "timestamp": now.isoformat(),
            "strategy": "wanjun_models",
            "asset": code,
            "asset_type": "stock",
            "direction": "long",
            "confidence": confidence,
            "expiry": expiry,
            "metadata": {
                "stock_name": info["name"],
                "models": info["models"],
                "model_count": model_count,
                "resonance": resonance,
                "reasons": info["reasons"],
                "scan_date": scan_date,
            },
        }
        signals.append(signal)

    return signals


def emit_scanner_signals(
    scan_result: dict,
    *,
    dry_run: bool = False,
    verbose: bool = True,
) -> int:
    """将 scanner scan() 结果写入信号流。

    Args:
        scan_result: scanner.scan() 返回
        dry_run: True 只打印预览，不写入
        verbose: True 时打印每只股票写入结果

    Returns:
        成功写入条数
    """
    signals = scanner_hits_to_signals(scan_result)

    if dry_run:
        print(f"\n[scanner_adapter] DRY RUN — 不写入")
        for sig in signals:
            print(f"  {sig['signal_id']} | {sig['metadata']['stock_name']} "
                  f"| models={sig['metadata']['models']} "
                  f"| resonance={sig['metadata']['resonance']} "
                  f"| confidence={sig['confidence']}")
        return len(signals)

    count = 0
    for sig in signals:
        try:
            write_signal(sig)
            count += 1
            if verbose:
                print(f"  ✓ {sig['asset']} {sig['metadata']['stock_name']} "
                      f"→ {sig['metadata']['model_count']}模型 "
                      f"({sig['metadata']['resonance']}共振) "
                      f"confidence={sig['confidence']}")
        except Exception as e:
            print(f"  ⚠ scanner 信号写入失败 ({sig['metadata']['stock_name']}): {e}")

    print(f"\n[scanner_adapter] 写入 {count}/{len(signals)} 条信号")
    return count


if __name__ == "__main__":
    # 自检：用预构造数据验证格式
    sample_result = {
        "date": "2026-06-22",
        "hits": {
            "钱坤寻龙": [
                {"code": "000001", "name": "平安银行", "reasons": ["龙虎榜出现", "量能放大"]},
                {"code": "000002", "name": "万科A", "reasons": ["突破平台"]},
            ],
            "向上缺口": [
                {"code": "000001", "name": "平安银行", "reasons": ["向上跳空 1.2%"]},
            ],
            "回调狙击": [
                {"code": "000003", "name": "金田股份", "reasons": ["回调至 20 日均线"]},
            ],
        },
    }

    signals = scanner_hits_to_signals(sample_result)
    print(f"自检：{len(signals)} 条信号")
    for s in signals:
        print(f"  {s['signal_id']} | confidence={s['confidence']} "
              f"| models={s['metadata']['models']} "
              f"| resonance={s['metadata']['resonance']}")
    assert len(signals) == 3, f"期望 3 条，实际 {len(signals)}"
    # 000001 被2模型命中 → 0.75
    assert signals[0]["confidence"] == 0.75, f"000001 期望 0.75"
    # 000002 单模型 → 0.55
    assert signals[1]["confidence"] == 0.55, f"000002 期望 0.55"
    print("✓ 自检全部通过")
