"""
daily.py — CycleRadar 日报自动化生成

用法：
  python daily.py                     # 完整流水线（数据采集 + LLM 生成）
  python daily.py --date 2026-03-03   # 指定日期
  python daily.py --data-only         # 仅采集数据，不调 LLM
  python daily.py --from-cache        # 用缓存数据 + LLM 生成
  python daily.py --dry-run           # 打印 prompt，不实际调 API

三阶段流水线：
  Phase 1: 数据采集（MCP + score.py + stock_analysis.py + verify.py）
  Phase 2: LLM 生成（Claude API → _rotation.json + HTML body）
  Phase 3: 保存（_rotation.json + _daily_data.json + v4 HTML）

依赖：anthropic, requests
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import traceback
from datetime import datetime, timedelta
from html import escape as _esc
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# Windows 终端 UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── 复用已有模块 ────────────────────────────────────────
from score import (
    mcp_call, set_mcp_cache_date, scan_all_industries, _load_plates, append_to_ledger,
    append_to_ledger_full,
    detect_rotation_signals, detect_exit_warnings,
    enrich_scan_results, compute_market_temperature,
    scan_concept_plates, _load_concept_plates,
    append_to_concept_ledger, filter_persistent_concepts,
    compute_multi_period_heat, _load_ledger,
    fetch_leader_board_institutional, fetch_block_trade_summary,
    fetch_margin_balance_surplus,
    INDUSTRY_LEADERS, RAW_DIR, HISTORY_DIR,
    save_scan,
)
from factor_agent import compose_score, enrich_with_composite, dedup_correlated_industries
from stock_analysis import analyze_stock, extract_stock_codes_from_wechat, STOCK_NAMES
from thesis_extractor import extract_all_sources
from stock_agent import build_smart_pool, score_stocks_within_industry
from event_agent import (
    detect_institutional_anomalies, detect_block_trade_signals,
    prioritize_events,
)
from verify import update_track_record, generate_block4_html, evaluate_signal_evolution

# V3.9: 行业轮动因子引擎（规则引擎，LLM fallback）
from rotation_factor import run_rotation_analysis

# P1: 并购/重组信号采集（AKShare 东方财富公告）
from ma_signals import collect_ma_signals, filter_ma_from_rss

# ── 配置 ──────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent
TEMPLATE_PATH = PROJECT_ROOT / "模板" / "行业轮动日报v4_模板.html"

# 新闻搜索方向（3 路）
NEWS_QUERIES = [
    "A股 行业 利好 今日",
    "地缘 冲突 经济 风险",
    "政策 产业 科技 两会",
]


# ══════════════════════════════════════════════════════
# 微信主题预提取（解决原文塞入prompt效率低的问题）
# ══════════════════════════════════════════════════════

_THEME_EXTRACTION_PROMPT = """\
你是股市信息提取助手。从以下微信公众号文章中提取所有讨论到的投资主题。

对每个主题，输出：
1. name: 主题名称（如"储能出海+算电协同"）
2. heat: 群友/作者对该主题的热度评价（如"全天最强主线"、"亢奋"、"平淡"）
3. stocks: 该主题下讨论的个股列表，每只包含：
   - name: 股票名称
   - code: 6位股票代码（如有，没有则留空字符串）
   - logic: 一句话博弈逻辑（如"大储出海龙头"、"甲醇涨价弹性"）
4. sentiment: 群友/市场情绪（如"极度FOMO"、"谨慎乐观"、"恐慌"）
5. key_debate: 核心分歧点（如"长周期反转 vs 短期脉冲"）

要求：
- 提取所有主题，不要遗漏
- 每只个股只归入最相关的一个主题
- 每个主题最多列出 10 只核心标的（优先选有代码的、讨论最多的）
- 如果文章是新闻摘要类（多条独立新闻），每条重要新闻算一个主题
- logic 字段尽量简短（5-10字）
- 输出纯 JSON 数组，不要其他内容
"""


def extract_wechat_themes(articles: list[dict]) -> list[dict]:
    """用便宜的 LLM 调用预提取微信文章中的投资主题结构。

    Returns:
        [{"name": str, "heat": str, "stocks": [...], "sentiment": str, "key_debate": str}, ...]
    """
    if not articles:
        return []

    # 拼接所有文章内容
    content_parts = []
    for a in articles:
        source = a.get("source", "未知")
        title = a.get("title", "")
        content = a.get("content", "")
        if content:
            content_parts.append(f"=== {source}：{title} ===\n{content}")

    if not content_parts:
        return []

    full_text = "\n\n".join(content_parts)

    # 调用便宜模型提取
    try:
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        base_url = os.environ.get("ANTHROPIC_BASE_URL")
        if not api_key:
            print("        → 无 API key，跳过主题提取")
            return []

        client = anthropic.Anthropic(api_key=api_key, base_url=base_url)

        # V3.2 Sprint 4: 从统一 MODEL_TIERS 读取 cheap 层级
        from report_agent import MODEL_TIERS, MODEL_FALLBACK
        CHEAP_MODELS = [MODEL_TIERS["cheap"]] + MODEL_FALLBACK["cheap"]
        last_err = None
        for model in CHEAP_MODELS:
            try:
                resp = client.messages.create(
                    model=model,
                    max_tokens=16000,
                    system=_THEME_EXTRACTION_PROMPT,
                    messages=[{"role": "user", "content": full_text[:15000]}],
                )
                raw = resp.content[0].text
                usage = resp.usage
                truncated = resp.stop_reason == "max_tokens"
                cost = (usage.input_tokens * 0.8 + usage.output_tokens * 4) / 1_000_000
                print(f"        → 主题提取完成 (模型={model}, "
                      f"输入={usage.input_tokens}, 输出={usage.output_tokens}, "
                      f"截断={'是' if truncated else '否'}, ~${cost:.3f})")

                # 解析 JSON（含截断修复）
                json_match = re.search(r"\[.*", raw, re.DOTALL)
                if json_match:
                    raw_json = json_match.group(0)
                    raw_json = re.sub(r",\s*([}\]])", r"\1", raw_json)
                    try:
                        themes = json.loads(raw_json)
                    except json.JSONDecodeError:
                        # 截断修复：从尾部扫描所有 } 位置，逐一尝试闭合数组
                        # rfind 只找最后一个，但截断点可能在字符串内部，需要向前回退
                        themes = None
                        pos = len(raw_json)
                        while pos > 0:
                            pos = raw_json.rfind("}", 0, pos)
                            if pos < 0:
                                break
                            candidate = raw_json[:pos + 1]
                            candidate = re.sub(r",\s*\]", "]", candidate) + "]"
                            candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
                            try:
                                themes = json.loads(candidate)
                                print(f"        → (输出被截断，已修复，保留至第 {len(themes)} 个主题)")
                                break
                            except json.JSONDecodeError:
                                pass
                        if themes is None:
                            print(f"        → (JSON 修复失败，跳过主题提取)")
                            return []
                    total_stocks = sum(len(t.get("stocks", [])) for t in themes)
                    print(f"        → {len(themes)} 个主题, {total_stocks} 只个股")
                    return themes
                else:
                    print(f"        → 模型输出无 JSON 数组")
                    return []
            except Exception as e:
                err_msg = str(e)
                if "403" in err_msg or "not found" in err_msg.lower():
                    last_err = e
                    continue
                raise

        print(f"        → 主题提取模型不可用: {last_err}")
        return []
    except ImportError:
        print("        → anthropic 未安装，跳过主题提取")
        return []
    except Exception as e:
        print(f"        → 主题提取失败: {e}")
        return []


# ── 新闻事件结构化预处理 ──

_NEWS_EVENT_EXTRACTION_PROMPT = """\
你是新闻事件分类助手。将多条新闻去重并按事件聚合。

输入：多条新闻的标题和摘要。
输出：JSON 数组，每个元素代表一个独立事件：
[
  {
    "title": "事件标题（10-20字）",
    "sources": ["来源1标题", "来源2标题"],
    "impact_sectors": ["可能影响的申万一级行业"],
    "timestamp": "最新相关新闻的时间",
    "importance": "high/medium/low",
    "summary": "事件核心内容（50字以内）"
  }
]

规则：
- 同一事件的多条新闻合并为一个 event（去重）
- 按影响力排序（high > medium > low）
- 最多输出 10 个事件
- 输出纯 JSON 数组，不要其他内容
"""


# ══════════════════════════════════════════════════════
# 晨报模式 (--morning)
# ══════════════════════════════════════════════════════

MORNING_SYSTEM_PROMPT = """\
你是「周期雷达」晨报分析师。基于下方多源数据，生成今日市场热点排名晨报。

## 核心任务
将所有数据源聚合为 **5-8 个排名主题**，回答："今天市场最关心什么？"

## 数据源及权重
1. **热度TOP30个股** (权重30%): 按关注度排序的30只股票，同一主题/概念的股票聚类
2. **微信信源主题** (权重25%): 公众号文章中的投资主题和散户情绪
3. **投资风口/热门行业** (权重20%): 当日概念板块涨幅和龙头股
4. **龙虎榜异动** (权重15%): 机构大额买卖、游资动向
5. **财经早报/新闻** (权重10%): 重大新闻事件催化

## 主题排名规则
- 排名依据 heat_score (0-100)，综合以上5个维度
- heat_score 计算：各维度归一化到0-20后按权重加总
  - hot30: 该主题关联的TOP30个股数量 × 2（最高20分）
  - wechat: 该主题在微信文章中被讨论的篇数/段落数 × 5（最高20分）
  - trending: 该主题在热门行业/概念中的排名（TOP3=20, TOP5=15, TOP10=10, 其他=0）
  - dragon_tiger: 该主题相关个股上龙虎榜的数量 × 5（最高20分）
  - news: 该主题相关新闻的重要性（high=20, medium=10, low=5, 无=0）

## 每个主题必须包含
1. theme: 主题名称（如"存储芯片超级周期"、"锂电反内卷"、"中东停火博弈"）
2. heat_score: 0-100 综合热度分
3. heat_sources: 各维度得分明细 {"hot30": N, "wechat": N, "trending": N, "dragon_tiger": N, "news": N}
4. related_sectors: 关联的申万一级行业列表
5. key_stocks: 该主题最受关注的2-3只个股 [{"name", "code", "reason"}]
   **code 必填要求**：必须是真实的 6 位数字股票代码（如 "300308"），不允许填 "未知" / 空字符串 / 占位符 / 港股代码（5位）。
   如果某只股票你不确定代码，请**不要列出**它，宁可少列也不要瞎写。
   code 可以从数据包中的 hot_top30 / trending_industry.faucet / 微信个股等处查找真实代码。
6. catalyst: 核心催化剂（一句话：是什么在驱动？）
7. so_what: 可操作含义（一句话：今天应该怎么做？）
8. sentiment: 市场情绪（亢奋/乐观/中性/谨慎/恐慌）

