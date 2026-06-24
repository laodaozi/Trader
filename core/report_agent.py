"""
report_agent.py -- CycleRadar V3.0 报告生成 Agent

职责：LLM 调用 + prompt 构建 + JSON 解析/验证 + HTML 程序化构建 + 文件输出。
从 daily.py Phase 2/3 渐进抽取。

依赖：anthropic SDK, score.py 常量, daily.py 模板路径
"""
from __future__ import annotations

import difflib
import json
import re
from html import escape as _esc
from pathlib import Path

from score import INDUSTRY_LEADERS, RAW_DIR
from factor_agent import (
    INDUSTRY_ETF_MAP, INDUSTRY_FUTURES_MAP,
    get_etf_for_industry, get_futures_for_industry,
)

PROJECT_ROOT = Path(__file__).parent
TEMPLATE_PATH = PROJECT_ROOT / "模板" / "行业轮动日报v4_模板.html"

# ══════════════════════════════════════════════════════
# SYSTEM PROMPT
# ══════════════════════════════════════════════════════

SYSTEM_PROMPT = """\
你是「周期雷达」事件解读引擎。基于下方信源数据，完成事件聚合、信号提取、行业预判三项任务。

<methodology>
## 核心原则
- 信息/数据第一位，结构化是辅助
- 事件驱动一切：所有产出的起点是事件解读，不是因子排名
- 前瞻 > 后视镜：预判"接下来什么会涨"，不是总结"过去涨了什么"
- 宁缺毋滥：无高质量信号时输出空数组，不凑数

## 信源优先级
1. **微信公众号正文**（历史胜率最高 81.8%）→ 深度解读 + 个股挖掘 → **这是核心输入，不是补充参考**
   - 微信信源提到的热门事件、热门标的、热门行业必须全部体现在 events 中
   - 微信信源提到的个股逻辑必须在 stock_impact 中保留
   - 如果微信信源和结构化数据有冲突，以微信信源为主（它反映的是当日市场真实共识）
2. **财经新闻 + 早报**（事件触发）→ 行业传导链推演
3. **龙虎榜/大宗交易**（资金验证）→ 确认事件是否有资金跟随
4. **商品数据**（先导信号）→ 行业传导预判
5. **行业因子数据**（后视镜验证）→ 确认/否决事件预判
6. **并购重组公告**（产业整合信号）→ 产业层面整合加速/行业集中度提升线索
   - 同一行业多笔公告 = 产业整合信号加强，优先权重等同于财经新闻
   - 行业龙头/知名公司的重组公告 → 行业格局变革信号，升权至第 2 档

## 任务一：事件聚合与解读
从多信源中识别今日 3-8 个重要事件，按市场冲击力排序。

每个事件必须回答：
- 利好/利空哪些行业？传导链是什么？
- 哪些个股直接受益？逻辑是什么？
- 对商品/期货有什么方向性含义？
- 事件是否已被 price in？（rank=1 头条通常已消化，降权处理）

事件筛选标准：
- 市场冲击力：影响多个板块/大盘？
- 可传导性：能清晰传导到具体行业和标的？
- 时效性：当日/近期 > 旧闻
- 确定性：已发生 > 进行中 > 预期中

## 任务二：今日 Alpha 信号提取
从事件解读中提取 0-5 条可交易个股信号。这是最核心的产出。

信号来源优先级：
1. 微信正文中的非显性推荐（深层提取，历史最高 alpha）
2. 事件传导链末端的直接受益标的
3. 资金异动验证的标的

每条信号硬要求：
- ⚠ stock.code 和 stock.name 优先从你在 events.stock_impact 中已识别的个股中选取（事件驱动）
- 如果下方「深度分析标的」或「行业内因子选股」中有该股数据，则用其真实价格推算 entry/target/stop
- 如果 stock_impact 个股没有估值数据，可基于事件逻辑给出合理的 entry/target/stop 区间估算，但必须在 thesis 中注明"价格为估算"
- 严禁使用训练知识中未在本日数据中出现过的标的
- R:R 盈亏比 (target-entry)/(entry-stop) ≥ 1.5
- time_horizon 只允许 "2w" 或 "1m"（严禁 "1w"，回测证明是反向指标）
- confidence: "high"（事件+资金双确认）/ "medium"（事件有，资金初步）/ "low"（纯逻辑推演）
- 必须有明确的 thesis（一句话投资逻辑）
- 宁缺毋滥：没有足够数据支撑的信号，alpha_signals 留空 []

降权规则：
- rank=1 头条事件的标的自动降为 medium confidence（已被 price in）
- rank=2/3 次级事件是甜蜜区（54-56% 胜率）

## 任务三：行业预判（前瞻性）
基于事件传导链，预判未来 2-4 周哪些行业将走强/走弱。

前瞻性来源：
- 事件传导链推演："电网投资加速" → 2-4 周后电网设备受益
- 商品先导信号："铜价突破" → 滞后 1 周电网设备/有色走强（IC +0.58 验证）
- 政策催化预期："新能源补贴即将出台" → 光伏/储能预判

因子验证状态（不是驱动，是确认）：
- confirmed：事件预判 + 因子已确认（资金流入+动量启动）→ 高置信度
- initial：事件预判有，因子刚开始确认 → 中置信度（提前布局窗口）
- unconfirmed：事件预判有，因子未确认 → 低置信度（观察）

## 行业名称规范（必须遵守）
sector 字段必须使用以下标准名称（申万一级，49个）：
有色金属, 贵金属, 小金属, 能源金属, 工业金属, 石油石化, 煤炭采选, 钢铁, 基础化工,
计算机, 电子, 半导体, 通信, 光学光电子, 电子化学品,
食品饮料行业, 家用电器, 汽车整车, 美容护理, 纺服行业, 商贸零售, 轻工制造, 农林牧渔,
医药, 化学制药, 生物制品,
银行, 非银金融, 证券, 多元金融,
建筑工程, 建筑材料, 机械设备, 通用设备, 电网设备, 交运设备, 电力设备, 电新行业, 环保,
航运港口, 交通运输, 机场,
房地产, 公用事业,
国防军工, 文化传媒, 影视院线, 出版, 社会服务

## JSON 转义要求
- 字符串值内部引用一律使用中文引号「」或『』
- 禁止裸露英文双引号（会破坏 JSON 解析）

## 品类纯化
- alpha_signals 中只推个股，严禁 ETF/基金代码
- ETF 信息放在 sector_outlook 的 etf 字段中
</methodology>

## 输出格式（严格 JSON，只输出一个 ```json 块）

```json
{
  "date": "YYYY-MM-DD",
  "events": [
    {
      "rank": 1,
      "title": "事件标题（简洁有力）",
      "source": "微信-叙事平权 / 新闻-财联社 / 龙虎榜 / 商品异动",
      "event_time": {"occurred_at": "YYYY-MM-DD", "certainty": "occurred/ongoing/expected"},
      "interpretation": "2-3句话解读：发生了什么→意味着什么→影响什么",
      "sector_impact": [{"sector": "标准行业名", "direction": "利好/利空/中性", "logic": "传导逻辑"}],
      "stock_impact": [{"code": "6位代码", "name": "股票名", "logic": "受益逻辑"}],
      "commodity_impact": [{"commodity": "铜/原油/黄金/白银/铁矿", "direction": "利多/利空", "logic": "逻辑"}]
    }
  ],
  "alpha_signals": [
    {
      "signal_id": "ALPHA-YYYYMMDD-NNN",
      "stock": {"code": "6位代码", "name": "股票名"},
      "direction": "long/short",
      "entry_price": 数字,
      "target_price": 数字,
      "stop_loss": 数字,
      "confidence": "high/medium/low",
      "time_window": "2w/1m",
      "event_source": "信号来源（微信-XX / 新闻-XX / 资金异动）",
      "thesis": "一句话投资逻辑",
      "sector_context": "所属行业 + 因子验证状态"
    }
  ],
  "sector_outlook": [
    {
      "sector": "标准行业名",
      "direction": "看多/看空/中性",
      "confidence": "high/medium/low",
      "event_driver": "驱动事件描述",
      "factor_status": "confirmed/initial/unconfirmed",
      "time_horizon": "2-4w",
      "etf": {"code": "ETF代码", "name": "ETF名称"},
      "commodity_link": "关联商品方向（如有）"
    }
  ],
  "commodity_signals": [
    {
      "commodity": "铜/原油/黄金/白银/铁矿",
      "direction": "多/空/观望",
      "confidence": "high/medium/low",
      "driver": "驱动逻辑",
      "sector_transmission": ["受影响行业1", "受影响行业2"]
    }
  ],
  "global_conclusion": {
    "market_regime": "进攻/均衡/防守",
    "confidence": 0-100,
    "action": "加仓/持仓/减仓/观望",
    "key_thesis": "一句话核心判断",
    "risk_warnings": ["风险1", "风险2"]
  }
}
```

## 输出前自检
1. alpha_signals 每条 R:R ≥ 1.5？entry/target/stop 全为数字？
2. alpha_signals 中无 ETF 代码（510/511/512/513/515/516/518/159/563）？
3. sector_outlook 中 sector 名称在 49 个标准名中？
4. time_window 全部为 "2w" 或 "1m"？
5. events 至少 3 个？
6. 无信号时 alpha_signals 为空数组 []，不凑数？
7. global_conclusion 包含 market_regime + key_thesis？
"""


# ══════════════════════════════════════════════════════
# Prompt 构建
# ══════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════
# Prompt 构建
# ══════════════════════════════════════════════════════


def _format_industry_scorecard(r: dict) -> str:
    """将单个行业格式化为计分卡一行（合并因子/资金/ETF/期货信息）。"""
    s = r.get("scores", {})
    factors = [k for k in ["A1", "A2", "B1", "C1", "C2", "D1", "D2", "E1"] if s.get(k)]
    factor_str = "+".join(factors) if factors else "无"

    parts = [f"  #{r['rank']} {r['name']}: "
             f"涨{r['price_chg']:+.2f}% 资金{r.get('fund_flow', 0):+.2f}亿 "
             f"因子[{factor_str}] {r.get('stage', '观望')}"]

    comp = r.get("composite_score")
    if comp is not None:
        parts[0] += f" 复合{comp}"
    if r.get("weekly_flow"):
        parts[0] += f" 周资金{r['weekly_flow']:+.1f}亿"
    if r.get("consecutive_top10", 0) >= 2:
        parts[0] += f" 连续{r['consecutive_top10']}期"

    etf = get_etf_for_industry(r['name'])
    if etf:
        parts[0] += f" ETF:{etf['name']}({etf['code']})"
    futures = get_futures_for_industry(r['name'])
    if futures:
        parts[0] += f" 期货:{'/'.join(f['name'] for f in futures[:2])}"

    return parts[0]

