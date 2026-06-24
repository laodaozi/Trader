"""
commodity_radar.py — 商品期货方向信号生成器 V1.0

职责：
  从新浪期货数据（AKShare futures_main_sina）获取 5 个核心品种的
  日内行情，基于价格变动方向和幅度生成 long/short 信号。

覆盖品种：
  原油 SC0 ─ 上海国际能源交易中心 ─ 全球需求/地缘
  黄金 AU0 ─ 上期所 ── 避险/实际利率
  白银 AG0 ─ 上期所 ── 工业+避险双属性
  沪铜 CU0 ─ 上期所 ── 全球经济晴雨表
  铁矿 I0  ─ 大商所 ── 中国基建/地产

信号规则：
  - 涨跌幅 ≥ 1.0% → 生成信号，方向与涨跌同向
  - 置信度 = clamp(0.50 + |chg_pct| × 0.08, 0.45, 0.90)
    （1%变动 ≈ 0.58，5%变动 ≈ 0.90）
  - 有效期 = 7 天
  - asset 使用中文简称（原油/黄金/白银/铁矿/沪铜）

用法：
  from commodity_radar import generate_commodity_signals

  signals = generate_commodity_signals("2026-06-08")
  → [{"signal_id": "commodity_radar-20260608-001", ...}, ...]
"""
from __future__ import annotations

import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# ── 重试参数 ────────────────────────────────────────────
FETCH_MAX_RETRIES = 3
FETCH_RETRY_DELAY = 5  # seconds

# ── 品种定义 ────────────────────────────────────────────
COMMODITIES: dict[str, dict[str, str]] = {
    "SC0": {
        "name": "原油",
        "exchange": "上海国际能源交易中心",
        "symbol": "SC0",
        "asset": "原油",
    },
    "AU0": {
        "name": "黄金",
        "exchange": "上海期货交易所",
        "symbol": "AU0",
        "asset": "黄金",
    },
    "AG0": {
        "name": "白银",
        "exchange": "上海期货交易所",
        "symbol": "AG0",
        "asset": "白银",
    },
    "CU0": {
        "name": "沪铜",
        "exchange": "上海期货交易所",
        "symbol": "CU0",
        "asset": "沪铜",
    },
    "I0": {
        "name": "铁矿",
        "exchange": "大连商品交易所",
        "symbol": "I0",
        "asset": "铁矿",
    },
}

# 信号置信度参数
CHG_THRESHOLD = 1.0         # |涨跌幅| ≥ 1.0% 才生成信号
CONF_SLOPE = 0.08           # 每 1% 变动增加 0.08 置信度
CONF_BASE = 0.50            # 基线置信度（1% 变动时）
CONF_MIN = 0.45             # 最低置信度
CONF_MAX = 0.90             # 最高置信度
SIGNAL_TTL_DAYS = 7         # 信号有效期（天）


# ── 降级缓存 ────────────────────────────────────────────
_CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "commodity_cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _fetch_with_retry(fetch_fn, symbol: str, desc: str):
    """带重试和降级缓存的 AKShare 数据拉取。

    策略：最多 3 次重试（间隔 5s），全失败则从本地缓存读取上日数据。
    """
    import pandas as pd

    last_err = None
    for attempt in range(1, FETCH_MAX_RETRIES + 1):
        try:
            df = fetch_fn(symbol)
            if df is not None and not df.empty:
                if attempt > 1:
                    print(f"  ⚠ 第 {attempt} 次重试成功", end=" ")
                return df
        except Exception as e:
            last_err = e
            if attempt < FETCH_MAX_RETRIES:
                print(f"\n  🔄 第 {attempt}/{FETCH_MAX_RETRIES} 次失败 ({e}), {FETCH_RETRY_DELAY}s 后重试...", end="")
                time.sleep(FETCH_RETRY_DELAY)

    # 全部重试失败 → 降级到本地缓存（上日数据）
    cache_path = _CACHE_DIR / f"{symbol}.json"
    if cache_path.exists():
        try:
            import json
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            row_data = cached.get("row_data", {})
            if row_data:
                print(f"\n  ⚠ 全部重试失败，降级使用缓存数据 ({cache_path.name})")
                # 重建一行 DataFrame
                df = pd.DataFrame([row_data])
                return df
        except Exception as ce:
            print(f"\n  ⚠ 缓存读取也失败: {ce}")

    print(f"\n  ❌ {desc} 获取失败（{FETCH_MAX_RETRIES}次重试+无缓存）: {last_err or 'unknown'}")
    return None


def _save_cache(symbol: str, row_data: dict) -> None:
    """保存单品种原始数据到本地缓存（用于下次降级）。"""
    import json
    cache_path = _CACHE_DIR / f"{symbol}.json"
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"symbol": symbol, "row_data": row_data, "cached_at": datetime.now().isoformat()}, f, ensure_ascii=False)
    except Exception:
        pass  # 缓存写入失败不阻塞主流程


def _compute_confidence(chg_pct: float) -> float:
    """从涨跌幅计算置信度。"""
    raw = CONF_BASE + abs(chg_pct) * CONF_SLOPE
    return round(min(CONF_MAX, max(CONF_MIN, raw)), 2)


