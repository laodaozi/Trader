import json, os, sys, re
from datetime import datetime
from pathlib import Path

TRADER_STRATEGY = Path("/opt/cycleradar-trader/data/trader_strategy.jsonl")
MORNING_JSON    = Path("/opt/cycleradar-trader/data/morning.json")
CONTRACTS_DIR   = Path("/opt/trader/output/contracts")
HOT_ENRICHMENT  = Path("/opt/cycleradar-trader/data/hot_enrichment.json")

sys.path.insert(0, str(Path(__file__).parent.parent))
from report_agent import call_claude_api

def _read_json(p):
    try:
        if p.exists():
            with open(p) as f:
                return json.load(f)
    except: pass
    return {}

def generate_alpha():
    signals = []
    date_str = None
    if TRADER_STRATEGY.exists():
        with open(TRADER_STRATEGY) as f:
            for line in f:
                line = line.strip()
                if not line: continue
                try: s = json.loads(line)
                except: continue
                if date_str is None:
                    date_str = s.get("date", "")
                signals.append({
                    "signal_id": f"ALPHA-{s.get('date','')}-{len(signals)+1:03d}",
                    "stock": {"code": s.get("code",""), "name": s.get("name","")},
                    "direction": "long",
                    "entry_price": s.get("entry_low"),
                    "target_price": None,
                    "stop_loss": s.get("stop_loss"),
                    "confidence": min(round(s.get("score",0)/20, 1), 5.0),
                    "time_window": "1w",
                    "event_source": s.get("source",""),
                    "thesis": f"{s.get('name','')} {s.get('strategy','')} score={s.get('score',0)}",
                    "sector_context": s.get("sector_context",""),
                    "enhanced_nx": s.get("nx","")
                })
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")
    alpha = {"date": date_str, "signals": signals}
    morning = _read_json(MORNING_JSON)
    if morning:
        alpha["global_conclusion"] = morning.get("global_conclusion","")
        alpha["sector_outlook"] = morning.get("sector_outlook",[])
        alpha["commodity_signals"] = morning.get("commodity_signals",[])
    return alpha

# ── V3.7 Claude 事件叙事 ──

NARRATIVE_SYSTEM_PROMPT = """你是穿跃一号（Cycleradar）的事件叙事 Agent。你的任务是把一组市场事件的标题/摘要，转化为可指导交易操作的结构化叙事。

## 你的输入限制
- 只有事件标题和简短摘要（没有正文），你需要用金融知识推断事件的影响链
- 输入包含个股 alpha 信号、行业轮动因子、商品雷达（辅助上下文）
- 有些事件是垃圾（如"分享图片"、纯广告），直接丢弃

## 输出格式（严格 JSON，无其他文字）
```json
{
  "global_conclusion": {
    "regime": "进攻|均衡|防守",
    "label": "一句话市场定调（≤25字）",
    "summary": "2-3句话的市场综述，要点：主要矛盾/方向/风险",
    "confidence": "高|中|低",
    "key_watch": "未来1-3天最关键的观察点",
    "color": "red|yellow|green"
  },
  "events": [
    {
      "rank": 1,
      "title": "你提炼的事件标题（≤30字，不要照抄原标题）",
      "source_title": "原始标题",
      "source": "来源",
      "color": "red|yellow|green",
      "time_dimension": "事件时间窗口（如：本周一到周三）",
      "trigger_event": "触发事件：发生了什么（1-2句）",
      "direct_reaction": "直接反应：市场/资产价格第一时间怎么走",
      "sector_transmission": [
        {"sector": "行业名", "direction": "看多|看空|中性", "reason": "传导逻辑"}
      ],
      "valuation_impact": "估值重塑：对哪些资产/板块的估值逻辑产生什么影响",
      "trading_window": "操作窗口：具体在什么时间/条件下可执行操作",
      "stock_mapping": [
        {"code": "6位数字代码", "name": "股票简称", "type": "受益|受损|弹性", "logic": "映射逻辑"}
      ]
    }
  ],
  "sector_outlook": [
    {"sector": "行业名", "direction": "看多|看空|中性|观望", "color": "green|yellow|red", "driver": "驱动逻辑"}
  ],
  "etf_allocation": [
    {"code": "6位代码", "name": "ETF简称", "weight": 0.25, "direction": "long|short"}
  ],
  "commodity_signals": [
    {"symbol": "商品代码（CL/SI/AU/NG/CU/RB/I/JM）", "direction": "多|空|观望", "color": "green|yellow|red", "reason": "判断逻辑"}
  ]
}
```

## 质量戒律
1. **每件事必须给绿/黄/红标记**。黄色不是骑墙，是"方向明确但有不确定性"。
2. **五步推导必须连贯**：触发事件 → 直接反应 → 产业传导 → 估值重塑 → 操作窗口。不允许跳步。
3. **个股映射**：尽量给出 1-2 只最相关的 A 股（可根据行业常识推断，你不会因猜测被惩罚）。code 必须 6 位纯数字。type 用"受益|受损|弹性"三选一。
4. **global_conclusion.color** 是红/黄/绿，代表整体风险偏好判断，不是涨跌。
5. **ETF 配置**：weight 之和 = 1.0，direction 指定多空。
6. **商品信号**：symbol 用交易所代码（CL=原油/SI=白银/AU=黄金/NG=天然气/CU=铜/RB=螺纹钢/I=铁矿/JM=焦煤）。direction 用"多/空/观望"。
7. **TOP 3-5 个事件**即可，不要硬凑数量。质量 > 数量。
8. **中文输出**，但 JSON key 用英文。"""