def build_data_prompt(data: dict) -> str:
    """将采集数据构造为 LLM user prompt。"""
    # 预定义 confirmed 供后续个股分层使用
    scan_top10_pre = data.get("scan_top10", [])
    confirmed_pre = [r for r in scan_top10_pre[:10]
                     if r.get("stage") == "确认"
                     and r.get("consecutive_top10", 0) >= 2
                     and r.get("weekly_flow", 0) > 0]

    date_str = data["date"]
    parts = [f"日期：{date_str}\n"]

    # 数据可用性告警(优先级最高,LLM 必须先看到)
    if data.get("plates_unavailable"):
        parts.append(
            "⚠️ **行业排名数据不可用**(MCP get_plate_rate_ranking 返 null/空);"
            "本日报无 49 行业评分。请基于事件驱动 + 个股层面分析,**不要编造**行业涨跌幅或 4D8I 评分;"
            "rotation 字段允许只填 stage='事件催化',score_ref 留空。\n"
        )

    # ══ 微信信源（核心输入，最高优先级，必须放在最前面） ══
    wechat = data.get("wechat_sources", {})
    articles = wechat.get("articles", [])
    themes = data.get("wechat_themes", [])

    if themes:
        parts.append(f"## ★ 微信信源主题（{len(themes)}个主题 · 核心输入 · 事件/行业/个股的首要来源）\n")
        parts.append("⚠ 以下主题来自用户精选的8个专业信源，是本日报事件聚合和个股推荐的首要数据来源。")
        parts.append("  每个主题中的个股、事件、逻辑链必须在 events 和 alpha_signals 中体现。\n")
        for t in themes:
            theme_name = t.get("name") or t.get("theme", "")
            parts.append(f"### 主题: {theme_name}")
            parts.append(f"  热度: {t.get('heat', 'medium')}")
            sentiment = t.get("sentiment") or t.get("summary", "")
            if sentiment:
                parts.append(f"  情绪: {sentiment}")
            key_debate = t.get("key_debate", "")
            if key_debate:
                parts.append(f"  核心分歧: {key_debate}")
            stocks = t.get("stocks", [])
            if stocks:
                stock_strs = []
                for s in stocks:
                    if isinstance(s, dict):
                        name = s.get("name", "")
                        code = s.get("code", "")
                        logic = s.get("logic", "")
                        entry = f"{name}({code})" if code else name
                        if logic:
                            entry += f"·{logic}"
                        stock_strs.append(entry)
                    else:
                        stock_strs.append(str(s))
                parts.append(f"  个股: {', '.join(stock_strs)}")
            src = t.get("source", "")
            if src:
                parts.append(f"  信源: {src}")
            parts.append("")
    elif articles:
        parts.append(f"## ★ 微信信源（{len(articles)}篇文章 · 核心输入）\n")
        for art in articles[:5]:
            source = art.get("source", "")
            content = art.get("content", "")
            if content:
                parts.append(f"### {source}")
                parts.append(content[:1500])
                parts.append("")

    # 微信深度解析（thesis）— 紧跟主题之后
    wechat_deep = data.get("wechat_deep", {})
    if wechat_deep:
        thesis_items = []
        for src_name, items in wechat_deep.items():
            if isinstance(items, dict):
                items = [items]
            for item in (items if isinstance(items, list) else []):
                if isinstance(item, dict) and item.get("thesis"):
                    th = item["thesis"]
                    if isinstance(th, dict) and (th.get("signal") or th.get("logic_chain")):
                        thesis_items.append((src_name, item))

        if thesis_items:
            parts.append(f"## ★ 微信深度解析 · Thesis（{len(thesis_items)}条前瞻信号）\n")
            parts.append("以下为信源深度提取的结构化 thesis，confidence=high 的可直接作为 alpha 候选：\n")
            for src_name, item in thesis_items:
                th = item["thesis"]
                conf = th.get("confidence", "medium")
                parts.append(f"### [{src_name}] {th.get('signal', th.get('logic_chain', ''))[:80]} (confidence={conf})")
                parts.append(f"  逻辑链: {th.get('logic_chain', '')}")
                parts.append(f"  目标行业: {th.get('target_industry', '')}")
                targets = th.get("target_stocks", [])
                if targets:
                    t_str = ", ".join(targets[:10]) if isinstance(targets, list) else str(targets)
                    parts.append(f"  目标个股: {t_str}")
                parts.append(f"  时间窗口: {th.get('time_horizon', '2w')}")
                cp = item.get("cycle_phase")
                if cp and cp.get("phase"):
                    parts.append(f"  情绪周期: {cp['phase']} (第{cp.get('day_count', '?')}天)")
                    if cp.get("trend_call"):
                        parts.append(f"  趋势判断: {cp['trend_call']}")
                rw = item.get("risk_warning")
                if rw:
                    parts.append(f"  风险提示: {rw}")
                parts.append("")

    # 全球宏观环境
    global_ctx = data.get("global_context", {})
    if global_ctx:
        parts.append("## 全球宏观环境\n")
        comms = global_ctx.get("commodities", {})
        if comms:
            parts.append("大宗商品:")
            for name, d in comms.items():
                line = f"  {name}: {d['price']:.1f} ({d['chg_pct']:+.2f}%)"
                if d.get("5d_chg_pct") is not None:
                    line += f", 5日{d['5d_chg_pct']:+.2f}%"
                parts.append(line)
        indices = global_ctx.get("indices", {})
        if indices:
            parts.append("全球指数:")
            for name, d in indices.items():
                parts.append(f"  {name}: {d['price']:.1f} ({d['chg_pct']:+.2f}%)")
        ivix = global_ctx.get("china_ivix", {})
        if ivix:
            parts.append(f"中国iVIX(波动率): {ivix['value']:.2f}")
        parts.append("")

    # 国内宏观环境
    macro_ctx = data.get("macro_context", {})
    if macro_ctx:
        parts.append("## 国内宏观环境\n")
        lpr = macro_ctx.get("lpr", {})
        if lpr:
            line = f"LPR({lpr.get('date', '')}): 1年期={lpr.get('lpr_1y', '?')}%, 5年期={lpr.get('lpr_5y', '?')}%"
            if lpr.get("prev_1y") and lpr["prev_1y"] != lpr.get("lpr_1y"):
                line += f" (上期: 1Y={lpr['prev_1y']}%, 5Y={lpr.get('prev_5y', '?')}%)"
            elif lpr.get("prev_1y"):
                line += " (持平)"
            parts.append(line)
        cb = macro_ctx.get("central_bank_weekly", {})
        if cb:
            parts.append(f"央行周操作({cb.get('date', '')}): 净投放={cb.get('net_injection', '?')}亿"
                         f", 逆回购={cb.get('reverse_repo', '?')}亿"
                         f", 到期={cb.get('reverse_repo_expire', '?')}亿")
        pmi = macro_ctx.get("pmi", {})
        if pmi:
            line = f"PMI({pmi.get('date', '')}): 制造业={pmi.get('manufacturing_pmi', '?')}"
            line += f", 非制造业={pmi.get('non_manufacturing_pmi', '?')}"
            line += f", 综合={pmi.get('composite_pmi', '?')}"
            if pmi.get("prev_manufacturing"):
                line += f" (上月制造业={pmi['prev_manufacturing']})"
            parts.append(line)
        cpi = macro_ctx.get("cpi", {})
        if cpi:
            parts.append(f"CPI({cpi.get('date', '')}): 同比={cpi.get('cpi_national', '?')}")
        shibor = macro_ctx.get("shibor", {})
        if shibor:
            parts.append(f"Shibor({shibor.get('date', '')}): 隔夜={shibor.get('overnight', '?')}, 1周={shibor.get('1w', '?')}")
        bond = macro_ctx.get("cn_us_bond", {})
        if bond:
            parts.append(f"中美利差({bond.get('date', '')}): 中国10Y={bond.get('cn_10y', '?')}%, 美国10Y={bond.get('us_10y', '?')}%, 利差={bond.get('spread_10y', '?')}")
        parts.append("")

    # 新闻事件
    news_events = data.get("news_events", [])
    morning = data.get("morning_news", {})
    search = data.get("search_results", {})

    if news_events:
        parts.append(f"## 新闻事件聚合（共{len(news_events)}个事件，已去重排序）\n")
        for i, ev in enumerate(news_events, 1):
            imp = ev.get("importance", "medium")
            parts.append(f"### 事件{i} [{imp}]: {ev.get('title', '')}")
            parts.append(f"  摘要: {ev.get('summary', '')}")
            sectors = ev.get("impact_sectors", [])
            if sectors:
                parts.append(f"  影响行业: {', '.join(sectors)}")
            sources = ev.get("sources", [])
            if sources:
                parts.append(f"  来源({len(sources)}条): {'; '.join(sources[:3])}")
            parts.append("")
    else:
        # 降级：使用原始新闻数据
        if morning:
            morning_items = (morning if isinstance(morning, list)
                             else morning.get("data", []))
            # V3.2: 早报正文注入（含财联社+陆家嘴早餐等完整内容）
            rich_items = [it for it in morning_items if it.get("content")]
            if rich_items:
                parts.append(f"## 财经早报（{len(rich_items)}篇，含完整内容）\n")
                for item in rich_items[:3]:
                    title = item.get("title", "")
                    source = item.get("from_source") or item.get("author", "")
                    content = item.get("content", "")[:3000]
                    # 清理HTML标签
                    import re as _re
                    content = _re.sub(r"<br\s*/?>", "\n", content)
                    content = _re.sub(r"<img[^>]*>", "", content)
                    content = _re.sub(r"<[^>]+>", "", content)
                    parts.append(f"### [{source}] {title}")
                    parts.append(content.strip())
                    parts.append("")
            else:
                parts.append("## 今日要闻\n")
                for item in morning_items[:10]:
                    title = item.get("title", "")
                    if title:
                        parts.append(f"- {title}")
                parts.append("")

        if search:
            parts.append("## 新闻搜索\n")
            for query, results in search.items():
                if not results:
                    continue
                items = results if isinstance(results, list) else results.get("data", [])
                if items:
                    parts.append(f"### {query}")
                    for item in items[:5]:
                        title = item.get("title", "")
                        summary = item.get("summary", "")[:100]
                        parts.append(f"- {title}")
                        if summary:
                            parts.append(f"  {summary}")
                    parts.append("")

    # RSS 公开信源（财联社/36氪/格隆汇/财新/金十/第一财经等）
    rss_news = data.get("rss_news", [])
    if rss_news:
        by_src = {}
        for art in rss_news:
            src = art.get("source", "RSS")
            by_src.setdefault(src, []).append(art)
        parts.append(f"## RSS 财经信源（{len(rss_news)}条，{len(by_src)}个信源）\n")
        for src_name, arts in by_src.items():
            parts.append(f"### {src_name}")
            for art in arts[:6]:
                title = art.get("title", "")
                content = art.get("content", "")[:300]
                if title:
                    parts.append(f"- {title}")
                    if content:
                        parts.append(f"  {content}")
            parts.append("")

    # （微信信源已移至 prompt 最前面，此处不再重复）

    # ── 并购重组信号（P1） ──
    ma_signals = data.get("ma_signals", {})
    if ma_signals and ma_signals.get("count", 0) > 0:
        parts.append(f"## 并购重组公告（今日 {ma_signals.get('count', 0)} 条）\n")
        parts.append(f"【摘要】{ma_signals.get('summary', '')}\n")

        by_ind = ma_signals.get("by_industry", {})
        if by_ind:
            parts.append("### 行业信号强度")
            for ind, sig in by_ind.items():
                cnt = sig.get("count", 0)
                strength = sig.get("strength", "low")
                flag = "★" if strength == "high" else ("☆" if strength == "medium" else "△")
                stocks = "、".join(sig.get("stocks", [])[:5])
                parts.append(f"  {flag} {ind}({cnt}家): {stocks}")
                if sig.get("notable"):
                    for note in sig.get("notable", [])[:2]:
                        parts.append(f"    - {note}")
            parts.append("")

        rss_ma_articles = ma_signals.get("rss_ma_articles", [])
        if rss_ma_articles:
            parts.append(f"### RSS M&A 相关报道（{len(rss_ma_articles)} 条）")
            for art in rss_ma_articles[:5]:
                parts.append(f"  - [{art.get('source', 'RSS')}] {art.get('title', '')}")
            parts.append("")

    # 行业扫描 TOP10 → 合并为"行业计分卡"（减少冗余）
    scan_top10 = data.get("scan_top10", [])
    signals = data.get("rotation_signals", [])
    exit_warnings = data.get("exit_warnings", [])
    heat = data.get("multi_period_heat", {})
    money_signals = data.get("money_signals", [])

    if scan_top10:
        # ── 确认信号区 vs 投机观察区 ──
        confirmed = [r for r in scan_top10[:10]
                     if r.get("stage") == "确认"
                     and r.get("consecutive_top10", 0) >= 2
                     and r.get("weekly_flow", 0) > 0]
        speculative = [r for r in scan_top10[:10] if r not in confirmed]

        if confirmed:
            parts.append("## ★ 确认信号（高置信度，构成报告骨架）\n")
            for r in confirmed:
                parts.append(_format_industry_scorecard(r))
            parts.append("")

        if speculative:
            parts.append("## 投机观察（低置信度，需事件支撑）\n")
            for r in speculative:
                parts.append(_format_industry_scorecard(r))
            parts.append("")

        # 预热区（精简）
        preheat = [r for r in scan_top10 if r.get("preheat")]
        if preheat:
            parts.append("### 预热区（排名11-20 + B1资金转正）")
            for r in preheat[:3]:
                parts.append(f"  #{r['rank']} {r['name']}: 涨{r['price_chg']:+.2f}% "
                             f"资金{r.get('fund_flow', 0):+.2f}亿")
            parts.append("")

    # 轮动信号 + 退出预警 + 多周期热度 → 合并为简短段
    signal_lines = []
    if signals:
        for sig in signals:
            signal_lines.append(f"  [{sig['type']}] {sig['industry']}: {sig['detail']}")
    if exit_warnings:
        for w in exit_warnings:
            signal_lines.append(f"  ⚠退出 [{w['type']}] {w['industry']}: {w['detail']}")
    if signal_lines:
        parts.append("## 轮动信号 & 退出预警\n")
        parts.extend(signal_lines)
        parts.append("")

    if heat:
        parts.append("## 多周期热度\n")
        wh = heat.get("weekly_hot", [])
        if wh:
            parts.append("持续热门: " + ", ".join(
                f"{h['name']}(连续{h['consecutive_weeks']}期)" for h in wh))
        tu = heat.get("trending_up", [])
        if tu:
            parts.append("新晋: " + ", ".join(h['name'] for h in tu))
        cd = heat.get("cooling_down", [])
        if cd:
            parts.append("退潮: " + ", ".join(h['name'] for h in cd))
        parts.append("")

    # 概念板块 TOP10
    concept_top10 = data.get("concept_top10", [])
    if concept_top10:
        parts.append("## 概念板块 TOP10（短期资金主题）\n")
        for r in concept_top10[:10]:
            s = r.get("scores", {})
            line = (f"  #{r['rank']} {r['name']}: "
                    f"涨{r['price_chg']:+.2f}% 资金{r.get('fund_flow', 0):+.2f}亿 "
                    f"涨跌比{r.get('rise_ratio', 0):.0f}% "
                    f"评分{r['score_auto']}")
            if r.get("consecutive_days", 0) >= 2:
                line += f" ★连续{r['consecutive_days']}天热门"
            parts.append(line)
        parts.append("")

    # （轮动信号、退出预警、多周期热度已合并到行业计分卡上方）

    # 资金异动信号（S3新增）
    money_signals = data.get("money_signals", [])
    if money_signals:
        parts.append(f"## 资金异动信号（{len(money_signals)}个，L0优先级）\n")
        for sig in money_signals:
            stocks_str = ""
            sig_stocks = sig.get("stocks", [])
            if sig_stocks:
                stocks_str = " | ".join(
                    f"{s.get('name', '')}({s.get('code', '')})"
                    for s in sig_stocks[:3]
                )
            top10_tag = " ★TOP10共振" if sig.get("in_top10") else ""
            parts.append(f"  [{sig['type']}] {sig['detail']}{top10_tag}")
            if stocks_str:
                parts.append(f"    标的: {stocks_str}")
        parts.append("")

    # 事件优先级排序（S3新增）
    prioritized = data.get("prioritized_events", [])
    if prioritized:
        parts.append(f"## 事件优先级（L0资金→L1数据→L2新闻，共{len(prioritized)}条）\n")
        for i, ev in enumerate(prioritized[:15], 1):
            parts.append(f"  {i}. [{ev['priority_level']}·{ev['priority_label']}] "
                         f"{ev['detail']}")
        parts.append("")

    # 行业共振股（S4新增）
    resonance = data.get("resonance_stocks", [])
    if resonance:
        parts.append(f"## 行业共振股（热度TOP30 ∩ TOP10行业，{len(resonance)}只）\n")
        for rs in resonance:
            parts.append(f"  {rs['name']}({rs['code']}) "
                         f"共振分{rs['resonance_score']} | "
                         f"{' + '.join(rs['reasons'])}")
        parts.append("")

    # V3.2 Sprint 2: 当日热点 ground truth（强制覆盖清单，触发及时性保障）
    hotspots = data.get("must_cover_hotspots") or {}
    top3_s = hotspots.get("top3_sectors", [])
    top3_c = hotspots.get("top3_concepts", [])
    hot_s = hotspots.get("hot_stocks", [])
    if top3_s or top3_c or hot_s:
        parts.append("## 🎯 今日必须覆盖的热点（ground truth · 北极星①）")
        parts.append("以下为当日市场真实热点，你的 events 必须覆盖其中至少 2/3：")
        parts.append("")
        if top3_s:
            parts.append(f"**涨幅 TOP3 行业**（申万一级，按当日涨幅）：{', '.join(top3_s)}")
        if top3_c:
            parts.append(f"**涨幅 TOP3 概念**（按当日涨幅）：{', '.join(top3_c)}")
        if hot_s:
            names = ", ".join(f"{s['name']}({s['code']})" for s in hot_s[:10])
            parts.append(f"**热度 TOP10 个股**（市场关注度）：{names}")
        parts.append("")
        parts.append(
            "**覆盖要求**：你选出的 events 中，sectors[].name 或 stocks[].code "
            "必须包含上述清单中至少 2/3 的元素。不覆盖将被质量门禁标记为信号漏抓。"
        )
        parts.append("**注意**：覆盖 ≠ 推荐买入。某个板块是规避方向时也要提及（depth=avoid）。")
        parts.append("")

    # V3.2 Sprint 3: 学习反馈（过去30天策略表现，用于本期校准）
    fb = data.get("learning_feedback") or {}
    if fb and fb.get("sample_size", 0) > 0:
        parts.append("## 🧠 过去30天策略表现反馈（用于本期信号校准）")
        parts.append(f"**回测窗口**：{fb.get('period', 'N/A')} · 样本 {fb['sample_size']} 条")
        overall = fb.get("overall") or {}
        wr = overall.get("win_rate")
        pnl = overall.get("avg_pnl")
        if wr is not None:
            parts.append(f"**整体表现**：胜率 {wr*100:.0f}% · 平均盈亏 {pnl:+.2f}% "
                        f"· R:R 中位 {fb.get('rr_stats', {}).get('median', 'N/A')}")

        # 分桶表现
        by_str = fb.get("by_strength") or {}
        if by_str:
            lines = []
            for s in ["strong", "medium", "weak"]:
                b = by_str.get(s)
                if b and b.get("win_rate") is not None:
                    lines.append(f"{s}: 胜率 {b['win_rate']*100:.0f}%（{b['verified']}条）")
            if lines:
                parts.append(f"**按强度**：{'  |  '.join(lines)}")

        by_rank = fb.get("by_event_rank") or {}
        if by_rank:
            lines = []
            for rk in sorted(by_rank.keys()):
                b = by_rank[rk]
                if b.get("win_rate") is not None:
                    lines.append(f"rank{rk}: {b['win_rate']*100:.0f}%（{b['verified']}条）")
            if lines:
                parts.append(f"**按 event_rank**：{'  |  '.join(lines)}")

        # 信号演化
        evo = fb.get("evolution_stats") or {}
        total_evo = sum(evo.values())
        if total_evo > 0:
            parts.append(
                f"**信号演化**：强化 {evo['strengthened']} / 弱化 {evo['weakened']} "
                f"/ 证伪 {evo['falsified']} / 观察 {evo['pending']}"
            )

        # 自动洞察
        insights = fb.get("insights") or []
        if insights:
            parts.append("")
            parts.append("**💡 关键洞察（基于历史数据）**：")
            for i, ins in enumerate(insights, 1):
                parts.append(f"  {i}. {ins}")

        # 校准建议
        calib = fb.get("calibration") or []
        if calib:
            parts.append("")
            parts.append("**🎯 本期校准建议（务必采纳）**：")
            for i, c in enumerate(calib, 1):
                parts.append(f"  {i}. {c}")

        parts.append("")
        parts.append("**重要**：以上反馈基于真实历史数据，请在本期生成时主动参考。"
                     "如历史显示某标签胜率偏低，不要对当期的该类信号过于自信。")
        parts.append("")

    # 热度TOP30个股（V3.2：过滤ETF后注入，品类纯化）
    hot_top30 = data.get("hot_top30_raw") or []
    if hot_top30:
        # V3.2 过滤 ETF/基金
        etf_prefixes = ("510", "511", "512", "513", "515", "516", "518",
                        "159", "563")
        def _is_stock(item):
            code = item.get("security_code", "") or item.get("code", "")
            name = item.get("security_name", "") or item.get("name", "")
            if code and code.startswith(etf_prefixes):
                return False
            if any(kw in name for kw in ("ETF", "基金", "LOF", "货币")):
                return False
            return True
        hot_stocks_only = [it for it in hot_top30 if _is_stock(it)]

        if hot_stocks_only:
            parts.append(f"## 市场热度TOP个股（{len(hot_stocks_only)}只，按关注度排序，已过滤ETF）\n")
            parts.append("以下是当日市场关注度最高的**个股**（非ETF），请结合你对个股所属行业的认知，"
                         "优先从中选取 depth=full/flex 标的（尤其是属于 TOP10 行业的个股）：")
            for i, item in enumerate(hot_stocks_only[:20], 1):
                code = (item.get("security_code", "") or item.get("code", ""))
                name = (item.get("security_name", "") or item.get("name", ""))
                desc = item.get("description", "")[:60]
                if code and name:
                    parts.append(f"  {i}. {name}({code}) {desc}")
            parts.append("")

    # 个股数据（分层：深度分析 vs 一行摘要）
    stock_data = data.get("stock_data", {})
    wechat_all = data.get("wechat_all_stocks", [])

    # ⚠ 白名单约束：明确告知 LLM 只能从这些标的中选
    if stock_data:
        whitelist_codes = sorted(stock_data.keys())
        whitelist_lines = []
        for c in whitelist_codes:
            s = stock_data[c]
            name = s.get("name", c)
            price = s.get("price", {}).get("last", "?")
            pe = s.get("valuation", {}).get("pe_ttm", "?")
            tier = s.get("catalyst_tier", "?")
            whitelist_lines.append(f"{name}({c}) 现价={price} PE={pe} [{tier}]")
        parts.append("## ⚠ Alpha 信号可选标的白名单（严格约束）\n")
        parts.append("alpha_signals 中的 stock.code 只能从以下列表中选取。")
        parts.append("不在此列表中的标的一律不得出现在 alpha_signals 中。")
        parts.append("entry_price 必须基于「现价」设定，严禁编造价格。\n")
        for line in whitelist_lines:
            parts.append(f"  - {line}")
        parts.append("")

    if stock_data:
        # 区分深度分析股（强推）和提及股
        wechat_codes = {ws["code"] for ws in wechat_all}
        top_industries = {r["name"] for r in confirmed_pre}
        full_stocks = {}
        mention_stocks = {}

        for code, sd in stock_data.items():
            tier = sd.get("catalyst_tier", "")
            if tier == "强推":
                full_stocks[code] = sd
            else:
                mention_stocks[code] = sd

        if full_stocks:
            parts.append(f"## 深度分析标的（{len(full_stocks)}只·强推）\n")
            for code, sd in full_stocks.items():
                name = sd.get("name", code)
                tag = ""
                if code in wechat_codes:
                    ws = next((w for w in wechat_all if w["code"] == code), {})
                    src = sd.get("_wechat_source", ws.get("source", ""))
                    tag = f" [微信·{src}]" if src else " [微信]"
                parts.append(f"### {name} ({code}){tag}")

                # 估值
                val = sd.get("valuation", {})
                if val:
                    pe = val.get("pe_ttm")
                    pb = val.get("pb")
                    pe_str = f"{pe}" if pe is not None else "N/A"
                    pb_str = f"{pb}" if pb is not None else "N/A"
                    parts.append(f"  估值: PE_TTM={pe_str}, PB={pb_str}")

                # 资金
                flow = sd.get("fund_flow", {})
                if flow:
                    parts.append(f"  资金: {flow.get('summary', '')}")
                    if flow.get("4d_total") is not None:
                        parts.append(f"  近4日累计: {flow['4d_total']:+.2f}亿")

                # NX 信号
                nx = sd.get("nx_signal", {})
                if nx and nx.get("signal"):
                    parts.append(
                        f"  NX: signal={nx['signal']}, swing_pos={nx.get('swing_position', 'N/A')}, "
                        f"elasticity={nx.get('elasticity_20d', 'N/A')}")

                # 新闻（只取1条最重要的）
                news = sd.get("news", [])
                if news:
                    parts.append(f"  新闻: {news[0].get('title', '')}")

                cat_score = sd.get("catalyst_score")
                if cat_score is not None:
                    parts.append(f"  综合评分: {cat_score}/100")
                parts.append("")

        if mention_stocks:
            parts.append(f"## 提及标的（{len(mention_stocks)}只·摘要）\n")
            for code, sd in list(mention_stocks.items())[:15]:
                name = sd.get("name", code)
                cat_score = sd.get("catalyst_score", "")
                cat_tier = sd.get("catalyst_tier", "")
                pe = sd.get("valuation", {}).get("pe_ttm", "N/A")
                nx_sig = sd.get("nx_signal", {}).get("signal", "")
                tag = " [微信]" if code in wechat_codes else ""
                parts.append(f"  {name}({code}){tag} PE={pe} NX={nx_sig} "
                             f"评分={cat_score} [{cat_tier}]")
            parts.append("")

    # 行业内因子选股排序（MVP核心）
    industry_ranks = data.get("industry_stock_ranks", {})
    if industry_ranks:
        parts.append(f"## 行业内因子选股（确认行业内量化排序）\n")
        for ind, ranked in industry_ranks.items():
            etf = ranked[0].get("etf_alternative", {}) if ranked else {}
            etf_str = f" | ETF替代: {etf.get('name','')}({etf.get('code','')})" if etf else ""
            parts.append(f"### {ind}{etf_str}")
            for s in ranked[:5]:
                parts.append(f"  #{s['rank_in_industry']} {s['name']}({s['code']}) "
                             f"评分{s['mini_score']}/100 "
                             f"NX={s.get('nx_signal','?')} "
                             f"资金4日={s.get('flow_4d','N/A')}亿 "
                             f"弹性={s.get('elasticity','N/A')}")
            parts.append("")

    # 信号追踪反馈（让LLM看到自己上次的信号表现）
    track = data.get("track_record", {})
    track_recent = data.get("track_recent", [])
    if track or track_recent:
        parts.append("## 信号追踪反馈\n")
        wr_1w = track.get("win_rate_1w")
        if wr_1w is not None:
            parts.append(f"  上期1周胜率: {wr_1w:.1%}")
        if track_recent:
            parts.append("  最近验证信号:")
            for tr in track_recent[-5:]:
                name = tr.get("name", "?")
                stage = tr.get("stage", "?")
                v1w = tr.get("verification", {}).get("1w", {})
                ret = v1w.get("return_pct")
                if ret is not None:
                    parts.append(f"    {name}({stage}): 1周{ret:+.2f}%")
        parts.append("  → 请据此校准本期信号置信度\n")

    # Token 计量警告
    total_chars = sum(len(p) for p in parts)
    est_tokens = total_chars // 2  # 中文约2字符/token
    if est_tokens > 6000:
        parts.insert(0, f"⚠ 数据量较大（约{est_tokens} tokens），请确保分析质量不被压缩\n")

    # ── 票池进攻性指标（V3.5 新增）──
    pool_agg = data.get("pool_aggressiveness")
    if pool_agg and pool_agg.get("n_stocks", 0) > 0:
        parts.append("## 票池进攻性\n")
        parts.append(f"  综合评分: {pool_agg['score']}/100 ({pool_agg['level']})")
        bd = pool_agg.get("breakdown", {})
        parts.append(f"  弹性={bd.get('elasticity', 0):.0f} 高位={bd.get('limit_up', 0):.0f} "
                     f"资金={bd.get('fund_flow', 0):.0f} NX买点={bd.get('nx_buy', 0):.0f}")
        if pool_agg["level"] == "高":
            parts.append("  → 票池偏进攻，建议在日报中提示控制仓位和止损纪律")
        parts.append("")

    # ── ETF vs 个股 alpha 反馈（V3.5 新增）──
    etf_feedback = data.get("etf_vs_stock")
    if etf_feedback and etf_feedback.get("verdict") != "数据不足":
        parts.append("## ETF vs 个股 Alpha 反馈\n")
        summary = etf_feedback.get("summary", {})
        parts.append(f"  结论: {etf_feedback['verdict']}")
        parts.append(f"  平均 Alpha: {summary.get('avg_alpha', '?')}%, "
                     f"胜率: {summary.get('win_rate', '?')}, "
                     f"样本: {summary.get('n_comparisons', 0)} 组")
        for c in etf_feedback.get("comparisons", [])[:5]:
            alpha = c.get("alpha")
            if alpha is not None:
                parts.append(f"  {c['industry']}: 个股{c.get('stock_avg_2w', '?')}% vs "
                             f"行业{c.get('sector_avg_2w', '?')}% → alpha {alpha:+.2f}%")
        parts.append("  → 个股 alpha 为负的行业，考虑在日报中降低个股推荐信心\n")

    # ── 强制覆盖清单：微信主题不可丢弃 ──
    if themes:
        theme_names = [t.get("name") or t.get("theme", "?") for t in themes]
        parts.append("## ⚠ 覆盖要求（硬约束）\n")
        parts.append("以下微信主题必须全部出现在 events 中（可合并相近主题为一个事件，但不可丢弃）：")
        for i, name in enumerate(theme_names, 1):
            heat = themes[i-1].get("heat", "")
            n_stocks = len(themes[i-1].get("stocks", []))
            parts.append(f"  {i}. {name} (热度:{heat}, {n_stocks}只个股)")
        parts.append(f"\n共 {len(theme_names)} 个微信主题 + 新闻/宏观事件 → 目标 6-8 个 events。")
        parts.append("相近主题可合并（如'AI硬件'+'CPO光模块'→一个事件），但合并后标题须体现两者。")
        parts.append("每个事件至少带 1 只个股。覆盖不全将被退回重做。\n")

    return "\n".join(parts)