## 输出格式
输出一个 ```json 代码块，内容为：
{
  "date": "YYYY-MM-DD",
  "market_pulse": "一句话市场脉搏（20字以内）",
  "themes": [
    {"rank": 1, "theme": "...", "heat_score": 85, "heat_sources": {...}, "related_sectors": [...], "key_stocks": [...], "catalyst": "...", "so_what": "...", "sentiment": "..."},
    ...
  ]
}

## JSON 转义要求（V3.2 Sprint 4 必须遵守）
- **所有字符串值内部如需引用，请一律使用中文引号「」或『』，绝对不允许使用英文双引号 "**
- 错误示例：`"reason": "微信信源"极度看好"标的"`（会破坏 JSON 解析）
- 正确示例：`"reason": "微信信源「极度看好」标的"`
- 如确需英文引号表示专有名词（如 "iPhone"），必须用 \\" 转义：`"reason": "\\\"iPhone\\\" 概念股"`

## 写作风格
- 结论先行：so_what 必须有明确操作方向
- 数据嵌入：引用具体个股、具体涨幅、具体金额
- 不虚构：没有数据支撑的主题不要编造
- 仅供参考，不构成投资建议
"""


def collect_morning_data(date_str: str) -> dict:
    """轻量数据采集：晨报专用（5-7个MCP调用，<2分钟）。"""
    from datetime import datetime, timedelta
    import re as _re

    print(f"\n{'━' * 50}")
    print(f"  晨报数据采集")
    print(f"  日期: {date_str}")
    print(f"{'━' * 50}")

    data = {"date": date_str}
    t1 = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")

    # 1. 早报
    print("  [1/7] 财经早报...")
    try:
        morning = mcp_call("news", "get_alpha_morning", {})
        items = morning if isinstance(morning, list) else morning.get("data", [])
        data["morning_news"] = items
        print(f"        → {len(items)} 条")
    except Exception as e:
        print(f"        → 失败: {e}")
        data["morning_news"] = []

    # 2. 热度TOP30
    print("  [2/7] 热度TOP30...")
    try:
        hot = mcp_call("news", "get_stock_hot_top30", {"is_new": 1})
        hot_items = hot if isinstance(hot, list) else hot.get("data", [])
        if hot_items:
            latest = max((it.get("date", "") for it in hot_items if isinstance(it, dict)), default="")
            if latest:
                hot_items = [it for it in hot_items if isinstance(it, dict) and it.get("date") == latest]
        data["hot_top30"] = hot_items[:30]
        print(f"        → {len(data['hot_top30'])} 只")
    except Exception as e:
        print(f"        → 失败: {e}")
        data["hot_top30"] = []

    # 3. 热门概念+龙头
    print("  [3/7] 投资风口...")
    try:
        trending = mcp_call("market_quote", "get_trending_industry", {"date": t1})
        data["trending_industry"] = trending.get("trendingInfo", []) if isinstance(trending, dict) else []
        print(f"        → {len(data['trending_industry'])} 个概念")
    except Exception as e:
        print(f"        → 失败: {e}")
        data["trending_industry"] = []

    # 4. 龙虎榜
    print("  [4/7] 龙虎榜...")
    try:
        from factor_agent import fetch_leader_board_institutional
        lb = fetch_leader_board_institutional(t1)
        data["leader_board"] = lb
        n = len(lb.get("by_stock", {})) if isinstance(lb, dict) else 0
        print(f"        → {n} 只个股")
    except Exception as e:
        print(f"        → 失败: {e}")
        data["leader_board"] = {}

    # 5. 微信主题（直接从 [2.5/8] 已采集的 wechat_sources 提取）
    print("  [5/7] 微信主题...")
    wechat_themes = []
    cache_source_articles = {}

    wechat_sources = data.get("wechat_sources", {})
    wechat_articles = wechat_sources.get("articles", [])

    if wechat_articles:
        quality = [a for a in wechat_articles
                   if len(a.get("content", "")) >= 200
                   and not any(kw in a.get("content", "")[:100]
                               for kw in ["免费领取", "加我微信", "违规删"])]
        if quality:
            wechat_themes = extract_wechat_themes(quality[:15])
            source_label = wechat_sources.get("source", "unknown")
            for a in quality:
                src = a.get("source", "unknown")
                cache_source_articles.setdefault(src, []).append(a)
            print(f"        → {len(wechat_themes)} 个主题（{source_label} {len(quality)} 篇）")
        else:
            print(f"        → 微信文章质量不足: {len(wechat_articles)} 篇无长文（需 ≥200 字）")
    else:
        print(f"        → 无微信文章（[2.5/8] wechat_sources 为空）")

    data["wechat_themes"] = wechat_themes

    # 5.1 微信深度解析（thesis 提取）
    wechat_deep = {}
    if cache_source_articles:
        print("  [5.1] 微信深度解析（thesis_extractor）...")
        try:
            wechat_deep = extract_all_sources(cache_source_articles)
            n_thesis = sum(len(v) for v in wechat_deep.values())
            print(f"        → {n_thesis} 条 thesis 提取")
            # 保存 thesis 结果到 cache 目录供 dashboard 读取
            from wechat_cache_collector import CACHE_DIR
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            thesis_path = CACHE_DIR / f"thesis_{date_str}.json"
            with open(thesis_path, "w", encoding="utf-8") as f:
                json.dump(wechat_deep, f, ensure_ascii=False, indent=2)
            # 入库 thesis_ledger
            try:
                from thesis_ledger import ingest_from_cache
                ingest_from_cache(date_str)
            except Exception:
                pass
        except Exception as e:
            print(f"        → thesis_extractor 异常（非阻塞）: {e}")
    data["wechat_deep"] = wechat_deep

    # 6. 新闻聚合
    print("  [6/7] 新闻搜索+聚合...")
    search_results = {}
    for q in ["A股 市场 热点 行业", "政策 产业 科技 利好"]:
        try:
            r = mcp_call("news", "search_news", {"keyword": q, "topk": 5,
                                                  "start_date": t1, "end_date": date_str})
            items = r if isinstance(r, list) else r.get("data", [])
            search_results[q] = items
        except Exception:
            search_results[q] = []
    total_news = sum(len(v) for v in search_results.values())
    print(f"        → {total_news} 条新闻")

    news_events = []
    if data["morning_news"] or total_news > 0:
        news_events = extract_news_events(data["morning_news"], search_results)
        print(f"        → {len(news_events)} 个聚合事件")
    data["news_events"] = news_events

    # 7. T-1 行业资金面（读缓存，0成本）
    print("  [7/7] T-1行业资金...")
    plates_t1 = []
    for d in [t1, date_str]:
        pf = RAW_DIR / f"{d}.json"
        if pf.exists():
            try:
                with open(pf, "r", encoding="utf-8") as f:
                    pdata = json.load(f)
                plates_t1 = pdata.get("plates", pdata.get("plates_ranking", []))
                if plates_t1:
                    print(f"        → {len(plates_t1)} 个行业（来自 {d}）")
                    break
            except Exception:
                pass
    data["plates_t1"] = plates_t1
    if not plates_t1:
        print("        → 无缓存")

    print(f"\n  晨报数据采集完成 ✓")
    return data


def build_morning_prompt(data: dict) -> str:
    """将晨报数据构造为 LLM user prompt。"""
    import re as _re
    parts = [f"日期：{data['date']}\n"]

    # 1. 早报正文
    morning = data.get("morning_news", [])
    if morning:
        parts.append("## 财经早报\n")
        for item in morning[:3]:
            title = item.get("title", "")
            source = item.get("from_source") or item.get("author", "")
            content = item.get("content", "")[:2000]
            content = _re.sub(r"<br\s*/?>", "\n", content)
            content = _re.sub(r"<img[^>]*>", "", content)
            content = _re.sub(r"<[^>]+>", "", content)
            if content.strip():
                parts.append(f"### [{source}] {title}")
                parts.append(content.strip())
                parts.append("")

    # 2. 热度TOP30
    hot = data.get("hot_top30", [])
    if hot:
        parts.append(f"## 热度TOP30个股（{len(hot)}只）\n")
        for i, item in enumerate(hot[:20], 1):
            code = item.get("security_code", "") or item.get("code", "")
            name = item.get("security_name", "") or item.get("name", "")
            desc = item.get("description", "")[:60]
            if name:
                parts.append(f"  {i}. {name}({code}) {desc}")
        parts.append("")

    # 3. 投资风口
    trending = data.get("trending_industry", [])
    if trending:
        parts.append(f"## 投资风口/热门概念（{len(trending)}个）\n")
        for ti in trending:
            title = ti.get("trendingTitle", "")
            interp = ti.get("interpret", "")[:100]
            chg = ti.get("trendingPriceChangeRate", "")
            faucets = ti.get("faucet", [])
            stocks_str = ", ".join(
                f"{f.get('securityName', '')}({f.get('priceChangeRate', '')})"
                for f in faucets if f.get("securityName"))
            parts.append(f"### {title} ({chg})")
            if interp:
                parts.append(f"  {interp}")
            if stocks_str:
                parts.append(f"  龙头: {stocks_str}")
            parts.append("")

    # 4. 微信主题
    themes = data.get("wechat_themes", [])
    if themes:
        parts.append(f"## 微信信源主题（{len(themes)}个）\n")
        for t in themes:
            name = t.get("theme", t.get("name", ""))
            heat = t.get("heat", "")
            summary = t.get("summary", "")[:100]
            stocks = t.get("stocks", [])
            stocks_str = ", ".join(
                (f"{s.get('name', '')}({s.get('code', '')})" if isinstance(s, dict) else str(s))
                for s in stocks[:5])
            parts.append(f"  【{name}】热度:{heat}")
            if summary:
                parts.append(f"    {summary}")
            if stocks_str:
                parts.append(f"    个股: {stocks_str}")
        parts.append("")

    # 5. 龙虎榜
    lb = data.get("leader_board", {})
    by_stock = lb.get("by_stock", {}) if isinstance(lb, dict) else {}
    if by_stock:
        # 取净买入最大的TOP10
        sorted_stocks = sorted(by_stock.items(), key=lambda x: x[1].get("net", 0), reverse=True)
        parts.append(f"## 龙虎榜异动（{len(by_stock)}只个股）\n")
        parts.append("净买入TOP10:")
        for code, info in sorted_stocks[:10]:
            name = info.get("name", code)
            net = info.get("net", 0)
            parts.append(f"  {name}({code}) 净买入{net:.0f}万元")
        parts.append("")

    # 6. 新闻事件聚合
    events = data.get("news_events", [])
    if events:
        parts.append(f"## 新闻事件聚合（{len(events)}个）\n")
        for ev in events:
            imp = ev.get("importance", "medium")
            title = ev.get("title", "")
            summary = ev.get("summary", "")
            sectors = ev.get("impact_sectors", [])
            parts.append(f"  [{imp}] {title}")
            if summary:
                parts.append(f"    {summary}")
            if sectors:
                parts.append(f"    影响行业: {', '.join(sectors)}")
        parts.append("")

    # 7. T-1行业资金
    plates = data.get("plates_t1", [])
    if plates:
        # 按资金净流入排序
        sorted_plates = sorted(plates, key=lambda p: float(
            str(p.get("major_net_flow_in", 0)).replace(",", "").replace("亿", "").strip() or 0
        ), reverse=True)
        parts.append("## T-1行业资金TOP10\n")
        for p in sorted_plates[:10]:
            name = p.get("plate_name", "")
            chg = p.get("price_change_rate", "")
            flow = p.get("major_net_flow_in", "")
            if name:
                parts.append(f"  {name}: 涨跌{chg} 主力净流入{flow}")
        parts.append("")

    return "\n".join(parts)


def generate_morning_report(data: dict, dry_run: bool = False,
                            model: str | None = None) -> dict | None:
    """晨报 LLM 生成。"""
    from report_agent import call_claude_api, parse_llm_output

    print(f"\n{'━' * 50}")
    print(f"  晨报 LLM 生成")
    print(f"{'━' * 50}")

    user_prompt = build_morning_prompt(data)

    if dry_run:
        print("\n  ── System Prompt ──")
        print(MORNING_SYSTEM_PROMPT[:300] + "...")
        print(f"\n  ── User Prompt ({len(user_prompt)} 字符) ──")
        print(user_prompt)
        return None

    text = call_claude_api(MORNING_SYSTEM_PROMPT, user_prompt,
                           model=model, tier="standard")
    report, _ = parse_llm_output(text)

    if report:
        themes = report.get("themes", [])
        if not themes:
            print("  ⚠ 无主题输出")
            return None

        # V3.2 P3: 清理无效的 key_stocks code（必须是 6 位数字）
        import re as _re
        invalid_count = 0
        for t in themes:
            clean_stocks = []
            for s in t.get("key_stocks", []):
                if not isinstance(s, dict):
                    continue
                code = str(s.get("code", "")).strip()
                name = str(s.get("name", "")).strip()
                if not name:
                    invalid_count += 1
                    continue
                # code 必须是 6 位纯数字
                if not _re.fullmatch(r"\d{6}", code):
                    print(f"    ⚠ 剔除无效 code: {name}({code or '空'}) — {t.get('theme', '')}")
                    invalid_count += 1
                    continue
                clean_stocks.append(s)
            t["key_stocks"] = clean_stocks
        if invalid_count:
            print(f"  ⚠ 共清理 {invalid_count} 个无效 code 个股")

        print(f"  晨报: {len(themes)} 个主题 ✓")
        for t in themes:
            print(f"    #{t.get('rank', '?')} [{t.get('heat_score', '?')}] {t.get('theme', '?')}")
    else:
        print("  ⚠ 未提取到 JSON")

    return report


def build_morning_html(date_str: str, report: dict) -> Path | None:
    """从晨报 JSON 构建 HTML。"""
    themes = report.get("themes", [])
    if not themes:
        return None

    pulse = report.get("market_pulse", "")

    # CSS
    css = """
body { font-family: -apple-system, 'PingFang SC', sans-serif; max-width: 520px; margin: 0 auto;
       padding: 0 16px 32px; background: #f5f5f5; color: #1a1a1a; font-size: 16px; }
.hd { padding: 24px 0; text-align: center; }
.hd-brand { font-size: 12px; color: #aaa; letter-spacing: 2px; }
.hd h1 { font-size: 22px; font-weight: 800; margin: 12px 0 0; line-height: 1.4; }
.card { background: #fff; border-radius: 12px; padding: 20px; margin: 12px 0;
        box-shadow: 0 1px 4px rgba(0,0,0,0.06); }
.card-top { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.rank-badge { width: 32px; height: 32px; border-radius: 50%; background: #4f46e5; color: #fff;
              display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 14px; flex-shrink: 0; }
.rank-badge.hot { background: #dc2626; }
.theme-name { font-size: 18px; font-weight: 700; flex: 1; }
.heat-score { font-size: 14px; font-weight: 700; color: #4f46e5; }
.heat-bar { height: 4px; border-radius: 2px; background: #e5e7eb; margin: 0 0 12px; }
.heat-fill { height: 100%; border-radius: 2px; background: linear-gradient(90deg, #4f46e5, #dc2626); }
.catalyst { font-size: 15px; color: #555; margin-bottom: 8px; }
.stocks { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.chip { display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 13px;
        background: #f0f4ff; color: #4f46e5; font-weight: 500; }
.so-what { background: #f0fdf4; border-left: 3px solid #16a34a; padding: 10px 14px;
           font-size: 15px; font-weight: 600; color: #166534; border-radius: 0 8px 8px 0; margin-bottom: 8px; }
.sentiment { font-size: 12px; padding: 2px 8px; border-radius: 4px; display: inline-block; }
.s-bullish { background: #dcfce7; color: #166534; }
.s-neutral { background: #f3f4f6; color: #6b7280; }
.s-bearish { background: #fee2e2; color: #dc2626; }
.ft { text-align: center; padding: 20px 0; color: #aaa; font-size: 12px; }
"""

    parts = [f'<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8">',
             f'<meta name="viewport" content="width=device-width,initial-scale=1">',
             f'<title>周期雷达晨报 {date_str}</title>',
             f'<style>{css}</style></head><body>']

    # Header
    from html import escape as _esc
    parts.append(f'<div class="hd">')
    parts.append(f'  <div class="hd-brand">周期雷达 · 晨报 &nbsp; {date_str}</div>')
    parts.append(f'  <h1>{_esc(pulse)}</h1>')
    parts.append(f'</div>')

    # Theme cards
    sentiment_map = {"亢奋": "s-bullish", "乐观": "s-bullish", "中性": "s-neutral",
                     "谨慎": "s-bearish", "恐慌": "s-bearish"}
    for t in themes:
        rank = t.get("rank", "?")
        theme = _esc(t.get("theme", ""))
        score = t.get("heat_score", 0)
        catalyst = _esc(t.get("catalyst", ""))
        so_what = _esc(t.get("so_what", ""))
        sentiment = t.get("sentiment", "中性")
        s_cls = sentiment_map.get(sentiment, "s-neutral")
        rank_cls = "rank-badge hot" if rank <= 2 else "rank-badge"

        parts.append(f'<div class="card">')
        parts.append(f'  <div class="card-top">')
        parts.append(f'    <div class="{rank_cls}">#{rank}</div>')
        parts.append(f'    <div class="theme-name">{theme}</div>')
        parts.append(f'    <div class="heat-score">{score}/100</div>')
        parts.append(f'  </div>')
        parts.append(f'  <div class="heat-bar"><div class="heat-fill" style="width:{score}%"></div></div>')
        if catalyst:
            parts.append(f'  <div class="catalyst">{catalyst}</div>')

        stocks = t.get("key_stocks", [])
        if stocks:
            chips = " ".join(
                f'<span class="chip">{_esc(s.get("name", ""))}({_esc(s.get("code", ""))})</span>'
                for s in stocks[:3])
            parts.append(f'  <div class="stocks">{chips}</div>')

        if so_what:
            parts.append(f'  <div class="so-what">{so_what}</div>')

        parts.append(f'  <span class="sentiment {s_cls}">{_esc(sentiment)}</span>')
        parts.append(f'</div>')

    # Footer
    parts.append(f'<div class="ft">周期雷达 · {date_str} · 仅供参考，不构成投资建议</div>')
    parts.append(f'</body></html>')

    out_dir = PROJECT_ROOT / "output" / "morning"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"晨报_{date_str.replace('-', '')}.html"
    out_path.write_text("\n".join(parts), encoding="utf-8")
    return out_path


def save_morning_outputs(date_str: str, report: dict | None, raw_data: dict):
    """保存晨报输出。"""
    from report_agent import _save_json

    print(f"\n{'━' * 50}")
    print(f"  晨报保存")
    print(f"{'━' * 50}")

    RAW_DIR.mkdir(parents=True, exist_ok=True)

    data_path = RAW_DIR / f"{date_str}_morning_data.json"
    _save_json(data_path, raw_data)
    print(f"  数据包: {data_path}")

    if report:
        report_path = RAW_DIR / f"{date_str}_morning.json"
        _save_json(report_path, report)
        print(f"  主题JSON: {report_path}")

        html_path = build_morning_html(date_str, report)
        if html_path:
            print(f"  晨报HTML: {html_path}")


# ══════════════════════════════════════════════════════
# 公众号文章模式 (--article)
# ══════════════════════════════════════════════════════

ARTICLE_SYSTEM_PROMPT = """\
你是「周期雷达」的公众号主笔。你的任务是把结构化的晨报数据改写成一篇**有人格、有态度、通俗易懂**的公众号文章。

## 你的人格
- 你是一个在券商干了10年、现在出来做自媒体的老司机
- 说话直接，敢下判断，但不忽悠
- 擅长用类比和生活化的语言解释复杂的金融逻辑
- 偶尔自嘲，有幽默感，但不油腻

## 文章结构（严格遵守）

### 1. 标题（12-20字）
- 必须制造冲突、悬念或数字冲击
- 好标题："原油一周崩了15%，谁在偷偷抄底？"
- 烂标题："2026年4月10日行业轮动日报"
- 输出两个标题供选择

### 2. 开头钩子（50字以内）
- 禁止用"今日A股三大指数..."开头
- 用一个让人想继续读的问题、冲突或画面感开场
- 例："昨天还是板块老大，今天就跌到第41名——石油股的投资者，昨晚怕是没睡好。"

### 3. 今日一句话（加粗，一句话说清楚今天该干嘛）

### 4. 三件事你必须知道（编号❶❷❸）
- 从晨报TOP主题中选3个最重要的
- 每件事≤100字，说人话
- 格式：一句话结论 + 一句话数据支撑

### 5. 深度拆解（只拆1-2个主题）

**五步推导格式（必须显式呈现每一步）**：
触发事件 → 直接反应 → 产业传导 → 估值重塑 → 操作窗口

每步必须附具体数据（数字/机构名/时间节点），禁止空洞表述。

**线框图（每个拆解主题必须包含一张）**：
用以下 HTML 结构输出传导链可视化（微信兼容，禁用外部资源）：
```html
<div style="display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin:16px 0;">
  <div style="background:#1a1a2e;color:#fff;padding:8px 14px;border-radius:8px;font-size:14px;font-weight:700;">触发事件名</div>
  <div style="color:#e94560;font-size:18px;font-weight:700;">→</div>
  <div style="background:#16213e;color:#e2e8f0;padding:8px 14px;border-radius:8px;font-size:14px;">直接反应</div>
  <div style="color:#e94560;font-size:18px;font-weight:700;">→</div>
  <div style="background:#0f3460;color:#e2e8f0;padding:8px 14px;border-radius:8px;font-size:14px;">产业传导</div>
  <div style="color:#e94560;font-size:18px;font-weight:700;">→</div>
  <div style="background:#e94560;color:#fff;padding:8px 14px;border-radius:8px;font-size:14px;font-weight:700;">操作窗口</div>