def build_narrative_user_prompt(morning: dict) -> str:
    """从 morning.json 构建 user prompt。"""
    events = morning.get("events", [])
    if not events:
        return "（今日无事件）"

    lines = ["## 今日事件列表\n"]
    garbage_count = 0
    for i, ev in enumerate(events):
        title = ev.get("title", "").strip()
        summary = ev.get("summary", "").strip()
        source = ev.get("source", "").strip()
        tier = ev.get("tier", "").strip()

        if title in ("分享图片", "") and not summary:
            garbage_count += 1
            continue

        lines.append(f"### 事件 {i+1 - garbage_count}")
        lines.append(f"- 标题: {title}")
        if summary:
            lines.append(f"- 摘要: {summary[:200]}")
        lines.append(f"- 来源: {source}")
        if tier:
            lines.append(f"- 评级: {tier}")
        lines.append("")

    if garbage_count:
        lines.insert(2, f"（已自动丢弃 {garbage_count} 条垃圾事件：空白/分享图片）\n")

    # alpha_signals 上下文
    alphas = morning.get("alpha_signals", [])
    if alphas:
        lines.append("## 个股 Alpha 信号（辅助参考）\n")
        for a in alphas[:10]:
            name = a.get("name", "") or a.get("stock_name", "")
            code = a.get("code", "") or a.get("stock_code", "")
            thesis = a.get("thesis", "") or a.get("signal_desc", "")
            nx = a.get("enhanced_nx", "") or a.get("nx", "")
            label = f"{name}({code})" if name and code else name or code
            if label:
                line = f"- {label}"
                if direction := (a.get("direction") or a.get("signal_direction", "")):
                    line += f" | 方向:{direction}"
                if thesis:
                    line += f" | {thesis[:80]}"
                if nx:
                    line += f" | NX:{nx}"
                lines.append(line)
        lines.append("")

    # sector_outlook 上下文
    sector_outlook = morning.get("sector_outlook", [])
    if sector_outlook:
        lines.append("## 行业轮动信号\n")
        for so in sector_outlook[:8]:
            s = so.get("sector", "")
            d = so.get("direction", "") or so.get("outlook", "")
            if s:
                lines.append(f"- {s}: {d or '无方向'}")
        lines.append("")

    # commodity_signals 上下文
    commodity_signals = morning.get("commodity_signals", [])
    if commodity_signals:
        lines.append("## 商品雷达\n")
        for cs in commodity_signals[:5]:
            c = cs.get("commodity", "") or cs.get("symbol", "")
            d = cs.get("direction", "") or cs.get("signal", "")
            if c:
                lines.append(f"- {c}: {d or '无方向'}")
        lines.append("")

    lines.append("请基于以上信息，生成结构化事件叙事 JSON。")
    return "\n".join(lines)


