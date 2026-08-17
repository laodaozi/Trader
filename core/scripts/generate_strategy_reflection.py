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
DATA_DIR = PROJECT_ROOT / "data"
# V7.9: 契约文件已迁至平台 data/（generate_contracts.py 不再写冻结目录 /opt/trader/output/contracts）
# 反思消费端跟随迁移，否则读到 07-13 冻结的陈旧契约（events=0）
CONTRACTS_DIR = DATA_DIR
ALPHA_FILE = CONTRACTS_DIR / "alpha_latest.json"
NARRATIVE_FILE = CONTRACTS_DIR / "event_narrative_latest.json"
WATCHLIST_FILE = CONTRACTS_DIR / "watchlist_signals.json"
EVENT_CATALOG_FILE = DATA_DIR / "event_catalog.json"   # V7.9: Admin 手动录入事件
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
        if isinstance(gc, dict):
            ctx["market_snapshot"] = {
                "regime": gc.get("market_regime", ""),
                "sentiment": gc.get("market_sentiment", ""),
                "risk_level": gc.get("risk_level", ""),
            }
        else:
            # V7.6: global_conclusion 现为 LLM summary 字符串，不再是 dict
            ctx["market_snapshot"] = {"regime": "", "sentiment": "", "risk_level": "", "summary": str(gc)}

    # 2) event_narrative_latest.json — 事件解读
    if NARRATIVE_FILE.exists():
        en = json.loads(NARRATIVE_FILE.read_text(encoding="utf-8"))
        en_gc = en.get("global_conclusion", {})
        # V7.6: global_conclusion 可能是 dict（旧 schema）或 summary 字符串（新 schema）
        ctx["narrative_summary"] = en_gc.get("summary", "") if isinstance(en_gc, dict) else str(en_gc)
        # V8.0: 按 stock_mapping 核心股票代码去重，同一股为主角的多条事件只保留 rank 最小那条
        _seen_primary = set()
        _deduped_events = []
        for ev in sorted(en.get("events", []), key=lambda e: e.get("rank", 99)):
            sm = ev.get("stock_mapping", [])
            primary_code = sm[0]["code"] if isinstance(sm, list) and sm else None
            if primary_code and primary_code in _seen_primary:
                continue  # 同核心股已有更高优先级的事件，跳过
            if primary_code:
                _seen_primary.add(primary_code)
            _deduped_events.append(ev)
        for ev in _deduped_events[:8]:
            sm = ev.get("stock_mapping", [])
            ctx["events"].append({
                "title": ev.get("title", ""),
                "interpretation": ev.get("trigger_event", ev.get("interpretation", ""))[:200],
                "sector_impact": ev.get("sector_transmission", ev.get("sector_impact", "")),
                "rank": ev.get("rank", 99),
                "stock_mapping": sm[:3],  # 传给 prompt 最多3只关联股
            })

    # 3) V7.9: event_catalog.json — Admin 手动录入事件（优先级高）
    ctx["user_events"] = []
    if EVENT_CATALOG_FILE.exists():
        try:
            catalog = json.loads(EVENT_CATALOG_FILE.read_text(encoding="utf-8"))
            items = catalog if isinstance(catalog, list) else catalog.get("events", [])
            # 只取最近7天内录入的
            from datetime import datetime, timedelta
            cutoff = (datetime.now() - timedelta(days=7)).isoformat()[:19]
            for item in items:
                if item.get("added_at", "9999") >= cutoff:
                    ctx["user_events"].append({
                        "title": item.get("title", ""),
                        "summary": item.get("summary", ""),
                        "added_at": item.get("added_at", ""),
                    })
            ctx["user_events"] = ctx["user_events"][:5]
        except Exception:
            pass

    # 4) V7.10: trader_tracker.jsonl — 信号验证历史摘要（Loop Learning 真实数据）
    ctx["tracker_summary"] = {}
    tracker_log = DATA_DIR / "trader_tracker.jsonl"
    if tracker_log.exists():
        try:
            from datetime import datetime, timedelta
            cutoff_30d = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            records = []
            with open(tracker_log, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    r = json.loads(line)
                    if r.get("signal_date", "") >= cutoff_30d and r.get("result") not in ("NODATA", "PENDING", "EXPIRED"):
                        records.append(r)
            if records:
                # 5日窗口为主
                r5 = [r for r in records if r.get("horizon") == 5]
                hits = sum(1 for r in r5 if r.get("result") == "HIT")
                misses = sum(1 for r in r5 if r.get("result") == "MISS")
                neutral = sum(1 for r in r5 if r.get("result") == "NEUTRAL")
                total = len(r5)
                # 策略维度命中率
                by_strategy: dict = {}
                for r in r5:
                    s = r.get("strategy", "未知")
                    by_strategy.setdefault(s, {"hit": 0, "total": 0})
                    by_strategy[s]["total"] += 1
                    if r.get("result") == "HIT":
                        by_strategy[s]["hit"] += 1
                strat_rates = {s: round(v["hit"] / v["total"], 2) for s, v in by_strategy.items() if v["total"] >= 3}
                # 近5次 MISS 标的
                recent_miss = [{"name": r.get("name", r.get("code", "")), "code": r.get("code", ""),
                                 "signal_date": r.get("signal_date", ""), "final_return": r.get("final_return", 0)}
                                for r in sorted(r5, key=lambda x: x.get("signal_date", ""), reverse=True)
                                if r.get("result") == "MISS"][:5]
                ctx["tracker_summary"] = {
                    "hit_rate_5d": round(hits / total, 2) if total else None,
                    "total_5d": total,
                    "hits": hits, "misses": misses, "neutral": neutral,
                    "by_strategy": strat_rates,
                    "recent_miss": recent_miss,
                }
        except Exception:
            pass

    # 5) V7.10: event_hit_log.json — 事件标的回查命中率
    ctx["event_hit_summary"] = {}
    event_hit_log = DATA_DIR / "event_hit_log.json"
    if event_hit_log.exists():
        try:
            hl = json.loads(event_hit_log.read_text(encoding="utf-8"))
            summary = hl.get("summary", {})
            # 取命中率最高/最低的信源（≥3条数据）
            by_source = summary.get("by_source", {})
            recent_events = []
            for ev in hl.get("events", [])[:8]:
                hits_ev = [tk for tk in ev.get("tickers", []) if tk.get("verdicts", {}).get("d5") == "HIT"]
                misses_ev = [tk for tk in ev.get("tickers", []) if tk.get("verdicts", {}).get("d5") == "MISS"]
                recent_events.append({
                    "title": ev.get("title", "")[:30],
                    "hit_rate_5d": ev.get("hit_rate_5d"),
                    "hit_tickers": [t["name"] for t in hits_ev][:2],
                    "miss_tickers": [t["name"] for t in misses_ev][:2],
                })
            ctx["event_hit_summary"] = {
                "overall_hit_rate_5d": summary.get("overall_hit_rate_5d"),
                "total_verdicts": summary.get("total_verdicts_5d", 0),
                "by_source": by_source,
                "recent_events": recent_events,
            }
        except Exception:
            pass

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

    # V7.9: 用户手动录入事件（Admin 事件录入页）
    user_events = ctx.get("user_events", [])
    if user_events:
        lines.append("- 用户补充事件（高优先级，请重点分析）:")
        for ue in user_events[:5]:
            lines.append(f"  · {ue.get('title','')} | {ue.get('summary','')[:150]}")

    # V7.10: 信号验证历史（tracker 真实数据，非 LLM 估计）
    ts = ctx.get("tracker_summary", {})
    if ts and ts.get("total_5d", 0) >= 3:
        lines.append("")
        lines.append("## 信号验证历史（过去30天真实回测，这是最重要的参考数据）")
        hr = ts.get("hit_rate_5d")
        lines.append(f"- 5日信号命中率: {hr:.0%} ({ts.get('hits',0)}命中 / {ts.get('misses',0)}失误 / {ts.get('neutral',0)}中性，共{ts.get('total_5d',0)}条)")
        by_s = ts.get("by_strategy", {})
        if by_s:
            strat_str = "  |  ".join(f"{k}:{v:.0%}" for k, v in sorted(by_s.items(), key=lambda x: -x[1]))
            lines.append(f"- 各策略命中率: {strat_str}")
        miss = ts.get("recent_miss", [])
        if miss:
            lines.append(f"- 近期失误标的: {', '.join(m['name'] + '(' + str(round(m.get('final_return',0),1)) + '%)' for m in miss[:4])}")
        lines.append("⚠️ 反思必须以上述真实命中率为依据，不得脱离数据泛泛而谈。")

    # V7.10: 事件命中率历史（事件→标的的实际兑现记录）
    eh = ctx.get("event_hit_summary", {})
    if eh and eh.get("total_verdicts", 0) >= 3:
        lines.append("")
        lines.append("## 事件标的兑现历史（过去90天，事件发出后N日实际涨跌）")
        lines.append(f"- 整体5日命中率: {eh.get('overall_hit_rate_5d', 0):.0%}（共{eh.get('total_verdicts',0)}个标的验证）")
        by_src = eh.get("by_source", {})
        if by_src:
            src_sorted = sorted(by_src.items(), key=lambda x: -(x[1] or 0))[:4]
            src_str = "  |  ".join(f"{s}:{r:.0%}" for s, r in src_sorted if r is not None)
            lines.append(f"- 信源可信度排名: {src_str}")
        recent = eh.get("recent_events", [])
        if recent:
            lines.append("- 近期事件回查:")
            for ev in recent[:4]:
                hr_ev = ev.get("hit_rate_5d")
                hr_str = f"{hr_ev:.0%}" if hr_ev is not None else "待定"
                hits_str = "/".join(ev.get("hit_tickers", [])) or "无"
                miss_str = "/".join(ev.get("miss_tickers", [])) or "无"
                lines.append(f"  · {ev.get('title','')} → 命中率{hr_str} 命中:{hits_str} 失误:{miss_str}")
        lines.append("⚠️ 请用上述兑现率评估当前事件的投资价值，高命中率信源的信号应给予更高权重。")

    lines.append("")
    lines.append("## 输出要求")
    lines.append("严格输出 JSON（无 markdown 围栏）：")
    lines.append("""
{
  "reflections": [
    {
      "section": "事件驱动有效性回顾",
      "content": "分析当前关键事件与自选标的PnL/信号的因果关系链。哪些事件确实驱动了持仓涨跌？哪些被市场忽视？给出证据（引用具体标的+事件）。100-200字。",
      "confidence": "high|medium|low"
    },
    {
      "section": "失败信号根因分析",
      "content": "聚焦PnL<-10%或NX=sell的标的：是入场点错了、止损太松、还是逻辑本身已失效？至少分析1-2个具体亏损标的，给出根本原因（不要泛泛说'市场震荡'，要说'因为X导致Y'）。100-200字。",
      "confidence": "high|medium|low"
    },
    {
      "section": "行业轮动因子质量",
      "content": "评估行业轮动信号的合理性。是否存在矛盾信号？轮动方向是否与事件叙事一致？当前最强/最弱的2个行业，给出具体操作建议（加仓/减仓/观望某行业ETF）。80-150字。",
      "confidence": "high|medium|low"
    },
    {
      "section": "信号策略质量自评",
      "content": "基于当前数据，评估各策略的信号质量：report_agent（事件驱动）、scanner（14模型形态）、wanjun_models（量化）哪个策略今日/本周信号更可信？哪个存在系统性误判风险？给出对各策略置信度的主观打分（0-100）和理由。80-150字。",
      "confidence": "high|medium|low"
    },
    {
      "section": "中短期标的调仓清单",
      "content": "基于NX信号+生命周期+PnL，给出明确的调仓建议清单（不要模糊）：加仓哪些（理由）、减仓哪些（理由）、止损清单（触发条件）、可埋伏哪些（等待入场）。每类最多3只，总计不超过8只。100-200字。",
      "confidence": "high|medium|low"
    },
    {
      "section": "因子与叙事交叉验证",
      "content": "量化因子（NX/MA/Fib/lifecycle）与AI叙事判断的一致性检验。因子和事件在哪些标的上一致（高置信度）？在哪些标的上分歧（需人工判断）？列出1-2个临界案例，给出倾向性结论。100-150字。",
      "confidence": "high|medium|low"
    },
    {
      "section": "下期策略改进建议",
      "content": "基于本期反思，给出3条可执行的策略改进建议（Loop Learning）：①信号层面（调整哪个策略的权重/阈值）；②仓位层面（止损纪律/加仓条件调整）；③信源层面（哪类事件/行业值得更高关注度）。每条建议必须可验证（说清楚'如果X则Y'）。100-200字。",
      "confidence": "high|medium|low"
    }
  ],
  "action_items": [
    {"priority": "high|medium|low", "action": "具体可执行操作", "trigger": "触发条件或时间"}
  ],
  "summary": "一句话策略总结（40字内）：当前最值得关注的矛盾/机会/风险，含1个具体标的或行业",
  "loop_learning": {
    "strategy_weights": {"report_agent": 0.0, "scanner": 0.0, "wanjun_models": 0.0},
    "key_lesson": "本期最重要的一条教训（20字内）",
    "next_focus": "下期最需关注的变量（15字内）"
  }
}""")

    return "\n".join(lines)


def _clean_json(text: str) -> dict:
    """从 Claude 响应中提取 JSON，多策略修复后返回；全部失败才返回 {}。"""
    if not text:
        return {}
    original = text

    def _try_parse(s):
        # LLM 常在 JSON 字符串内使用中文弯引号（" " ' '），破坏 JSON 语法
        # 直接替换为普通直引号，不影响语义
        s = s.replace('\u201c', '"').replace('\u201d', '"')   # " "
        s = s.replace('\u2018', "'").replace('\u2019', "'")   # ' '
        # 清理 trailing commas、控制字符
        s = re.sub(r",\s*([}\]])", r"\1", s)
        s = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', s)
        return json.loads(s)

    # 策略 1: ```json fence
    m = re.search(r'```(?:json)?\s*\n(.*?)\n\s*```', text, re.DOTALL)
    if m:
        try:
            return _try_parse(m.group(1))
        except Exception:
            pass

    # 策略 2: 找最外层 { ... }
    start = text.find("{")
    if start >= 0:
        # 从右扫描，找最长可解析前缀
        for end in range(len(text) - 1, start, -1):
            if text[end] == "}":
                try:
                    return _try_parse(text[start:end + 1])
                except Exception:
                    continue

    # 策略 3: 逐行拼接直到 JSON 合法（应对 LLM 多余输出）
    candidate = ""
    for line in original.split("\n"):
        candidate += line + "\n"
        if candidate.strip().endswith("}"):
            try:
                return _try_parse(candidate.strip())
            except Exception:
                pass

    # 全部失败：保存 debug，返回 {}
    debug_path = OUTPUT_FILE.parent / "strategy_reflection_debug_raw.txt"
    debug_path.write_text(
        f"# ParseFailure: all strategies failed\n# At: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n\n"
        f"## Original response (first 2000 chars):\n{original[:2000]}",
        encoding="utf-8"
    )
    print(f"  ⚠ JSON 解析全部失败，原始响应已存: {debug_path}")
    return {}


def _call_until_ok(fn, *, deadline_sec=1800, backoff_start=5, backoff_cap=60):
    """网关/网络瞬断时无限重试直到调通。
    - 只对网络类错误重试（连接/超时/限流），鉴权/参数错立即抛（重试无意义）。
    - 退避递增至 backoff_cap 封顶，避免打爆接口。
    - deadline_sec 安全阀：超时放弃，交给次日 cron，防止 cron 环境僵尸进程堆积。
    """
    import anthropic as _ant
    NET_ERRS = [_ant.APIConnectionError, _ant.APITimeoutError, _ant.RateLimitError]
    try:
        import openai as _oai  # DeepSeek 兜底路径才需要；环境未装则跳过
        NET_ERRS += [_oai.APIConnectionError, _oai.APITimeoutError, _oai.RateLimitError]
    except ImportError:
        pass
    NET_ERRS = tuple(NET_ERRS)
    start = time.time()
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except NET_ERRS as e:
            elapsed = time.time() - start
            if elapsed >= deadline_sec:
                print(f"  ✗ 已重试 {attempt} 次 / {int(elapsed)}s 仍未调通，放弃（交次日 cron）", file=sys.stderr)
                raise
            wait = min(backoff_start * attempt, backoff_cap)
            print(f"  第{attempt}次连接失败({type(e).__name__})，{wait}s后重试... [已耗时{int(elapsed)}s]")
            time.sleep(wait)


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

    # 主通道：S1 Opus 4.8 → 4.7 → 4.6 → sonnet-4-6 逐档降级，每档内 _call_until_ok 抗网络抖动
    MAIN_MODELS = ["s1-claude-opus-4-8", "s1-claude-opus-4-7", "s1-claude-opus-4-6", "claude-sonnet-4-6"]
    resp = None
    for m in MAIN_MODELS:
        try:
            print(f"  尝试主通道: {m}")
            resp = _call_until_ok(lambda m=m: client.messages.create(
                model=m,
                max_tokens=4096,
                system="你是A股量化策略顾问。输出简洁、有证据支撑的策略反思。",
                messages=[{"role": "user", "content": prompt}],
            ))
            break
        except Exception as e:
            print(f"  {m} 不可用: {e}，降级下一档...")
            continue

    if resp is None:
        # 三档 S1 Opus 全挂，落 DeepSeek 直连兜底（需环境装有 openai，未装则明确报错）
        print(f"  S1 Opus 三档全失败, 尝试 deepseek-chat...")
        try:
            import openai
        except ImportError:
            print("  ✗ 未安装 openai 模块，DeepSeek 兜底不可用，放弃（交次日 cron）", file=sys.stderr)
            sys.exit(1)
        openai_client = openai.OpenAI(
            api_key=os.environ.get("DEEPSEEK_API_KEY", API_KEY),
            base_url="https://api.deepseek.com/v1",
        )
        resp_openai = _call_until_ok(lambda: openai_client.chat.completions.create(
            model="deepseek-chat",
            max_tokens=4096,
            temperature=0.7,
            messages=[
                {"role": "system", "content": "你是A股量化策略顾问。"},
                {"role": "user", "content": prompt},
            ],
        ))
        raw_text = resp_openai.choices[0].message.content
        result = _clean_json(raw_text)
        result["model"] = "deepseek-chat"
        result["prompt_len"] = len(prompt)
    else:
        # thinking 模式下 content[0] 可能是 ThinkingBlock，需取首个 text 块（非硬取 [0].text）
        raw_text = next(
            (b.text for b in resp.content if getattr(b, "type", None) == "text"),
            None,
        )
        if raw_text is None:
            print(f"  ✗ 响应无 text 块（content types={[getattr(b,'type','?') for b in resp.content]}），放弃（交次日 cron）", file=sys.stderr)
            sys.exit(1)
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
            for s in ["事件驱动有效性回顾", "失败信号根因分析", "行业轮动因子质量",
                      "信号策略质量自评", "中短期标的调仓清单", "因子与叙事交叉验证", "下期策略改进建议"]
        ]
    # 确保 loop_learning 字段存在
    if "loop_learning" not in result:
        result["loop_learning"] = {"strategy_weights": {}, "key_lesson": "", "next_focus": ""}
    if "action_items" not in result:
        result["action_items"] = []

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ 输出: {OUTPUT_FILE} ({len(json.dumps(result, ensure_ascii=False))} bytes)")
    sections = [r.get("section","") for r in result.get("reflections",[])]
    print(f"  章节: {', '.join(sections[:4])}")


if __name__ == "__main__":
    main()