</div>
```
节点文字用实际事件/行业/标的替换，节点数量视逻辑链长度调整（3-6个）。

**结论必须有数据背书**：每个"所以呢？"段落必须引用至少2个具体数据点（涨幅/PE分位/成交额/机构持仓变化等）作为依据，不允许只有观点没有数据。

提到个股时用「」标注，如「长芯博创」

### 6. 今日关注清单
- 3-5只个股，卡片式展示
- 每只**必须包含3个数据点**（从以下选：涨幅/换手率/PE分位/主力净流入/机构持仓/关键价位/目标价）
- 数据缺失的字段直接省略，禁止用"—"或"N/A"占位
- 格式：一句话核心逻辑 + 3个数据标签

### 7. 风险提醒（一句话，口语化）

### 8. 尾签
周期雷达 · 让数据说话
（关注公众号，每天早上8点更新）

## 写作红线
- 禁止使用"确认/关注/观望"等行业术语
- 禁止暴露"数据缺失""N/A""评分X/3"
- 禁止超过2500字
- 禁止全文没有一个具体数字
- 禁止深度拆解中没有线框图
- 禁止结论段没有数据支撑
- "仅供参考，不构成投资建议"只在文末出现一次

## 输出格式
直接输出 ```html 代码块，包含完整的公众号文章HTML。
使用微信公众号兼容的内联样式（不用class，用style属性）。
字号18px，行高2.0，正文宽度100%。
"""


def generate_article(date_str: str, morning_json: dict,
                     dry_run: bool = False,
                     model: str | None = None) -> str | None:
    """从晨报JSON二次创作公众号文章。"""
    from report_agent import call_claude_api

    print(f"\n{'━' * 50}")
    print(f"  公众号文章生成")
    print(f"{'━' * 50}")

    user_prompt = f"日期：{date_str}\n\n晨报数据：\n```json\n{json.dumps(morning_json, ensure_ascii=False, indent=2)}\n```"

    if dry_run:
        print(f"\n  ── Prompt ({len(user_prompt)} 字符) ──")
        print(user_prompt[:500] + "...")
        return None

    text = call_claude_api(ARTICLE_SYSTEM_PROMPT, user_prompt,
                           model=model, tier="premium")

    # 提取 HTML
    import re
    html_match = re.search(r"```html\s*\n(.*?)\n```", text, re.DOTALL)
    if html_match:
        html = html_match.group(1).strip()
        print(f"  文章生成完成 ✓ ({len(html)} 字符)")
        return html

    # 兜底：如果没有 html 块，整段当正文
    if "<" in text and ">" in text:
        print(f"  文章生成完成（无html标记） ✓ ({len(text)} 字符)")
        return text

    print(f"  ⚠ 未提取到文章 HTML")
    return None


def save_article(date_str: str, html: str) -> Path | None:
    """保存公众号文章。"""
    out_dir = PROJECT_ROOT / "output" / "article"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"公众号_{date_str.replace('-', '')}.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


def extract_news_events(morning_news, search_results: dict) -> list[dict]:
    """用便宜 LLM 对新闻做去重+聚合+排序预处理。"""
    seen_titles: set[str] = set()
    news_parts: list[str] = []

    # 从 morning_news 提取
    if isinstance(morning_news, list):
        items = morning_news
    elif isinstance(morning_news, dict) and not morning_news.get("_error"):
        items = morning_news.get("data", morning_news.get("items", []))
        if not isinstance(items, list):
            items = []
    else:
        items = []

    for item in items:
        title = item.get("title", "") if isinstance(item, dict) else str(item)
        if title and title not in seen_titles:
            seen_titles.add(title)
            summary = (item.get("summary", "") or item.get("content", "")
                        if isinstance(item, dict) else "")
            # 截断长内容，保留核心信息
            if len(summary) > 500:
                summary = summary[:500] + "..."
            news_parts.append(f"标题: {title}\n摘要: {summary}")

    # 从 search_results 提取
    for query, result in search_results.items():
        if not result or (isinstance(result, dict) and result.get("_error")):
            continue
        result_items = result if isinstance(result, list) else result.get("data", [])
        if not isinstance(result_items, list):
            continue
        for item in result_items:
            title = item.get("title", "") if isinstance(item, dict) else ""
            if title and title not in seen_titles:
                seen_titles.add(title)
                summary = (item.get("summary", "") or item.get("content", "")
                            if isinstance(item, dict) else "")
                if len(summary) > 500:
                    summary = summary[:500] + "..."
                news_parts.append(f"标题: {title}\n摘要: {summary}")

    if not news_parts:
        return []

    full_text = "\n---\n".join(news_parts)

    try:
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        base_url = os.environ.get("ANTHROPIC_BASE_URL")
        if not api_key:
            print("        → 无 API key，跳过新闻聚合")
            return []

        client = anthropic.Anthropic(api_key=api_key, base_url=base_url)

        # V3.2 Sprint 4: 统一 cheap 层级
        from report_agent import MODEL_TIERS, MODEL_FALLBACK
        CHEAP_MODELS = [MODEL_TIERS["cheap"]] + MODEL_FALLBACK["cheap"]
        last_err = None
        for model in CHEAP_MODELS:
            try:
                resp = client.messages.create(
                    model=model,
                    max_tokens=4000,
                    system=_NEWS_EVENT_EXTRACTION_PROMPT,
                    messages=[{"role": "user", "content": full_text[:10000]}],
                )
                raw = resp.content[0].text
                usage = resp.usage
                cost = (usage.input_tokens * 0.8 + usage.output_tokens * 4) / 1_000_000
                print(f"        → 新闻聚合完成 (模型={model}, "
                      f"输入={usage.input_tokens}, 输出={usage.output_tokens}, "
                      f"~${cost:.3f})")

                # 解析 JSON（含截断修复 + 容错）
                # 剥离 markdown code fence
                cleaned = raw.strip()
                if cleaned.startswith("```"):
                    cleaned = re.sub(r"^```\w*\n?", "", cleaned)
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3].rstrip()
                start_idx = cleaned.find("[")
                if start_idx >= 0:
                    raw_json = cleaned[start_idx:]
                    # 找最后一个 ]，截掉之后的内容
                    end_idx = raw_json.rfind("]")
                    if end_idx > 0:
                        raw_json = raw_json[:end_idx + 1]
                    try:
                        events = json.loads(raw_json)
                    except json.JSONDecodeError:
                        # 尝试去除尾逗号后重试
                        raw_json2 = re.sub(r",\s*([}\]])", r"\1", raw_json)
                        try:
                            events = json.loads(raw_json2)
                        except json.JSONDecodeError:
                            # 截断修复：找最后一个完整 }，闭合数组
                            last_brace = raw_json2.rfind("}")
                            if last_brace > 0:
                                raw_json2 = raw_json2[:last_brace + 1] + "]"
                                try:
                                    events = json.loads(raw_json2)
                                    print(f"        → (输出被截断，已修复)")
                                except json.JSONDecodeError:
                                    events = []
                                    print(f"        → JSON 修复失败，跳过")
                            else:
                                events = []
                                print(f"        → JSON 结构无法解析，跳过")
                    print(f"        → {len(events)} 个聚合事件")
                    return events
                else:
                    print(f"        → 模型输出无 JSON 数组")
                    return []
            except Exception as e:
                err_msg = str(e)
                if "403" in err_msg or "not found" in err_msg.lower():
                    last_err = e
                    continue
                raise

        print(f"        → 新闻聚合模型不可用: {last_err}")
        return []
    except ImportError:
        print("        → anthropic 未安装，跳过新闻聚合")
        return []
    except Exception as e:
        print(f"        → 新闻聚合失败: {e}")
        return []


# ══════════════════════════════════════════════════════
# Phase 1: 数据采集
# ══════════════════════════════════════════════════════

def _load_prev_plates(date_str: str) -> list | None:
    """加载前一个交易日的 plates 快照（用于 D3 涨跌比对比）。"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    for days_back in range(1, 8):
        prev_dt = dt - timedelta(days=days_back)
        prev_compact = prev_dt.strftime("%Y%m%d")
        path = HISTORY_DIR / f"plates_{prev_compact}.json"
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("plates", [])
            except (json.JSONDecodeError, OSError):
                continue
    return None


def _ingest_wechat_urls(urls: list[str], date_str: str):
    """入库微信 URL 列表到 {date}_wechat.json，复用 scrape_wechat 逻辑。"""
    try:
        from scrape_wechat import fetch_article
    except ImportError:
        print("  ⚠ scrape_wechat 不可用，跳过 URL 入库")
        return

    articles = []
    for u in urls:
        art = fetch_article(u)
        if art:
            print(f"    ✓ {art['source']} | {art['title']} | {art['content_length']}字")
            articles.append(art)
    if not articles:
        return

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RAW_DIR / f"{date_str}_wechat.json"

    if out_path.exists():
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            existing_urls = {a.get("url") for a in existing.get("articles", []) if a.get("url")}
            existing_titles = {a.get("title") for a in existing.get("articles", [])}
            new_articles = [a for a in articles
                           if (not a.get("url") or a["url"] not in existing_urls)
                           and (not a.get("title") or a["title"] not in existing_titles)]
            if new_articles:
                existing["articles"].extend(new_articles)
                existing["collected_at"] = datetime.now().isoformat()
                print(f"    追加 {len(new_articles)} 篇（共 {len(existing['articles'])} 篇）")
            else:
                print(f"    所有文章已存在，无需更新")
                return
            result = existing
        except (json.JSONDecodeError, OSError):
            result = {"date": date_str, "collected_at": datetime.now().isoformat(),
                      "articles": articles, "sogou_snippets": []}
    else:
        result = {"date": date_str, "collected_at": datetime.now().isoformat(),
                  "articles": articles, "sogou_snippets": []}

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"    保存: {out_path.name}")