def parse_narrative_response(text: str) -> dict | None:
    """从 Claude 响应中提取 JSON，比 report_agent 的 parse_llm_output 更宽松。"""
    if not text:
        return None

    # 策略 1: ```json fence
    m = re.search(r'```json\s*\n(.*?)\n\s*```', text, re.DOTALL)
    if m:
        raw = m.group(1)
    else:
        # 策略 2: 找最外层 { }
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            raw = text[start:end + 1]
        else:
            return None

    # 清理常见 LLM 输出问题
    raw = re.sub(r",\s*([}\]])", r"\1", raw)  # trailing commas
    raw = raw.replace("\u201c", '"').replace("\u201d", '"')  # 中文引号

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  ⚠ Narrative JSON 解析失败: {e}")
        pos = e.pos or 0
        print(f"    错误位置: ...{raw[max(0,pos-40):pos+40]}...")
        return None


def validate_and_fix_narrative(narrative: dict) -> dict:
    """校验并自动修复叙事 JSON。"""
    gc = narrative.get("global_conclusion", {})
    if not gc or not isinstance(gc, dict):
        narrative["global_conclusion"] = {
            "regime": "均衡", "label": "市场方向不明",
            "summary": "暂无足够信息判断市场方向。", "confidence": "低",
            "key_watch": "等待明确信号", "color": "yellow"
        }
    else:
        if not gc.get("regime"): gc["regime"] = "均衡"
        if not gc.get("color"): gc["color"] = "yellow"
        if not gc.get("confidence"): gc["confidence"] = "低"
        if not gc.get("label"): gc["label"] = "市场方向不明"

    # 清理个股 code
    for ev in narrative.get("events", []):
        stocks = ev.get("stock_mapping", [])
        clean = []
        for s in stocks:
            if not isinstance(s, dict): continue
            code = str(s.get("code", "")).strip()
            if code and re.fullmatch(r"\d{6}", code):
                clean.append(s)
            elif code:
                print(f"    ⚠ 剔除无效 code: {s.get('name','?')}({code})")
        ev["stock_mapping"] = clean
        # 补默认
        if not ev.get("color"): ev["color"] = "yellow"
        if not ev.get("time_dimension"): ev["time_dimension"] = "当日"
        if not ev.get("sector_transmission"): ev["sector_transmission"] = []
        if not ev.get("stock_mapping"): ev["stock_mapping"] = []

    # ETF weight 归一化
    etfs = narrative.get("etf_allocation", [])
    if etfs:
        total = sum(e.get("weight", 0) for e in etfs if isinstance(e.get("weight"), (int, float)))
        if total > 0 and abs(total - 1.0) > 0.01:
            for e in etfs:
                e["weight"] = round(e.get("weight", 0) / total, 2)

    return narrative


