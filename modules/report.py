"""
modules/report.py — HTML 日报生成器

generate(date, timing, pool_summary, sig_results, scan_result, health, holdings, prices)
  → output/daily/trader_YYYY-MM-DD.html

自包含 HTML，无外部依赖，支持手机阅读。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

OUTPUT_DIR = Path(__file__).parent.parent / "output" / "daily"

# ── 颜色常量 ──────────────────────────────────────────────
_LC_COLOR  = {"生·进入": "#f59e0b", "住·持有": "#22c55e", "坏·注意": "#f97316", "灭·出局": "#ef4444", "未知": "#9ca3af"}
_SIG_COLOR = {"可介入": "#22c55e", "观望": "#f59e0b", "止损警戒": "#ef4444", "数据不足": "#9ca3af"}
_PH_COLOR  = {"冲刺": "#22c55e", "反弹": "#86efac", "试盘": "#fde68a",
              "回调": "#fcd34d", "震荡休整": "#d1d5db", "反抽": "#d1d5db",
              "即将见顶": "#f97316", "警惕下杀": "#ef4444", "下跌": "#ef4444", "探底": "#ef4444"}
_STATE_COLOR = {"良性": "#22c55e", "WARNING": "#f59e0b", "EXIT": "#ef4444"}


def _temp_bar(temp: float) -> str:
    pct = min(max(temp, 0), 100)
    color = "#22c55e" if pct < 50 else "#f59e0b" if pct < 75 else "#ef4444"
    return f"""
    <div style="background:#e5e7eb;border-radius:8px;height:18px;width:100%;margin:6px 0">
      <div style="background:{color};width:{pct}%;height:100%;border-radius:8px;
                  display:flex;align-items:center;justify-content:flex-end;padding-right:6px;
                  font-size:11px;font-weight:700;color:#fff;box-sizing:border-box">
        {pct:.0f}°C
      </div>
    </div>"""


def _card(title: str, body: str, accent: str = "#3b82f6") -> str:
    return f"""
  <div style="background:#fff;border-radius:12px;padding:18px 20px;margin-bottom:16px;
              box-shadow:0 1px 4px rgba(0,0,0,.08);border-left:4px solid {accent}">
    <div style="font-size:13px;font-weight:700;color:#6b7280;margin-bottom:10px;letter-spacing:.5px">{title}</div>
    {body}
  </div>"""


def _badge(text: str, color: str = "#3b82f6", bg: str = "") -> str:
    bg = bg or color + "22"
    return f'<span style="background:{bg};color:{color};padding:2px 8px;border-radius:4px;font-size:12px;font-weight:600">{text}</span>'


def _row(*cells, header: bool = False) -> str:
    tag = "th" if header else "td"
    style = "padding:6px 10px;text-align:left;" + ("background:#f8fafc;font-size:12px;color:#6b7280;font-weight:600" if header else "font-size:13px;border-top:1px solid #f1f5f9")
    return "<tr>" + "".join(f"<{tag} style='{style}'>{c}</{tag}>" for c in cells) + "</tr>"


def _table(rows: str) -> str:
    return f'<table style="width:100%;border-collapse:collapse">{rows}</table>'


# ── 各板块 HTML ──────────────────────────────────────────

def _timing_html(t: dict) -> str:
    phase = t["phase"]
    ph_color = _PH_COLOR.get(phase, "#9ca3af")
    pos = int(t["recommended_position"] * 100)
    pos_min = int(t["position_min"] * 100)
    pos_max = int(t["position_max"] * 100)
    body = f"""
    {_temp_bar(t['temperature'])}
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin:8px 0">
      {_badge(phase, ph_color)}
      {_badge(t['index_direction'], "#6b7280")}
      {_badge(f"建议仓位 {pos}%", "#3b82f6")}
    </div>
    <div style="color:#6b7280;font-size:13px;margin-top:6px">仓位区间：{pos_min}% ~ {pos_max}%</div>
    <div style="color:#374151;font-size:13px;margin-top:4px">{t['message']}</div>"""
    return _card(f"📊 市场择时 · {t['date']}", body, ph_color)


def _pool_html(pool_summary: dict) -> str:
    if not pool_summary or not pool_summary["total"]:
        return _card("🗂 观察票池", "<div style='color:#9ca3af;font-size:13px'>票池为空</div>", "#9ca3af")

    rows = _row("代码", "名称", "阶段", "加入理由", "更新日", header=True)
    for s in pool_summary["stocks"]:
        lc = s.get("lifecycle", "未知")
        color = _LC_COLOR.get(lc, "#9ca3af")
        rows += _row(
            f"<code style='font-size:12px'>{s['code']}</code>",
            s["name"],
            _badge(lc, color),
            s.get("add_reason", ""),
            s.get("lifecycle_updated", ""),
        )
    body = _table(rows)
    return _card(f"🗂 观察票池 · {pool_summary['total']} 只", body, "#8b5cf6")


def _signals_html(sig_results: list[dict]) -> str:
    if not sig_results:
        return _card("📈 买卖点信号", "<div style='color:#9ca3af;font-size:13px'>无信号</div>", "#9ca3af")

    rows = _row("代码", "名称", "状态", "介入区间", "止损", "目标1", "信号", header=True)
    for r in sig_results:
        color = _SIG_COLOR.get(r["status"], "#9ca3af")
        entry = r.get("entry_zone", [0, 0])
        tp = r.get("take_profit", [])
        sigs = " · ".join(r.get("signal_basis", [])) or "—"
        rows += _row(
            f"<code style='font-size:12px'>{r['code']}</code>",
            r["name"],
            _badge(r["status"], color),
            f"{entry[0]:.2f}~{entry[1]:.2f}",
            f"<span style='color:#ef4444'>{r.get('stop_loss',0):.2f}</span>",
            f"<span style='color:#22c55e'>{tp[0]:.2f}</span>" if tp else "—",
            f"<span style='color:#6b7280;font-size:12px'>{sigs}</span>",
        )
    return _card(f"📈 买卖点信号 · {len(sig_results)} 只", _table(rows), "#10b981")


def _scan_html(scan_result: dict) -> str:
    if not scan_result:
        return ""
    total = scan_result.get("total_hits", 0)
    hits  = scan_result.get("hits", {})

    sections = ""
    for model_name, stocks in hits.items():
        if not stocks:
            continue
        rows = _row("代码", "名称", "信号", header=True)
        for s in (stocks[:8]):
            reasons = s.get("reasons", [])
            reasons_str = " · ".join(reasons[:3])
            rows += _row(
                f"<code style='font-size:12px'>{s['code']}</code>",
                s["name"],
                f"<span style='color:#6b7280;font-size:12px'>{reasons_str}</span>",
            )
        if len(stocks) > 8:
            rows += f"<tr><td colspan='3' style='padding:4px 10px;font-size:12px;color:#9ca3af'>… 另有 {len(stocks)-8} 只</td></tr>"
        sections += f"""
        <div style="margin-bottom:14px">
          <div style="font-size:12px;font-weight:700;color:#374151;margin-bottom:6px">
            {model_name}  <span style="color:#9ca3af;font-weight:400">({len(stocks)} 只)</span>
          </div>
          {_table(rows)}
        </div>"""

    body = f"""
    <div style="color:#6b7280;font-size:13px;margin-bottom:12px">
      候选池 {scan_result.get('candidate_count',0)} 只 → 命中 {total} 只
    </div>
    {sections}"""
    return _card(f"🔍 选股扫描 · {scan_result.get('date','')}", body, "#f59e0b")


def _account_html(health: Optional[dict], holdings: list[dict], prices: dict) -> str:
    if not health:
        return _card("💼 账户状态", "<div style='color:#9ca3af;font-size:13px'>暂无持仓</div>", "#9ca3af")

    state = health.get("account_state", "良性")
    color = _STATE_COLOR.get(state, "#9ca3af")
    pnl   = health.get("daily_pnl", 0)
    pct   = health.get("daily_pnl_pct", 0)
    sign  = "+" if pnl >= 0 else ""
    pnl_color = "#22c55e" if pnl >= 0 else "#ef4444"

    rows = _row("代码", "名称", "成本", "现价", "盈亏%", "止损", "状态", header=True)
    for h in holdings:
        code  = h["code"]
        price = prices.get(code)
        cost  = h["entry_price"]
        stop  = h["stop_loss"]
        if price:
            ph = (price - cost) / cost * 100
            ph_color = "#22c55e" if ph >= 0 else "#ef4444"
            at_stop = price <= stop
            rows += _row(
                f"<code style='font-size:12px'>{code}</code>",
                h["name"],
                f"{cost:.2f}",
                f"<b>{price:.2f}</b>",
                f"<span style='color:{ph_color}'>{'+' if ph>=0 else ''}{ph:.1f}%</span>",
                f"{stop:.2f}",
                _badge("止损!", "#ef4444") if at_stop else "✓",
            )
        else:
            rows += _row(f"<code>{code}</code>", h["name"], f"{cost:.2f}", "—", "—", f"{stop:.2f}", "")

    loss_days = health.get("consecutive_loss_days", 0)
    msgs = health.get("messages", [])
    warnings = "".join(
        f"<div style='background:#fef2f2;color:#ef4444;padding:6px 10px;border-radius:6px;font-size:12px;margin-top:6px'>⚡ {m}</div>"
        for m in msgs
    )

    body = f"""
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px">
      {_badge(state, color)}
      {_badge(f"仓位 {health.get('position_ratio',0)*100:.0f}%", "#6b7280")}
      <span style="font-size:13px;color:{pnl_color};font-weight:600">{sign}{pnl:,.0f} 元  ({sign}{pct:.2f}%)</span>
      {_badge(f"连亏 {loss_days} 日", "#ef4444") if loss_days > 0 else ""}
    </div>
    {_table(rows)}
    {warnings}"""
    return _card("💼 账户状态", body, color)


_VERDICT_COLOR = {
    "可介入": "#22c55e", "观望": "#f59e0b",
    "R:R不足": "#f97316", "回避": "#ef4444",
}


def _diagnose_html(diag_results: list[dict]) -> str:
    if not diag_results:
        return ""
    rows = _row("代码", "名称", "周线", "日线", "资金", "R:R", "策略", "结论", header=True)
    for r in diag_results:
        vc    = _VERDICT_COLOR.get(r["verdict"], "#9ca3af")
        rr_s  = f"{r['rr']:.1f}" if r.get("rr") is not None else "—"
        net3d = r.get("capital_net3d", 0)
        flow_s = f"{net3d/1e4:+.0f}万" if net3d else r.get("capital_dir", "—")
        rows += _row(
            f"<code style='font-size:12px'>{r['code']}</code>",
            r.get("name", ""),
            r.get("weekly_dir", "—"),
            r.get("daily_lc", "—"),
            flow_s,
            rr_s,
            r.get("strategy", "—"),
            _badge(r["verdict"], vc),
        )
    return _card(f"🔬 深度诊断 · {len(diag_results)} 只", _table(rows), "#8b5cf6")


def _sectors_html(themes: list[dict]) -> str:
    if not themes:
        return _card("🔥 活跃主线", "<div style='color:#9ca3af;font-size:13px'>无热点数据</div>", "#9ca3af")

    rows = _row("板块", "分", "涨幅", "净流入", "龙头（TOP2）", header=True)
    for t in themes:
        flow_str = f"{t['net_flow']/1e8:.1f}亿" if t.get("net_flow", 0) > 0 else "—"
        lb_tag   = " 🔥" if t.get("on_leaderboard") else ""
        leaders  = t.get("leaders", [])[:2]
        ldrs_str = "  ".join(
            f"{l['name']}{'★' if l.get('on_lb') else ''} {l['change_rate']:+.1f}%"
            for l in leaders
        )
        chg = t["change_rate"]
        chg_color = "#22c55e" if chg >= 3 else "#f59e0b" if chg >= 1 else "#9ca3af"
        rows += _row(
            f"<b>{t['name']}</b>{lb_tag}",
            f"<b>{t['score']}</b>",
            f"<span style='color:{chg_color}'>{chg:+.2f}%</span>",
            flow_str,
            f"<span style='font-size:12px;color:#374151'>{ldrs_str}</span>",
        )
    return _card(f"🔥 活跃主线 · {len(themes)} 条", _table(rows), "#ef4444")



def _todo_html() -> str:
    done = [
        ("MCP 客户端", "公共模块提取去重", "所有模块统一 mcp_call()，6 文件 240+ 行重复代码 → modules/mcp.py 61行"),
        ("scanner.py", "P0: 回调狙击 htji 影线逻辑翻转", "下影线 > 上影线（试探支撑后买回），≤5% 上限，25 个单元测试回归覆盖"),
        ("sectors.py", "P0: _pct 阈值从 2 收窄为 0.5", "防误判 — 1.5% 变动不再被当作 150% 渲染"),
        ("timing.py", "P1: 跌停因子 + MA3 方向判断", "跌停 >50 只时温度 -15°C（下限 0）；index_direction 改用 3 日 MA 比较"),
        ("account.py", "P1: 交易成本计算", "佣金万分之1.5(min5元) + 印花税万分之5 + 过户费十万分之1"),
        ("signals.py", "P1: 信号过期打标", "expires 字段，信号日起 +3 交易日，跳过周末"),
        ("pool.py", "P1: 持仓保护", "refresh_lifecycles 持仓中跳过移除 [保留]"),
        ("timing.py", "P2: apply_buffer 持久化", "data/timing_history.json，按日期 upsert，保留 90 条"),
        ("scanner.py", "P3: 候选池灭·出局过滤", "scan 阶段读 pool.json，过滤灭·出局标签避免无效 API"),
        ("tests/", "P3: scanner 单元测试", "25 测试全部通过（纯标准库 unittest），覆盖 _is_st / 一字板 / 均量 / 影线 P0 回归"),
        ("min_backtest.py", "P3: 最小回测框架（~280行）", "walk-forward 引擎：逐日切片→T+1 入场→持有/止盈止损退出，10 模型可用"),
        ("验证: 宁德时代 300750", "实盘 2 年回测完成", "htji 回调狙击 +3.1% / rzq 弱转强 +4.8% / zxji 中线狙击 -30% ⚠️"),
    ]
    pending = [
        ("回测矩阵", "11 模型 × 多股票全量跑分", "当前仅验证 300750 一只，需茅台/比亚迪等交叉验证，建立模型基线"),
        ("参数调优", "持有周期 + 阈值网格搜索", "htji 持有 5/10/15/20 天对比，rzq 信号过少(2年4笔)需扩大候选池"),
        ("券商接口", "持仓手工录入", "positions.json 手动维护，无券商接口对接，暂缓"),
    ]

    done_rows = _row("模块", "完成项", "说明", header=True)
    for mod, point, desc in done:
        done_rows += _row(
            f"<code style='font-size:12px'>{mod}</code>",
            f"<span style='color:#22c55e;font-weight:600'>✅ {point}</span>",
            f"<span style='color:#6b7280;font-size:12px'>{desc}</span>",
        )

    pending_rows = _row("模块", "待办项", "说明", header=True)
    for mod, point, desc in pending:
        pending_rows += _row(
            f"<code style='font-size:12px'>{mod}</code>",
            f"<b style='font-size:13px'>{point}</b>",
            f"<span style='color:#6b7280;font-size:12px'>{desc}</span>",
        )

    # 回测结果摘要
    bt_summary = """
    <div style="margin:14px 0 10px;font-size:12px;font-weight:700;color:#3b82f6">📊 回测验证（宁德时代 2024.6→2026.5）</div>
    <table style="width:100%;border-collapse:collapse">
      <tr><th style='padding:5px 8px;text-align:left;background:#f8fafc;font-size:11px;color:#6b7280'>模型</th>
          <th style='padding:5px 8px;text-align:center;background:#f8fafc;font-size:11px;color:#6b7280'>信号</th>
          <th style='padding:5px 8px;text-align:center;background:#f8fafc;font-size:11px;color:#6b7280'>胜率</th>
          <th style='padding:5px 8px;text-align:center;background:#f8fafc;font-size:11px;color:#6b7280'>累计</th>
          <th style='padding:5px 8px;text-align:center;background:#f8fafc;font-size:11px;color:#6b7280'>盈亏比</th></tr>
      <tr><td style='padding:4px 8px;font-size:12px;border-top:1px solid #f1f5f9'>回调狙击 htji</td>
          <td style='padding:4px 8px;text-align:center;font-size:12px;border-top:1px solid #f1f5f9'>9</td>
          <td style='padding:4px 8px;text-align:center;font-size:12px;border-top:1px solid #f1f5f9'><span style='color:#22c55e'>44.4%</span></td>
          <td style='padding:4px 8px;text-align:center;font-size:12px;border-top:1px solid #f1f5f9'><span style='color:#22c55e'>+3.1%</span></td>
          <td style='padding:4px 8px;text-align:center;font-size:12px;border-top:1px solid #f1f5f9'>1.16</td></tr>
      <tr><td style='padding:4px 8px;font-size:12px;border-top:1px solid #f1f5f9'>弱转强 rzq</td>
          <td style='padding:4px 8px;text-align:center;font-size:12px;border-top:1px solid #f1f5f9'>4</td>
          <td style='padding:4px 8px;text-align:center;font-size:12px;border-top:1px solid #f1f5f9'><span style='color:#22c55e'>75.0%</span></td>
          <td style='padding:4px 8px;text-align:center;font-size:12px;border-top:1px solid #f1f5f9'><span style='color:#22c55e'>+4.8%</span></td>
          <td style='padding:4px 8px;text-align:center;font-size:12px;border-top:1px solid #f1f5f9'>2.02</td></tr>
      <tr><td style='padding:4px 8px;font-size:12px;border-top:1px solid #f1f5f9'>中线狙击 zxji</td>
          <td style='padding:4px 8px;text-align:center;font-size:12px;border-top:1px solid #f1f5f9'>15</td>
          <td style='padding:4px 8px;text-align:center;font-size:12px;border-top:1px solid #f1f5f9'><span style='color:#ef4444'>26.7%</span></td>
          <td style='padding:4px 8px;text-align:center;font-size:12px;border-top:1px solid #f1f5f9'><span style='color:#ef4444'>-30.0%</span></td>
          <td style='padding:4px 8px;text-align:center;font-size:12px;border-top:1px solid #f1f5f9'>0.46</td></tr>
    </table>
    <div style="margin-top:6px;font-size:11px;color:#6b7280">持有 10 天 | 止损 -8% | 止盈 +15% | ⚠️ 历史回测不代表未来表现</div>"""

    body = f"""
    <div style="margin-bottom:10px;font-size:12px;font-weight:700;color:#22c55e">已完成（12 项）</div>
    {_table(done_rows)}
    {bt_summary}
    <div style="margin:14px 0 10px;font-size:12px;font-weight:700;color:#f97316">待完善（3 项）</div>
    {_table(pending_rows)}"""
    return _card("📋 进度看板", body, "#f97316")


# ── 主入口 ───────────────────────────────────────────────

def generate(
    date: str,
    timing: dict,
    pool_summary: dict,
    sig_results: list[dict],
    scan_result: Optional[dict],
    health: Optional[dict],
    holdings: list[dict],
    prices: dict[str, float],
    sector_themes: Optional[list[dict]] = None,
    diag_results: Optional[list[dict]] = None,
) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    timing_html  = _timing_html(timing)
    sectors_html = _sectors_html(sector_themes or [])
    pool_html    = _pool_html(pool_summary)
    signals_html = _signals_html(sig_results)
    diag_html    = _diagnose_html(diag_results or [])
    scan_html    = _scan_html(scan_result)
    account_html = _account_html(health, holdings, prices)
    todo_html    = _todo_html()

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>交易员日报 {date}</title>
<style>
  * {{ box-sizing:border-box; margin:0; padding:0 }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
         background:#f1f5f9;color:#1e293b;padding:16px }}
  code {{ font-family:"SF Mono",Menlo,Monaco,monospace }}
  @media(min-width:900px) {{ .grid {{ display:grid;grid-template-columns:1fr 1fr;gap:0 16px }} }}
</style>
</head>
<body>
<div style="max-width:1200px;margin:0 auto">

  <div style="background:linear-gradient(135deg,#1e3a5f,#2563eb);color:#fff;border-radius:12px;padding:20px 24px;margin-bottom:16px">
    <div style="font-size:22px;font-weight:800">🤖 交易员日报</div>
    <div style="font-size:14px;opacity:.7;margin-top:4px">{date} &nbsp;·&nbsp; 生成于 {ts} &nbsp;·&nbsp; 仅供参考，不构成投资建议</div>
  </div>

  <div class="grid">
    <div>
      {timing_html}
      {sectors_html}
      {pool_html}
      {signals_html}
      {diag_html}
    </div>
    <div>
      {scan_html}
      {account_html}
      {todo_html}
    </div>
  </div>

</div>
</body>
</html>"""

    path = OUTPUT_DIR / f"trader_{date}.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path