def collect_data(date_str: str, from_cache: bool = False, wechat_urls: list[str] | None = None) -> dict:
    """采集全部数据，返回结构化数据包。"""
    # 启用 MCP 缓存：同一天重跑自动命中缓存
    set_mcp_cache_date(date_str)

    print(f"\n{'━' * 50}")
    print(f"  Phase 1: 数据采集")
    print(f"  日期: {date_str}  模式: {'缓存' if from_cache else '在线（MCP 自动缓存）'}")
    print(f"{'━' * 50}")

    data = {"date": date_str, "collected_at": datetime.now().isoformat()}

    # ── 1. 全行业扫描（最先跑，后续步骤依赖 TOP 行业列表）──
    print("\n  [1/8] 全行业扫描...")
    plates = _load_plates(date_str, from_cache=from_cache)
    if plates:
        market_temp = compute_market_temperature(plates)
        mt_score = market_temp.get("score", 50) if isinstance(market_temp, dict) else market_temp
        mt_label = market_temp.get("temperature", "normal") if isinstance(market_temp, dict) else "normal"
        print(f"        → 市场温度: {mt_label} ({mt_score})")

        # V3.0: 采集 D1/D2 因子数据
        inst_data = {}
        block_data = {}
        margin_data = {}
        if not from_cache:
            print("  [1.1/8] 龙虎榜机构数据...")
            inst_data = fetch_leader_board_institutional(date_str)
            n_inst_stocks = len(inst_data.get("by_stock", {}))
            n_inst_ind = len(inst_data.get("by_industry", {}))
            print(f"        → {inst_data.get('raw_count', 0)}条龙虎榜, "
                  f"{n_inst_stocks}只有机构席位, {n_inst_ind}个行业命中")
            print("  [1.2/8] 大宗交易数据...")
            block_data = fetch_block_trade_summary(date_str)
            n_block_stocks = len(block_data.get("by_stock", {}))
            n_block_ind = len(block_data.get("by_industry", {}))
            print(f"        → {n_block_stocks}只有大宗交易, {n_block_ind}个行业命中")
            print("  [1.3/8] 融资热度数据（B2因子）...")
            margin_data = fetch_margin_balance_surplus(date_str)
            n_margin = sum(1 for v in margin_data.values() if v)
            print(f"        → {len(margin_data)}行业入TOP30, {n_margin}个净买入为正")

        scan_results = scan_all_industries(plates)
        # 保存 D1/D2/B2 原始数据供事件Agent和个股Agent使用
        data["inst_data"] = inst_data
        data["block_data"] = block_data
        data["margin_data"] = margin_data
        scan_results = enrich_scan_results(scan_results, {})
        # S1.6: 复合因子分（加权合成，替代简单加和排序）
        scan_results = enrich_with_composite(scan_results)
        # V3.8: 相关性去重（同风格行业只保留排名最高的）
        deduped_top10 = dedup_correlated_industries(scan_results, top_n=10)
        ledger = append_to_ledger(date_str, scan_results)
        # V3.0: 全行业台账（存储全部49行业因子明细，供IC验证）
        full_ledger = append_to_ledger_full(date_str, scan_results)
        n_full = full_ledger["weeks"][-1]["count"] if full_ledger["weeks"] else 0
        print(f"        → 全行业台账: {n_full} 行业, "
              f"累计 {len(full_ledger['weeks'])} 期")
        signals = detect_rotation_signals(ledger, date_str, scan_results)
        save_scan(date_str, scan_results, signals)

        data["scan_top10"] = deduped_top10[:10]
        data["scan_all_count"] = len(scan_results)
        data["scan_top10_raw"] = scan_results[:10]  # 未去重版本供回溯
        data["rotation_signals"] = signals
        data["market_temp"] = market_temp

        # 退出预警（D1/D2/D3）
        prev_plates = _load_prev_plates(date_str)
        exit_warns = detect_exit_warnings(ledger, scan_results,
                                          prev_plates=prev_plates,
                                          curr_plates=plates)
        data["exit_warnings"] = exit_warns

        # S3: 资金异动检测（利用 D1/D2 个股级数据）
        inst_signals = detect_institutional_anomalies(inst_data, scan_results)
        block_signals = detect_block_trade_signals(block_data, scan_results)
        money_signals = inst_signals + block_signals
        data["money_signals"] = money_signals
        if money_signals:
            in_top10 = sum(1 for s in money_signals if s.get("in_top10"))
            print(f"        → {len(money_signals)} 个资金异动信号"
                  f"（{in_top10} 个与TOP10行业共振）")

        confirmed = sum(1 for r in scan_results[:10] if r["stage"] == "确认")
        print(f"        → {len(scan_results)} 个行业, "
              f"TOP10 最高评分 {scan_results[0]['score_auto']:.1f}/8, "
              f"{confirmed} 个确认, "
              f"{len(signals)} 个轮动信号, "
              f"{len(exit_warns)} 个退出预警")
    else:
        print("        → 无板块数据(MCP plates 返 null/空,降级)")
        data["scan_top10"] = []
        data["scan_all_count"] = 0
        data["rotation_signals"] = []
        data["exit_warnings"] = []
        data["plates_unavailable"] = True
        # 仍写一份空 _scan.json,留痕标记缺失
        save_scan(date_str, [], [], plates_unavailable=True)

    top_industries = [r["name"] for r in data.get("scan_top10", [])[:3]]

    # ── 1.5. 概念板块扫描 ──
    print("  [1.5/7] 概念板块扫描...")
    concept_plates = _load_concept_plates(date_str, from_cache=from_cache)
    if concept_plates:
        concept_scan = scan_concept_plates(concept_plates)
        append_to_concept_ledger(date_str, concept_scan)
        persistent = filter_persistent_concepts(concept_scan, date_str)
        data["concept_persistent"] = persistent
        data["concept_top20"] = concept_scan[:20]  # 全量保留供参考
        top_concept = concept_scan[0] if concept_scan else {}
        print(f"        → {len(concept_scan)} 个概念, "
              f"TOP1: {top_concept.get('name', '?')} "
              f"({top_concept.get('price_chg', 0):+.2f}%), "
              f"{len(persistent)} 个持续性概念")
    else:
        data["concept_persistent"] = []
        data["concept_top20"] = []
        print("        → 无概念板块数据")

    # ── 2. 今日要闻（始终采集，新闻不可缓存）──
    print("  [2/8] 拉取今日要闻...")
    try:
        morning = mcp_call("news", "get_alpha_morning", {})
        data["morning_news"] = morning
        print(f"        → 获取到 {_count_items(morning)} 条要闻")
    except Exception as e:
        print(f"        → 要闻采集失败: {e}")
        data["morning_news"] = {}

    # ── 2.4 微信文章自动入库（--url / wechat_urls.txt / 剪贴板）──
    urls_to_ingest = list(wechat_urls or [])

    # wechat_urls.txt
    try:
        from scrape_wechat import load_manual_urls, URLS_FILE
        manual = load_manual_urls()
    except ImportError:
        manual = []
        URLS_FILE = None
    if manual:
        print(f"  [2.4] 从 wechat_urls.txt 读取 {len(manual)} 个 URL")
        urls_to_ingest.extend(manual)

    # 剪贴板（可选，需 pyperclip）
    try:
        import pyperclip
        clip = pyperclip.paste() or ""
        clip_urls = [u.strip() for u in clip.split() if "mp.weixin.qq.com" in u]
        if clip_urls:
            print(f"  [2.4] 剪贴板检测到 {len(clip_urls)} 个微信 URL")
            urls_to_ingest.extend(clip_urls)
    except (ImportError, Exception):
        pass

    if urls_to_ingest:
        urls_to_ingest = list(dict.fromkeys(urls_to_ingest))  # 去重保序
        wechat_date = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"  [2.4] 入库 {len(urls_to_ingest)} 个微信 URL → {wechat_date}_wechat.json")
        _ingest_wechat_urls(urls_to_ingest, wechat_date)
        if manual and URLS_FILE and URLS_FILE.exists():
            URLS_FILE.write_text("", encoding="utf-8")
            print(f"    wechat_urls.txt 已清空")

    # ── 2.5 微信公众号信源（优先 WeWe RSS，降级 Mac Share Data）──
    # WeWe RSS 部署在服务器，8/8 账号全覆盖（含杜牛牛），不依赖 Mac 点击行为
    # Share Data 作为兜底：WeWe RSS 缓存缺失时或补充 wechat_cache_results 供 dashboard 用

    # ── Session 健康前置检测 ──
    DASH_URL = "http://139.196.115.64:8080/dash"
    try:
        from wewe_health import check_health, Health as WeWeHealth
        health_status, health_detail = check_health()
        if health_status == WeWeHealth.DEAD:
            print(f"  🔴 WeWe RSS Session 已死！fulltext={health_detail['overall_ok']}/{health_detail['overall_total']}")
            print(f"  → 打开 {DASH_URL} 扫码重登「哈哈哈」账号（不勾选24h自动退出）后继续")
        elif health_status == WeWeHealth.DEGRADED:
            print(f"  ⚠ WeWe RSS Session 退化: fulltext={health_detail['overall_ok']}/{health_detail['overall_total']} ({health_detail['overall_rate']:.0%})")
            print(f"  → 建议尽快 {DASH_URL} 扫码续期")
        elif health_status == WeWeHealth.UNREACHABLE:
            print(f"  💀 WeWe RSS API 不可达！容器可能挂了")
        else:
            print(f"  ✅ WeWe RSS Session 健康: {health_detail['overall_ok']}/{health_detail['overall_total']} fulltext OK")
    except ImportError:
        pass  # wewe_health.py 未部署
    except Exception as e:
        print(f"  ⚠ Health check 异常: {e}")

    print("  [2.5/8] 微信信源采集...")
    wechat_loaded = False

    # 优先路径：WeWe RSS 自动采集（服务器 139.196.115.64:4001，rss_collector.py 20:00 定时拉取）
    try:
        from rss_collector import get_cached_wechat_articles
        wx_articles = get_cached_wechat_articles(date_str, days=2)
        if wx_articles:
            # ── Plan B: 对 fulltext_ok=False 的文章用 scrape_wechat 直接抓公众号页面 ──
            # 这解决"WeRead session 死但文章本身在微信服务器上仍可公开访问"的场景
            plan_b_count = 0
            plan_b_fail = 0
            try:
                from scrape_wechat import fetch_article as scrape_article
                for article in wx_articles:
                    article_url = article.get("url", "") or article.get("link", "")
                    if not article.get("fulltext_ok") and article_url.startswith("https://mp.weixin.qq.com"):
                        scraped = scrape_article(article_url)
                        if scraped and scraped.get("content"):
                            article["description"] = scraped["content"]
                            article["content_html"] = scraped["content"]
                            article["fulltext_ok"] = True
                            article["_plan_b"] = True  # 标记为 Plan B 恢复
                            plan_b_count += 1
                        else:
                            plan_b_fail += 1
                if plan_b_count:
                    print(f"        🔄 Plan B 恢复: {plan_b_count} 篇从公众号页面直接抓取成功")
                if plan_b_fail:
                    print(f"        ⚠ Plan B 放弃: {plan_b_fail} 篇抓取失败（需用户扫码续期后 Plan A 恢复）")
            except ImportError:
                pass  # scrape_wechat 不可用，跳过 Plan B
            except Exception as eb:
                print(f"        ⚠ Plan B 异常: {eb}")

            wechat_data = {
                "date": date_str,
                "collected_at": datetime.now().isoformat(),
                "articles": wx_articles,
                "source": "wewe_rss",
            }
            out_path = RAW_DIR / f"{date_str}_wechat.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(wechat_data, f, ensure_ascii=False, indent=2)
            data["wechat_sources"] = wechat_data
            sources_seen = list({a.get("source", "") for a in wx_articles})
            print(f"        → WeWe RSS 采集成功: {len(wx_articles)} 篇 / {len(sources_seen)} 个信源")
            wechat_loaded = True
        else:
            print(f"        ⚠ 第1路(WeWe RSS): 缓存中无微信文章 — 检查 rss_collector 定时任务")
    except ImportError:
        print(f"        ⚠ 第1路(WeWe RSS): rss_collector 模块不可用")
    except Exception as e:
        print(f"        ⚠ 第1路(WeWe RSS) 异常: {e}")

    # 降级路径：Mac Share Data（wechat_cache_collector，需用户在 Mac 微信点击）
    # 同时补充 wechat_cache_results 供 dashboard 展示覆盖率
    try:
        from wechat_cache_collector import collect as cache_collect, save_cache, CACHE_DIR
        cache_results = cache_collect(days=3, fetch_content=True)
        if cache_results:
            cache_file = save_cache(cache_results)
            data["wechat_cache_results"] = cache_results  # dashboard 需要
            if not wechat_loaded:
                articles_flat = []
                for source_name, arts in cache_results.items():
                    for a in arts:
                        articles_flat.append({
                            "title": a.get("title", ""),
                            "content": a.get("content", ""),
                            "source": source_name,
                            "url": a.get("url", ""),
                            "content_length": a.get("content_length", len(a.get("content", ""))),
                            "click_date": a.get("click_date", ""),
                        })
                if articles_flat:
                    wechat_data = {
                        "date": date_str,
                        "collected_at": datetime.now().isoformat(),
                        "articles": articles_flat,
                        "source": "cache_collector",
                    }
                    out_path = RAW_DIR / f"{date_str}_wechat.json"
                    with open(out_path, "w", encoding="utf-8") as f:
                        json.dump(wechat_data, f, ensure_ascii=False, indent=2)
                    data["wechat_sources"] = wechat_data
                    n_sources = len(cache_results)
                    print(f"        → Share Data 采集成功: {len(articles_flat)} 篇 / {n_sources} 个信源")
                    wechat_loaded = True
            else:
                n_sources = len(cache_results)
                print(f"        → Share Data 补充 dashboard 覆盖率: {n_sources} 个信源")
        else:
            print(f"        ⚠ 第2路(Mac Share Data): 未采集到 8 信源文章 — 确认 Mac 微信中已点击")
    except ImportError:
        print(f"        ⚠ 第2路(Mac Share Data): wechat_cache_collector 模块不可用")
    except Exception as e:
        print(f"        ⚠ 第2路(Mac Share Data) 异常: {e}")

    # 降级路径：读已有的 {date}_wechat.json（前溯最多 4 天）
    if not wechat_loaded:
        found_file = False
        for days_back in range(4):
            d = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=days_back)).strftime("%Y-%m-%d")
            wf = RAW_DIR / f"{d}_wechat.json"
            n = 0
            if wf.exists():
                with open(wf, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                n = len(loaded.get("articles", []))
                if n > 0:
                    data["wechat_sources"] = loaded
                    found_file = True
            if found_file:
                suffix = f"（来自 {d}）" if days_back > 0 else ""
                print(f"        → 第3路(历史文件): {n} 篇公众号文章{suffix}")
                wechat_loaded = True
                break
        if not found_file:
            print(f"        ⚠ 第3路(历史文件): 4天内无有效 {date_str}_wechat.json — 进入自动采集")
    if not wechat_loaded:
        if not from_cache:
            # 尝试自动采集
            print("        → 未找到微信数据，尝试自动采集...")
            try:
                from wechat_collector import collect_all, save_result
                wechat_date = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
                auto_result = collect_all(wechat_date)
                if auto_result.get("articles"):
                    save_result(wechat_date, auto_result)
                    # 重新查找
                    for days_back in range(4):
                        d = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=days_back)).strftime("%Y-%m-%d")
                        wf = RAW_DIR / f"{d}_wechat.json"
                        if wf.exists():
                            with open(wf, "r", encoding="utf-8") as f:
                                data["wechat_sources"] = json.load(f)
                            n = len(data["wechat_sources"].get("articles", []))
                            print(f"        → 第4路(自动采集) 成功: {n} 篇")
                            wechat_loaded = True
                            break
                else:
                    print(f"        ⚠ 第4路(自动采集): collect_all 返回空文章列表 — 检查 wechat_collector 采集逻辑")
            except ImportError:
                print(f"        ⚠ 第4路(自动采集): wechat_collector 模块不可用")
            except Exception as e:
                print(f"        ⚠ 第4路(自动采集) 异常: {e}")

            if not wechat_loaded:
                # 交互提醒：让用户粘贴 URL
                print("        → 未找到微信数据")
                print("        → 粘贴微信 URL（每行一个，直接回车跳过）：")
                interactive_urls = []
                try:
                    while True:
                        line = input("        > ").strip()
                        if not line:
                            break
                        if "mp.weixin.qq.com" in line:
                            interactive_urls.append(line)
                        else:
                            print("        ! 非微信 URL，已忽略")
                except (EOFError, KeyboardInterrupt):
                    pass
                if interactive_urls:
                    wechat_date = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
                    _ingest_wechat_urls(interactive_urls, wechat_date)
                    # 重新查找
                    for days_back in range(4):
                        d = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=days_back)).strftime("%Y-%m-%d")
                        wf = RAW_DIR / f"{d}_wechat.json"
                        if wf.exists():
                            with open(wf, "r", encoding="utf-8") as f:
                                data["wechat_sources"] = json.load(f)
                            n = len(data["wechat_sources"].get("articles", []))
                            print(f"        → {n} 篇公众号文章")
                            wechat_loaded = True
                            break
                if not wechat_loaded:
                    data["wechat_sources"] = {}
                    print("        → 跳过微信信源")
        else:
            data["wechat_sources"] = {}
            print("        → 无微信信源")

    # ── 2.8 RSS 公开信源采集（财联社/36氪/格隆汇/财新/金十/第一财经等）──
    print("  [2.8/8] RSS 公开信源...")
    try:
        from rss_collector import get_cached_articles
        rss_all = get_cached_articles(date_str, days=1)
        rss_public = [a for a in rss_all if not a.get("is_wechat")]
        data["rss_news"] = rss_public
        if rss_public:
            by_src = {}
            for a in rss_public:
                by_src.setdefault(a.get("source", "RSS"), []).append(a)
            detail = ", ".join(f"{k}({len(v)})" for k, v in by_src.items())
            print(f"        -> {len(rss_public)} 条: {detail}")
        else:
            print("        -> 0 条（缓存为空，需先运行 rss_collector.py）")
    except ImportError:
        data["rss_news"] = []
        print("        -> rss_collector 未安装")
    except Exception as e:
        data["rss_news"] = []
        print(f"        -> RSS 读取异常: {e}")

    # ── 2.9 并购/重组信号采集（AKShare 公告 + RSS M&A 增强）──
    print("  [2.9/9] 并购重组信号...")
    try:
        ma_signals = collect_ma_signals(date_str)
        ma_count = ma_signals.get("count", 0)

        # Layer 2: RSS M&A 关键词过滤增强（零成本）
        rss_ma = filter_ma_from_rss(data.get("rss_news", []))
        ma_signals["_rss_enhanced"] = len(rss_ma)
        if rss_ma:
            ma_signals["rss_ma_articles"] = rss_ma

        data["ma_signals"] = ma_signals
        if ma_count > 0:
            summary = ma_signals.get("summary", f"今日 {ma_count} 条并购重组公告")
            print(f"        -> {ma_count} 条公告 | {summary[:100]}")
        else:
            print("        -> 0 条（今日无资产重组/重大事项公告）")
    except Exception as e:
        data["ma_signals"] = {"announcements": [], "count": 0, "_error": str(e)}
        print(f"        -> 采集异常: {e}")

    # ── 2.6 微信主题结构化提取（LLM预处理，解决原文解析低效问题）──
    wechat_articles = data.get("wechat_sources", {}).get("articles", [])
    # 质量过滤：丢弃内容<200字的（引导页/广告/空壳）
    quality_articles = [a for a in wechat_articles
                        if a.get("content_length", 0) >= 200
                        or len(a.get("content", "")) >= 200]
    # 内容过滤：丢弃明显非投资内容（培训广告、引导关注等）
    JUNK_KEYWORDS = ["培训班", "线下班", "报名入口", "扫码入群", "课程安排",
                     "免费领取", "加我微信", "点击阅读原文即可", "违规删"]
    invest_articles = []
    for a in quality_articles:
        content = a.get("content", "")
        if any(kw in content[:300] for kw in JUNK_KEYWORDS):
            print(f"        → 过滤垃圾: [{a.get('source','')}] {a.get('title','')[:30]}（含广告/非投资内容）")
            continue
        invest_articles.append(a)
    filtered_count = len(wechat_articles) - len(invest_articles)
    if filtered_count > 0:
        print(f"        → 质量过滤: {filtered_count}篇已丢弃（内容不足或非投资内容）")
    if invest_articles:
        print("  [2.6/7] 微信主题结构化提取...")
        themes = extract_wechat_themes(invest_articles)
        data["wechat_themes"] = themes
    else:
        data["wechat_themes"] = []

    # ── 2.7 微信深度解析（thesis_extractor）──
    wechat_deep = {}
    cache_source_articles = data.get("wechat_cache_results", {})
    if not cache_source_articles and invest_articles:
        # 从 flat articles 构造 {source: [articles]} 格式
        for a in invest_articles:
            src = a.get("source", "unknown")
            cache_source_articles.setdefault(src, []).append(a)
    if cache_source_articles:
        print("  [2.7/8] 微信深度解析（thesis_extractor）...")
        try:
            wechat_deep = extract_all_sources(cache_source_articles)
            n_thesis = sum(len(v) for v in wechat_deep.values())
            print(f"        → {n_thesis} 条 thesis 提取")
            # 保存 thesis 结果供 dashboard 读取
            try:
                from wechat_cache_collector import CACHE_DIR
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                thesis_path = CACHE_DIR / f"thesis_{date_str}.json"
                with open(thesis_path, "w", encoding="utf-8") as f:
                    json.dump(wechat_deep, f, ensure_ascii=False, indent=2)
                # 入库 thesis_ledger
                try:
                    from thesis_ledger import ingest_from_cache
                    ingest_from_cache(date_str)
                except Exception:
                    pass
            except ImportError:
                pass
        except Exception as e:
            print(f"        → thesis_extractor 异常（非阻塞）: {e}")
    data["wechat_deep"] = wechat_deep

    # ── 3. 热点新闻搜索（始终采集 + 基于 TOP 行业的定向搜索）──
    print("  [3/8] 搜索热点新闻...")
    data["search_results"] = {}
    # 固定 3 路 + 动态 TOP 行业定向搜索
    dynamic_queries = [f"{ind} 行业 政策 利好" for ind in top_industries]
    all_queries = NEWS_QUERIES + dynamic_queries
    # 日期过滤：当日及前1天
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    news_start = (dt - timedelta(days=1)).strftime("%Y-%m-%d")
    for q in all_queries:
        try:
            result = mcp_call("news", "search_news", {
                "keyword": q, "topk": 5,
                "start_date": news_start, "end_date": date_str,
            })
            data["search_results"][q] = result
            print(f"        → '{q}': {_count_items(result)} 条")
        except Exception:
            print(f"        → '{q}': 搜索失败")

    # ── 3.5 新闻事件结构化预处理 ──
    print("  [3.5/8] 新闻事件聚合...")
    news_events = extract_news_events(
        data.get("morning_news", {}),
        data.get("search_results", {})
    )
    data["news_events"] = news_events

    # ── 4. 智能票池构建（S4升级：龙头+微信+共振+评分）──
    print("  [4/8] 智能票池构建...")
    wechat = data.get("wechat_sources", {})

    # 热度TOP30（始终拉取：轻量MCP调用，供共振检测+LLM选股用）
    hot_top30_raw = None
    try:
        hot_top30 = mcp_call("news", "get_stock_hot_top30", {"is_new": 1})
        hot_items = hot_top30 if isinstance(hot_top30, list) else (
            hot_top30.get("data", []) if isinstance(hot_top30, dict) else []
        )
        if hot_items:
            latest_date = max(
                (it.get("date", "") for it in hot_items if isinstance(it, dict)),
                default=""
            )
            if latest_date:
                hot_items = [it for it in hot_items
                             if isinstance(it, dict) and it.get("date") == latest_date]
        hot_top30_raw = hot_items[:30]
    except Exception as e:
        print(f"        → 热度TOP30获取失败: {e}")

    pool = build_smart_pool(
        date_str=date_str,
        top_industries=top_industries,
        scan_top10=data.get("scan_top10", []),
        wechat_sources=wechat,
        inst_data=data.get("inst_data"),
        block_data=data.get("block_data"),
        hot_top30_raw=hot_top30_raw,
        from_cache=from_cache,
    )
    data["stock_data"] = pool["stock_data"]
    data["wechat_all_stocks"] = pool["wechat_all_stocks"]
    data["hot_top30_raw"] = pool.get("hot_top30_raw")
    data["resonance_stocks"] = pool.get("resonance_stocks", [])
    stats = pool["stats"]
    print(f"        → 龙头{stats['leaders']} 微信{stats['wechat_new']} "
          f"共振{stats.get('resonance', 0)} 兜底{stats['hot_fallback']}")
    scored = [s for s in pool["stock_data"].values() if s.get("catalyst_tier") == "强推"]
    if scored:
        print(f"        → {len(scored)} 只强推标的")

    # ── 4.5 行业内因子选股（MVP核心：确认行业→量化个股排序）──
    confirmed_industries = [r["name"] for r in data.get("scan_top10", [])[:10]
                            if r.get("stage") == "确认"]
    if confirmed_industries:
        print(f"  [4.5/8] 行业内因子选股（{len(confirmed_industries)} 个确认行业）...")
        industry_stock_ranks = {}
        for ind in confirmed_industries[:5]:  # 最多5个行业
            try:
                ranked = score_stocks_within_industry(ind, date_str, from_cache=from_cache)
                if ranked:
                    industry_stock_ranks[ind] = ranked
                    top_name = ranked[0]["name"] if ranked else "?"
                    top_score = ranked[0]["mini_score"] if ranked else 0
                    print(f"        → {ind}: {len(ranked)} 只, TOP1={top_name}({top_score}分)")
            except Exception as e:
                print(f"        → {ind}: 失败 {e}")
        data["industry_stock_ranks"] = industry_stock_ranks
    else:
        data["industry_stock_ranks"] = {}

    # ── 4.6 多周期行业热度 ──
    print("  [4.6/7] 多周期行业热度...")
    ledger = _load_ledger()
    scan_top10 = data.get("scan_top10", [])
    if scan_top10:
        heat = compute_multi_period_heat(ledger, scan_top10)
        data["multi_period_heat"] = heat
        print(f"        → 持续热门 {len(heat['weekly_hot'])} 个, "
              f"新晋 {len(heat['trending_up'])} 个, "
              f"退潮 {len(heat['cooling_down'])} 个")
    else:
        data["multi_period_heat"] = {}

    # ── 4.7 事件优先级重排（S3.4）──
    news_events = data.get("news_events", [])
    money_signals = data.get("money_signals", [])
    rotation_sigs = data.get("rotation_signals", [])
    exit_warns = data.get("exit_warnings", [])
    if money_signals or rotation_sigs or news_events:
        prioritized = prioritize_events(
            news_events, money_signals, rotation_sigs, exit_warns)
        data["prioritized_events"] = prioritized
        l0 = sum(1 for e in prioritized if e["priority_level"] == "L0")
        l1 = sum(1 for e in prioritized if e["priority_level"] == "L1")
        l2 = sum(1 for e in prioritized if e["priority_level"] == "L2")
        print(f"  [4.7/8] 事件优先级重排: L0资金{l0} + L1数据{l1} + L2新闻{l2}")
    else:
        data["prioritized_events"] = []

    # ── 5. 验证追踪 ──
    print("  [5/8] 更新验证台账...")
    try:
        track = update_track_record(from_cache=True)
        data["track_record"] = track.get("summary", {})
        data["track_recent"] = track.get("recommendations", [])[-4:]
    except Exception as e:
        print(f"        → 验证追踪跳过: {e}")
        data["track_record"] = {}
        data["track_recent"] = []

    # ── 5.1 V3.1: 信号演化追踪 ──
    try:
        evolutions = evaluate_signal_evolution(from_cache=from_cache)
        data["signal_evolutions"] = evolutions
        if evolutions:
            evo_counts = {}
            for evo in evolutions:
                s = evo.get("evolution", "pending")
                evo_counts[s] = evo_counts.get(s, 0) + 1
            evo_str = " / ".join(f"{k}={v}" for k, v in evo_counts.items())
            print(f"        → 信号演化: {len(evolutions)} 条 ({evo_str})")
    except Exception as e:
        print(f"        → 信号演化跳过: {e}")
        data["signal_evolutions"] = []

    # ── 6. 全球宏观环境（AKShare：大宗商品/全球指数/iVIX） ──
    print("  [6/8] 全球宏观环境...")
    try:
        global_ctx = collect_global_context()
        data["global_context"] = global_ctx
        n_comm = len(global_ctx.get("commodities", {}))
        n_idx = len(global_ctx.get("indices", {}))
        ivix = global_ctx.get("china_ivix", {}).get("value")
        parts_summary = []
        if n_comm:
            parts_summary.append(f"{n_comm}种商品")
        if n_idx:
            parts_summary.append(f"{n_idx}个指数")
        if ivix:
            parts_summary.append(f"iVIX={ivix:.1f}")
        print(f"        → {', '.join(parts_summary) if parts_summary else '无数据'}")
    except Exception as e:
        print(f"        → 全球宏观跳过: {e}")
        data["global_context"] = {}

    # ── 7. 国内宏观环境（Finstep MCP：LPR/央行/PMI/CPI/Shibor/利差） ──
    print("  [7/8] 国内宏观环境...")
    try:
        macro_ctx = collect_macro_context(from_cache=from_cache)
        data["macro_context"] = macro_ctx
        parts_m = []
        if macro_ctx.get("lpr"):
            parts_m.append(f"LPR={macro_ctx['lpr'].get('lpr_1y', '?')}%")
        if macro_ctx.get("pmi"):
            parts_m.append(f"PMI={macro_ctx['pmi'].get('manufacturing_pmi', '?')}")
        if macro_ctx.get("shibor"):
            parts_m.append(f"Shibor数据已获取")
        if macro_ctx.get("cn_us_bond"):
            parts_m.append(f"中美利差已获取")
        print(f"        → {', '.join(parts_m) if parts_m else '无数据'}")

        # V3.0: 补充 C2 跨资产传导信号到 scan_results
        # V3.4 Sprint C: 新增 global_ctx 参数，启用商品传导规则
        if macro_ctx and data.get("scan_top10"):
            from score import compute_cross_asset_signals
            global_ctx_for_c2 = data.get("global_context")
            c2_signals = compute_cross_asset_signals(macro_ctx, global_ctx_for_c2)
            if c2_signals:
                c2_names = [f"{k}({v:+d})" for k, v in c2_signals.items() if v != 0]
                print(f"        → C2跨资产传导: {', '.join(c2_names) if c2_names else '无信号'}")
                data["cross_asset_signals"] = c2_signals

    except Exception as e:
        print(f"        → 国内宏观跳过: {e}")
        data["macro_context"] = {}

    # ── V3.2 Sprint 2: 热点前置注入（ground truth） ──
    print("  [8/8] 当日热点 ground truth...")
    try:
        data["must_cover_hotspots"] = collect_daily_hotspots(date_str, data)
        h = data["must_cover_hotspots"]
        n_s = len(h.get("top3_sectors", []))
        n_c = len(h.get("top3_concepts", []))
        n_k = len(h.get("hot_stocks", []))
        print(f"        → 行业TOP3 {n_s} 个 · 概念TOP3 {n_c} 个 · 热度TOP {n_k} 只")
    except Exception as e:
        print(f"        → 热点计算跳过: {e}")
        data["must_cover_hotspots"] = {}

    # V3.2 Sprint 3: 验证-学习闭环（过去30天策略表现反馈）
    print("  [9/9] 学习反馈（近30天策略表现）...")
    try:
        from backtest import compute_learning_feedback
        fb = compute_learning_feedback(days=30)
        data["learning_feedback"] = fb
        n_ins = len(fb.get("insights", []))
        n_cal = len(fb.get("calibration", []))
        overall = fb.get("overall", {})
        wr = overall.get("win_rate")
        wr_str = f"{wr*100:.0f}%" if wr is not None else "N/A"
        print(f"        → 样本 {fb.get('sample_size', 0)} 条 胜率 {wr_str} "
              f"R:R中位 {fb.get('rr_stats', {}).get('median', 'N/A')} "
              f"洞察 {n_ins} 条 校准 {n_cal} 条")
    except Exception as e:
        print(f"        → 学习反馈跳过: {e}")
        data["learning_feedback"] = {}

    print(f"\n  Phase 1 完成 ✓")
    return data