def generate_narrative():
    """生成事件叙事（Claude 富化版）。

    V3.7: raw 标题 copy → Claude 五步推导 + 行业传导 + ETF 配置 + 商品信号
    """
    today = datetime.now()
    date_str = today.strftime("%Y-%m-%d")
    generated_at = today.strftime("%Y-%m-%dT%H:%M:%S")

    morning = _read_json(MORNING_JSON)
    print(f"\n{'━' * 50}")
    print(f"  事件叙事 AI 生成")
    print(f"{'━' * 50}")

    user_prompt = build_narrative_user_prompt(morning) if morning else "今日无事件"
    print(f"  User prompt: {len(user_prompt)} 字符")
    print(f"  事件数: {len(morning.get('events', [])) if morning else 0}")

    # ── 调 Claude 富化 ──
    try:
        text = call_claude_api(
            NARRATIVE_SYSTEM_PROMPT,
            user_prompt,
            tier="standard"
        )
        print(f"  Claude 响应: {len(text)} 字符")

        narrative = parse_narrative_response(text)

        if narrative:
            narrative = validate_and_fix_narrative(narrative)
            narrative["date"] = date_str
            narrative["source"] = "cycleradar-trader AI narrative v3.7"
            narrative["generated_at"] = generated_at

            ev_count = len(narrative.get("events", []))
            sector_count = len(narrative.get("sector_outlook", []))
            etf_count = len(narrative.get("etf_allocation", []))
            commodity_count = len(narrative.get("commodity_signals", []))

            gc = narrative.get("global_conclusion", {})
            print(f"  ✓ AI 叙事: {ev_count} 事件, {sector_count} 行业, "
                  f"{etf_count} ETF, {commodity_count} 商品")
            print(f"    定调: [{gc.get('color', '?')}] {gc.get('label', '?')} "
                  f"({gc.get('regime', '?')}, 置信度{gc.get('confidence', '?')})")

            for ev in narrative.get("events", [])[:5]:
                print(f"    #{ev.get('rank', '?')} [{ev.get('color', '?')}] {ev.get('title', '')[:50]}")

            return narrative

        else:
            print("  ⚠ Claude 返回无法解析，降级到 raw 模式")

    except Exception as e:
        print(f"  ✗ Claude 调用失败: {e}")
        print("  → 降级到 raw copy 模式")

    # ── 降级：raw copy ──
    events = []
    if morning:
        for ev in morning.get("events", [])[:8]:
            title = ev.get("title", "").strip()
            if title in ("分享图片", ""):
                continue
            events.append({
                "rank": len(events) + 1,
                "title": title[:100],
                "source_title": title,
                "source": ev.get("source", ""),
                "color": "yellow",
                "time_dimension": "当日",
                "trigger_event": title,
                "direct_reaction": "",
                "sector_transmission": [],
                "valuation_impact": "",
                "trading_window": "",
                "stock_mapping": []
            })

    hot = _read_json(HOT_ENRICHMENT)
    if hot:
        for key, val in hot.items():
            if not isinstance(val, dict): continue
            ts = val.get("enriched_at", "")
            tickers = val.get("tickers", [])
            if ts:
                try:
                    occurred = datetime.strptime(str(ts)[:10], "%Y-%m-%d")
                    decay = val.get("decay_days", 3)
                    if (today - occurred).days > int(decay):
                        continue
                except (ValueError, TypeError):
                    pass
            events.append({
                "rank": len(events) + 1,
                "title": val.get("thesis", "")[:100],
                "source_title": val.get("thesis", ""),
                "source": "RSS hot enrichment",
                "color": "yellow",
                "time_dimension": str(ts)[:10] if ts else date_str,
                "trigger_event": val.get("thesis", ""),
                "direct_reaction": "",
                "sector_transmission": [],
                "valuation_impact": "",
                "trading_window": "",
                "stock_mapping": [{"code": t.get("code", ""), "name": t.get("name", ""), "type": "受益", "logic": t.get("reason", "")} for t in tickers[:5]]
            })

    return {
        "date": date_str,
        "source": "cycleradar-trader server pipeline (fallback)",
        "generated_at": generated_at,
        "events": events[:12],
        "global_conclusion": {
            "regime": "均衡", "label": "AI 富化未成功，使用 raw 模式",
            "summary": "", "confidence": "低", "key_watch": "", "color": "yellow"
        },
        "sector_outlook": morning.get("sector_outlook", []) if morning else [],
        "etf_allocation": [],
        "commodity_signals": morning.get("commodity_signals", []) if morning else []
    }


def main():
    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
    alpha = generate_alpha()
    with open(CONTRACTS_DIR / "alpha_latest.json", "w") as f:
        json.dump(alpha, f, ensure_ascii=False, indent=2)
    print(f"alpha_latest.json: {len(alpha.get('signals', []))} signals")
    narrative = generate_narrative()
    with open(CONTRACTS_DIR / "event_narrative_latest.json", "w") as f:
        json.dump(narrative, f, ensure_ascii=False, indent=2)
    print(f"event_narrative_latest.json: {len(narrative.get('events', []))} events")


if __name__ == "__main__":
    main()
