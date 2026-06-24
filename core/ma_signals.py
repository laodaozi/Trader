"""
ma_signals.py — P1 并购/重组信号采集模块

从 AKShare 公告接口抽取资产重组/重大事项公告，
产出行业级产业整合信号，供 daily.py 和 report_agent.py 消费。

用法：
  signals = collect_ma_signals("2026-05-29")
  → {"announcements": [...], "by_industry": {...}, "summary": "...", "count": N}

信源优先级：
  Layer 1: AKShare stock_notice_report（资产重组 + 重大事项）
  Layer 2: 已有 RSS 信源 M&A 关键词过滤（daily.py 中零成本）
  Layer 3: finstep search_news 补充（可选，暂未启用）

行业映射：基于公司名称关键词 + 申万 31 行业分类，不调 MCP/API。
LLM 在 report_agent.py 中做最终行业判定。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

# ── 公司名称 → 行业关键词映射 ──────────────────────────
# 用于 prompt 内初步分组，精确行业判定留给 LLM
NAME_INDUSTRY_MAP: dict[str, str] = {
    # 按关键词优先级排序
    "银行": "银行",
    "证券|券商|期货": "非银金融",
    "保险": "非银金融",
    "信托": "非银金融",
    "药|医|生物|基因|疫苗|试剂|诊断|器械|莱特": "医药生物",
    "电子|光电|芯片|半导体|集成电路|晶圆|封测": "电子",
    "计算机|软件|信息|数据|智能|科技|数字|讯": "计算机",
    "通信|通讯|网络|电信|互联|5G": "通信",
    "传媒|影视|文化|出版|广告|游戏|娱乐|直播|教育": "传媒",
    "食品|饮料|酒|乳业|肉食|调味|榨菜|零食|有友": "食品饮料",
    "汽车|车辆|客车|轮胎|汽配|零部件": "汽车",
    "军工|航天|航空|兵器|卫星|火箭": "国防军工",
    "电力|能源|新能源|光伏|风电|太阳能|核电|储能|锂电|核|杉杉|盛弘|湘电": "电力设备",
    "发电|电网|供电|水电|火电|燃气|水务": "公用事业",
    "地产|房地产|置业|物业|中新|万科": "房地产",
    "建筑|建设|工程|路桥|隧道|基建|交建|建工|建科": "建筑装饰",
    "建材|水泥|玻璃|陶瓷|防水|管材": "建筑材料",
    "钢铁|钢": "钢铁",
    "有色|矿业|铝业|铜业|黄金|稀土|钨|钼|钛": "有色金属",
    "化工|化学|材料|塑料|橡胶|纤维|涂料|树脂|聚酯|新材|海利": "基础化工",
    "石油|石化|油气|炼化": "石油石化",
    "煤炭|煤业": "煤炭",
    "机械|机器|设备|装备|机床|工控|机器人|泵|重工|太重|港迪|天秦|绿田|徐工|科创新": "机械设备",
    "电气|电器|家电|空调|冰箱|洗衣机|厨卫|雷曼|民爆|照明|大明": "家用电器",
    "纺织|服装|服饰|家纺|化纤|印染|毛纺": "纺织服饰",
    "轻工|造纸|家居|家具|包装|印刷|文具|美克|翔港": "轻工制造",
    "商贸|零售|百货|超市|连锁|电商|购物": "商贸零售",
    "农林|农牧|农业|种业|渔业|养殖|饲料|化肥|农机|林业|丰林|林木": "农林牧渔",
    "港口|航运|物流|运输|铁路|公路|机场|快递|交运|顺丰|嘉友|厦门": "交通运输",
    "环保|水务|污水|固废|大气|东江|惠城": "环保",
    "旅游|酒店|景区|免税|餐饮|旅游|东时": "社会服务",
    "美容|护理|日化|化妆品|丸美": "美容护理",
    "综合": "综合",
}

# ── M&A 相关公告类型白名单 ────────────────────────────
# stock_notice_report 的"重大事项"分类太宽，含回购/质押/激励/理财等噪音
# 只保留真正涉及并购重组的类型
MA_RELEVANT_TYPES = frozenset([
    "收购出售资产/股权",
    "吸收合并",
    "股权转让",
    "资产重组债权人会议",
    "重组进展公告",
    "增资扩股",
    "权益变动报告书",
    "资产重组方案",
    "资产置换",
    "借壳上市",
    "要约收购",
    "合并",
])


def _guess_industry(stock_name: str) -> str:
    """基于公司名称关键词快速猜测行业（供 prompt 分组用）。"""
    for pattern, industry in NAME_INDUSTRY_MAP.items():
        if re.search(pattern, stock_name):
            return industry
    return "其他"


# ── M&A 关键词（用于 RSS 回退方案） ────────────────────
MA_KEYWORDS = [
    "并购", "重组", "兼并", "收购", "借壳",
    "要约收购", "资产注入", "控制权变更", "股权转让",
    "合并", "要约", "换股", "私有化", "资产置换",
]


def filter_ma_from_rss(rss_articles: list[dict]) -> list[dict]:
    """从已有 RSS 文章中过滤 M&A 相关内容（零成本 Layer 2）。

    在 daily.py Step 2.8 之后自动调用，不产生额外 API 调用。
    """
    if not rss_articles:
        return []
    ma_articles = []
    for art in rss_articles:
        title = art.get("title", "")
        content = art.get("content", "")
        text = f"{title} {content}"
        if any(kw in text for kw in MA_KEYWORDS):
            ma_articles.append({
                "title": title,
                "source": art.get("source", "RSS"),
                "summary": (content[:200] + "…" if len(content) > 200 else content),
                "matched_keywords": [kw for kw in MA_KEYWORDS if kw in text],
            })
    return ma_articles


# ══════════════════════════════════════════════════════
# 主采集函数
# ══════════════════════════════════════════════════════

def collect_ma_signals(date_str: str) -> dict[str, Any]:
    """采集当日 M&A 公告信号（AKShare Layer 1）。

    Args:
        date_str: "2026-05-29" 格式

    Returns:
        {"announcements": [...], "by_industry": {...},
         "summary": "...", "count": N, "_source": "akshare_notice_report"}
        失败时返回 {"announcements": [], "count": 0}
    """
    result: dict[str, Any] = {
        "announcements": [],
        "by_industry": {},
        "summary": "",
        "count": 0,
        "date": date_str,
        "_source": "akshare_notice_report",
    }

    try:
        import akshare as ak
    except ImportError:
        result["_error"] = "akshare 未安装"
        return result

    # AKShare 日期格式：YYYYMMDD
    date_compact = date_str.replace("-", "")

    # ── 采集资产重组 + 重大事项两类公告（内部按 MA_RELEVANT_TYPES 过滤）──
    notice_types = ["资产重组", "重大事项"]
    all_announcements: list[dict] = []

    for notice_type in notice_types:
        try:
            df = ak.stock_notice_report(symbol=notice_type, date=date_compact)
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                stock_name = str(row.get("名称", row.get("股票简称", ""))).strip()
                stock_code = str(row.get("代码", row.get("股票代码", ""))).strip()
                title = str(row.get("标题", row.get("公告标题", ""))).strip()
                typ = str(row.get("类型", row.get("公告类型", notice_type))).strip()

                # 过滤非 M&A 类型（回购/质押/激励/理财/合同等噪音）
                if typ not in MA_RELEVANT_TYPES:
                    continue

                if not stock_code or not title:
                    continue

                industry = _guess_industry(stock_name)

                ann = {
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "title": title,
                    "notice_type": typ,
                    "industry_hint": industry,
                }
                all_announcements.append(ann)
        except Exception:
            # 单个 notice_type 失败不影响另一个
            pass

    if not all_announcements:
        return result

    # ── 去重（按 stock_code + title prefix）──
    seen = set()
    deduped = []
    for ann in all_announcements:
        key = f"{ann['stock_code']}|{ann['title'][:30]}"
        if key not in seen:
            seen.add(key)
            deduped.append(ann)

    all_announcements = deduped

    # ── 按行业分组 ──
    by_industry: dict[str, list] = {}
    for ann in all_announcements:
        ind = ann["industry_hint"]
        by_industry.setdefault(ind, []).append(ann)

    # ── 信号强度评分 ──
    # 简单规则：同行业 ≥3 条 = 强信号，≥2 条 = 中等，1 条 = 弱
    industry_signals = {}
    for ind, anns in by_industry.items():
        n = len(anns)
        if n >= 3:
            strength = "high"
        elif n >= 2:
            strength = "medium"
        else:
            strength = "low"
        names = [a["stock_name"] for a in anns]
        industry_signals[ind] = {
            "count": n,
            "strength": strength,
            "stocks": names[:5],  # 最多 5 家
            "notable": [a["title"][:80] for a in anns[:3]],
        }

    # ── 生成摘要 ──
    high_signals = {k: v for k, v in industry_signals.items() if v["strength"] == "high"}
    med_signals = {k: v for k, v in industry_signals.items() if v["strength"] == "medium"}
    other_count = sum(v["count"] for k, v in industry_signals.items()
                      if v["strength"] == "low")
    total = len(all_announcements)

    summary_parts = ["今日共 {} 家上市公司发布并购重组/股权转让/吸收合并等公告".format(total)]
    if high_signals:
        items = ["{}({}家)".format(k, v["count"]) for k, v in high_signals.items()]
        summary_parts.append("强信号行业: {}".format(", ".join(items)))
    if med_signals:
        items = ["{}({}家)".format(k, v["count"]) for k, v in med_signals.items()]
        summary_parts.append("中等信号行业: {}".format(", ".join(items)))
    if other_count:
        summary_parts.append(f"其他行业共 {other_count} 条")
    summary = "；".join(summary_parts)

    result.update({
        "announcements": all_announcements,
        "by_industry": industry_signals,
        "summary": summary,
        "count": total,
    })

    return result