def collect_daily_hotspots(date_str: str, data: dict) -> dict:
    """V3.2 Sprint 2: 计算当日市场热点 ground truth（用户确认定义）。

    热点 = 涨幅TOP3板块/概念 ∪ 热度TOP30前10只个股

    V3.2 P2 (2026-04-20): 兜底逻辑 —— 当日 scan_top10 为空时，
    尝试从 T-1 交易日的 daily_data.json 读取 scan_top10/concept_top10，
    避免 ground truth 退化为空集。

    Args:
        date_str: 当日日期
        data: 当前 daily_data（已包含 scan_top10 / concept_top10 / hot_top30_raw）

    Returns:
        {
            "date": str,
            "top3_sectors": [str],     # 涨幅TOP3 申万一级
            "top3_concepts": [str],    # 涨幅TOP3 概念
            "hot_stocks": [{"name", "code"}],  # 热度TOP10
            "fallback_used": bool,     # 是否启用了 T-1 兜底
        }
    """
    scan = data.get("scan_top10", [])
    concepts = data.get("concept_top10", [])
    fallback_used = False

    # V3.2 P2: scan 为空时从 T-1/T-2 缓存回退
    if not scan or not concepts:
        for days_back in range(1, 5):
            t_prev = (datetime.strptime(date_str, "%Y-%m-%d")
                      - timedelta(days=days_back)).strftime("%Y-%m-%d")
            cache_path = RAW_DIR / f"{t_prev}_daily_data.json"
            if not cache_path.exists():
                continue
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    prev = json.load(f)
                if not scan and prev.get("scan_top10"):
                    scan = prev["scan_top10"]
                    fallback_used = True
                    print(f"        → scan_top10 从 {t_prev} 兜底")
                if not concepts and prev.get("concept_top10"):
                    concepts = prev["concept_top10"]
                    fallback_used = True
                    print(f"        → concept_top10 从 {t_prev} 兜底")
                if scan and concepts:
                    break
            except (json.JSONDecodeError, OSError):
                continue

    # 1. 行业涨幅 TOP3
    plates_by_chg = sorted(
        scan, key=lambda p: float(p.get("price_chg", 0) or 0), reverse=True)
    top3_sectors = [p.get("name", "") for p in plates_by_chg[:3]
                    if p.get("name")]

    # 2. 概念涨幅 TOP3
    concepts_by_chg = sorted(
        concepts, key=lambda p: float(p.get("price_chg", 0) or 0), reverse=True)
    top3_concepts = [p.get("name", "") for p in concepts_by_chg[:3]
                     if p.get("name")]

    # 3. 热度TOP10（从 hot_top30_raw，过滤 ETF）
    hot = data.get("hot_top30_raw") or []
    etf_prefixes = ("510", "511", "512", "513", "515", "516", "518",
                    "159", "563")
    hot_stocks = []
    for it in (hot if isinstance(hot, list) else [])[:20]:
        code = it.get("security_code", "") or it.get("code", "")
        name = it.get("security_name", "") or it.get("name", "")
        if not (code and name):
            continue
        if code.startswith(etf_prefixes):
            continue
        if any(kw in name for kw in ("ETF", "基金", "LOF")):
            continue
        hot_stocks.append({"name": name, "code": code})
        if len(hot_stocks) >= 10:
            break

    return {
        "date": date_str,
        "top3_sectors": top3_sectors,
        "top3_concepts": top3_concepts,
        "hot_stocks": hot_stocks,
        "fallback_used": fallback_used,
    }