def _generate_signal_id(date_str: str, seq: int) -> str:
    """生成唯一 signal_id。"""
    short_uuid = str(uuid.uuid4())[:8]
    return f"commodity_radar-{date_str.replace('-', '')}-{seq:03d}-{short_uuid}"


def _format_expiry(date_str: str) -> str:
    """计算信号到期时间（ISO 8601）。"""
    dt = datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=SIGNAL_TTL_DAYS)
    return dt.strftime("%Y-%m-%dT16:30:00")


def generate_commodity_signals(
    date_str: str,
    *,
    write: bool = False,
) -> list[dict[str, Any]]:
    """获取 5 个核心商品期货行情，生成方向信号。

    Args:
        date_str: 日期，格式 YYYY-MM-DD（用于 signal_id 和 expiry）。
        write: True 时直接写入 upstream_signals.jsonl（需在项目根运行）。

    Returns:
        信号 dict 列表，每个包含完整 signal_id/timestamp/strategy/asset/...
    """
    try:
        import akshare as ak
    except ImportError:
        print("[commodity_radar] AKShare 未安装，跳过", file=sys.stderr)
        return []

    timestamp = f"{date_str}T16:30:00"
    expiry = _format_expiry(date_str)
    signals: list[dict[str, Any]] = []

    print(f"[commodity_radar] 开始采集 {date_str} 商品数据...")

    for seq, (symbol, info) in enumerate(COMMODITIES.items(), 1):
        asset_name = info["asset"]
        print(f"  [{symbol}] {asset_name}...", end=" ")

        df = _fetch_with_retry(ak.futures_main_sina, symbol, asset_name)
        if df is None:
            continue

        if df.empty:
            print("无数据")
            continue

        row = df.iloc[-1]  # 最新一行
        # 保存缓存供下次降级使用
        _save_cache(symbol, row.to_dict())

        # 提取价格字段（兼容中英文列名，futures_main_sina 返回中文列名）
        price = float(row.get("price") or row.get("最新价") or row.get("收盘价") or 0)
        open_price = float(row.get("open") or row.get("开盘价") or 0)
        prev_close = float(row.get("pre_close") or row.get("前收盘价") or 0)
        volume = float(row.get("volume") or row.get("成交量") or 0)

        # 计算涨跌幅：优先用前收盘价，否则用开盘价作日内基准
        if prev_close and prev_close > 0 and price > 0:
            chg_pct = (price - prev_close) / prev_close * 100
        elif open_price and open_price > 0 and price > 0:
            chg_pct = (price - open_price) / open_price * 100
        else:
            print(f"无有效价格 (price={price}, open={open_price}, prev_close={prev_close})")
            continue

        abs_chg = abs(chg_pct)
        print(f"chg={chg_pct:+.2f}% vol={volume:.0f}", end=" ")

        # 阈值判断
        if abs_chg < CHG_THRESHOLD:
            print("→ 未达阈值，跳过")
            continue

        direction = "long" if chg_pct > 0 else "short"
        confidence = _compute_confidence(abs_chg)

        signal = {
            "signal_id": _generate_signal_id(date_str, seq),
            "timestamp": timestamp,
            "strategy": "commodity_radar",
            "asset": asset_name,
            "asset_type": "commodity",
            "direction": direction,
            "confidence": confidence,
            "expiry": expiry,
            "metadata": {
                "symbol": symbol,
                "chg_pct": round(chg_pct, 2),
                "price": round(price, 2),
                "volume": int(volume),
                "exchange": info["exchange"],
            },
        }

        signals.append(signal)
        print(f"→ {direction} conf={confidence:.2f} ✓")

    print(f"[commodity_radar] 完成: {len(signals)}/{len(COMMODITIES)} 个品种产生信号")

    # 可选写入（需在 cycleradar-trader 目录下运行）
    if write and signals:
        try:
            # 确保能导入 upstream_signals
            _PROJECT_ROOT = Path(__file__).resolve().parents[1]
            if str(_PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(_PROJECT_ROOT))
            from core.signals.upstream_signals import write_signal as _ws
            for s in signals:
                _ws(s, normalize=True)
            print(f"[commodity_radar] 已写入 {len(signals)} 条信号到 jsonl")
        except Exception as e:
            print(f"[commodity_radar] 写入失败（不阻塞）: {e}", file=sys.stderr)

    return signals


# ── CLI ──────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="commodity_radar — 商品期货方向信号")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"),
                        help="日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--write", action="store_true",
                        help="写入 upstream_signals.jsonl")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印不写入")
    args = parser.parse_args()

    signals = generate_commodity_signals(
        args.date,
        write=args.write and not args.dry_run,
    )

    if not signals:
        print("无信号产出。")
        sys.exit(0)

    print(f"\n{'='*60}")
    print(f"  commodity_radar · {args.date}")
    print(f"  信号总数: {len(signals)}")
    print(f"{'='*60}")
    for s in signals:
        m = s["metadata"]
        emoji = "📈" if s["direction"] == "long" else "📉"
        print(f"  {emoji} {s['asset']:6s} {s['direction']:5s} "
              f"chg={m['chg_pct']:+.2f}%  conf={s['confidence']:.2f}  "
              f"exp={s['expiry'][:10]}")
