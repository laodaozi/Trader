#!/usr/bin/env python3
"""
策略反思 AI 生成器
=================
读取当日信号数据（自选/事件叙事/行业轮动/商品雷达），调用 Claude 生成结构化策略反思。
输出 data/strategy_reflection.json，供 /m 策略反思 Tab 展示。

用法：
    python3.9 generate_strategy_reflection.py                # 默认：日度反思
    python3.9 generate_strategy_reflection.py --weekly        # 周度反思（含7天回溯）
"""

import argparse, json, os, re, sys, time
from pathlib import Path
from anthropic import Anthropic

# ── 路径 ──
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = Path("/opt/trader/output/contracts")
DATA_DIR = PROJECT_ROOT / "data"
ALPHA_FILE = CONTRACTS_DIR / "alpha_latest.json"
NARRATIVE_FILE = CONTRACTS_DIR / "event_narrative_latest.json"
WATCHLIST_FILE = CONTRACTS_DIR / "watchlist_signals.json"
OUTPUT_FILE = DATA_DIR / "strategy_reflection.json"
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# 如果不设置 API_KEY，尝试从 .env 读取
if not API_KEY:
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().split("\n"):
            if line.startswith("ANTHROPIC_API_KEY="):
                API_KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
                break


def _load_watchlist_lookup() -> dict:
    """读取 watchlist_signals.json，构建 code→详细信息的查找表。"""
    lookup = {}
    if WATCHLIST_FILE.exists():
        wl = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
        for ws in wl.get("signals", []):
            code = ws.get("code", "")
            if code:
                lookup[code] = ws
    return lookup


def _build_context(weekly: bool = False) -> dict:
    """构建发给 LLM 的上下文数据。"""
    ctx = {"signals_summary": {}, "events": [], "sector_signals": [], "commodity_signals": [],
           "market_snapshot": {}, "narrative_summary": ""}

    # 预加载 watchlist 查找表（alpha_latest 的 signals 里 name/code/nx_signal 在嵌套 stock 对象内）
    wl_lookup = _load_watchlist_lookup()

    # 1) alpha_latest.json — 自选信号 + 行业 + 商品
    if ALPHA_FILE.exists():
        raw = json.loads(ALPHA_FILE.read_text(encoding="utf-8"))
        signals = raw.get("signals", [])
        ctx["signals_summary"] = {
            "total": len(signals),
            "long": 0, "short": 0, "lifecycle_breakdown": {},
        }

        for s in signals:
            code = s.get("stock", {}).get("code", "") or s.get("code", "")
            wl = wl_lookup.get(code, {})
            # watchlist 中有更准的 nx_signal / lifecycle
            nx = wl.get("nx_signal", "") or s.get("nx_signal", "") or s.get("enhanced_nx", "")
            lc = wl.get("lifecycle", "") or s.get("lifecycle", "") or s.get("enhanced_lc", "未知")
            if nx == "buy":
                ctx["signals_summary"]["long"] += 1
            elif nx == "sell":
                ctx["signals_summary"]["short"] += 1
            ctx["signals_summary"]["lifecycle_breakdown"][lc] = \
                ctx["signals_summary"]["lifecycle_breakdown"].get(lc, 0) + 1

        # Top 自选标的
        ctx["top_watchlist"] = []
        for s in signals[:15]:
            stock = s.get("stock", {})
            code = stock.get("code", "") or s.get("code", "")
            wl = wl_lookup.get(code, {})
            ctx["top_watchlist"].append({
                "name": wl.get("name", "") or stock.get("name", "") or s.get("name", ""),
                "code": code,
                "lifecycle": wl.get("lifecycle", ""),
                "pnl_pct": wl.get("pnl_pct", 0),
                "nx_signal": wl.get("nx_signal", ""),
                "signal_basis": wl.get("signal_basis", []),
            })

        # sector_outlook: 优先 alpha_latest，空则从 watchlist 的 sector 字段汇总
        sector_outlook = raw.get("sector_outlook", [])
        if not sector_outlook and wl_lookup:
            sector_count = {}
            for w in wl_lookup.values():
                sec = w.get("sector", "")
                if sec:
                    sector_count[sec] = sector_count.get(sec, 0) + 1
            sector_outlook = [{"sector": k, "count": v, "source": "watchlist"} for k, v in
                              sorted(sector_count.items(), key=lambda x: -x[1])[:10]]
        ctx["sector_signals"] = sector_outlook[:10]

        ctx["commodity_signals"] = raw.get("commodity_signals", [])[:8]
        gc = raw.get("global_conclusion") or {}
        ctx["market_snapshot"] = {
            "regime": gc.get("market_regime", ""),
            "sentiment": gc.get("market_sentiment", ""),
            "risk_level": gc.get("risk_level", ""),
        }

    # 2) event_narrative_latest.json — 事件解读
    if NARRATIVE_FILE.exists():
        en = json.loads(NARRATIVE_FILE.read_text(encoding="utf-8"))
        ctx["narrative_summary"] = en.get("global_conclusion", {}).get("summary", "")
        for ev in en.get("events", [])[:8]:
            ctx["events"].append({
                "title": ev.get("title", ""),
                "interpretation": ev.get("interpretation", "")[:200],
                "sector_impact": ev.get("sector_impact", ""),
                "rank": ev.get("rank", 99),
            })

    return ctx