# ══════════════════════════════════════════════════════
# V3.5 Sprint A: ETF 轮动日报 SYSTEM_PROMPT
# ══════════════════════════════════════════════════════

ETF_SYSTEM_PROMPT = """\
你是「周期雷达 · ETF 轮动交易员」。基于日+周+月三频段轮动因子，输出**可直接执行的 ETF 交易方案**。
不是做配置、不是做对冲——ETF 是独立的交易品种，像个股一样做波段。

## 产品定位
- 频段：2-4 周持仓（比个股日报的 2 周更长，ETF 稳定性更高）
- 因子：日(A系价量) + 周(B1/C/D系资金估值) + 月(D系产业资本、多周期热度)
- 标的：行业 ETF（申万一级映射）+ 宽基 ETF（50/300/500/创业板/科创50）
- 期权：**仅作为放大器**，不是主角（strong 信号时可选 option_boost）

## 事件驱动 × 因子确认 = ETF 交易信号

### 事件筛选（4 维）
- 市场冲击力：是否影响整个行业板块？（ETF 交易关心的是行业而非单票）
- 持续性：能否维持 2-4 周？（短期脉冲不适合 ETF 波段）
- 因子共振：轮动分层（A/B/C/D/E）有多少因子共同确认？
- 宽度确认：板块内涨跌比 / 连续 TOP10 天数

### ETF 分层（和个股的 depth 对齐）
- **depth=full**：2-3 个确认行业的 ETF（主仓位，必带 signal）
- **depth=mention**：关注阶段行业的 ETF（观察池，signal 可选）
- **depth=avoid**：已退出 TOP10 / 衰退预警的 ETF

### signal 硬要求（V3.5 ETF 版）
- entry_price：ETF 最新收盘价 / 回调支撑位（数字，必填）
- target_price：entry × (1 + 预期涨幅)；**ETF 典型涨幅 5-12%**
- stop_loss：entry × 0.94（-6% 止损，比个股 -8% 严格；ETF 波动小）
- **R:R 硬要求 ≥ 1.3**（ETF 波段门槛比个股 1.5 低，但仍需正期望）
- time_horizon：只允许 "2w" 或 "1m"（不允许 "1w"）
- strength："strong"（3+ 因子共振）/ "medium"（2 因子）/ "weak"（1 因子）

### 期权放大器（option_boost 可选字段）
仅当 signal.strength == "strong" 且 ETF 期权可用时给出：
```
"option_boost": {
    "type": "call" | "put",
    "strike": 建议行权价（ETF 价格附近），
    "expiry": "下个月" | "下下月",
    "iv_note": "IV 分位 < 30 可买方 / > 70 建议卖方",
    "position_hint": "不超过 ETF 仓位的 20%"
}
```

### 品类纯化（硬门禁）
- ETF 日报的 etfs[] 数组**只允许 ETF/LOF 代码**（510/511/512/513/515/516/518/159/563 开头）
- 禁止出现个股代码（留给个股日报）
- 禁止出现商品期货代码
- 每个事件 2-4 只 ETF，全局 8-15 只

## 事件数要求
- 6-8 个事件（和个股日报对齐）
- rank 1-2 主线事件：完整 chain 传导图 + 3 只 ETF（full ≥ 2）
- rank 3-5 次线事件：2 只 ETF
- rank 6-8 背景事件：1 只 ETF（mention 即可）

## 数据输入来源（和个股日报一致）
- 行业扫描 TOP10 + 复合评分
- 板块资金流（日/周）
- 多周期热度（持续热门/新晋/退潮）
- 宏观数据（LPR/PMI/CPI/利差）
- 商品传导（C2：铜→电网设备 滞后 IC+0.58 等已验证规则）
- 历史 30 天学习反馈

## 输出格式

输出一个 ```json 代码块：
{
  "date": "YYYY-MM-DD",
  "product": "etf_rotation",  // 标识这是 ETF 日报
  "market_context": "当日市场背景 + 轮动主线 + 因子共振情况",
  "events": [
    {
      "rank": 1,
      "title": "事件标题",
      "profile": {"type": "...", "surprise": "...", "status": "..."},
      "transmission": {
        "direct": "一阶传导",
        "indirect": "二阶传导",
        "sentiment": "市场情绪",
        "chain": [{"from": "...", "to": "...", "label": "...", "type": "sector|etf"}]
      },
      "sectors": [{"name": "申万一级", "why": "...", "rotation": {...}}],
      "etfs": [
        {
          "code": "159611",
          "name": "电力ETF",
          "depth": "full",
          "selection_reason": "电网设备行业代理 + 铜价传导",
          "data_ref": {
            "last_price": 1.235,
            "5d_chg_pct": 3.2,
            "sector_composite_score": 67.5,
            "consecutive_top10": 3,
            "weekly_flow_yi": 8.5
          },
          "signal": {
            "entry_price": 1.235,
            "target_price": 1.35,
            "stop_loss": 1.16,
            "strength": "strong",
            "time_horizon": "2w",
            "entry_logic": "回调至20日均线入场"
          },
          "option_boost": {
            "type": "call",
            "strike": 1.25,
            "expiry": "2026-05",
            "iv_note": "IV 分位 25，买方成本低",
            "position_hint": "ETF 仓位 20% 上限"
          },
          "catalysts": ["铜价周涨3%", "国网投资加速"],
          "risks": ["铜价冲高回落"]
        }
      ],
      "bull_bear": {...},
      "drivers": {...},
      "scenarios": {...},
      "historical": {...}
    }
  ]
}

## JSON 转义要求（硬性）
- 字符串内引用一律用中文引号「」或『』，禁止裸露英文双引号 "
- 正确：`"reason": "「铜」价突破"` | 错误：`"reason": ""铜"价突破"`

## 写作风格
- 像交易员晨会，不是研究员研报
- 每个 ETF 必须回答「所以呢」——明确的进场方向
- 数据嵌入行文（"B1+D1+E1 三因子共振，周资金 +8.5 亿"）
- 末尾标注「仅供参考，不构成投资建议」

## 输出前自检
1. 所有 etfs[].code 都是 ETF 代码？（510/511/512/513/515/516/518/159/563 开头）
2. depth=full 的 ETF 的 signal 三项都是数字？R:R ≥ 1.3？
3. time_horizon 没用 "1w"？
4. 主线事件有 chain 字段？
5. 事件总数 6-8？
6. option_boost 只在 strength=strong 时出现？
"""


