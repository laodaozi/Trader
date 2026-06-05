"""
modules/strategy.py — 自选池诊断 + 策略输出

从 Excel 自选池读取股票 → 逐只诊断 → 打分排序 → 策略分类 → HTML + JSONL 输出

诊断链：
  signals.py（NX 买点 + MA 排列 + Fib 位）
      ×
  weekly direction（周线 MA5/MA10 方向）
      ×
  capital flow（近 3 日主力资金）
      ×
  scanner overlay（11 模型命中作为共振加分项）
      →
  打分 → 分类（进攻/买入/埋伏） → 排名 → HTML 输出

用法：
  python3 modules/strategy.py --date 2026-05-28
  python3 modules/strategy.py --date 2026-05-28 --capital 1000000
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import openpyxl

from modules.mcp import mcp_call

# ── 导入 signals 分析函数 ──────────────────────────────────
from modules.signals import (
    _get_kline as _get_daily_kline,
    _compute_nx,
    _check_ma,
    _compute_fibonacci,
)

# ── 导入 scanner 模型函数（不含 qkxl，需龙虎榜数据）────────
from modules.scanner import (
    _model_zsji,
    _model_htji,
    _model_xsqk,
    _model_zxji,
    _model_bdxy,
    _model_rzq,
    _model_sldb,
    _model_ztht,
    _model_gwzl,
    _model_jxgz,
)

MODEL_CHECKS = [
    ("zsji", "主升狙击", _model_zsji),
    ("htji", "回调狙击", _model_htji),
    ("xsqk", "向上缺口", _model_xsqk),
    ("zxji", "中线狙击", _model_zxji),
    ("bdxy", "波段雄鹰", _model_bdxy),
    ("rzq",  "弱转强",   _model_rzq),
    ("sldb", "缩量地板", _model_sldb),
    ("ztht", "涨停回踩", _model_ztht),
    ("gwzl", "高位整理", _model_gwzl),
    ("jxgz", "均线共振", _model_jxgz),
]

OUTPUT_DIR  = Path(__file__).parent.parent / "output" / "strategy"
DATA_DIR    = Path(__file__).parent.parent / "data"
PANEL_PATH  = Path("/Users/scott/Desktop/一小步/stock panel.xlsx")


# ══════════════════════════════════════════════════════════════
# 1. 数据读取
# ══════════════════════════════════════════════════════════════

def load_panel(path: Optional[Path] = None) -> list[dict]:
    """从 Excel 读取自选池。返回 [{code, name, row}]。"""
    path = Path(path) if path else PANEL_PATH
    wb = openpyxl.load_workbook(str(path))
    ws = wb.active

    stocks = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if len(row) < 6:
            continue
        seq, code, name = row[3], row[4], row[5]
        if code is None:
            continue
        code_str = str(int(code)) if isinstance(code, (int, float)) else str(code).strip()
        name_str = str(name).strip() if name else ""
        stocks.append({"code": code_str, "name": name_str})

    print(f"[load_panel] 读取 {len(stocks)} 只自选股")
    return stocks


# ══════════════════════════════════════════════════════════════
# 2. 周线方向
# ══════════════════════════════════════════════════════════════

def _ma(values: list[float], n: int) -> list[Optional[float]]:
    result: list[Optional[float]] = []
    for i in range(len(values)):
        result.append(None if i < n - 1 else sum(values[i - n + 1: i + 1]) / n)
    return result


def _get_weekly_kline(code: str, end_date: str, bars: int = 26) -> list[dict]:
    """周K，取最近 bars 根（约半年）。"""
    start = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=bars * 7 + 30)).strftime("%Y-%m-%d")
    data = mcp_call("market_quote", "get_kline", {
        "keyword":            code,
        "start_date":         start,
        "end_date":           end_date,
        "kline_type":         2,
        "reinstatement_type": 2,
    })
    raw = data if isinstance(data, list) else data.get("list", [])
    result = sorted([
        {"date": b.get("trade_date", ""), "close": float(b.get("close_price") or b.get("close") or 0)}
        for b in raw
    ], key=lambda x: x["date"])
    return result[-bars:]


def _weekly_direction(weekly_bars: list[dict]) -> str:
    if len(weekly_bars) < 10:
        return "数据不足"
    closes = [b["close"] for b in weekly_bars]
    ma5  = _ma(closes, 5)[-1]
    ma10 = _ma(closes, 10)[-1]
    if ma5 is None or ma10 is None:
        return "数据不足"
    if ma5 > ma10 * 1.005:
        return "上升"
    if ma5 < ma10 * 0.995:
        return "下降"
    return "震荡"


# ══════════════════════════════════════════════════════════════
# 3. 资金流向
# ══════════════════════════════════════════════════════════════

def _get_capital_flow(code: str, end_date: str) -> dict:
    """近3日主力资金净流入（万元）。"""
    start = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=10)).strftime("%Y-%m-%d")
    data = mcp_call("market_quote", "get_net_flow_list", {
        "keyword":    code,
        "start_date": start,
        "end_date":   end_date,
    })
    rows = data if isinstance(data, list) else data.get("list", [])
    # 字段名查实: major_net_flow_in, 日期字段: date
    rows = sorted(rows, key=lambda x: x.get("date", ""), reverse=True)[:3]
    if not rows:
        return {"net_3d": 0.0, "direction": "无数据"}
    total = 0.0
    for r in rows:
        val = r.get("major_net_flow_in", 0)  # ← 修正字段名
        total += float(val)
    direction = "净流入" if total > 0 else "净流出" if total < 0 else "中性"
    return {"net_3d": total, "direction": direction}


# ══════════════════════════════════════════════════════════════
# 4. 单只股票分析
# ══════════════════════════════════════════════════════════════

def analyze_one(code: str, name: str, date: str) -> dict:
    """
    对单只股票做完整诊断，返回所有字段的 dict。

    包含：NX 信号 / MA 排列 / Fib / 周线方向 / 资金流 / scanner 模型命中
    """
    result = {
        "code": code, "name": name, "date": date,
        "nx": "neutral", "ma_align": "neutral", "ma_touch": None,
        "fib_zone": "unknown", "weekly_dir": "无数据",
        "capital_dir": "无数据", "capital_net3d": 0.0,
        "close": 0.0,
        "entry_low": 0.0, "entry_high": 0.0,
        "stop_loss": 0.0, "take_profit": [],
        "rr": None, "model_hits": [], "signal_basis": [],
        "score": 0, "strategy": "观望", "signal_type": "—",
        "is_st": code.startswith("*ST") or "ST" in name,
        "error": None,
    }

    try:
        # ── 日线 K 线 ──
        stock_name, bars = _get_daily_kline(code, date, days=90)
        name = stock_name or name
        result["name"] = name

        # 补算涨跌幅（Scanner 模型依赖 chg 字段）
        for i, b in enumerate(bars):
            if i == 0:
                b["chg"] = 0.0
            else:
                prev = bars[i - 1]["close"]
                b["chg"] = (b["close"] - prev) / max(prev, 0.01)

        if len(bars) < 15:
            result["error"] = "K线数据不足"
            return result

        close = bars[-1]["close"]
        result["close"] = close

        # ── NX / MA / Fib ──
        nx  = _compute_nx(bars)
        ma  = _check_ma(bars)
        fib = _compute_fibonacci(bars, lookback=60)

        result["nx"]       = nx
        result["ma_align"] = ma["alignment"]
        result["ma_touch"] = ma.get("touch_zone")
        result["fib_zone"] = fib["current_zone"]

        # ── 信号收集 ──
        if nx == "buy":
            result["signal_basis"].append("NX买点")
        elif nx == "rising":
            result["signal_basis"].append("NX趋势上升")
        elif nx == "sell":
            result["signal_basis"].append("NX卖点")

        if ma["alignment"] in ("bull_full", "bull_partial"):
            result["signal_basis"].append("MA多头排列")
        if ma.get("touch_zone"):
            result["signal_basis"].append(f"回踩{ma['touch_zone'].upper()}")

        if fib["current_zone"] == "support" and fib["supports"]:
            result["signal_basis"].append(f"Fib支撑带({fib['supports'][1]:.2f})")

        # ── 入场 / 止损 / 目标 ──
        entry_low, entry_high = close * 0.98, close * 1.01
        if ma.get("ma10") and ma["touch_zone"] == "ma10":
            entry_low  = round(ma["ma10"] * 0.98, 2)
            entry_high = round(ma["ma10"] * 1.02, 2)
        elif ma.get("ma20") and ma["touch_zone"] == "ma20":
            entry_low  = round(ma["ma20"] * 0.98, 2)
            entry_high = round(ma["ma20"] * 1.02, 2)
        elif fib["current_zone"] == "support" and fib["supports"]:
            ref = fib["supports"][1]  # 0.5 回撤支撑
            entry_low  = round(ref * 0.985, 2)
            entry_high = round(ref * 1.015, 2)

        entry_mid = round((entry_low + entry_high) / 2, 2)

        # 止损：只用入场下方的 MA 做参考，否则用入场×0.95
        stop_candidates = [round(entry_mid * 0.95, 2)]
        if ma.get("ma10") and ma["ma10"] < entry_mid:
            stop_candidates.append(round(ma["ma10"] * 0.97, 2))
        if ma.get("ma20") and ma["ma20"] < entry_mid:
            stop_candidates.append(round(ma["ma20"] * 0.97, 2))
        # 取最保守（最高）的止损价
        stop_loss = round(max(stop_candidates), 2)

        # 目标：用 Fib 支撑位作阻力目标，不能高出入场 15% 以上
        if fib["supports"]:
            if fib["current_zone"] == "below_support":
                # 在支撑下方 → 目标就是支撑带本身（先回到支撑）
                tp_candidates = [s for s in fib["supports"] if s > entry_mid]
                tp = tp_candidates[:2] if tp_candidates else [round(close * 1.05, 2), round(close * 1.10, 2)]
            elif fib["current_zone"] == "support":
                # 在支撑带内 → 目标为上一层支撑或 swing_high
                tp_candidates = [s for s in fib["supports"] if s > entry_mid]
                if tp_candidates:
                    tp = [tp_candidates[-1]]  # 最近阻力
                    if fib["swing_high"] > tp[0]:
                        tp.append(round(fib["swing_high"], 2))
                    else:
                        tp.append(round(tp[0] * 1.05, 2))
                else:
                    tp = [round(close * 1.05, 2), round(close * 1.10, 2)]
            else:
                # above_support → 用简单百分比
                tp = [round(close * 1.05, 2), round(close * 1.10, 2)]
        else:
            tp = [round(close * 1.05, 2), round(close * 1.10, 2)]

        # 目标上限：最高不超过入场中位 × 1.15
        tp = [min(t, round(entry_mid * 1.15, 2)) for t in tp[:2]]

        result["entry_low"]  = entry_low
        result["entry_high"] = entry_high
        result["stop_loss"]  = stop_loss
        result["take_profit"] = tp

        # ── 周线方向 ──
        try:
            weekly_bars = _get_weekly_kline(code, date)
            result["weekly_dir"] = _weekly_direction(weekly_bars)
        except Exception as e:
            result["weekly_dir"] = f"获取失败:{e}"

        # ── 资金流向 ──
        try:
            flow = _get_capital_flow(code, date)
            result["capital_dir"]   = flow["direction"]
            result["capital_net3d"] = flow["net_3d"]
        except Exception as e:
            result["capital_dir"] = f"获取失败:{e}"

        # ── Scanner 模型命中 ──
        for model_key, model_name, model_fn in MODEL_CHECKS:
            try:
                if model_fn(code, name, bars):
                    result["model_hits"].append(model_name)
            except Exception:
                pass  # 单个模型失败不影响整体

        # ── R:R ──
        mid = (entry_low + entry_high) / 2
        risk = mid - stop_loss
        if risk > 0 and tp:
            result["rr"] = round((tp[0] - mid) / risk, 2)

    except Exception as e:
        result["error"] = str(e)[:100]

    return result


# ══════════════════════════════════════════════════════════════
# 5. 打分 + 分类
# ══════════════════════════════════════════════════════════════

def score_and_classify(r: dict) -> dict:
    """对单只股票的结果打分并分类。"""
    score = 0

    # NX 信号（核心）
    if r["nx"] == "buy":
        score += 30
    elif r["nx"] == "rising":
        score += 10

    # MA 排列
    if r["ma_align"] == "bull_full":
        score += 20
    elif r["ma_align"] == "bull_partial":
        score += 10

    # Fib 位置
    if r["fib_zone"] == "support":
        score += 15

    # 主力资金
    if r["capital_dir"] == "净流入":
        score += 15

    # R:R
    rr = r.get("rr")
    if rr is not None and rr >= 2.0:
        score += 15

    # Scanner 模型共振（每个 +10）
    score += len(r.get("model_hits", [])) * 10

    # 周线方向
    if r["weekly_dir"] == "上升":
        score += 10

    # ST 惩罚
    if r["is_st"]:
        score -= 50

    r["score"] = max(0, score)

    # ── 策略分类 ──
    nx_ok   = r["nx"] == "buy"
    ma_ok   = r["ma_align"] in ("bull_full", "bull_partial")
    fib_ok  = r["fib_zone"] == "support"
    cap_ok  = r["capital_dir"] == "净流入"
    rr_ok   = r.get("rr") is not None and r["rr"] >= 2.0

    if score >= 55 and nx_ok and ma_ok:
        r["signal_type"] = "🔥 进攻"
        r["strategy"]    = "趋势" if r["weekly_dir"] == "上升" else "波段"
    elif score >= 35 and (nx_ok or len(r.get("model_hits", [])) > 0):
        if nx_ok:
            r["signal_type"] = "✅ 买入"
        else:
            r["signal_type"] = "✅ 买入(模型)"
        r["strategy"] = "波段"
    elif fib_ok and rr_ok:
        r["signal_type"] = "🕐 埋伏"
        r["strategy"]    = "低吸"
    else:
        r["signal_type"] = "—"
        r["strategy"]    = "观望"

    return r


# ══════════════════════════════════════════════════════════════
# 6. HTML 生成
# ══════════════════════════════════════════════════════════════

def _html_page(results: list[dict], date: str, market_temp: int = 0,
               recom_position: int = 0) -> str:
    """生成完整 HTML 策略页面。"""
    from html import escape

    # 颜色辅助
    def color_nx(nx: str) -> str:
        return {"buy": "#22c55e", "rising": "#3b82f6", "sell": "#ef4444"}.get(nx, "#6b7280")

    def color_score(s: int) -> str:
        if s >= 55: return "#22c55e"
        if s >= 35: return "#3b82f6"
        return "#6b7280"

    rows_html = []
    for i, r in enumerate(results):
        rank = i + 1
        star = "⭐" if rank <= 5 else ""
        name_display = f"{star} {r['name']}"
        if r["is_st"]:
            name_display += " ⚠️ST"

        code = r["code"]
        signal = r["signal_type"]
        stra  = r["strategy"]
        score = r["score"]
        nx    = r["nx"]

        # 入场 / 止损 / 目标
        if r["entry_low"] > 0:
            entry_str = f"{r['entry_low']:.2f}"
            stop_str  = f"{r['stop_loss']:.2f}"
        else:
            entry_str = "—"
            stop_str  = "—"

        tp_str = f"{r['take_profit'][0]:.2f}" if r["take_profit"] else "—"

        # 信号标签
        sig_tags = " ".join(r["signal_basis"]) if r["signal_basis"] else "—"
        model_tags = " ".join(r["model_hits"]) if r["model_hits"] else ""

        # 行样式
        row_style = ""
        if signal.startswith("🔥"):
            row_style = "background:#fef2f2"
        elif signal.startswith("✅"):
            row_style = "background:#eff6ff"

        nocolor = "color:#6b7280"
        rows_html.append(f"""<tr style='{row_style}'>
      <td style='padding:5px 8px;text-align:center;font-size:12px;border-top:1px solid #f1f5f9'>{rank}</td>
      <td style='padding:5px 8px;text-align:left;font-size:12px;border-top:1px solid #f1f5f9;white-space:nowrap'><code style='font-size:11px;{nocolor}'>{code}</code></td>
      <td style='padding:5px 8px;text-align:left;font-size:12px;border-top:1px solid #f1f5f9;white-space:nowrap'>{escape(name_display)}</td>
      <td style='padding:5px 8px;text-align:center;font-size:12px;border-top:1px solid #f1f5f9'><span style='color:{color_nx(nx)};font-weight:600'>{nx}</span></td>
      <td style='padding:5px 8px;text-align:center;font-size:12px;border-top:1px solid #f1f5f9'><b>{signal}</b></td>
      <td style='padding:5px 8px;text-align:center;font-size:12px;border-top:1px solid #f1f5f9'>{stra}</td>
      <td style='padding:5px 8px;text-align:right;font-size:12px;border-top:1px solid #f1f5f9;font-weight:600;color:{color_score(score)}'>{score}</td>
      <td style='padding:5px 8px;text-align:right;font-size:12px;border-top:1px solid #f1f5f9'>{entry_str}</td>
      <td style='padding:5px 8px;text-align:right;font-size:12px;border-top:1px solid #f1f5f9'>{stop_str}</td>
      <td style='padding:5px 8px;text-align:right;font-size:12px;border-top:1px solid #f1f5f9'>{tp_str}</td>
      <td style='padding:5px 8px;text-align:left;font-size:11px;border-top:1px solid #f1f5f9;{nocolor}'>{escape(sig_tags)}</td>
      <td style='padding:5px 8px;text-align:left;font-size:11px;border-top:1px solid #f1f5f9;color:#7c3aed'>{escape(model_tags)}</td>
    </tr>""")

    # 统计
    attack = sum(1 for r in results if r["signal_type"].startswith("🔥"))
    buy    = sum(1 for r in results if r["signal_type"].startswith("✅"))
    ambush = sum(1 for r in results if r["signal_type"].startswith("🕐"))
    errors = sum(1 for r in results if r.get("error"))

    temp_label = f"{market_temp}°C" if market_temp else "—"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>自选池策略 · {date}</title>
<style>
  body {{ font-family: -apple-system, "Microsoft YaHei", sans-serif; margin:0; padding:20px; background:#fafbfc; color:#1e293b; }}
  .container {{ max-width:1400px; margin:0 auto; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .subtitle {{ color:#6b7280; font-size:13px; margin-bottom:16px; }}
  .summary {{ display:flex; gap:20px; margin-bottom:16px; flex-wrap:wrap; }}
  .stat {{ background:#fff; border-radius:8px; padding:10px 16px; box-shadow:0 1px 3px rgba(0,0,0,.06); }}
  .stat .num {{ font-size:24px; font-weight:700; }}
  .stat .label {{ font-size:11px; color:#6b7280; }}
  .red {{ color:#ef4444; }} .green {{ color:#22c55e; }} .blue {{ color:#3b82f6; }} .amber {{ color:#f59e0b; }}
  table {{ width:100%; border-collapse:collapse; background:#fff; border-radius:8px; overflow:hidden; box-shadow:0 1px 3px rgba(0,0,0,.06); }}
  th {{ padding:8px 6px; text-align:left; background:#f8fafc; font-size:11px; color:#6b7280; font-weight:600; position:sticky; top:0; }}
  td {{ font-size:12px; }}
  tr:hover {{ background:#f8fafc !important; }}
  .disclaimer {{ margin-top:20px; font-size:11px; color:#9ca3af; }}
</style>
</head>
<body>
<div class="container">
<h1>自选池策略</h1>
<div class="subtitle">{date} · 开盘前 · 市场温度 {temp_label}</div>

<div class="summary">
  <div class="stat"><div class="num red">{attack}</div><div class="label">🔥 进攻</div></div>
  <div class="stat"><div class="num blue">{buy}</div><div class="label">✅ 买入</div></div>
  <div class="stat"><div class="num amber">{ambush}</div><div class="label">🕐 埋伏</div></div>
  <div class="stat"><div class="num" style="color:#6b7280">{len(results) - attack - buy - ambush}</div><div class="label">观望</div></div>
  <div class="stat"><div class="num" style="color:#ef4444">{errors}</div><div class="label">⚠️ 异常</div></div>
</div>

<table>
<tr>
  <th style='width:36px;text-align:center'>#</th>
  <th style='width:70px'>代码</th>
  <th style='width:110px'>名称</th>
  <th style='width:50px;text-align:center'>NX</th>
  <th style='width:70px;text-align:center'>信号</th>
  <th style='width:48px;text-align:center'>策略</th>
  <th style='width:40px;text-align:right'>分</th>
  <th style='width:60px;text-align:right'>入场</th>
  <th style='width:60px;text-align:right'>止损</th>
  <th style='width:60px;text-align:right'>目标</th>
  <th style='width:160px'>信号详情</th>
  <th style='width:120px'>模型共振</th>
</tr>
{chr(10).join(rows_html)}
</table>
<div class="disclaimer">
⚠️ 仅供个人辅助决策，不构成投资建议。历史信号不代表未来表现。NX 买点基于典型价 RSI(6) 转折，需结合盘面验证。
</div>
</div>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════
# 7. JSONL 日志
# ══════════════════════════════════════════════════════════════

def _log_jsonl(results: list[dict], date: str):
    """追加结构化日志到 data/strategy_log.jsonl。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_path = DATA_DIR / "strategy_log.jsonl"
    with open(log_path, "a", encoding="utf-8") as f:
        for r in results:
            record = {
                "date":         date,
                "code":         r["code"],
                "name":         r["name"],
                "nx":           r["nx"],
                "ma_align":     r["ma_align"],
                "fib_zone":     r["fib_zone"],
                "weekly_dir":   r["weekly_dir"],
                "capital_dir":  r["capital_dir"],
                "rr":           r.get("rr"),
                "model_hits":   r.get("model_hits", []),
                "signal_type":  r["signal_type"],
                "strategy":     r["strategy"],
                "score":        r["score"],
                "entry_low":    r["entry_low"],
                "entry_high":   r["entry_high"],
                "stop_loss":    r["stop_loss"],
                "take_profit":  r["take_profit"],
                "error":        r.get("error"),
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"[log] 写入 {len(results)} 条 → {log_path}")