def _build_prompt(ctx: dict, weekly: bool) -> str:
    """构建 Claude prompt。"""
    period = "周度" if weekly else "日度"
    lines = [
        f"你是一位 A 股量化策略顾问。请基于以下{period}数据，输出结构化策略反思。",
        "",
        "## 数据摘要",
    ]

    # 市场快照
    ms = ctx.get("market_snapshot", {})
    if ms:
        lines.append(f"- 市场风格: {ms.get('regime','未知')} | 情绪: {ms.get('sentiment','未知')} | 风险: {ms.get('risk_level','未知')}")

    # 信号汇总
    ss = ctx.get("signals_summary", {})
    if ss:
        lines.append(f"- 自选标的: {ss.get('total',0)}只 (多头{ss.get('long',0)}/空头{ss.get('short',0)})")
        lb = ss.get("lifecycle_breakdown", {})
        if lb:
            lines.append(f"- 生命周期: {', '.join(f'{k}:{v}只' for k,v in sorted(lb.items(), key=lambda x:-x[1])[:5])}")

    # 事件叙事
    ns = ctx.get("narrative_summary", "")
    if ns:
        lines.append(f"- AI事件定调: {ns[:200]}")

    # 行业轮动
    sectors = ctx.get("sector_signals", [])
    if sectors:
        lines.append("- 行业轮动信号:")
        for s in sectors[:6]:
            lines.append(f"  · {s.get('sector','?')}: {s.get('direction','') or s.get('outlook','')} | 置信度{s.get('confidence','')}")

    # 商品雷达
    comms = ctx.get("commodity_signals", [])
    if comms:
        lines.append("- 商品雷达:")
        for c in comms[:4]:
            lines.append(f"  · {c.get('commodity','?')}: {c.get('direction','') or c.get('signal','')}")

    # 自选明细
    top = ctx.get("top_watchlist", [])
    if top:
        lines.append("- 重点自选 (前15):")
        for t in top[:10]:
            basis = ",".join(t.get("signal_basis", [])) or "无"
            lines.append(f"  · {t['name']}({t['code']}) {t['lifecycle']} PnL={t['pnl_pct']:.1f}% NX={t['nx_signal']} [{basis}]")

    # 关键事件
    events = ctx.get("events", [])
    if events:
        lines.append("- 关键事件:")
        for ev in sorted(events, key=lambda e: e.get("rank", 99)):
            lines.append(f"  · [{ev.get('rank','?')}] {ev.get('title','')} → {ev.get('interpretation','')[:100]}")

    lines.append("")
    lines.append("## 输出要求")
    lines.append("严格输出 JSON（无 markdown 围栏）：")
    lines.append("""
{
  "reflections": [
    {
      "section": "事件驱动有效性回顾",
      "content": "分析当前关键事件（标题+解读）与自选标的PnL/信号的因果关系链。哪些事件确实驱动了持仓涨跌？哪些被市场忽视？给出证据（引用具体标的+事件）。100-200字。",
      "confidence": "high|medium|low"
    },
    {
      "section": "行业轮动因子质量",
      "content": "评估行业轮动信号（sector_outlook）的合理性和一致性。是否存在矛盾信号？轮动方向是否与事件叙事一致？给出1-2个具体行业的操作建议。80-150字。",
      "confidence": "high|medium|low"
    },
    {
      "section": "中短期标的关联",
      "content": "分析top自选标的与当前事件/行业信号的关系。哪些标的受益于轮动？哪些面临逆风？NX信号与生命周期状态是否一致？给出调仓建议（具体标的）。100-200字。",
      "confidence": "high|medium|low"
    },
    {
      "section": "因子×思考结合点",
      "content": "量化因子（NX/MA/Fib/lifecycle）输出与AI叙事判断的交叉验证。因子信号和事件解读在哪些标的上一致（高置信度）？在哪些标的上分歧？给出1-2个需要人工判断的临界案例。100-200字。",
      "confidence": "high|medium|low"
    }
  ],
  "summary": "一句话策略总结（30字内）：当前最值得关注的矛盾/机会/风险"
}""")

    return "\n".join(lines)