# ══════════════════════════════════════════════════════
# LLM 调用
# ══════════════════════════════════════════════════════

# V3.2 Sprint 4: 多模型智能路由
# 按任务层级分配不同能力模型，核心产品用 Opus，结构化任务用 Sonnet，预处理用 Haiku
MODEL_TIERS = {
    "premium":  "claude-sonnet-4-6",              # 日报（Sonnet 性价比最优，Opus 做 fallback）
    "standard": "claude-sonnet-4-6",              # 晨报等（结构化生成）
    "cheap":    "claude-haiku-4-5-20251001",      # 主题提取、新闻聚合（信息抽取）
}

MODEL_FALLBACK = {
    "premium":  ["claude-opus-4-6", "claude-sonnet-4-5-20250929"],
    "standard": ["claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001"],
    "cheap":    ["claude-sonnet-4-6"],
}


def select_model(tier: str = "premium") -> str:
    """按任务层级返回合适的模型 ID。

    Tiers:
        premium  — 核心对外产品（日报 / 公众号文章）
        standard — 结构化生成（晨报 / 期权日报）
        cheap    — 预处理任务（主题提取 / 新闻聚合）
    """
    return MODEL_TIERS.get(tier, MODEL_TIERS["premium"])


def call_claude_api(system: str, user_prompt: str,
                    model: str | None = None,
                    tier: str = "premium") -> str:
    """调用 Claude API，返回完整文本响应。

    V3.2 Sprint 4: 支持 tier 参数按任务分层路由。
    - 显式传 model 优先（用于临时覆盖，如 --model 参数）
    - 否则按 tier 选择默认模型 + 降级链
    """
    import anthropic
    import os

    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    base_url = os.environ.get("ANTHROPIC_BASE_URL")

    if not api_key:
        raise RuntimeError("未设置 ANTHROPIC_AUTH_TOKEN 或 ANTHROPIC_API_KEY 环境变量")

    client = anthropic.Anthropic(
        api_key=api_key,
        base_url=base_url,
        timeout=180.0,
    )

    # 构造模型尝试顺序
    if model:
        # 显式指定模型，按原有逻辑
        MODELS_TO_TRY = [model,
                         "claude-opus-4-7",
                         "claude-sonnet-4-6",
                         "claude-sonnet-4-5-20250929"]
    else:
        # 按 tier 路由
        primary = MODEL_TIERS.get(tier, MODEL_TIERS["premium"])
        fallback = MODEL_FALLBACK.get(tier, MODEL_FALLBACK["premium"])
        MODELS_TO_TRY = [primary] + fallback

    # 去重保序
    seen = set()
    MODELS_TO_TRY = [m for m in MODELS_TO_TRY
                     if m and not (m in seen or seen.add(m))]

    print("\n  调用 Claude API...")
    if base_url:
        print(f"  端点: {base_url}")
    print(f"  Prompt 长度: ~{len(system) + len(user_prompt)} 字符")

    last_error = None
    for m in MODELS_TO_TRY:
        if not m:
            continue
        try:
            print(f"  尝试模型: {m}")
            with client.messages.stream(
                model=m,
                max_tokens=20000,
                system=system,
                messages=[{"role": "user", "content": user_prompt}],
            ) as stream:
                response = stream.get_final_message()

            text = response.content[0].text
            usage = response.usage
            stop = response.stop_reason
            print(f"  模型: {m} ✓")
            print(f"  Token 用量: 输入={usage.input_tokens}, 输出={usage.output_tokens}")
            print(f"  停止原因: {stop}")
            cost_est = (usage.input_tokens * 3 + usage.output_tokens * 15) / 1_000_000
            print(f"  预估费用: ~${cost_est:.3f}")

            needs_continuation = (stop == "max_tokens")
            if not needs_continuation and usage.output_tokens > 5000:
                stripped = text.rstrip()
                if not stripped.endswith("```") and not stripped.endswith("}"):
                    needs_continuation = True
                    print(f"  ⚠ 输出疑似被中转API截断(end_turn但JSON未闭合)")

            MAX_CONTINUATIONS = 3
            continuation_count = 0
            while needs_continuation and continuation_count < MAX_CONTINUATIONS:
                continuation_count += 1
                print(f"  ⚠ 续写第{continuation_count}次...")
                with client.messages.stream(
                    model=m,
                    max_tokens=20000,
                    system=system,
                    messages=[
                        {"role": "user", "content": user_prompt},
                        {"role": "assistant", "content": text},
                        {"role": "user", "content": "你的JSON输出不完整,请从断点处继续输出剩余内容。只输出剩余部分,不要重复。确保JSON完整闭合并以 ``` 结束。"},
                    ],
                ) as cont_stream:
                    continuation = cont_stream.get_final_message()
                cont_text = continuation.content[0].text
                cont_usage = continuation.usage
                cont_stop = continuation.stop_reason
                print(f"  续写完成: +{cont_usage.output_tokens} tokens, 停止原因={cont_stop}")
                text = text + cont_text

                stripped = text.rstrip()
                if stripped.endswith("```") or stripped.endswith("}"):
                    needs_continuation = False
                elif cont_stop == "max_tokens" or cont_usage.output_tokens > 5000:
                    needs_continuation = True
                else:
                    needs_continuation = False

            return text

        except Exception as e:
            err_msg = str(e)
            retryable = any(k in err_msg for k in
                           ("403", "无权访问", "not found", "524", "529",
                            "500", "502", "503", "overloaded", "timeout",
                            "Timeout", "timed out", "rate_limit"))
            if retryable:
                print(f"  → {m}: 失败({err_msg[:80]}), 尝试下一个...")
                last_error = e
                continue
            else:
                raise

    raise RuntimeError(f"所有模型都不可用。最后一个错误: {last_error}")


