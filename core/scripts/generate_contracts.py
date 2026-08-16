import json, os, sys
from datetime import datetime
from pathlib import Path

# tracer 可选导入
try:
    _base = Path(__file__).parent.parent.parent
    if str(_base) not in sys.path:
        sys.path.insert(0, str(_base))
    from core.utils.tracer import trace as _trace, new_run_id as _new_run_id
    from core.utils.events import EVT as _EVT
    _TRACE_OK = True
except ImportError:
    _TRACE_OK = False
    def _trace(*a, **kw): pass  # noqa: E731
    def _new_run_id(): return "noop"  # noqa: E731
    class _EVT:  # noqa: E302
        CONTRACT_WRITTEN = "ContractWrittenEvent"
        REPORT_AGENT_COMPLETED = "ReportAgentRunCompleted"

# V7.6 融合：路径归位到平台 data/（与其他脚本一致），CYCLERADAR_DATA_DIR 可覆盖；
# 契约文件输出到平台 data/，不再写交易员冻结目录 /opt/trader/output/contracts
DATA_DIR        = Path(os.environ.get("CYCLERADAR_DATA_DIR", Path(__file__).parent.parent.parent / "data"))
TRADER_STRATEGY = DATA_DIR / "trader_strategy.jsonl"
MORNING_JSON    = DATA_DIR / "morning.json"
CONTRACTS_DIR   = DATA_DIR
HOT_ENRICHMENT  = DATA_DIR / "hot_enrichment.json"

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

def generate_narrative():
    today = datetime.now()
    date_str = today.strftime("%Y-%m-%d")
    generated_at = today.strftime("%Y-%m-%dT%H:%M:%S")
    events = []
    morning = _read_json(MORNING_JSON)
    if morning:
        for ev in morning.get("events", [])[:8]:
            events.append({
                "rank": len(events)+1,
                "title": ev.get("title",""),
                "source": ev.get("source",""),
                "event_time": {"occurred_at": date_str, "certainty": "occurred", "decay_days": 0},
                "interpretation": ev.get("ai_abstract", ev.get("title","")),
                "sector_impact": [],
                "stock_impact": [],
                "commodity_impact": ""
            })
    hot = _read_json(HOT_ENRICHMENT)
    if hot:
        # 兼容 list（新格式）和 dict（旧格式），按 source_date 降序（发布日期新的优先），其次 enriched_at
        hot_items = hot if isinstance(hot, list) else list(hot.values())
        hot_items = sorted(hot_items,
            key=lambda x: (x.get("source_date", "") or x.get("enriched_at", "")[:10]),
            reverse=True)
        for val in hot_items:
            if not isinstance(val, dict): continue
            ts = val.get("enriched_at","")
            # ── decay 过期过滤 ──
            decay_days = val.get("decay_days", 3)  # 默认 3 天，旧数据无此字段时用默认值
            if ts and isinstance(decay_days, (int, float)) and decay_days > 0:
                try:
                    occurred = datetime.strptime(str(ts)[:10], "%Y-%m-%d")
                    if (today - occurred).days > int(decay_days):
                        continue  # 事件已过期，跳过
                except (ValueError, TypeError):
                    pass  # 日期解析失败，保留（不误删）
            # ── end decay ──
            thesis = val.get("thesis", "")
            # 过滤非市场内容（LLM 对营销/个人感悟类文章的标记）
            if not thesis or "非市场分析内容" in thesis:
                continue
            tickers = val.get("tickers", [])
            events.append({
                "rank": len(events)+1,
                "title": val.get("thesis","")[:100],
                "source": val.get("source", "ingest"),
                "source_title": val.get("title",""),
                "trigger_event": val.get("thesis","")[:200],
                "direct_reaction": "",
                "time_dimension": str(ts)[:10] if ts else date_str,
                "sector_transmission": [],
                "valuation_impact": "",
                "trading_window": "",
                "stock_mapping": [{"code": t.get("code",""), "name": t.get("name",""), "type": "long", "logic": t.get("reason","")} for t in tickers[:5]],
            })
    narrative = {
        "date": date_str, "source": "cycleradar-trader server pipeline",
        "generated_at": generated_at, "events": events[:12],
        "sector_outlook": morning.get("sector_outlook",[]) if morning else [],
        "global_conclusion": morning.get("global_conclusion","") if morning else "",
        "alpha_signals": morning.get("alpha_signals",[])[:10] if morning else []
    }
    return narrative

def main():
    import time
    t0 = time.time()
    run_id = _new_run_id()

    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)

    alpha = generate_alpha()
    alpha_path = CONTRACTS_DIR / "alpha_latest.json"
    with open(alpha_path, "w") as f:
        json.dump(alpha, f, ensure_ascii=False, indent=2)
    alpha_count = len(alpha.get("signals", []))
    print(f"alpha_latest.json: {alpha_count} signals")
    _trace(_EVT.CONTRACT_WRITTEN,
           input={"filename": "alpha_latest.json"},
           output={"signal_count": alpha_count, "path": str(alpha_path)},
           run_id=run_id)

    narrative = generate_narrative()
    narr_path = CONTRACTS_DIR / "event_narrative_latest.json"
    with open(narr_path, "w") as f:
        json.dump(narrative, f, ensure_ascii=False, indent=2)
    event_count = len(narrative.get("events", []))
    print(f"event_narrative_latest.json: {event_count} events")
    _trace(_EVT.CONTRACT_WRITTEN,
           input={"filename": "event_narrative_latest.json"},
           output={"signal_count": event_count, "path": str(narr_path)},
           run_id=run_id)

    _trace(_EVT.REPORT_AGENT_COMPLETED,
           input={"date": datetime.now().strftime("%Y-%m-%d")},
           output={"alpha_count": alpha_count, "event_count": event_count},
           run_id=run_id,
           latency_ms=int((time.time() - t0) * 1000))

if __name__ == "__main__":
    main()