def _clean_json(text: str) -> dict:
    """从 Claude 响应中提取 JSON。"""
    if not text:
        return {}
    # 策略 1: ```json fence
    m = re.search(r'```(?:json)?\s*\n(.*?)\n\s*```', text, re.DOTALL)
    if m:
        text = m.group(1)
    # 策略 2: 找最外层 {}
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    # 清理
    text = re.sub(r",\s*([}\]])", r"\1", text)  # trailing commas
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def main():
    parser = argparse.ArgumentParser(description="策略反思 AI 生成")
    parser.add_argument("--weekly", action="store_true", help="周度反思（否则日度）")
    args = parser.parse_args()

    if not API_KEY:
        print("❌ ANTHROPIC_API_KEY 未设置", file=sys.stderr)
        sys.exit(1)

    ctx = _build_context(weekly=args.weekly)
    prompt = _build_prompt(ctx, weekly=args.weekly)

    client = Anthropic(api_key=API_KEY, base_url="https://new-api.finstep.cn")

    print(f"  事件数: {len(ctx.get('events',[]))} | 自选: {ctx['signals_summary'].get('total',0)}只 | 行业: {len(ctx.get('sector_signals',[]))}")
    print(f"  调用 Claude...")
    print(f"  Prompt: ~{len(prompt)} 字符")

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            temperature=0.7,
            system="你是A股量化策略顾问。输出简洁、有证据支撑的策略反思。",
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        # fallback model
        print(f"  claude-sonnet-4-6 失败: {e}, 尝试 deepseek-chat...")
        import openai
        openai_client = openai.OpenAI(
            api_key=os.environ.get("DEEPSEEK_API_KEY", API_KEY),
            base_url="https://api.deepseek.com/v1",
        )
        resp_openai = openai_client.chat.completions.create(
            model="deepseek-chat",
            max_tokens=4096,
            temperature=0.7,
            messages=[
                {"role": "system", "content": "你是A股量化策略顾问。"},
                {"role": "user", "content": prompt},
            ],
        )
        raw_text = resp_openai.choices[0].message.content
        result = _clean_json(raw_text)
        result["model"] = "deepseek-chat"
        result["prompt_len"] = len(prompt)
    else:
        raw_text = resp.content[0].text
        print(f"  模型: {resp.model}")
        print(f"  Token: 输入={resp.usage.input_tokens}, 输出={resp.usage.output_tokens}")
        result = _clean_json(raw_text)
        result["model"] = resp.model
        result["tokens"] = {"input": resp.usage.input_tokens, "output": resp.usage.output_tokens}
        result["prompt_len"] = len(prompt)

    result["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    result["period"] = "weekly" if args.weekly else "daily"

    # 确保 reflections 至少有空结构
    if "reflections" not in result or not result["reflections"]:
        result["reflections"] = [
            {"section": s, "content": "LLM 生成失败，请稍后重试", "confidence": "low"}
            for s in ["事件驱动有效性回顾", "行业轮动因子质量", "中短期标的关联", "因子×思考结合点"]
        ]

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ 输出: {OUTPUT_FILE} ({len(json.dumps(result, ensure_ascii=False))} bytes)")
    sections = [r.get("section","") for r in result.get("reflections",[])]
    print(f"  章节: {', '.join(sections[:4])}")


if __name__ == "__main__":
    main()