def parse_llm_output(text: str) -> tuple[dict | None, str | None]:
    """从 LLM 输出中提取 JSON 和 HTML 块。

    V3.2 Sprint 2.6: 两段式解析策略 —
      Step 1: 先尝试原始文本解析（不做任何清理）
      Step 2: 失败时才做引号/逗号/截断清理
    避免误伤 LLM 正确转义的字符串内引号。
    """
    json_match = re.search(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
    rotation_json = None

    raw = None
    if json_match:
        raw = json_match.group(1)
    else:
        # 兜底：找 { 开头到最后一个 } 的范围
        start = text.find('{\n  "date"')
        if start >= 0:
            # 找最后一个完整的 }
            end = text.rfind("}")
            if end > start:
                raw = text[start:end + 1]
                print(f"  ⚠ 未找到 ```json 块，使用兜底提取 ({end - start} chars)")

    if raw:
        # ── Step 1: 先尝试原始解析（不清理）──
        try:
            rotation_json = json.loads(raw)
            return rotation_json, _extract_html_body(text)
        except json.JSONDecodeError as e0:
            print(f"  ⚠ 原始 JSON 解析失败 (预期内): {str(e0)[:80]}")
            # 进入 Step 2 清理模式

        # ── Step 2: 清理 + 补全 ──
        raw_cleaned = raw
        # 修复 control characters（续写拼接时常见：字符串值内的裸换行/tab）
        # 保留结构性换行(\n在引号外),只清理引号内的裸换行
        def _fix_control_chars(s: str) -> str:
            result = []
            in_string = False
            escape_next = False
            for ch in s:
                if escape_next:
                    result.append(ch)
                    escape_next = False
                    continue
                if ch == '\\' and in_string:
                    result.append(ch)
                    escape_next = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    result.append(ch)
                    continue
                if in_string and ord(ch) < 0x20 and ch not in ('\n',):
                    result.append(' ')
                elif in_string and ch == '\n':
                    result.append('\\n')
                else:
                    result.append(ch)
            return ''.join(result)
        raw_cleaned = _fix_control_chars(raw_cleaned)
        raw_cleaned = re.sub(r",\s*([}\]])", r"\1", raw_cleaned)  # trailing commas

        # V3.2 Sprint 2.6: 引号替换更保守 —
        # 只替换 JSON 结构位置（前后有 : 或 , 或 换行）的中文引号
        # 避免误伤字符串内的 "强确认" 等正确引用
        # 旧策略：raw.replace("\u201c", '"').replace("\u201d", '"')
        # 新策略：只替换孤立的中文引号（前后是 ASCII 结构字符）
        raw_cleaned = re.sub(
            r'(?<=[:\[,\s])["\u201c]\s*([^"\u201c\u201d]*?)\s*["\u201d](?=[,\]\s:}])',
            lambda m: '"' + m.group(1).replace('"', '\\"') + '"',
            raw_cleaned
        )
        # em/en dash 保留清理（无害）
        raw_cleaned = raw_cleaned.replace("\u2014", "-").replace("\u2013", "-")

        # 处理截断的 JSON（含字符串值中间截断）
        def _try_truncation_repair(s: str) -> str | None:
            """逐行回退到最后一个完整 JSON 行，然后补全括号。"""
            lines = s.split("\n")
            for cut in range(len(lines), 0, -1):
                candidate = "\n".join(lines[:cut]).rstrip()
                if not candidate:
                    continue
                last_char = candidate[-1]
                if last_char not in (",", "{", "[", "}", "]", '"'):
                    continue
                trimmed = re.sub(r",\s*$", "", candidate)
                bc = trimmed.count("{") - trimmed.count("}")
                bk = trimmed.count("[") - trimmed.count("]")
                if bc < 0 or bk < 0:
                    continue
                repaired = trimmed + "\n" + "]" * bk + "}" * bc
                try:
                    json.loads(repaired)
                    print(f"  ⚠ JSON 截断修复: 回退 {len(lines)-cut} 行, 补 }}×{bc} ]×{bk}")
                    return repaired
                except json.JSONDecodeError:
                    continue
            return None

        try:
            rotation_json = json.loads(raw_cleaned)
            print(f"  ✓ Step 2 清理后解析成功")
        except json.JSONDecodeError as e:
            print(f"  ⚠ Step 2 清理后仍失败: {e}")
            repaired = _try_truncation_repair(raw_cleaned)
            if repaired:
                rotation_json = json.loads(repaired)
                return rotation_json, _extract_html_body(text)

            pos = e.pos or 0
            ctx = raw_cleaned[max(0, pos - 80):pos + 80]
            print(f"    上下文: ...{ctx}...")

            # Step 2.5 (V3.2 Sprint 4): 暴力清理 — 把 JSON 字符串值内所有裸露的 "
            # 转义为 \"。用"字段键": "值" 的模式识别字符串边界。
            try:
                # 正则匹配 "key": "...." 形式，把 value 内部的 " 全替换
                def _fix_inner_quotes(match):
                    key = match.group(1)
                    value = match.group(2)
                    # 把 value 中所有未转义的 " 转为 \"
                    # 先还原已有的 \" 占位，再统一处理
                    value_fixed = value.replace('\\"', '\x00').replace('"', '\\"').replace('\x00', '\\"')
                    return f'"{key}": "{value_fixed}"'
                # 匹配 "key": "value" 其中 value 可能含裸 "
                # 用贪婪到最近 "," 或 "}" 或 "]"前的 " 作为结束
                pattern = r'"([^"\\]+)":\s*"((?:[^"\\]|\\.|"(?=[^,\]\}]))*?)"(?=\s*[,\]\}])'
                raw_forced = re.sub(pattern, _fix_inner_quotes, raw_cleaned, flags=re.DOTALL)
                rotation_json = json.loads(raw_forced)
                print(f"  ✓ Step 2.5 暴力清理成功")
                return rotation_json, _extract_html_body(text)
            except (json.JSONDecodeError, Exception) as e2:
                print(f"  ⚠ Step 2.5 暴力清理失败: {str(e2)[:60]}")

            # Step 3: 保存原始文本供手动恢复
            from pathlib import Path
            from datetime import datetime
            dump_path = Path("/tmp") / f"rotation_raw_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            try:
                dump_path.write_text(text, encoding="utf-8")
                print(f"  💾 LLM 原始输出已 dump 到: {dump_path}")
            except Exception:
                pass

    return rotation_json, _extract_html_body(text)


def _extract_html_body(text: str) -> str | None:
    """从 LLM 输出中提取 ```html 块（辅助函数）。"""
    html_match = re.search(r"```html\s*\n(.*?)\n```", text, re.DOTALL)
    return html_match.group(1).strip() if html_match else None


def _parse_llm_output_old(text: str) -> tuple[dict | None, str | None]:
    """旧版解析函数（保留 fallback 用，不再调用）。"""
    json_match = re.search(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
    rotation_json = None

    raw = None
    if json_match:
        raw = json_match.group(1)
    else:
        # 兜底：找 { 开头到最后一个 } 的范围
        start = text.find('{\n  "date"')
        if start >= 0:
            # 找最后一个完整的 }
            end = text.rfind("}")
            if end > start:
                raw = text[start:end + 1]
                print(f"  ⚠ 未找到 ```json 块，使用兜底提取 ({end - start} chars)")

    if raw:
        # 清理常见 LLM 输出问题
        raw = re.sub(r",\s*([}\]])", r"\1", raw)  # trailing commas
        raw = raw.replace("\u201c", '"').replace("\u201d", '"')  # 中文引号
        raw = raw.replace("\u2018", "'").replace("\u2019", "'")
        raw = raw.replace("\u2014", "-").replace("\u2013", "-")  # em/en dash

        # 处理截断的 JSON（output_tokens 到上限）
        if not raw.rstrip().endswith("}"):
            # 尝试补全：找到最后一个完整的 event 对象
            last_complete = raw.rfind("}\n    ]")
            if last_complete > 0:
                raw = raw[:last_complete] + "}\n    ]\n  ]\n}"
                print(f"  ⚠ JSON 被截断，自动补全到最后一个完整事件")
            else:
                # 更激进的补全
                brace_count = raw.count("{") - raw.count("}")
                bracket_count = raw.count("[") - raw.count("]")
                raw += "]" * bracket_count + "}" * brace_count
                print(f"  ⚠ JSON 被截断，强制补全括号 (}}×{brace_count}, ]×{bracket_count})")

        try:
            rotation_json = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"  ⚠ JSON 解析失败: {e}")
            pos = e.pos or 0
            ctx = raw[max(0, pos - 80):pos + 80]
            print(f"    上下文: ...{ctx}...")

            # 最后手段：逐字符修复常见问题
            try:
                # 删除有问题的行再试
                lines = raw.split("\n")
                fixed_lines = []
                for line in lines:
                    try:
                        # 测试每行是否导致 JSON 问题
                        fixed_lines.append(line)
                    except Exception:
                        pass
                raw_fixed = "\n".join(fixed_lines)
                raw_fixed = re.sub(r',(\s*[}\]])', r'\1', raw_fixed)
                rotation_json = json.loads(raw_fixed)
                print(f"  ✓ JSON 二次修复成功")
            except json.JSONDecodeError:
                pass

    html_match = re.search(r"```html\s*\n(.*?)\n```", text, re.DOTALL)
    html_body = html_match.group(1).strip() if html_match else None

    return rotation_json, html_body


# ══════════════════════════════════════════════════════
# 质量验证
# ══════════════════════════════════════════════════════

def validate_rotation_json(rot: dict, hotspots: dict = None,
                            stock_universe: dict | None = None) -> tuple[list[str], list[str]]:
    """V3.6: 验证事件驱动引擎输出的结构化 JSON 质量。

    检查 events / alpha_signals / sector_outlook / commodity_signals / global_conclusion。
    """
    errors: list[str] = []
    warnings: list[str] = []
    valid_sectors = set(INDUSTRY_LEADERS.keys())

    # ── events 校验 ──
    n_events = len(rot.get("events", []))
    if n_events < 3:
        errors.append(f"事件数不足: {n_events} (最少3个)")
    elif n_events > 8:
        warnings.append(f"事件数过多: {n_events} (最多8个)")

    for i, ev in enumerate(rot.get("events", [])):
        rank = ev.get("rank", i + 1)
        if not ev.get("title"):
            errors.append(f"事件{rank} 缺少 title")
        if not ev.get("interpretation"):
            warnings.append(f"事件{rank} 缺少 interpretation")
        et = ev.get("event_time")
        if et:
            if et.get("certainty") not in ("occurred", "ongoing", "expected"):
                warnings.append(f"事件{rank} certainty 非法: '{et.get('certainty')}'")

    # ── alpha_signals 校验 ──
    signals = rot.get("alpha_signals", [])
    if len(signals) > 5:
        warnings.append(f"Alpha 信号过多: {len(signals)} (最多5条)")

    for sig in signals:
        name = sig.get("stock", {}).get("name", "?")
        code = sig.get("stock", {}).get("code", "")
        if not (code and len(code) == 6 and code.isdigit()):
            errors.append(f"Alpha信号无效代码: '{code}' ({name})")

        # ETF 禁入
        etf_prefixes = ("510", "511", "512", "513", "515", "516", "518", "159", "563")
        if code and code.startswith(etf_prefixes):
            errors.append(f"Alpha信号混入ETF: {name}({code})")

        # 白名单校验：标的必须来自输入数据
        if stock_universe and code and code not in stock_universe:
            errors.append(f"Alpha信号标的不在输入数据中(LLM幻觉): {name}({code})")

        entry = sig.get("entry_price")
        target = sig.get("target_price")
        stop = sig.get("stop_loss")

        for field, val in [("entry_price", entry), ("target_price", target), ("stop_loss", stop)]:
            if val is None:
                errors.append(f"Alpha信号缺{field}: {name}")
            elif not isinstance(val, (int, float)) or val <= 0:
                errors.append(f"Alpha信号{field}非有效数字: {name} = '{val}'")

        if all(isinstance(v, (int, float)) and v > 0 for v in [entry, target, stop]):
            direction = sig.get("direction", "long")
            if direction == "long":
                risk = entry - stop
                reward = target - entry
            else:
                risk = stop - entry
                reward = entry - target
            if risk <= 0:
                errors.append(f"Alpha信号止损逻辑错误: {name} entry={entry} stop={stop}")
            elif reward <= 0:
                errors.append(f"Alpha信号目标逻辑错误: {name} entry={entry} target={target}")
            else:
                rr = reward / risk
                if rr < 1.5:
                    errors.append(f"Alpha信号R:R不达标: {name} R:R={rr:.2f} (需≥1.5)")

        th = sig.get("time_window", "2w")
        if th == "1w":
            errors.append(f"Alpha信号time_window=1w: {name} — 1w是反向指标，只允许2w/1m")

        if not sig.get("thesis"):
            warnings.append(f"Alpha信号缺thesis: {name}")

    # ── sector_outlook 校验 ──
    outlook = rot.get("sector_outlook", [])
    for so in outlook:
        sector = so.get("sector", "")
        if sector and sector not in valid_sectors:
            matches = difflib.get_close_matches(sector, list(valid_sectors), n=1, cutoff=0.6)
            if matches:
                warnings.append(f"行业名近似匹配: '{sector}' → '{matches[0]}'")
            else:
                warnings.append(f"非标行业名: '{sector}'")
        direction = so.get("direction", "")
        if direction not in ("看多", "看空", "中性", "观望"):
            warnings.append(f"sector_outlook direction 非法: '{direction}' ({sector})")
        if not so.get("event_driver"):
            warnings.append(f"sector_outlook 缺 event_driver: {sector}")

    # ── commodity_signals 校验 ──
    commodities = rot.get("commodity_signals", [])
    valid_commodities = {"铜", "原油", "黄金", "白银", "铁矿", "螺纹钢", "焦煤", "天然气"}
    for cs in commodities:
        comm = cs.get("commodity", "")
        if comm and comm not in valid_commodities:
            warnings.append(f"非标商品名: '{comm}'")
        direction = cs.get("direction", "")
        if direction not in ("多", "空", "观望"):
            warnings.append(f"commodity direction 非法: '{direction}' ({comm})")

    # ── global_conclusion 校验 ──
    gc = rot.get("global_conclusion")
    if not gc:
        warnings.append("缺少 global_conclusion")
    else:
        for field in ("market_regime", "confidence", "action", "key_thesis"):
            if not gc.get(field):
                warnings.append(f"global_conclusion 缺少 {field}")
        regime = gc.get("market_regime", "")
        if regime and regime not in ("进攻", "均衡", "防守"):
            warnings.append(f"global_conclusion.market_regime 非法: '{regime}'")
        action = gc.get("action", "")
        if action and action not in ("加仓", "持仓", "减仓", "观望"):
            warnings.append(f"global_conclusion.action 非法: '{action}'")

    return errors, warnings


def postprocess_rotation_json(rot: dict) -> dict:
    """V3.6: 自动修正结构化输出中的常见质量问题。"""
    valid_sectors = list(INDUSTRY_LEADERS.keys())

    # sector_outlook 行业名模糊匹配修正
    for so in rot.get("sector_outlook", []):
        sector = so.get("sector", "")
        if sector and sector not in valid_sectors:
            matches = difflib.get_close_matches(sector, valid_sectors, n=1, cutoff=0.6)
            if matches:
                print(f"  [后处理] 行业名修正: '{sector}' → '{matches[0]}'")
                so["sector"] = matches[0]

    # events 中 sector_impact 行业名修正
    for ev in rot.get("events", []):
        for si in ev.get("sector_impact", []):
            name = si.get("sector", "")
            if name and name not in valid_sectors:
                matches = difflib.get_close_matches(name, valid_sectors, n=1, cutoff=0.6)
                if matches:
                    print(f"  [后处理] 事件行业名修正: '{name}' → '{matches[0]}'")
                    si["sector"] = matches[0]

    # alpha_signals 自动生成 signal_id（如果 LLM 未生成）
    date_str = rot.get("date", "")
    for i, sig in enumerate(rot.get("alpha_signals", []), 1):
        if not sig.get("signal_id") and date_str:
            sig["signal_id"] = f"ALPHA-{date_str.replace('-', '')}-{i:03d}"

    # 自动计算 event_time.decay_days
    if date_str:
        try:
            from datetime import datetime
            rpt_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            for ev in rot.get("events", []):
                et = ev.get("event_time")
                if et and et.get("occurred_at"):
                    try:
                        occ_date = datetime.strptime(et["occurred_at"], "%Y-%m-%d").date()
                        et["decay_days"] = (rpt_date - occ_date).days
                    except ValueError:
                        pass
        except ValueError:
            pass

    return rot


# ══════════════════════════════════════════════════════
# Phase 2: 生成
# ══════════════════════════════════════════════════════

def generate_report(data: dict, dry_run: bool = False,
                    model: str | None = None) -> dict | None:
    """Phase 2: 事件驱动引擎 — 构造 prompt → 调用 Claude API → 解析结构化输出。"""
    print(f"\n{'━' * 50}")
    print(f"  Phase 2: LLM 事件解读引擎")
    print(f"{'━' * 50}")

    # ── Preflight: 数据充分性检查 ──
    preflight = []
    wechat_themes = data.get("wechat_themes", [])
    scan_top10 = data.get("scan_top10", [])
    if not scan_top10:
        preflight.append("行业扫描为空")
    if len(wechat_themes) < 3:
        preflight.append(f"微信主题仅{len(wechat_themes)}个(建议≥5)")
    if preflight:
        print(f"  ⚠ Preflight 警告:")
        for p in preflight:
            print(f"    - {p}")
        print(f"  → 数据偏薄，LLM 输出质量可能受限")

    user_prompt = build_data_prompt(data)

    if dry_run:
        print("\n  ── System Prompt ──")
        print(SYSTEM_PROMPT[:500] + "\n  ... (共 " +
              str(len(SYSTEM_PROMPT)) + " 字符)")
        print("\n  ── User Prompt ──")
        print(user_prompt)
        print(f"\n  (dry-run 模式，未调用 API)")
        return None

    text = call_claude_api(SYSTEM_PROMPT, user_prompt, model=model)
    signals_json, _ = parse_llm_output(text)

    if signals_json:
        signals_json = postprocess_rotation_json(signals_json)

        # 白名单过滤：丢弃不在 stock_data 中的 alpha 信号（LLM 幻觉）
        universe = data.get("stock_data", {}) or {}
        if universe:
            raw_signals = signals_json.get("alpha_signals", [])
            kept, dropped = [], []
            for sig in raw_signals:
                code = sig.get("stock", {}).get("code", "")
                if code and code in universe:
                    # 用真实数据回填名称和现价
                    real_name = universe[code].get("name", "")
                    if real_name:
                        sig["stock"]["name"] = real_name
                    kept.append(sig)
                else:
                    dropped.append(sig.get("stock", {}).get("name", "?") + f"({code})")
            if dropped:
                print(f"  ⚠ 白名单过滤: 丢弃 {len(dropped)} 条幻觉信号: {', '.join(dropped)}")
            signals_json["alpha_signals"] = kept

        errors, warnings = validate_rotation_json(signals_json, stock_universe=data.get("stock_data", {}))

        if errors:
            print(f"  ✖ 质量检查不通过 ({len(errors)} 个阻塞问题):")
            for e in errors:
                print(f"    ✖ {e}")
            for w in warnings:
                print(f"    ⚠ {w}")
            print(f"  → 输出已拦截，不保存")
            return None

        # ── 质量摘要 ──
        n_events = len(signals_json.get("events", []))
        n_alpha = len(signals_json.get("alpha_signals", []))
        n_sector = len(signals_json.get("sector_outlook", []))
        n_commodity = len(signals_json.get("commodity_signals", []))

        signals_json["_quality_gate"] = {"passed": True}
        print(f"  ✓ 事件解读完成: {n_events}事件 {n_alpha}信号 "
              f"{n_sector}行业预判 {n_commodity}商品方向")

        if warnings:
            print(f"  ⚠ 非阻塞问题: {len(warnings)} 个")
            for w in warnings:
                print(f"    - {w}")
    else:
        print(f"  ⚠ 未提取到结构化 JSON")

    return signals_json


# ══════════════════════════════════════════════════════
# V5 生成入口
# ══════════════════════════════════════════════════════

def _load_v5_prompt() -> str:
    """加载 v5 system prompt。"""
    prompt_path = PROJECT_ROOT / "配置" / "prompts" / "daily_v5_system_prompt.py"
    if prompt_path.exists():
        ns = {}
        exec(prompt_path.read_text(encoding="utf-8"), ns)
        return ns.get("SYSTEM_PROMPT_V5", "")
    return ""


def generate_report_v5(data: dict, dry_run: bool = False,
                       model: str | None = None) -> dict | None:
    """Phase 2 (v5): 五板块日报生成。"""
    print(f"\n{'━' * 50}")
    print(f"  Phase 2: LLM 生成 (v5 五板块)")
    print(f"{'━' * 50}")

    system_prompt = _load_v5_prompt()
    if not system_prompt:
        print("  ✖ v5 prompt 加载失败")
        return None

    user_prompt = build_data_prompt(data)

    if dry_run:
        print("\n  ── V5 System Prompt ──")
        print(system_prompt[:500] + "\n  ... (共 " +
              str(len(system_prompt)) + " 字符)")
        print("\n  ── User Prompt ──")
        print(user_prompt)
        print(f"\n  (dry-run 模式，未调用 API)")
        return None

    text = call_claude_api(system_prompt, user_prompt, model=model)
    rot, _ = parse_llm_output(text)

    if rot:
        # v5 质量门禁
        s3 = rot.get("s3_pool", {})
        stocks = s3.get("stocks", [])
        s1 = rot.get("s1_events", {})
        events = s1.get("events", [])

        qg_issues = []
        if len(stocks) < 3:
            qg_issues.append(f"票池不足: {len(stocks)} (最少3)")
        if len(events) < 3:
            qg_issues.append(f"事件不足: {len(events)} (最少3)")
        for st in stocks:
            rr = st.get("rr", 0)
            if rr and rr < 1.5:
                qg_issues.append(f"{st.get('name', '')} R:R={rr:.1f} < 1.5")

        if qg_issues:
            print(f"  ⚠ v5 质量门禁警告:")
            for q in qg_issues:
                print(f"    ⚠ {q}")
        else:
            print(f"  ✓ v5 质量门禁通过 (事件{len(events)} 票池{len(stocks)})")

        # 构建 HTML
        html_path = build_html_v5(data["date"], rot, data)
        if html_path:
            print(f"  ✓ v5 HTML: {html_path.name}")

    return rot


# ══════════════════════════════════════════════════════
# Phase 3: 保存
# ══════════════════════════════════════════════════════

def save_outputs(date_str: str, rotation_json: dict | None, raw_data: dict):
    """Phase 3: V3.6 保存 — signals.json + alpha 卡片 HTML（移动端推送）。"""
    print(f"\n{'━' * 50}")
    print(f"  Phase 3: 保存")
    print(f"{'━' * 50}")

    RAW_DIR.mkdir(parents=True, exist_ok=True)

    # 原始数据包（不变）
    data_path = RAW_DIR / f"{date_str}_daily_data.json"
    _save_json(data_path, raw_data)
    print(f"  数据包: {data_path}")

    if rotation_json:
        # 完整结构化输出
        signals_path = RAW_DIR / f"{date_str}_signals.json"
        _save_json(signals_path, rotation_json)
        print(f"  信号JSON: {signals_path}")

        # Alpha 信号独立文件
        alpha_dir = PROJECT_ROOT / "output" / "alpha"
        alpha_dir.mkdir(parents=True, exist_ok=True)

        alpha_signals = rotation_json.get("alpha_signals", [])
        alpha_json_path = alpha_dir / f"{date_str}_alpha.json"
        _save_json(alpha_json_path, {
            "date": date_str,
            "signals": alpha_signals,
            "sector_outlook": rotation_json.get("sector_outlook", []),
            "commodity_signals": rotation_json.get("commodity_signals", []),
            "global_conclusion": rotation_json.get("global_conclusion", {}),
        })
        print(f"  Alpha JSON: {alpha_json_path}")

        # 移动端 HTML 卡片（推送到手机）
        html_path = _render_alpha_card_html(date_str, rotation_json)
        if html_path:
            print(f"  Alpha HTML: {html_path}")


def _save_json(path: Path, data):
    """保存 JSON 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════
# Alpha 卡片 HTML（移动端推送）
# ══════════════════════════════════════════════════════

ALPHA_DIR = PROJECT_ROOT / "output" / "alpha"


def _render_alpha_card_html(date_str: str, signals_json: dict) -> Path | None:
    """渲染移动端友好的 Alpha 信号卡片 HTML。"""
    alpha_signals = signals_json.get("alpha_signals", [])
    sector_outlook = signals_json.get("sector_outlook", [])
    commodity_signals = signals_json.get("commodity_signals", [])
    gc = signals_json.get("global_conclusion", {})

    ALPHA_DIR.mkdir(parents=True, exist_ok=True)

    # ── 信号卡片 ──
    signal_cards = ""
    if not alpha_signals:
        signal_cards = '<div class="card empty">今日无 Alpha 信号</div>'
    else:
        for sig in alpha_signals:
            stock = sig.get("stock", {})
            name = _esc(stock.get("name", "?"))
            code = stock.get("code", "")
            direction = sig.get("direction", "long")
            dir_label = "做多" if direction == "long" else "做空"
            dir_class = "long" if direction == "long" else "short"
            entry = sig.get("entry_price", 0)
            target = sig.get("target_price", 0)
            stop = sig.get("stop_loss", 0)
            confidence = sig.get("confidence", "medium")
            conf_label = {"high": "高", "medium": "中", "low": "低"}.get(confidence, confidence)
            thesis = _esc(sig.get("thesis", ""))
            time_window = sig.get("time_window", "2w")
            signal_id = sig.get("signal_id", "")

            if entry and stop and entry != stop:
                risk = abs(entry - stop)
                reward = abs(target - entry)
                rr = reward / risk if risk > 0 else 0
            else:
                rr = 0

            signal_cards += f'''<div class="card {dir_class}">
  <div class="card-header">
    <span class="stock-name">{name}</span>
    <span class="stock-code">{code}</span>
    <span class="direction {dir_class}">{dir_label}</span>
    <span class="confidence conf-{confidence}">{conf_label}</span>
  </div>
  <div class="price-row">
    <div class="price-item"><span class="label">入场</span><span class="value">{entry}</span></div>
    <div class="price-item"><span class="label">目标</span><span class="value target">{target}</span></div>
    <div class="price-item"><span class="label">止损</span><span class="value stop">{stop}</span></div>
    <div class="price-item"><span class="label">R:R</span><span class="value">{rr:.1f}</span></div>
  </div>
  <div class="thesis">{thesis}</div>
  <div class="meta">{time_window} | {signal_id}</div>
</div>'''

    # ── 行业预判摘要 ──
    sector_html = ""
    if sector_outlook:
        items = ""
        for so in sector_outlook[:5]:
            sector = _esc(so.get("sector", ""))
            direction = _esc(so.get("direction", ""))
            confidence = so.get("confidence", "medium")
            driver = _esc(so.get("event_driver", ""))
            etf = so.get("etf", {})
            etf_str = f'{_esc(etf.get("name", ""))}({etf.get("code", "")})' if etf else ""
            items += f'<div class="outlook-item"><strong>{sector}</strong> {direction} ({confidence}) — {driver}{" → " + etf_str if etf_str else ""}</div>'
        sector_html = f'<div class="section"><div class="section-title">行业预判</div>{items}</div>'

    # ── 商品方向摘要 ──
    commodity_html = ""
    if commodity_signals:
        items = ""
        for cs in commodity_signals:
            comm = _esc(cs.get("commodity", ""))
            direction = cs.get("direction", "")
            dir_icon = {"多": "↑", "空": "↓", "观望": "→"}.get(direction, "?")
            confidence = cs.get("confidence", "")
            driver = _esc(cs.get("driver", ""))
            items += f'<div class="outlook-item"><strong>{comm}</strong> {dir_icon} {direction} ({confidence}) — {driver}</div>'
        commodity_html = f'<div class="section"><div class="section-title">商品方向</div>{items}</div>'

    # ── 全局结论 ──
    gc_html = ""
    if gc:
        regime = _esc(gc.get("market_regime", ""))
        action = _esc(gc.get("action", ""))
        confidence = gc.get("confidence", 0)
        thesis = _esc(gc.get("key_thesis", ""))
        gc_html = f'''<div class="section gc">
  <div class="gc-header">{regime} · {action} · 置信度 {confidence}/100</div>
  <div class="gc-thesis">{thesis}</div>
</div>'''

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>周期雷达 · 今日Alpha {date_str}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f5f5f5; padding: 12px; color: #333; }}
.header {{ text-align: center; padding: 16px 0 12px; }}
.header h1 {{ font-size: 18px; font-weight: 600; }}
.header .date {{ font-size: 13px; color: #888; margin-top: 4px; }}
.card {{ background: #fff; border-radius: 12px; padding: 16px; margin: 12px 0; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }}
.card.empty {{ text-align: center; color: #999; padding: 32px 16px; }}
.card-header {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
.stock-name {{ font-size: 18px; font-weight: 700; }}
.stock-code {{ font-size: 13px; color: #888; }}
.direction {{ font-size: 12px; padding: 2px 8px; border-radius: 4px; font-weight: 600; }}
.direction.long {{ background: #fee; color: #c00; }}
.direction.short {{ background: #efd; color: #060; }}
.confidence {{ font-size: 11px; padding: 2px 6px; border-radius: 3px; background: #f0f0f0; }}
.conf-high {{ background: #d4edda; color: #155724; }}
.conf-medium {{ background: #fff3cd; color: #856404; }}
.conf-low {{ background: #f8d7da; color: #721c24; }}
.price-row {{ display: flex; justify-content: space-between; margin: 12px 0; padding: 10px; background: #f9f9f9; border-radius: 8px; }}
.price-item {{ text-align: center; }}
.price-item .label {{ display: block; font-size: 11px; color: #888; }}
.price-item .value {{ display: block; font-size: 16px; font-weight: 600; margin-top: 2px; }}
.price-item .value.target {{ color: #c00; }}
.price-item .value.stop {{ color: #080; }}
.thesis {{ font-size: 14px; line-height: 1.5; color: #555; margin: 8px 0; }}
.meta {{ font-size: 11px; color: #aaa; }}
.section {{ background: #fff; border-radius: 12px; padding: 14px 16px; margin: 12px 0; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }}
.section-title {{ font-size: 14px; font-weight: 700; margin-bottom: 8px; color: #333; }}
.outlook-item {{ font-size: 13px; line-height: 1.8; color: #555; }}
.gc {{ background: #f8f9fa; border: 1px solid #e0e0e0; }}
.gc-header {{ font-size: 15px; font-weight: 700; }}
.gc-thesis {{ font-size: 13px; color: #555; margin-top: 6px; }}
.footer {{ text-align: center; padding: 16px 0; font-size: 11px; color: #bbb; }}
</style>
</head>
<body>
<div class="header">
  <h1>周期雷达 · 今日 Alpha</h1>
  <div class="date">{date_str}</div>
</div>
{gc_html}
{signal_cards}
{sector_html}
{commodity_html}
<div class="footer">仅供参考，不构成投资建议</div>
</body>
</html>'''

    out_path = ALPHA_DIR / f"{date_str}_alpha.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


# ══════════════════════════════════════════════════════
# 旧版 HTML 构建（已废弃，保留空壳兼容）
# ══════════════════════════════════════════════════════

def _build_html(date_str: str, html_body: str, raw_data: dict) -> Path | None:
    """旧接口，V3.6 已废弃。"""
    return None


def _build_html_from_json(date_str: str, rot: dict, raw_data: dict) -> Path | None:
    """旧接口，V3.6 已废弃。"""
    return None


def build_wechat_html_from_json(date_str: str, rot: dict, raw_data: dict) -> Path | None:
    """旧接口，V3.6 已废弃。"""
    return None


def build_html_v5(date_str: str, rot: dict, raw_data: dict) -> Path | None:
    """旧接口，V3.6 已废弃。"""
    return None


# ══════════════════════════════════════════════════════
# 文章体日报生成（V3.6 新增）
# ══════════════════════════════════════════════════════

NARRATIVE_PROMPT = """\
你是「周期雷达」日报撰写引擎。基于下方结构化信号数据，生成一篇分析师晨报风格的日报。

## 写作风格
- 像券商晨报/策略日报，专业但不晦涩
- 结论先行，数据支撑
- 3-5 分钟阅读量（约 1500-2500 字）
- 有观点、有立场、有操作建议
- 不说废话，不堆砌数据

## 结构（三段式）

### 第一段：市场全景（300-500字）
- 今日市场核心矛盾是什么？
- 大盘处于什么状态？（进攻/均衡/防守）
- 主要驱动事件 2-3 个，简述影响

### 第二段：行业传导与机会（500-800字）
- 事件如何传导到具体行业？
- 哪些行业值得关注？为什么？
- 商品方向对 A 股的映射
- ETF 配置建议概述

### 第三段：操作建议（400-600字）
- 今日 Alpha 信号逐条解读（标的/逻辑/价格区间）
- 风险提示
- 明日关注点

## 格式要求
- 直接输出 HTML 正文（不含 <html>/<head>/<body> 标签）
- 使用 <h2> 作为段落标题
- 使用 <p> 包裹正文段落
- 个股用 <strong> 标注
- 重要数字用 <em> 标注
- 风险提示用 <div class="risk"> 包裹
"""

DAILY_DIR = PROJECT_ROOT / "output" / "daily"


def generate_narrative_report(signals_json: dict, raw_data: dict,
                              model: str | None = None) -> Path | None:
    """基于结构化信号生成文章体日报 HTML。"""
    date_str = signals_json.get("date", raw_data.get("date", ""))
    if not date_str:
        return None

    print(f"\n  文章体日报生成...")

    user_prompt = f"日期：{date_str}\n\n"
    user_prompt += "## 结构化信号数据\n\n"
    user_prompt += json.dumps(signals_json, ensure_ascii=False, indent=2)

    text = call_claude_api(NARRATIVE_PROMPT, user_prompt, model=model)
    if not text:
        print(f"  ✖ 文章体生成失败")
        return None

    html_body = text.strip()
    if html_body.startswith("```"):
        html_body = html_body.split("\n", 1)[1] if "\n" in html_body else html_body
        if html_body.endswith("```"):
            html_body = html_body[:-3]

    full_html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>周期雷达日报 {date_str}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #fff; color: #333; padding: 20px; max-width: 720px; margin: 0 auto; line-height: 1.8; }}
h1 {{ font-size: 20px; text-align: center; margin-bottom: 4px; }}
.date {{ text-align: center; color: #888; font-size: 13px; margin-bottom: 24px; }}
h2 {{ font-size: 17px; color: #1a1a2e; margin: 24px 0 12px; padding-bottom: 6px; border-bottom: 2px solid #4fc3f7; }}
p {{ margin: 10px 0; font-size: 15px; }}
strong {{ color: #c00; }}
em {{ color: #1565c0; font-style: normal; font-weight: 600; }}
.risk {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 10px 14px; margin: 16px 0; font-size: 13px; border-radius: 4px; }}
.footer {{ text-align: center; margin-top: 32px; padding-top: 16px; border-top: 1px solid #eee; font-size: 11px; color: #bbb; }}
</style>
</head>
<body>
<h1>周期雷达 · 日报</h1>
<div class="date">{date_str}</div>
{html_body}
<div class="footer">仅供参考，不构成投资建议</div>
</body>
</html>'''

    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DAILY_DIR / f"行业轮动日报_v4_{date_str.replace('-', '')}.html"
    out_path.write_text(full_html, encoding="utf-8")
    print(f"  ✓ 日报: {out_path}")
    return out_path