def _count_items(data) -> int:
    """统计 MCP 返回数据的条目数。"""
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        if "_error" in data:
            return 0
        items = data.get("data", data.get("items", data.get("news", [])))
        if isinstance(items, list):
            return len(items)
    return 1 if data else 0


# ══════════════════════════════════════════════════════
# Phase 1b: 全球宏观环境采集
# ══════════════════════════════════════════════════════

def collect_global_context() -> dict:
    """采集全球宏观环境数据（大宗商品 + 全球指数 + 波动率）。

    全部 API 调用均 try/except，任何失败不影响主流程。
    """
    try:
        import akshare as ak
    except ImportError:
        print("        → akshare 未安装，跳过全球宏观")
        return {}

    context = {}

    # ── 大宗商品（近5日收盘价）──
    COMMODITY_SYMBOLS = [
        ("GC", "黄金"), ("CL", "原油"), ("HG", "铜"), ("SI", "白银"),
    ]
    commodities = {}
    for sym, name in COMMODITY_SYMBOLS:
        try:
            df = ak.futures_foreign_hist(symbol=sym)
            recent = df.tail(5)
            latest = recent.iloc[-1]
            prev = recent.iloc[-2] if len(recent) >= 2 else latest
            price = float(latest["close"])
            prev_price = float(prev["close"])
            commodities[name] = {
                "price": price,
                "prev_close": prev_price,
                "chg_pct": round(((price / prev_price) - 1) * 100, 2) if prev_price else 0,
            }
            # 5日涨跌幅
            if len(recent) >= 5:
                p0 = float(recent.iloc[0]["close"])
                commodities[name]["5d_chg_pct"] = round(((price / p0) - 1) * 100, 2) if p0 else None
        except Exception:
            pass
    if commodities:
        context["commodities"] = commodities

    # ── 全球指数（实时快照）──
    INDEX_CODES = [
        ("SPX", "标普500"), ("NDX", "纳斯达克"), ("DJIA", "道琼斯"),
        ("UDI", "美元指数"), ("BDI", "波罗的海BDI"), ("CRB", "CRB商品指数"),
        ("N225", "日经225"), ("HSI", "恒生指数"),
    ]
    indices = {}
    try:
        df = ak.index_global_spot_em()
        for code, name in INDEX_CODES:
            row = df[df["代码"] == code]
            if not row.empty:
                r = row.iloc[0]
                indices[name] = {
                    "price": float(r["最新价"]),
                    "chg_pct": float(r["涨跌幅"]),
                }
    except Exception:
        pass
    if indices:
        context["indices"] = indices

    # ── 中国 iVIX（50ETF 期权波动率）──
    try:
        df = ak.index_option_50etf_qvix()
        latest = df.tail(1).iloc[0]
        context["china_ivix"] = {
            "value": float(latest["close"]),
            "date": str(latest["date"]),
        }
    except Exception:
        pass

    return context


# ══════════════════════════════════════════════════════
# Phase 1c: 国内宏观环境采集（Finstep MCP）
# ══════════════════════════════════════════════════════

def collect_macro_context(from_cache: bool = False) -> dict:
    """采集国内宏观经济数据（LPR/央行操作/PMI/CPI/Shibor/中美利差）。

    通过 Finstep MCP finstep-macro 服务获取，任何单项失败不影响整体。
    参考 macro-overview skill 的 6 步流程，只取对行业轮动有直接判断价值的指标。
    """
    from datetime import date as _date

    today = _date.today().strftime("%Y-%m-%d")
    # 经济指标查近 3 个月足够获取最新月度数据
    three_months_ago = (_date.today() - timedelta(days=90)).strftime("%Y-%m-%d")
    # LPR 查近 2 个月（月度发布）
    two_months_ago = (_date.today() - timedelta(days=60)).strftime("%Y-%m-%d")

    ctx = {}

    # ── 1. LPR 利率 ──
    try:
        data = mcp_call("macro", "get_china_lpr", {
            "start_date": two_months_ago, "end_date": today,
        })
        if isinstance(data, list) and data:
            latest = data[0]  # 按日期倒序，第一条是最新
            ctx["lpr"] = {
                "date": latest.get("trade_date", ""),
                "lpr_1y": latest.get("lpr_1y", ""),
                "lpr_5y": latest.get("lpr_5y", ""),
            }
            # 如果有前一期，记录变化
            if len(data) >= 2:
                prev = data[1]
                ctx["lpr"]["prev_1y"] = prev.get("lpr_1y", "")
                ctx["lpr"]["prev_5y"] = prev.get("lpr_5y", "")
    except Exception as e:
        print(f"        → LPR 获取失败: {e}")

    # ── 2. 央行公开市场操作（周汇总） ──
    try:
        # week_end_date 需要周六日期，找最近的周六
        from datetime import date as _d
        _today = _d.today()
        days_to_sat = (5 - _today.weekday()) % 7  # 0=Mon..6=Sun
        if days_to_sat == 0 and _today.weekday() != 5:
            days_to_sat = 7  # 如果不是周六，找上一个周六
        last_sat = _today - timedelta(days=((_today.weekday() - 5) % 7)) if _today.weekday() >= 5 else _today - timedelta(days=(_today.weekday() + 2))
        sat_str = last_sat.strftime("%Y-%m-%d")
        data = mcp_call("macro", "get_central_bank_operation_week", {
            "week_end_date": sat_str,
        })
        if isinstance(data, list) and data:
            latest = data[0]
            ctx["central_bank_weekly"] = {
                "date": latest.get("week_end", "").split(" ")[0],
                "net_injection": latest.get("net_currency_issue", ""),
                "reverse_repo": latest.get("repurchase_sell_size", ""),
                "reverse_repo_expire": latest.get("repurchase_sell_expire", ""),
            }
        elif isinstance(data, dict) and not data.get("_raw_text", "").startswith("Error"):
            ctx["central_bank_weekly"] = {
                "date": sat_str,
                "net_injection": data.get("net_put_in", data.get("周资金净投放总额", "")),
            }
    except Exception as e:
        print(f"        → 央行操作获取失败: {e}")

    # ── 3. PMI（制造业/非制造业/综合） ──
    try:
        data = mcp_call("macro", "get_pmi_monthly", {
            "start_date": three_months_ago, "end_date": today,
        })
        if isinstance(data, list) and data:
            latest = data[0]
            ctx["pmi"] = {
                "date": latest.get("end_date", ""),
                "manufacturing_pmi": latest.get("manufacturing_pmi", ""),
                "non_manufacturing_pmi": latest.get("non_manufacturing_pmi", ""),
                "composite_pmi": latest.get("composite_pmi_index", ""),
            }
            if len(data) >= 2:
                prev = data[1]
                ctx["pmi"]["prev_manufacturing"] = prev.get("manufacturing_pmi", "")
    except Exception as e:
        print(f"        → PMI 获取失败: {e}")

    # ── 4. CPI ──
    try:
        data = mcp_call("macro", "get_cpi_info", {
            "start_date": three_months_ago, "end_date": today,
        })
        if isinstance(data, list) and data:
            latest = data[0]
            ctx["cpi"] = {
                "date": latest.get("end_date", ""),
                "cpi_national": latest.get("national_cpi_yoy", ""),
                "cpi_mom": latest.get("national_cpi_mom", ""),
            }
    except Exception as e:
        print(f"        → CPI 获取失败: {e}")

    # ── 5. Shibor 银行间利率 ──
    try:
        data = mcp_call("macro", "get_rate_interbank", {
            "start_date": two_months_ago, "end_date": today,
            "symbol": "Shibor人民币", "indicator": "隔夜",
        })
        if isinstance(data, list) and data:
            latest = data[0]
            overnight = latest.get("interest_rate", "").replace("%", "")
            ctx["shibor"] = {
                "date": latest.get("report_date", ""),
                "overnight": overnight,
                "change_bp": latest.get("change_rate", ""),
            }
        # 追加 1 周 Shibor
        data_1w = mcp_call("macro", "get_rate_interbank", {
            "start_date": two_months_ago, "end_date": today,
            "symbol": "Shibor人民币", "indicator": "1周",
        })
        if isinstance(data_1w, list) and data_1w and "shibor" in ctx:
            ctx["shibor"]["1w"] = data_1w[0].get("interest_rate", "").replace("%", "")
    except Exception as e:
        print(f"        → Shibor 获取失败: {e}")

    # ── 6. 中美国债收益率 ──
    try:
        # 分别获取中国和美国国债收益率
        cn_data = mcp_call("macro", "get_bond_cn_rate", {
            "start_date": two_months_ago, "end_date": today,
        })
        us_data = mcp_call("macro", "get_bond_us_rate", {
            "start_date": two_months_ago, "end_date": today,
        })
        bond = {}
        if isinstance(cn_data, list) and cn_data:
            latest_cn = cn_data[0]
            bond["date"] = latest_cn.get("trade_date", "")
            bond["cn_10y"] = latest_cn.get("cn_bond_yield_10y", "")
            bond["cn_2y"] = latest_cn.get("cn_bond_yield_2y", "")
        if isinstance(us_data, list) and us_data:
            latest_us = us_data[0]
            bond["us_10y"] = latest_us.get("us_bond_yield_10y", latest_us.get("treasury_yield_10y", ""))
        # 计算利差
        if bond.get("cn_10y") and bond.get("us_10y"):
            try:
                spread = float(bond["cn_10y"]) - float(bond["us_10y"])
                bond["spread_10y"] = f"{spread:.2f}"
            except (ValueError, TypeError):
                pass
        if bond:
            ctx["cn_us_bond"] = bond
    except Exception as e:
        print(f"        → 中美利差获取失败: {e}")

    return ctx


# ══════════════════════════════════════════════════════
# Phase 2/3: 已迁移至 report_agent.py，re-export 保持兼容
# ══════════════════════════════════════════════════════

from report_agent import (  # noqa: E402
    SYSTEM_PROMPT,
    build_data_prompt,
    call_claude_api,
    parse_llm_output,
    validate_rotation_json,
    postprocess_rotation_json,
    generate_report,
    save_outputs,
    _save_json,
    generate_narrative_report,
)


def _truncate_json(data, max_chars: int = 2000) -> str:
    """将数据转为 JSON 字符串，截断到 max_chars。"""
    text = json.dumps(data, ensure_ascii=False, indent=None)
    if len(text) > max_chars:
        return text[:max_chars] + "...(截断)"
    return text


# ══════════════════════════════════════════════════════
# Pipeline Manifest（运行审计记录）
# ══════════════════════════════════════════════════════

