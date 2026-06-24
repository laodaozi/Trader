#!/usr/bin/env python3.9
"""
generate_backtest_html.py — 从 trader_strategy.jsonl 生成 /m 回测 tab HTML 报告
输出: data/backtest_reports/strategy_YYYY-MM-DD.html + latest.html
"""
import json
import os
import sys
from datetime import datetime

DATA_DIR = os.environ.get("CYCLERADAR_DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
REPORTS_DIR = os.path.join(DATA_DIR, "backtest_reports")
TS_PATH = os.path.join(DATA_DIR, "trader_strategy.jsonl")
TIMING_PATH = os.path.join(DATA_DIR, "timing_history.json")
UPSTREAM_PATH = os.path.join(DATA_DIR, "upstream_signals.jsonl")

os.makedirs(REPORTS_DIR, exist_ok=True)


def load_strategies():
    rows = []
    with open(TS_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_upstream_lookup():
    """Build code -> {nx, fib_zone, ma_align} from stock_agent signals"""
    lookup = {}
    try:
        with open(UPSTREAM_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                s = json.loads(line)
                if s.get("strategy") != "stock_agent":
                    continue
                meta = s.get("metadata", {})
                code = meta.get("stock_code") or s.get("symbol", "")
                if code:
                    lookup[code] = {
                        "nx": meta.get("nx", "unknown"),
                        "ma_align": meta.get("ma_align", "unknown"),
                        "fib_zone": meta.get("fib_zone", "unknown"),
                        "signal_details": meta.get("signal_details", ""),
                    }
    except FileNotFoundError:
        pass
    return lookup


def load_timing():
    """Get latest market temperature"""
    try:
        with open(TIMING_PATH) as f:
            d = json.load(f)
            history = d.get("history", [])
            if history:
                latest = sorted(history, key=lambda r: r.get("date", ""), reverse=True)[0]
                return latest.get("temperature", "--"), latest.get("phase", "--")
    except:
        pass
    return "--", "--"


def nx_label(nx_val):
    if nx_val == "buy":
        return '<span style="color:#22c55e">✔</span>'
    elif nx_val == "sell":
        return '<span style="color:#ef4444">✘</span>'
    elif nx_val == "neutral":
        return '<span style="color:#f59e0b">—</span>'
    return '<span style="color:#9ca3af">?</span>'


def signal_color(signal_type):
    if "进攻" in signal_type:
        return "red"
    elif "买入" in signal_type:
        return "blue"
    elif "埋伏" in signal_type:
        return "amber"
    return "#6b7280"


def strategy_label(code):
    """Derive strategy type from code/behavior"""
    return "趋势"


def model_resonance(hits):
    """Extract model resonance tags from hits array"""
    tags = []
    for h in hits:
        if "雄鹰" in h:
            tags.append('<span style="color:#7c3aed">波段雄鹰</span>')
        elif "狙击" in h:
            tags.append('<span style="color:#7c3aed">回调狙击</span>')
        elif "共振" in h:
            tags.append('<span style="color:#3b82f6">行业共振</span>')
    return " ".join(tags)


def generate_html(rows, date_str, temperature, phase):
    upstream = load_upstream_lookup()

    # Sort by score descending
    rows = sorted(rows, key=lambda r: r.get("score", 0), reverse=True)

    # Count signal types
    attack = sum(1 for r in rows if "进攻" in r.get("signal_type", ""))
    buy = sum(1 for r in rows if "买入" in r.get("signal_type", ""))
    ambush = sum(1 for r in rows if "埋伏" in r.get("signal_type", ""))
    watch = sum(1 for r in rows if "观望" in r.get("signal_type", ""))
    errors = sum(1 for r in rows if r.get("signal_type", "") == "⚠️ 异常")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>自选池策略 · {date_str}</title>
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
<div class="subtitle">{date_str} · 开盘前 · 市场温度 {temperature}° · {phase}</div>

<div class="summary">
  <div class="stat"><div class="num red">{attack}</div><div class="label">🔥 进攻</div></div>
  <div class="stat"><div class="num blue">{buy}</div><div class="label">✅ 买入</div></div>
  <div class="stat"><div class="num amber">{ambush}</div><div class="label">🕐 埋伏</div></div>
  <div class="stat"><div class="num" style="color:#6b7280">{watch}</div><div class="label">观望</div></div>
  <div class="stat"><div class="num" style="color:#ef4444">{errors}</div><div class="label">⚠️ 异常</div></div>
</div>

<table>
<thead>
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
</thead>
<tbody>
"""

    for idx, r in enumerate(rows):
        code = r.get("code", "?")
        name = r.get("name", "?")
        # NX from upstream (metadata), fallback to trader_strategy
        u = upstream.get(code, {})
        nx_val = u.get("nx") or r.get("nx", "unknown")
        ma = u.get("ma_align") or r.get("ma_align", "unknown")
        fib = u.get("fib_zone") or r.get("fib_zone", "unknown")
        sig_details = u.get("signal_details", "")
        
        signal_type = r.get("signal_type", "?").replace("✅ ", "").replace("⚠️ ", "")
        strategy_type = strategy_label(code)
        score = r.get("score", 0)
        entry = r.get("entry_high") or r.get("entry_low", 0)
        stop = r.get("stop_loss", 0)
        target_list = r.get("take_profit", [])
        target = target_list[0] if target_list else 0
        hits = r.get("model_hits", [])
        resonance = model_resonance(hits)

        # Build signal details string
        detail_parts = []
        if ma and ma != "unknown":
            detail_parts.append(ma)
        if nx_val and nx_val not in ("unknown", "neutral"):
            detail_parts.append("NX" + nx_val)
        if fib and fib != "unknown":
            detail_parts.append(fib)
        if sig_details:
            detail_parts.append(sig_details)
        details_str = " ".join(detail_parts) if detail_parts else "--"

        sc = signal_color(r.get("signal_type", ""))
        
        entry_str = f"{entry:.2f}" if isinstance(entry, (int, float)) and entry else str(entry)
        stop_str = f"{stop:.2f}" if isinstance(stop, (int, float)) and stop else str(stop)
        target_str = f"{target:.2f}" if isinstance(target, (int, float)) and target else str(target)

        html += f"""<tr>
  <td style='padding:5px 8px;text-align:center;font-size:12px;border-top:1px solid #f1f5f9'>{idx+1}</td>
  <td style='padding:5px 8px;text-align:left;font-size:12px;border-top:1px solid #f1f5f9;white-space:nowrap'>{code}</td>
  <td style='padding:5px 8px;text-align:left;font-size:12px;border-top:1px solid #f1f5f9;white-space:nowrap'>⭐ {name}</td>
  <td style='padding:5px 8px;text-align:center;font-size:12px;border-top:1px solid #f1f5f9'>{nx_label(nx_val)}</td>
  <td style='padding:5px 8px;text-align:center;font-size:12px;border-top:1px solid #f1f5f9;font-weight:600;color:{sc}'>{signal_type}</td>
  <td style='padding:5px 8px;text-align:center;font-size:12px;border-top:1px solid #f1f5f9'>{strategy_type}</td>
  <td style='padding:5px 8px;text-align:right;font-size:12px;border-top:1px solid #f1f5f9;font-weight:600;color:#22c55e'>{score}</td>
  <td style='padding:5px 8px;text-align:right;font-size:12px;border-top:1px solid #f1f5f9'>{entry_str}</td>
  <td style='padding:5px 8px;text-align:right;font-size:12px;border-top:1px solid #f1f5f9'>{stop_str}</td>
  <td style='padding:5px 8px;text-align:right;font-size:12px;border-top:1px solid #f1f5f9'>{target_str}</td>
  <td style='padding:5px 8px;text-align:left;font-size:11px;border-top:1px solid #f1f5f9;color:#6b7280'>{details_str}</td>
  <td style='padding:5px 8px;text-align:left;font-size:11px;border-top:1px solid #f1f5f9'>{resonance if resonance else "--"}</td>
</tr>
"""

    html += f"""</tbody>
</table>

<div class="disclaimer">
  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · 数据来源: CycleRadar stock_agent · 仅供参考，不构成投资建议
</div>
</div>
</body>
</html>"""

    return html


def main():
    if not os.path.exists(TS_PATH):
        print(f"ERROR: {TS_PATH} not found", file=sys.stderr)
        sys.exit(1)

    rows = load_strategies()
    if not rows:
        print("WARNING: no strategy rows found", file=sys.stderr)
        sys.exit(1)

    date_str = max((r.get("date", "") for r in rows), default=datetime.now().strftime("%Y-%m-%d"))
    temperature, phase = load_timing()

    html = generate_html(rows, date_str, temperature, phase)

    # Write dated report
    dated_path = os.path.join(REPORTS_DIR, f"strategy_{date_str}.html")
    with open(dated_path, "w") as f:
        f.write(html)
    print(f"Wrote: {dated_path} ({len(html)} bytes)")

    # Update latest.html
    latest_path = os.path.join(REPORTS_DIR, "latest.html")
    with open(latest_path, "w") as f:
        f.write(html)
    print(f"Wrote: {latest_path} ({len(html)} bytes)")

    print(f"Stats: {len(rows)} stocks, date={date_str}, temp={temperature}°, phase={phase}")


if __name__ == "__main__":
    main()