# ══════════════════════════════════════════════════════════════
# 8. 主入口
# ══════════════════════════════════════════════════════════════

def run(date: str, capital: float = 0, verbose: bool = True,
        market_temp: int = 0, recom_position: int = 0) -> list[dict]:
    """
    运行完整策略流水线。

    Parameters
    ----------
    date    : 分析日期
    capital : 总资金（保留参数，当前不做仓位计算）
    verbose : 是否打印进度

    Returns
    -------
    list[dict] : 排名后的策略结果列表
    """
    # 1. 加载自选池
    stocks = load_panel()

    # 2. 逐只分析
    results = []
    total = len(stocks)
    for i, s in enumerate(stocks):
        code = s["code"]
        name = s["name"]
        if verbose:
            print(f"  [{i+1}/{total}] {code} {name} ...", end=" ", flush=True)

        r = analyze_one(code, name, date)
        if r.get("error") and verbose:
            print(f"⚠️ {r['error']}")
        elif verbose:
            sig = r["signal_basis"]
            sig_str = " | ".join(sig[:3]) if sig else "—"
            print(f"NX={r['nx']} 周={r['weekly_dir']} 资金={r['capital_dir']} 模型={len(r.get('model_hits',[]))} → {sig_str}")

        results.append(r)

    # 3. 打分 + 分类
    for r in results:
        score_and_classify(r)

    # 4. 按 score 降序排列
    results.sort(key=lambda r: r["score"], reverse=True)

    # 5. 生成 HTML
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    html_path = OUTPUT_DIR / f"strategy_{date}.html"
    html = _html_page(results, date, market_temp=market_temp, recom_position=recom_position)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    # latest.html symlink equivalent
    latest_path = OUTPUT_DIR / "latest.html"
    with open(latest_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[output] HTML → {html_path}")
    print(f"[output] HTML → {latest_path}")

    # 6. JSONL 日志
    _log_jsonl(results, date)

    # 7. 摘要
    attack = sum(1 for r in results if r["signal_type"].startswith("🔥"))
    buy    = sum(1 for r in results if r["signal_type"].startswith("✅"))
    ambush = sum(1 for r in results if r["signal_type"].startswith("🕐"))
    print(f"\n  摘要: 🔥进攻{attack}  ✅买入{buy}  🕐埋伏{ambush}  观望{total-attack-buy-ambush}")
    top5 = results[:5]
    print(f"  Top 5:")
    for r in top5:
        print(f"    {r['code']} {r['name']}  得分{r['score']}  {r['signal_type']}  {r['strategy']}")

    return results


# ── 命令行 ────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    # 确保能在外层目录运行也能直接 python3 modules/strategy.py
    _root = Path(__file__).parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    parser = argparse.ArgumentParser(description="自选池策略输出")
    parser.add_argument("--date",    default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--capital", type=float, default=0, help="总资金（元）")
    parser.add_argument("--quiet",   action="store_true", help="安静模式")
    args = parser.parse_args()

    run(args.date, capital=args.capital, verbose=not args.quiet)