def _save_pipeline_manifest(date_str: str, data: dict,
                            rotation_json: dict | None,
                            integrity: dict) -> None:
    """保存本次运行的审计清单，供事后排查和自动化监控。"""
    manifest = {
        "date": date_str,
        "run_at": datetime.now().isoformat(),
        "phase1_integrity": integrity,
        "phase1_stats": {
            "scan_top10_count": len(data.get("scan_top10", [])),
            "stock_data_count": len(data.get("stock_data", {})),
            "wechat_articles": len(
                (data.get("wechat_sources") or {}).get("articles", [])),
            "news_count": len(data.get("news_articles", [])),
            "concept_count": len(data.get("concept_top20", [])),
        },
        "phase2_success": rotation_json is not None,
        "phase2_stats": {},
        "outputs": [],
    }

    if rotation_json:
        manifest["phase2_stats"] = {
            "events": len(rotation_json.get("events", [])),
            "alpha_signals": len(rotation_json.get("alpha_signals", [])),
            "sector_outlook": len(rotation_json.get("sector_outlook", [])),
            "commodity_signals": len(rotation_json.get("commodity_signals", [])),
            "quality_gate_passed": rotation_json.get("_quality_gate", {}).get("passed", False),
        }
        # 记录产出文件
        signals_path = RAW_DIR / f"{date_str}_signals.json"
        alpha_json = PROJECT_ROOT / "output" / "alpha" / f"{date_str}_alpha.json"
        alpha_html = PROJECT_ROOT / "output" / "alpha" / f"{date_str}_alpha.html"
        for p in [signals_path, alpha_json, alpha_html]:
            if p.exists():
                manifest["outputs"].append(str(p.name))

    # pipeline_status：ok / fail / degraded，供 health_check 快速读取
    if not manifest.get("phase2_success") and not manifest.get("phase1_integrity", {}).get("status") == "BLOCK":
        manifest["pipeline_status"] = "fail"
    elif manifest.get("phase1_integrity", {}).get("status") == "DEGRADED":
        manifest["pipeline_status"] = "degraded"
    else:
        manifest["pipeline_status"] = "ok"

    manifest_path = RAW_DIR / f"{date_str}_manifest.json"
    try:
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        # 同时写最新状态到固定路径，health_check 无需遍历日期文件
        latest_path = RAW_DIR / "_pipeline_latest_status.json"
        with open(latest_path, "w", encoding="utf-8") as f:
            json.dump({
                "date": date_str,
                "run_at": manifest["run_at"],
                "pipeline_status": manifest["pipeline_status"],
                "phase2_success": manifest["phase2_success"],
                "phase1_integrity_status": manifest.get("phase1_integrity", {}).get("status"),
            }, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


# ══════════════════════════════════════════════════════
# V3.9.4: 文件契约 — cycleradar → trader 上游信号对接
# ══════════════════════════════════════════════════════

# trader 侧数据目录（硬编码契约路径，不可配置）
TRADER_DATA_DIR = Path(os.path.expanduser("~/交易员/data"))
TRADER_CONTRACT_FILE = TRADER_DATA_DIR / "upstream_signals.jsonl"

# V4.3: cycleradar 自身数据目录（alpha_signals → /m 信号总线）
CYCLERADAR_DATA_DIR = PROJECT_ROOT.parent / "data"
CYCLERADAR_SIGNALS_FILE = CYCLERADAR_DATA_DIR / "upstream_signals.jsonl"


def _write_trader_contract(date_str: str):
    """从当天 _signals.json 提取 sector/stock 信号，写入 trader 侧契约文件。

    契约格式（JSONL，每行一个事件）：
      {"source":"cycleradar","date":"2026-06-05","generated_at":"...",
       "theme":"算力底座重构","direction":"利好","confidence":"high","decay_days":3,
       "sectors":["光学光电子","电子","通信"],
       "stocks":[{"code":"301205","name":"联特科技","logic":"..."}]}

    写入逻辑：
      - 如果当天 _signals.json 不存在，跳过（Phase 2 失败，不写契约）
      - 写入 trader 侧 data/upstream_signals.jsonl（create if not exists）
      - 当天 signals 全量覆盖（不追加），避免重复读取
    """
    signals_path = RAW_DIR / f"{date_str}_signals.json"
    if not signals_path.exists():
        print("  ⚠ 当日 _signals.json 不存在，跳过 trader 契约写入")
        return

    try:
        with open(signals_path, "r", encoding="utf-8") as f:
            signals = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  ⚠ 读取 _signals.json 失败: {e}")
        return

    events = signals.get("events", [])
    if not events:
        print("  ⚠ _signals.json 无事件，跳过 trader 契约写入")
        return

    # 转换：extract sector + stock + direction from each event
    contract_lines = []
    generated_at = datetime.now().isoformat()

    for evt in events:
        sectors = [si.get("sector", "") for si in evt.get("sector_impact", []) if si.get("sector")]
        if not sectors:
            continue  # 没有行业影响的事件不传给 trader

        stocks = [
            {"code": si.get("code", ""), "name": si.get("name", ""), "logic": si.get("logic", "")}
            for si in evt.get("stock_impact", []) if si.get("code")
        ]

        direction = evt.get("sector_impact", [{}])[0].get("direction", "利好") if evt.get("sector_impact") else "利好"

        # 置信度：rank 1→high, rank 2→medium, ≥3→low
        rank = evt.get("rank", 99)
        if rank == 1:
            confidence = "high"
        elif rank == 2:
            confidence = "medium"
        else:
            confidence = "low"

        # 衰减天数：按事件确定性推算
        certainty = (evt.get("event_time") or {}).get("certainty", "ongoing")
        if certainty == "occurred":
            decay_days = 1  # 已发生事件快速衰减
        elif certainty == "ongoing":
            decay_days = 3  # 进行中事件 3 天保质
        else:
            decay_days = 5

        contract_lines.append({
            "source": "cycleradar",
            "date": date_str,
            "generated_at": generated_at,
            "theme": evt.get("title", ""),
            "direction": direction,
            "confidence": confidence,
            "decay_days": decay_days,
            "sectors": sectors,
            "stocks": stocks,
        })

    if not contract_lines:
        print("  ⚠ 无可契约的行业信号，跳过 trader 契约写入")
        return

    # 写入 trader 侧 JSONL
    try:
        TRADER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(TRADER_CONTRACT_FILE, "w", encoding="utf-8") as f:
            for line in contract_lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        print(f"  ✅ trader 契约已写入: {TRADER_CONTRACT_FILE} ({len(contract_lines)} 条信号)")
    except OSError as e:
        print(f"  ⚠ 写入 trader 契约失败: {e}")

    # ── V4.3: alpha_signals → /m 信号总线 ──
    # 从 _signals.json 提取 LLM 生成的 alpha_signals（个股推票），
    # 转换为标准信号合约格式写入 cycleradar data/upstream_signals.jsonl，
    # 供 /m 消费（sync_to_ecs.sh 同步到 ECS）
    alpha_signals = signals.get("alpha_signals", [])
    if not alpha_signals:
        print("  ⚠ _signals.json 无 alpha_signals，跳过 cycleradar 信号总线写入")
    else:
        TIME_WINDOW_DAYS = {"2w": 14, "1m": 30}
        CONFIDENCE_MAP = {"high": 0.85, "medium": 0.70, "low": 0.55}
        today = datetime.strptime(date_str, "%Y-%m-%d")
        alpha_lines = []

        for sig in alpha_signals:
            stock = sig.get("stock", {})
            code = stock.get("code", "")
            name = stock.get("name", "")
            if not code:
                continue

            direction = sig.get("direction", "long")
            conf_tag = sig.get("confidence", "medium")
            confidence = CONFIDENCE_MAP.get(conf_tag, 0.70)
            tw = sig.get("time_window", "2w")
            expiry_date = today + timedelta(days=TIME_WINDOW_DAYS.get(tw, 14))
            expiry = expiry_date.strftime("%Y-%m-%dT23:59:59")

            alpha_lines.append({
                "signal_id": sig.get("signal_id", f"ALPHA-{date_str}-{len(alpha_lines)+1:03d}"),
                "timestamp": generated_at,
                "strategy": "report_agent",
                "asset": code,
                "asset_type": "stock",
                "direction": direction,
                "confidence": confidence,
                "expiry": expiry,
                "metadata": {
                    "stock_name": name,
                    "entry_price": sig.get("entry_price"),
                    "target_price": sig.get("target_price"),
                    "stop_loss": sig.get("stop_loss"),
                    "thesis": sig.get("thesis", ""),
                    "event_source": sig.get("event_source", ""),
                    "time_window": tw,
                    "sector_context": sig.get("sector_context", ""),
                },
            })

        if alpha_lines:
            try:
                CYCLERADAR_DATA_DIR.mkdir(parents=True, exist_ok=True)
                # 按 signal_id 去重：读已有行，合并新行后覆写
                existing = {}
                if CYCLERADAR_SIGNALS_FILE.exists():
                    with open(CYCLERADAR_SIGNALS_FILE, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                obj = json.loads(line)
                                sid = obj.get("signal_id", "")
                                if sid:
                                    existing[sid] = obj
                            except json.JSONDecodeError:
                                pass

                for line in alpha_lines:
                    existing[line["signal_id"]] = line

                with open(CYCLERADAR_SIGNALS_FILE, "w", encoding="utf-8") as f:
                    for obj in existing.values():
                        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
                print(f"  ✅ alpha_signals → cycleradar 信号总线: {CYCLERADAR_SIGNALS_FILE} ({len(alpha_lines)} 条新 / {len(existing)} 条总计)")
            except OSError as e:
                print(f"  ⚠ 写入 cycleradar 信号总线失败: {e}")


# ══════════════════════════════════════════════════════
# Phase 1.5: 数据完整性检查
# ══════════════════════════════════════════════════════

def check_data_integrity(data: dict) -> dict:
    """检查 Phase 1 采集数据的完整性，决定是否可以进入 Phase 2。

    返回:
        {"status": "OK"|"DEGRADED"|"BLOCK", "issues": [...]}
        - BLOCK: 核心数据缺失，LLM 调用无意义
        - DEGRADED: 部分数据缺失，可继续但质量受限
        - OK: 数据充分
    """
    issues = []
    block = False

    # 行业扫描是核心输入
    scan_top10 = data.get("scan_top10", [])
    if not scan_top10:
        if data.get("plates_unavailable"):
            issues.append("行业扫描完全失败（MCP plates 不可用）")
            block = True
        else:
            issues.append("scan_top10 为空")
            block = True

    # 微信信源是最高 alpha 来源 — 缺失时 DEGRADED（有 morning_news 替代）
    wechat = data.get("wechat_sources", {})
    wechat_articles = wechat.get("articles", []) if wechat else []
    if not wechat_articles:
        issues.append("微信信源为空（alpha 来源降级为 morning_news + search）")

    # 新闻至少要有一些
    news = data.get("news_articles", [])
    morning = data.get("morning_news", {})
    if not news and not morning:
        issues.append("新闻数据全部为空")

    # stock_data 白名单（防止 LLM 幻觉的关键）
    stock_data = data.get("stock_data", {})
    if not stock_data:
        issues.append("stock_data 白名单为空（无法约束 LLM 标的选择）")
        block = True
    elif len(stock_data) < 5:
        issues.append(f"stock_data 仅 {len(stock_data)} 只（过少，信号质量受限）")

    # 检查 stock_data 中是否有价格数据
    if stock_data:
        has_price = sum(1 for s in stock_data.values()
                        if s.get("close_price") or s.get("price", {}).get("last"))
        if has_price == 0:
            issues.append("stock_data 中无任何价格数据（entry_price 将无法校验）")
            block = True
        elif has_price < len(stock_data) * 0.5:
            issues.append(f"stock_data 仅 {has_price}/{len(stock_data)} 只有价格")

    if block:
        return {"status": "BLOCK", "issues": issues}
    elif issues:
        return {"status": "DEGRADED", "issues": issues}
    return {"status": "OK", "issues": []}


# ══════════════════════════════════════════════════════
# V3.9.6: 角色化文章 Pipeline
# ══════════════════════════════════════════════════════


def generate_role_articles_from_wechat(
    date_str: str,
    model: str | None = None,
    dry_run: bool = False,
) -> dict | None:
    """从 WeWe RSS 缓存文章按信源角色生成公众号文章。

    读取 RAW_DIR/{date}_wechat.json → 按 source(mp_id) 分组
    → 角色映射(source_registry) → LLM 写作 Pipeline → HTML 草稿

    Returns:
        PipelineReport dict，失败时 None
    """
    try:
        from core.writing.pipeline import run_pipeline, save_articles, show_pipeline_summary
        from core.writing.source_registry import get_source_meta
    except ImportError as e:
        print(f"\n  ⚠ V3.9.6 Pipeline 模块不可用: {e}")
        return None

    wf = RAW_DIR / f"{date_str}_wechat.json"
    if not wf.exists():
        print(f"\n  ⚠ 微信数据文件不存在: {wf}")
        print(f"  → 先运行 python daily.py --date {date_str} --data-only 采集数据")
        return None

    with open(wf, "r", encoding="utf-8") as f:
        wechat_data = json.load(f)

    articles = wechat_data.get("articles", [])
    if not articles:
        print(f"\n  ⚠ 无微信公众号文章数据")
        return None

    # ── 按 source (mp_id) 分组 ──
    articles_by_source: dict[str, list[dict]] = {}
    for art in articles:
        src = art.get("source", "") or art.get("mp_id", "")
        if not src:
            continue
        articles_by_source.setdefault(src, []).append(art)

    # ── 构建 sources 列表（含 metadata）──
    sources: list[dict] = []
    signals_by_source: dict[str, list[dict]] = {}
    for mp_id, arts in articles_by_source.items():
        meta = get_source_meta(mp_id)
        if meta:
            sources.append({
                "mp_id": mp_id,
                "mp_name": meta["name"],
                "category": meta.get("category", ""),
                "tags": meta.get("tags", []),
            })
        else:
            name = arts[0].get("source", mp_id) if arts else mp_id
            sources.append({
                "mp_id": mp_id,
                "mp_name": name,
                "category": "",
                "tags": [],
            })
        signals_by_source[mp_id] = arts

    if not sources:
        print(f"\n  ⚠ 无法匹配信源元数据 — 检查 source_registry.py")
        return None

    print(f"\n  {'─' * 50}")
    print(f"  📰 V3.9.6 角色化文章 Pipeline")
    print(f"  {'─' * 50}")
    print(f"  日期: {date_str}")
    print(f"  信源: {len(sources)} 个 / 文章: {len(articles)} 篇")

    report = run_pipeline(date_str, sources, signals_by_source,
                          model=model, dry_run=dry_run)

    if dry_run:
        return None

    return report.to_dict() if report else None


# ══════════════════════════════════════════════════════
# V3.9.6: 兼并重组文章（独立 AKShare 数据源）
# ══════════════════════════════════════════════════════

def generate_ma_article_from_signals(
    date_str: str,
    model: str | None = None,
    dry_run: bool = False,
) -> dict | None:
    """从 AKShare 并购重组公告生成兼并重组角色文章。

    独立于微信信源 Pipeline，消费 ma_signals 产出的结构化数据。
    写入 output/article/ 与微信角色文章并列。

    Returns:
        RoleArticle.to_dict() or None（当日无信号时）
    """
    try:
        from core.writing.pipeline import generate_ma_article, save_articles
        from core.writing.pipeline import PipelineReport, RoleArticle
    except ImportError as e:
        print(f"\n  ⚠ V3.9.6 Pipeline 模块不可用: {e}")
        return None

    print(f"\n  {'─' * 50}")
    print(f"  📊 兼并重组文章（AKShare M&A 公告）")
    print(f"  {'─' * 50}")

    article = generate_ma_article(date_str, model=model, dry_run=dry_run)

    if dry_run:
        return None

    if article:
        # 复用 save_articles 保存逻辑
        report = PipelineReport(date=date_str)
        report.articles.append(article)
        report.roles_used.append(article.role)
        save_articles(report)
        print(f"\n  ✅ 兼并重组文章已生成: {article.word_count} 字")
        return article.__dict__ if hasattr(article, '__dict__') else None

    return None

    if report and report.articles:
        paths = save_articles(report)
        show_pipeline_summary(report)
        print(f"\n  输出目录: {paths[0].parent if paths else 'N/A'}")
    elif report:
        print(f"\n  ⚠ 未生成任何文章")
        if report.errors:
            print(f"  错误详情:")
            for e in report.errors:
                print(f"    ✗ {e}")

    return report.to_dict() if report else None


# ══════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════


def _apply_rotation_factor_fallback(
    date_str: str,
    rotation_json: dict | None,
    data: dict,
) -> dict | None:
    """V3.9: 当 LLM sector_outlook 为空时，用 rotation_factor 规则引擎产出结论。

    - rotation_json 存在但 sector_outlook 为空 → 注入
    - rotation_json 完全为 None → 构建最小 rotation_json
    """
    from factor_agent import INDUSTRY_ETF_MAP

    scan_path = RAW_DIR / f"{date_str}_scan.json"
    if not scan_path.exists():
        print("  ⚠ scan.json 不存在，无法执行 rotation_factor fallback")
        if rotation_json is not None:
            # LLM 有输出但 sector_outlook 为空，只能保留原样
            return rotation_json
        # LLM 完全失败 + scan 不可用 → 构建最小日报骨架，不跳过 Phase 3
        rotation_json = {
            "alpha_signals": [],
            "sector_outlook": [],
            "commodity_signals": [],
            "global_conclusion": {
                "market_regime": "未知",
                "confidence": 30,
                "action": "观望",
                "key_thesis": (
                    "规则引擎与 LLM 均不可用，行业扫描数据缺失。"
                    "仅展示宏观环境与新闻摘要，信号置信度极低，请勿据此交易。"
                ),
                "risk_warnings": [
                    "行业扫描数据缺失（scan.json 不存在）",
                    "LLM 生成失败",
                    "所有信号均为空，无交易依据",
                ],
                "source": "signal_unavailable v1.0",
            },
        }
        data["_rf_fallback"] = True
        data["_signal_unavailable"] = True
        print("  → ⚠️ 行业扫描不可用，已构建最小日报骨架（仅宏观+新闻摘要）")
        return rotation_json

    try:
        with open(scan_path, "r", encoding="utf-8") as f:
            scan_data = json.load(f)

        scan_results = scan_data.get("rankings", [])
        rot_signals = scan_data.get("rotation_signals", [])

        # 尝试加载前一交易日 scan.json 用于构建 prev_scan_map
        prev_scan_map: dict[str, dict] = {}
        try:
            prev_date = (datetime.strptime(date_str, "%Y-%m-%d")
                         - timedelta(days=1)).strftime("%Y-%m-%d")
            # 往前最多找 5 天
            for _ in range(5):
                prev_path = RAW_DIR / f"{prev_date}_scan.json"
                if prev_path.exists():
                    with open(prev_path, "r", encoding="utf-8") as pf:
                        prev_scan = json.load(pf)
                    for r in prev_scan.get("rankings", []):
                        prev_scan_map[r["name"]] = r
                    break
                prev_date = (datetime.strptime(prev_date, "%Y-%m-%d")
                             - timedelta(days=1)).strftime("%Y-%m-%d")
        except Exception:
            pass

        rf_output = run_rotation_analysis(scan_results, rot_signals, prev_scan_map)

        # 将 rotation_factor 输出转为 sector_outlook 格式
        sector_outlook = []
        ranking = rf_output.get("sector_ranking", [])
        conclusion = rf_output.get("conclusion", {})

        for r in ranking[:10]:  # TOP10
            name = r.get("name", "")
            stage = r.get("stage", "观望")
            score = r.get("score_auto", 0)
            attribution = r.get("attribution", {})

            # direction 映射
            if stage == "确认":
                direction = "看多"
                confidence = "high"
            elif stage == "关注":
                direction = "看多"
                confidence = "medium"
            else:
                direction = "中性"
                confidence = "low"

            # event_driver: 从 attribution 生成因子描述
            factors_present = [
                k for k, v in attribution.items()
                if isinstance(v, (int, float)) and v > 0
            ]
            factor_desc_map = {
                "A1": "超额收益显著",
                "A2": "涨停热度高",
                "B1": "主力资金净流入",
                "D1": "大宗交易净买入",
                "D2": "机构席位净买入",
            }
            driver_parts = [factor_desc_map.get(f, f) for f in factors_present[:3]]
            event_driver = "、".join(driver_parts) if driver_parts else "规则引擎综合评分"

            # etf 查表
            etf_info = INDUSTRY_ETF_MAP.get(name, {})
            etf = None
            if etf_info:
                etf = {"code": etf_info["code"], "name": etf_info["name"]}

            sector_outlook.append({
                "sector": name,
                "direction": direction,
                "confidence": confidence,
                "event_driver": event_driver,
                "factor_status": "confirmed" if stage == "确认"
                                 else "initial" if stage == "关注"
                                 else "unconfirmed",
                "time_horizon": "2-4w",
                "etf": etf,
                "commodity_link": None,
                "_source": "rotation_factor",
            })

        if rotation_json is not None:
            # 注入 sector_outlook
            rotation_json["sector_outlook"] = sector_outlook
            # 补充 global_conclusion 中 rotation_factor 信息
            gc = rotation_json.get("global_conclusion", {})
            if not gc.get("key_thesis"):
                rotation_json["global_conclusion"] = {
                    **gc,
                    "key_thesis": conclusion.get("summary", "轮动信号由规则引擎生成"),
                    "market_regime": conclusion.get("rotation_phase", "混沌阶段"),
                    "_rf_intensity": rf_output.get("rotation_intensity", {}),
                }
            data["_rf_fallback"] = True
            print(f"  → ✅ rotation_factor 规则引擎替代 sector_outlook"
                  f"（{len(sector_outlook)} 行业）")
        else:
            # LLM 完全失败：构建最小 rotation_json
            rotation_json = {
                "alpha_signals": [],
                "sector_outlook": sector_outlook,
                "commodity_signals": [],
                "global_conclusion": {
                    "market_regime": conclusion.get("rotation_phase", "混沌阶段"),
                    "confidence": 60,
                    "action": "观望",
                    "key_thesis": conclusion.get("summary",
                        "LLM 不可用，行业展望由 rotation_factor 规则引擎生成"),
                    "risk_warnings": conclusion.get("risks", []),
                    "_rf_intensity": rf_output.get("rotation_intensity", {}),
                    "source": "rotation_factor v1.0",
                },
            }
            data["_rf_fallback"] = True
            print(f"  → ✅ rotation_factor 规则引擎完全替代 LLM"
                  f"（{len(sector_outlook)} 行业，LLM 不可用）")

    except Exception as e:
        import traceback
        print(f"  ⚠ rotation_factor fallback 失败: {e}")
        traceback.print_exc()

    return rotation_json


def main():
    parser = argparse.ArgumentParser(description="CycleRadar 日报自动化生成")
    parser.add_argument("--date", default=None,
                        help="指定日期 (YYYY-MM-DD)，默认今天")
    parser.add_argument("--data-only", action="store_true",
                        help="仅采集数据，不调 LLM")
    parser.add_argument("--from-cache", action="store_true",
                        help="用缓存数据（不调 MCP）")
    parser.add_argument("--dry-run", action="store_true",
                        help="打印 prompt，不实际调 API")
    parser.add_argument("--model", default=None,
                        help="指定模型 (默认自动选择)")
    parser.add_argument("--push", action="store_true",
                        help="推送到微信公众号草稿箱")
    parser.add_argument("--url", action="append", default=[],
                        help="微信文章 URL（可多次指定，自动入库）")
    parser.add_argument("--morning", action="store_true",
                        help="生成晨报（市场热点排名，轻量模式）")
    parser.add_argument("--article", action="store_true",
                        help="生成公众号文章（基于晨报二次创作）")
    parser.add_argument("--v5", action="store_true",
                        help="使用 v5 五板块日报格式（精炼版）")
    args = parser.parse_args()

    date_str = args.date or datetime.now().strftime("%Y-%m-%d")

    # ── 公众号文章模式（V3.9.6: 角色化 Pipeline）──
    if args.article:
        # 采集微信数据（如未采集则执行轻量版 wechat-only 采集）
        wechat_path = RAW_DIR / f"{date_str}_wechat.json"
        if not wechat_path.exists():
            print(f"  微信数据未缓存，正在从 WeWe RSS 采集...")
            try:
                from rss_collector import get_cached_wechat_articles
                wx_articles = get_cached_wechat_articles(date_str, days=2)
                if wx_articles:
                    wechat_data = {
                        "date": date_str,
                        "collected_at": datetime.now().isoformat(),
                        "articles": wx_articles,
                        "source": "wewe_rss",
                    }
                    with open(wechat_path, "w", encoding="utf-8") as f:
                        json.dump(wechat_data, f, ensure_ascii=False, indent=2)
                    print(f"  ✅ WeWe RSS: {len(wx_articles)} 篇已缓存")
                else:
                    print(f"  ⚠ WeWe RSS 缓存为空，尝试历史文件...")
            except Exception as e:
                print(f"  ⚠ WeWe RSS 采集失败: {e}")

        report = generate_role_articles_from_wechat(
            date_str, model=args.model, dry_run=args.dry_run
        )
        if report:
            print(f"\n{'═' * 50}")
            print(f"  V3.9.6 文章 Pipeline 完成！")
            print(f"{'═' * 50}")

        # ── 兼并重组文章（独立 AKShare 数据源） ──
        ma_report = generate_ma_article_from_signals(
            date_str, model=args.model, dry_run=args.dry_run
        )
        if ma_report:
            print(f"  📊 兼并重组文章已加入输出")
        return

    # ── 晨报模式：已合并进日报 (PD-003 I-004) ──
    # 晨报数据(morning_news)已在日报 collect_data() 中采集。
    # 独立晨报产出保留但标记 deprecated，后续版本移除。
    if args.morning:
        print(f"\n  ⚠️  晨报已合并进日报 (PD-003 I-004)")
        print(f"  晨报数据在日报流程中自动采集，无需单独运行。")
        print(f"  如需独立晨报，请使用: python daily.py --morning --force --date {date_str}")
        if not getattr(args, 'force', False):
            return
        print(f"\n╔══════════════════════════════════════════════════╗")
        print(f"║  周期雷达 · 晨报                                  ║")
        print(f"║  日期: {date_str}                                 ║")
        print(f"╚══════════════════════════════════════════════════╝")

        data = collect_morning_data(date_str)

        if args.data_only:
            data_path = RAW_DIR / f"{date_str}_morning_data.json"
            _save_json(data_path, data)
            print(f"\n  数据包已保存: {data_path}")
            print(f"  (data-only 模式，跳过 LLM 生成)")
            return

        report = generate_morning_report(data, dry_run=args.dry_run,
                                         model=args.model)
        if args.dry_run:
            return

        if report:
            save_morning_outputs(date_str, report, data)
        else:
            print(f"\n  ⚠ LLM 未返回有效晨报内容")

        print(f"\n{'═' * 50}")
        print(f"  晨报完成！")
        print(f"{'═' * 50}")
        return

    print(f"\n╔══════════════════════════════════════════════════╗")
    print(f"║  周期雷达 · 日报自动化                            ║")
    print(f"║  日期: {date_str}                                 ║")
    print(f"╚══════════════════════════════════════════════════╝")

    # Phase 1: 数据采集
    data = collect_data(date_str, from_cache=args.from_cache,
                        wechat_urls=args.url or None)

    # 保存数据包（即使 data-only 也保存）
    data_path = RAW_DIR / f"{date_str}_daily_data.json"
    _save_json(data_path, data)
    print(f"\n  数据包已保存: {data_path}")

    if args.data_only:
        print(f"\n  (data-only 模式，跳过 LLM 生成)")
        return

    # ── Phase 1.5: 数据完整性检查（阻塞门禁）──
    integrity = check_data_integrity(data)
    if integrity["status"] == "BLOCK":
        print(f"\n  ✖ 数据完整性检查不通过，中止 LLM 调用:")
        for issue in integrity["issues"]:
            print(f"    ✖ {issue}")
        print(f"  → 请检查 MCP 连通性或使用 --from-cache 重试")
        return
    elif integrity["status"] == "DEGRADED":
        print(f"\n  ⚠ 数据完整性降级（继续执行，质量可能受限）:")
        for issue in integrity["issues"]:
            print(f"    ⚠ {issue}")

    # Phase 2: LLM 事件解读引擎
    rotation_json = generate_report(data, dry_run=args.dry_run,
                                    model=args.model)

    if args.dry_run:
        return

    # ── V3.9: rotation_factor 规则引擎 fallback ──
    # 当 LLM sector_outlook 为空或 LLM 完全失败时，用规则引擎替代
    sector_outlook = rotation_json.get("sector_outlook", []) if rotation_json else []
    if not sector_outlook:
        rotation_json = _apply_rotation_factor_fallback(
            date_str, rotation_json, data
        )

    # ── V3.9.1: enhanced_nx 参考价位注入 alpha 信号 ──
    # 从 Phase 1 stock_data 提取 enhanced_nx ref_prices，注入对应 alpha 信号
    if rotation_json:
        stock_data = data.get("stock_data", {})
        alpha_signals = rotation_json.get("alpha_signals", [])
        nx_count = 0
        for sig in alpha_signals:
            stock_info = sig.get("stock", {})
            code = stock_info.get("code", "")
            if not code or code not in stock_data:
                continue
            sd = stock_data[code]
            enhanced = sd.get("nx", {}).get("enhanced", {})
            if isinstance(enhanced, dict) and enhanced.get("ref_prices"):
                grade_dict = enhanced.get("grade", {})
                sig["enhanced_nx"] = {
                    "grade": grade_dict,                        # 完整 grade dict
                    "ref_prices": enhanced["ref_prices"],
                    "summary": enhanced.get("summary", ""),
                    "confirmed_count": sum(
                        1 for k in ("L1", "L2", "L3")
                        if isinstance(grade_dict, dict) and grade_dict.get(k) is True
                    ),
                }
                nx_count += 1
        if nx_count:
            print(f"  → ✅ enhanced_nx 参考价位已注入 {nx_count} 个 alpha 信号")

    # Phase 3: 保存
    if rotation_json:
        save_outputs(date_str, rotation_json, data)
        # 文章体日报
        generate_narrative_report(rotation_json, data, model=args.model)
        # V3.9.4: 文件契约 → trader 上游信号对接
        _write_trader_contract(date_str)
    else:
        print(f"\n  ⚠ LLM 未返回有效内容，跳过保存")

    # Phase 4: 推送（可选）
    if args.push and rotation_json:
        alpha_html = PROJECT_ROOT / "output" / "alpha" / f"{date_str}_alpha.html"
        if alpha_html.exists():
            print(f"\n  Alpha 卡片已生成: {alpha_html}")
        else:
            print(f"\n  ⚠ Alpha HTML 不存在，跳过推送: {alpha_html}")

    # ── Pipeline Manifest（运行审计记录）──
    _save_pipeline_manifest(date_str, data, rotation_json, integrity)

    print(f"\n{'═' * 50}")
    print(f"  完成！")
    print(f"{'═' * 50}")


if __name__ == "__main__":
    main()
