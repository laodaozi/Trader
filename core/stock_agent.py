"""
stock_agent.py -- CycleRadar V3.0 个股票池 Agent

职责：构建个股分析票池（龙头+微信+热度兜底），统一调度 stock_analysis 底层工具。
从 daily.py collect_data() 4.0-4.55 节渐进抽取。

依赖：stock_analysis.py 底层分析工具，score.py 常量
"""
from __future__ import annotations

import json
from pathlib import Path

from score import mcp_call, INDUSTRY_LEADERS, RAW_DIR
from stock_analysis import (
    analyze_stock,
    extract_stock_codes_from_wechat,
    load_cached_stocks,
)
from factor_agent import INDUSTRY_ETF_MAP


_NAME_TO_CODE: dict[str, str] | None = None


def _load_stock_names_cache() -> dict[str, str]:
    """加载 name→code 反查缓存（懒加载）。"""
    global _NAME_TO_CODE
    if _NAME_TO_CODE is not None:
        return _NAME_TO_CODE
    cache_path = RAW_DIR / "stock_names_cache.json"
    _NAME_TO_CODE = {}
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                code_to_name = json.load(f)
            _NAME_TO_CODE = {v.strip(): k for k, v in code_to_name.items()}
        except (json.JSONDecodeError, OSError):
            pass
    return _NAME_TO_CODE


# ── MVP核心：行业内因子选股 ────────────────────────────────

def score_stocks_within_industry(industry: str, date_str: str,
                                 from_cache: bool = False,
                                 max_stocks: int = 8,
                                 ) -> list[dict]:
    """对"确认"行业内的成分股做因子排序，输出量化推荐。

    这是MVP的核心桥梁：从"行业确认"到"买哪只股"。

    评分维度（mini-score, 满分100）：
    - 5日涨幅行业内排名 × 30  （动量）
    - 资金流方向 × 30          （资金共识）
    - NX信号 × 20             （技术趋势）
    - 市值分层 × 20            （弹性偏好）

    Parameters:
        industry: 行业名（如"通信"）
        date_str: 日期
        from_cache: 是否使用缓存
        max_stocks: 最多分析几只

    Returns:
        按 mini_score 降序排列的个股列表
        [{code, name, mini_score, breakdown, rank_in_industry, etf_alternative}]
    """
    leaders = INDUSTRY_LEADERS.get(industry, [])
    if not leaders:
        return []

    # 分析每只龙头股
    stock_results = []
    for code in leaders[:max_stocks]:
        try:
            info = analyze_stock(code, date_str) if not from_cache else {}
            if from_cache:
                # 尝试从缓存加载
                cached = _load_cached_stock_map(date_str)
                info = cached.get(code, {})
                if not info:
                    continue

            # 提取评分要素
            name = info.get("name", code)

            # 1. 5日涨幅（从K线或资金数据推算）
            price_chg_5d = None
            flow = info.get("fund_flow", {})
            # 尝试从资金流数据中获取近期趋势
            trend = flow.get("trend", "")

            # 2. 资金流方向
            flow_4d = flow.get("4d_total")
            flow_direction = 0  # -1, 0, 1
            if flow_4d is not None:
                flow_direction = 1 if flow_4d > 0 else (-1 if flow_4d < -0.5 else 0)

            # 3. NX信号
            nx = info.get("nx_signal", info.get("nx", {}))
            nx_sig = nx.get("signal", nx.get("nx_signal", "neutral"))
            swing_pos = nx.get("swing_position")
            # V3.9: enhanced NX grade for fine-grained scoring
            enhanced = info.get("nx", {}).get("enhanced", {})
            nx_grade = enhanced.get("grade", {}).get("grade", "neutral")  # strong_买/弱_买/等/neutral/离场/弱_卖/strong_卖

            # 4. 估值（PE）
            val = info.get("valuation", {})
            pe = val.get("pe_ttm")

            stock_results.append({
                "code": code,
                "name": name,
                "flow_4d": flow_4d,
                "flow_direction": flow_direction,
                "flow_trend": trend,
                "nx_signal": nx_sig,
                "nx_grade": nx_grade,
                "swing_position": swing_pos,
                "pe_ttm": pe,
                "elasticity": nx.get("elasticity_20d"),
                "_raw": info,
            })
        except Exception as e:
            print(f"    ⚠ {code} 分析失败: {e}")

    if not stock_results:
        return []

    # ── 计算 mini-score ──

    # 资金排序（用于归一化）
    flows = [s["flow_4d"] for s in stock_results if s["flow_4d"] is not None]
    flow_max = max(abs(f) for f in flows) if flows else 1.0

    for s in stock_results:
        breakdown = {}

        # 1. 资金流方向 (30分)
        if s["flow_4d"] is not None and flow_max > 0:
            # 归一化到 0-30
            normalized = (s["flow_4d"] / flow_max + 1) / 2  # 0~1
            breakdown["fund_flow"] = round(normalized * 30)
        elif s["flow_direction"] > 0:
            breakdown["fund_flow"] = 20
        elif s["flow_direction"] < 0:
            breakdown["fund_flow"] = 5
        else:
            breakdown["fund_flow"] = 15

        # 2. 动量（基于资金趋势代理）(30分)
        if "持续流入" in s["flow_trend"] or "加速" in s["flow_trend"]:
            breakdown["momentum"] = 25
        elif "流入" in s["flow_trend"]:
            breakdown["momentum"] = 20
        elif "平衡" in s["flow_trend"] or not s["flow_trend"]:
            breakdown["momentum"] = 15
        elif "流出" in s["flow_trend"]:
            breakdown["momentum"] = 5
        else:
            breakdown["momentum"] = 10

        # 3. NX信号 (20分，V3.9 升级为增强版分级打分)
        grade = s.get("nx_grade", "neutral")
        if grade == "strong_买":
            breakdown["nx"] = 20
        elif grade == "弱_买":
            breakdown["nx"] = 16
        elif grade == "等":
            breakdown["nx"] = 12  # L1 buy 但 L2/L3 未确认
        elif grade == "neutral":
            breakdown["nx"] = 10
        elif grade in ("离场", "弱_卖"):
            breakdown["nx"] = 4
        elif grade == "strong_卖":
            breakdown["nx"] = 0
        else:
            # fallback: 无 enhanced 数据时使用 legacy 二值打分
            if s["nx_signal"] == "buy":
                breakdown["nx"] = 20
            elif s["nx_signal"] == "neutral":
                breakdown["nx"] = 10
            else:
                breakdown["nx"] = 0

        # 4. 弹性/市值偏好 (20分)
        # 高弹性 = 高波动 = 更高得分（适合短期交易）
        elasticity = s.get("elasticity")
        if elasticity is not None:
            try:
                e = float(elasticity)
                if e > 5:
                    breakdown["elasticity"] = 20
                elif e > 3:
                    breakdown["elasticity"] = 15
                else:
                    breakdown["elasticity"] = 10
            except (TypeError, ValueError):
                breakdown["elasticity"] = 10
        else:
            breakdown["elasticity"] = 10

        s["mini_score"] = sum(breakdown.values())
        s["breakdown"] = breakdown

    # 排序
    stock_results.sort(key=lambda x: -x["mini_score"])
    for i, s in enumerate(stock_results):
        s["rank_in_industry"] = i + 1

    # 附加ETF替代方案
    etf = INDUSTRY_ETF_MAP.get(industry)
    for s in stock_results:
        s["etf_alternative"] = etf
        s["industry"] = industry

    # 清理内部字段
    for s in stock_results:
        s.pop("_raw", None)

    return stock_results


def _load_cached_stock_map(date_str: str) -> dict[str, dict]:
    """加载历史个股缓存，合并当日+近3日数据。"""
    cached = {}
    from datetime import datetime, timedelta
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    for offset in range(4):
        d = (dt - timedelta(days=offset)).strftime("%Y-%m-%d")
        sf = RAW_DIR / f"{d}_stocks.json"
        if sf.exists():
            try:
                with open(sf, "r", encoding="utf-8") as f:
                    sdata = json.load(f)
                for s in sdata.get("stocks", []):
                    if s.get("code") not in cached:
                        cached[s["code"]] = s
            except (json.JSONDecodeError, OSError):
                pass
    return cached


# 模块级缓存：trending_industry 数据（同一日期同一进程内只调一次）
_TRENDING_CACHE: dict[str, dict] = {}


def compute_dynamic_leaders(industry: str, date_str: str,
                             top: int = 3,
                             from_cache: bool = False) -> list[dict]:
    """V3.3 S4: 计算行业当前动态龙头（鲶鱼）。

    数据源优先级：
    1. get_trending_industry（关注度高的概念 → faucet 个股）
    2. hot_top30_raw 中归属行业（关键词模糊匹配）
    3. INDUSTRY_LEADERS 硬编码兜底（不在动态结果中的）

    Returns:
        [{"code": str, "name": str, "source": str(trending|hot|hardcoded), "rank": int}]
        最多 top 个，按数据源优先级 + rank 排序
    """
    results = []
    seen_codes = set()

    # 1. trending_industry（缓存到模块级，避免一日内重复 MCP）
    if date_str not in _TRENDING_CACHE:
        _TRENDING_CACHE[date_str] = {}
        try:
            trending = mcp_call("market_quote", "get_trending_industry",
                                {"date": date_str})
            tinfo = (trending.get("trendingInfo", [])
                     if isinstance(trending, dict) else [])
            for ti in tinfo:
                title = ti.get("trendingTitle", "")
                faucets = ti.get("faucet", []) or []
                _TRENDING_CACHE[date_str][title] = [
                    {"name": f.get("securityName", ""),
                     "code": f.get("securityCode", "")}
                    for f in faucets if f.get("securityName")
                ]
        except Exception:
            pass

    trending_map = _TRENDING_CACHE[date_str]
    names_cache = _load_stock_names_cache()

    # 1a. 概念名模糊匹配（"计算机" 命中 "计算机应用"）
    ind_short = industry[:2] if len(industry) >= 2 else industry
    for concept, faucets in trending_map.items():
        if ind_short and (ind_short in concept or concept in industry):
            for f in faucets[:5]:
                code = f.get("code") or names_cache.get(f.get("name", ""))
                if not code or len(code) != 6 or not code.isdigit():
                    continue
                if code in seen_codes:
                    continue
                seen_codes.add(code)
                results.append({
                    "code": code,
                    "name": f.get("name", ""),
                    "source": "trending",
                    "rank": len(results) + 1,
                })
                if len(results) >= top:
                    break
        if len(results) >= top:
            break

    # 2. INDUSTRY_LEADERS 兜底（保留种子价值）
    if len(results) < top:
        for code in INDUSTRY_LEADERS.get(industry, [])[:top]:
            if code in seen_codes:
                continue
            seen_codes.add(code)
            # 反查 name
            name = ""
            for n, c in names_cache.items():
                if c == code:
                    name = n
                    break
            results.append({
                "code": code,
                "name": name,
                "source": "hardcoded",
                "rank": len(results) + 1,
            })
            if len(results) >= top:
                break

    return results[:top]


def build_stock_pool(date_str: str,
                     top_industries: list[str],
                     scan_top10: list[dict],
                     wechat_sources: dict,
                     from_cache: bool = False,
                     max_wechat: int = 15,
                     min_wechat_for_fallback: int = 3,
                     ) -> dict:
    """构建个股分析票池，返回完整数据包。

    三层票池：
    1. 龙头标的：TOP行业的 INDUSTRY_LEADERS（每行业最多2只）
    2. 微信票池：微信信源提取，与 TOP10 行业匹配的优先
    3. 热度兜底：微信票池不足时，从 hot_top30 补充

    Returns:
        {
            "stock_data": {code: {分析结果}},
            "wechat_all_stocks": [{code, name, source, reason}],
            "hot_top30_raw": [...] | None,
            "stats": {"leaders": int, "wechat_new": int, "hot_fallback": int},
        }
    """
    cached_stocks = _load_cached_stock_map(date_str) if from_cache else {}

    stock_data: dict[str, dict] = {}
    stats = {"leaders": 0, "wechat_new": 0, "hot_fallback": 0}

    # ── 1. 龙头标的（V3.3 S4: 动态龙头优先，硬编码兜底）──
    for ind in top_industries:
        # 动态龙头：trending_industry faucet → 鲶鱼优先
        # 兜底链：INDUSTRY_LEADERS 硬编码（compute_dynamic_leaders 内部已处理）
        dynamic = compute_dynamic_leaders(ind, date_str, top=3, from_cache=from_cache)
        added_for_ind = 0
        for leader in dynamic:
            code = leader["code"]
            if code in stock_data:
                continue
            if from_cache:
                if code in cached_stocks:
                    stock_info = cached_stocks[code]
                else:
                    continue   # 缓存模式不再调 MCP
            else:
                stock_info = analyze_stock(code, date_str)
            stock_info["_leader_source"] = leader["source"]   # trending | hardcoded
            stock_data[code] = stock_info
            stats["leaders"] += 1
            added_for_ind += 1
            if added_for_ind >= 2:
                break

    # ── 1.1 动态龙头补位（V3.2: 关注度=龙头）──
    # 对 0 龙头的 TOP10 行业，用 get_trending_industry faucet 补充
    industries_with_leaders = set()
    for ind in top_industries:
        if any(c in stock_data for c in INDUSTRY_LEADERS.get(ind, [])):
            industries_with_leaders.add(ind)

    gap_industries = [ind for ind in top_industries if ind not in industries_with_leaders]
    if gap_industries:
        try:
            trending = mcp_call("market_quote", "get_trending_industry",
                                {"date": date_str})
            trending_info = (trending.get("trendingInfo", [])
                             if isinstance(trending, dict) else [])
            # 概念名 → faucet 个股名
            faucet_names: dict[str, list[str]] = {}
            for ti in trending_info:
                title = ti.get("trendingTitle", "")
                faucets = ti.get("faucet", [])
                names = [f.get("securityName", "") for f in faucets
                         if f.get("securityName")]
                if names:
                    faucet_names[title] = names
        except Exception:
            faucet_names = {}

        # 用 stock_names_cache 反查代码
        names_cache = _load_stock_names_cache()
        for ind in gap_industries:
            # 尝试概念名模糊匹配（如"计算机"匹配"计算机应用"）
            matched_stocks = []
            for concept, names in faucet_names.items():
                if ind[:2] in concept or concept in ind:
                    for sname in names[:2]:
                        code = names_cache.get(sname)
                        if code and code not in stock_data:
                            matched_stocks.append(code)
            # V3.2: 品类纯化 — 不再用 ETF 兜底
            # 若 trending faucet 也无结果，则该行业本期无龙头（宁缺毋滥）
            # ETF 属于未来独立产品，不混入个股日报

            for code in matched_stocks[:2]:
                if code in stock_data:
                    continue
                if from_cache and code in cached_stocks:
                    stock_data[code] = cached_stocks[code]
                else:
                    stock_data[code] = analyze_stock(code, date_str)
                stats["leaders"] += 1

    # ── 2. 微信票池 ──
    wechat_stocks = extract_stock_codes_from_wechat(wechat_sources)

    # 智能排序：与当日 TOP10 行业匹配的优先
    hot_industries = {r["name"] for r in scan_top10[:10]}

    def _wechat_priority(ws):
        reason = ws.get("reason", "")
        if any(ind in reason for ind in hot_industries):
            return 0
        return 1
    wechat_stocks.sort(key=_wechat_priority)

    for ws in wechat_stocks[:max_wechat]:
        code = ws["code"]
        if code not in stock_data:
            if from_cache and code in cached_stocks:
                stock_info = cached_stocks[code]
            else:
                stock_info = analyze_stock(code, date_str)
            stock_info["_wechat_source"] = ws.get("source", "")
            stock_info["_wechat_reason"] = ws.get("reason", "")
            stock_data[code] = stock_info
            stats["wechat_new"] += 1

    # ── 3. 热度TOP30兜底 ──
    hot_top30_raw = None
    if len(wechat_stocks) < min_wechat_for_fallback:
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

            hot_added = 0
            for item in hot_items[:10]:
                code = (item.get("security_code", "")
                        or item.get("code", "")
                        or item.get("stock_code", ""))
                name = (item.get("security_name", "")
                        or item.get("name", "")
                        or item.get("stock_name", ""))
                if not code or not (len(code) == 6 and code.isdigit()):
                    continue
                if code in stock_data:
                    continue
                if from_cache and code in cached_stocks:
                    stock_info = cached_stocks[code]
                else:
                    stock_info = analyze_stock(code, date_str)
                stock_info["_wechat_source"] = "热度TOP30兜底"
                stock_info["_wechat_reason"] = f"热度TOP30兜底·{name}"
                stock_data[code] = stock_info
                wechat_stocks.append({
                    "code": code, "name": name,
                    "source": "热度TOP30", "reason": "热度TOP30兜底",
                })
                hot_added += 1
                if hot_added >= 5:
                    break
            hot_top30_raw = hot_items[:10]
            stats["hot_fallback"] = hot_added
        except Exception as e:
            print(f"  ⚠ 热度TOP30获取失败: {e}")

    return {
        "stock_data": stock_data,
        "wechat_all_stocks": wechat_stocks,
        "hot_top30_raw": hot_top30_raw,
        "stats": stats,
    }


# ── S4: 多信号交叉选股 ──────────────────────────────────

def find_resonance_stocks(hot_top30_raw: list[dict] | None,
                          scan_top10: list[dict],
                          inst_data: dict | None = None,
                          ) -> list[dict]:
    """S4.1 行业共振股：热度TOP30 ∩ TOP10行业 ∩ 机构认可。

    Parameters:
        hot_top30_raw: get_stock_hot_top30 原始数据
        scan_top10: 行业扫描 TOP10 结果（含 name 字段）
        inst_data: fetch_leader_board_institutional 输出（可选）

    Returns:
        [{code, name, reasons: [str], resonance_score: int}]
    """
    if not hot_top30_raw:
        return []

    top10_names = {r["name"] for r in scan_top10[:10]}

    # 机构买入个股集合
    inst_stocks = {}
    if inst_data:
        for code, info in inst_data.get("by_stock", {}).items():
            if info.get("net", 0) > 0:
                inst_stocks[code] = info

    # INDUSTRY_LEADERS 反查
    code_to_ind = {}
    for ind, codes in INDUSTRY_LEADERS.items():
        for c in codes:
            code_to_ind[c] = ind

    results = []
    for item in hot_top30_raw:
        code = (item.get("security_code", "")
                or item.get("code", "")
                or item.get("stock_code", ""))
        name = (item.get("security_name", "")
                or item.get("name", "")
                or item.get("stock_name", ""))
        if not code or not (len(code) == 6 and code.isdigit()):
            continue

        reasons = []
        score = 0

        # 热度本身
        reasons.append("热度TOP30")
        score += 10

        # 行业共振
        ind = code_to_ind.get(code)
        if ind and ind in top10_names:
            reasons.append(f"行业共振({ind}在TOP10)")
            score += 30

        # 机构认可
        if code in inst_stocks:
            net = inst_stocks[code].get("net", 0)
            reasons.append(f"机构净买入{net:.0f}万")
            score += 20

        if score >= 30:  # 至少热度+行业共振或机构
            results.append({
                "code": code,
                "name": name,
                "industry": ind,
                "reasons": reasons,
                "resonance_score": score,
            })

    results.sort(key=lambda x: -x["resonance_score"])
    return results


def filter_by_market_cap(stock_codes: list[str],
                         date_str: str,
                         exclude_top_n: int = 100,
                         ) -> dict[str, dict]:
    """S4.2 市值筛选：用 get_market_cap_ranking 排除大盘股。

    Returns:
        {code: {"market_cap_yi": float, "cap_rank": int, "cap_tier": str}}
        cap_tier: "大盘"(TOP100) / "中盘"(100-300) / "小盘"(300+)
    """
    try:
        ranking = mcp_call("market_quote", "get_market_cap_ranking", {
            "trade_date": date_str,
            "security_type": "SHARE",
            "top": 500,
        })
    except Exception as e:
        print(f"  ⚠ 市值排名获取失败: {e}")
        return {}

    items = ranking if isinstance(ranking, list) else []

    # 构建 code → rank 映射
    code_rank = {}
    for i, item in enumerate(items, 1):
        code = item.get("security_code", "")
        cap = item.get("market_value") or item.get("total_market_cap") or 0
        if isinstance(cap, str):
            try:
                cap = float(cap.replace("亿", "").replace(",", ""))
            except ValueError:
                cap = 0
        code_rank[code] = {"rank": i, "cap": cap}

    result = {}
    for code in stock_codes:
        info = code_rank.get(code)
        if info:
            rank = info["rank"]
            if rank <= exclude_top_n:
                tier = "大盘"
            elif rank <= 300:
                tier = "中盘"
            else:
                tier = "小盘"
            result[code] = {
                "market_cap_yi": round(info["cap"], 1),
                "cap_rank": rank,
                "cap_tier": tier,
            }
        else:
            # 不在 TOP500 → 小盘
            result[code] = {
                "market_cap_yi": 0,
                "cap_rank": 999,
                "cap_tier": "小盘",
            }

    return result


def score_multi_catalyst(stock_info: dict,
                         enrichments: dict | None = None,
                         ) -> dict:
    """S4.3 多催化剂叠加评分（满分100）。

    Parameters:
        stock_info: analyze_stock() 的输出
        enrichments: {
            "inst_net": float (机构净买入万元, 0 if not present),
            "in_top10_industry": bool,
            "cap_tier": "大盘"/"中盘"/"小盘",
            "resonance_score": int (from find_resonance_stocks),
        }

    评分维度：
    - NX信号: buy=20, neutral=10, sell=0
    - 资金趋势: 正=20, 平=10, 负=0
    - 机构认可: 有=15, 无=0
    - 行业共振: TOP10=15, 非=0
    - 市值偏好: 小盘=15, 中盘=10, 大盘=5
    - 估值安全: PE低分位=15, 中=10, 高=5
    """
    if enrichments is None:
        enrichments = {}

    breakdown = {}

    # 1. NX信号 (20分，V3.9 升级为增强版分级打分)
    nx = stock_info.get("nx", stock_info.get("nx_signal", {}))
    nx_sig = nx.get("nx_signal", nx.get("signal", "neutral"))
    enhanced = nx.get("enhanced", {})
    nx_grade = enhanced.get("grade", {}).get("grade", "neutral")
    if nx_grade == "strong_买":
        breakdown["nx"] = 20
    elif nx_grade == "弱_买":
        breakdown["nx"] = 16
    elif nx_grade == "等":
        breakdown["nx"] = 12
    elif nx_grade == "neutral":
        breakdown["nx"] = 10
    elif nx_grade in ("离场", "弱_卖"):
        breakdown["nx"] = 4
    elif nx_grade == "strong_卖":
        breakdown["nx"] = 0
    else:
        # fallback: legacy binary scoring
        if nx_sig == "buy":
            breakdown["nx"] = 20
        elif nx_sig == "neutral":
            breakdown["nx"] = 10
        else:
            breakdown["nx"] = 0

    # 2. 资金趋势 (20分)
    flow = stock_info.get("fund_flow", {})
    trend = flow.get("trend", "")
    if "流入" in trend:
        breakdown["fund"] = 20
    elif "平衡" in trend or not trend:
        breakdown["fund"] = 10
    else:
        breakdown["fund"] = 0

    # 3. 机构认可 (15分)
    inst_net = enrichments.get("inst_net", 0)
    breakdown["inst"] = 15 if inst_net > 0 else 0

    # 4. 行业共振 (15分)
    breakdown["resonance"] = 15 if enrichments.get("in_top10_industry") else 0

    # 5. 市值偏好 (15分)
    cap_tier = enrichments.get("cap_tier", "中盘")
    if cap_tier == "小盘":
        breakdown["cap"] = 15
    elif cap_tier == "中盘":
        breakdown["cap"] = 10
    else:
        breakdown["cap"] = 5

    # 6. 估值安全 (15分)
    val = stock_info.get("valuation", {})
    pe = val.get("pe_ttm")
    if pe is not None and isinstance(pe, (int, float)):
        if pe < 20:
            breakdown["valuation"] = 15
        elif pe < 40:
            breakdown["valuation"] = 10
        else:
            breakdown["valuation"] = 5
    else:
        breakdown["valuation"] = 8  # 无数据给中间值

    total = sum(breakdown.values())

    if total >= 70:
        tier = "强推"
    elif total >= 38:
        tier = "关注"
    else:
        tier = "观望"

    return {
        "total_score": total,
        "breakdown": breakdown,
        "tier": tier,
    }


def _catalyst_reasons(breakdown: dict) -> list[str]:
    """将 score_multi_catalyst 的 6 维 breakdown 翻译为中文短句。

    每句格式："{维度名称}{状态} +{分数}"。
    跳过 0 分维度（机构/行业/资金），保留弱信号但如实标注。
    """
    reasons = []

    _nx_labels = {
        20: "NX强买入", 16: "NX偏买", 12: "NX观望", 10: "NX中性",
        4: "NX偏卖", 0: "NX卖出",
    }
    _fund_labels = {20: "资金流入", 10: "资金平", 0: "资金流出"}
    _inst_label = {15: "机构买入", 0: ""}
    _resonance_label = {15: "行业共振", 0: ""}
    _cap_labels = {15: "小盘偏好", 10: "中盘", 5: "大盘"}
    _val_labels = {15: "PE低分位", 10: "PE中位", 5: "PE高分位", 8: "PE无数据"}

    mappings = [
        ("nx", _nx_labels),
        ("fund", _fund_labels),
        ("inst", _inst_label),
        ("resonance", _resonance_label),
        ("cap", _cap_labels),
        ("valuation", _val_labels),
    ]

    for key, labels in mappings:
        score = breakdown.get(key, 0)
        label = labels.get(score)
        if label is None:
            reasons.append(f"{key}+{score}")
        elif label:
            reasons.append(f"{label} +{score}")

    return reasons


def build_smart_pool(date_str: str,
                     top_industries: list[str],
                     scan_top10: list[dict],
                     wechat_sources: dict,
                     inst_data: dict | None = None,
                     block_data: dict | None = None,
                     hot_top30_raw: list[dict] | None = None,
                     from_cache: bool = False,
                     ) -> dict:
    """S4.4 智能票池：在原有三层基础上叠加共振股+多催化剂评分。

    扩展 build_stock_pool，新增：
    - 共振股注入（热度TOP30 ∩ TOP10行业 ∩ 机构认可）
    - 全票池多催化剂评分
    - 按 catalyst_score 排序
    """
    # 1. 基础票池
    pool = build_stock_pool(
        date_str=date_str,
        top_industries=top_industries,
        scan_top10=scan_top10,
        wechat_sources=wechat_sources,
        from_cache=from_cache,
    )

    stock_data = pool["stock_data"]
    wechat_stocks = pool["wechat_all_stocks"]

    # 2. 共振股注入
    resonance = find_resonance_stocks(hot_top30_raw, scan_top10, inst_data)
    resonance_added = 0
    for rs in resonance[:5]:
        code = rs["code"]
        if code not in stock_data:
            if from_cache:
                cached = _load_cached_stock_map(date_str)
                if code in cached:
                    stock_data[code] = cached[code]
                else:
                    stock_data[code] = analyze_stock(code, date_str)
            else:
                stock_data[code] = analyze_stock(code, date_str)
            stock_data[code]["_wechat_source"] = "行业共振"
            stock_data[code]["_wechat_reason"] = " + ".join(rs["reasons"])
            wechat_stocks.append({
                "code": code, "name": rs["name"],
                "source": "行业共振", "reason": " + ".join(rs["reasons"]),
            })
            resonance_added += 1

    # 3. 机构买入个股集合
    inst_stocks = {}
    if inst_data:
        for code, info in inst_data.get("by_stock", {}).items():
            if info.get("net", 0) > 0:
                inst_stocks[code] = info.get("net", 0)

    top10_names = {r["name"] for r in scan_top10[:10]}
    code_to_ind = {}
    for ind, codes in INDUSTRY_LEADERS.items():
        for c in codes:
            code_to_ind[c] = ind

    # 4. 多催化剂评分
    for code, sinfo in stock_data.items():
        ind = code_to_ind.get(code)
        enrichments = {
            "inst_net": inst_stocks.get(code, 0),
            "in_top10_industry": ind in top10_names if ind else False,
            "cap_tier": "中盘",  # 默认值，后续可用 filter_by_market_cap 补充
        }
        catalyst = score_multi_catalyst(sinfo, enrichments)
        sinfo["catalyst_score"] = catalyst["total_score"]
        sinfo["catalyst_tier"] = catalyst["tier"]
        sinfo["catalyst_breakdown"] = catalyst["breakdown"]

    pool["stats"]["resonance"] = resonance_added
    pool["resonance_stocks"] = resonance
    # 确保 hot_top30_raw 始终传递（调用方已采集，不依赖 build_stock_pool 兜底）
    if hot_top30_raw is not None:
        pool["hot_top30_raw"] = hot_top30_raw

    return pool


# ══════════════════════════════════════════════════════
# V3.5 Sprint A: ETF 票池构建
# ══════════════════════════════════════════════════════

def build_etf_pool(date_str: str,
                   scan_top10: list[dict],
                   concept_top10: list[dict] | None = None,
                   from_cache: bool = False,
                   ) -> dict:
    """V3.5 Sprint A: 为 ETF 日报构建行业 ETF 票池。

    和 build_smart_pool 的核心差异：
    - 不再查 INDUSTRY_LEADERS 个股，只查 INDUSTRY_ETF_MAP
    - 不做微信共振、多催化剂评分（这些是个股维度）
    - 每个 ETF 带最新价 + 5日涨跌 + 所属行业的 composite_score

    Args:
        date_str: 日期
        scan_top10: 行业扫描 TOP10（含 composite_score / consecutive_top10 / weekly_flow）
        concept_top10: 概念板块 TOP10（可选，用于覆盖宽基 ETF）
        from_cache: 是否缓存模式

    Returns:
        {
            "etf_data": {etf_code: {name, sector, last_price, 5d_chg, composite_score, ...}},
            "stats": {"industry_etfs": int, "broad_etfs": int, "missing_etf_industries": [...]},
        }
    """
    etf_data = {}
    missing = []

    # 1. 行业 ETF：遍历 scan_top10，匹配 INDUSTRY_ETF_MAP
    for sector in scan_top10[:20]:  # 取前 20 个行业，确保有充足选择
        name = sector.get("name", "")
        etf_info = INDUSTRY_ETF_MAP.get(name)
        if not etf_info:
            missing.append(name)
            continue

        etf_code = etf_info["code"]
        if etf_code in etf_data:
            continue  # 已收录（比如多个行业映射到同一 ETF）

        # 采集 ETF 最新行情（6 bars 足够算 5 日涨跌）
        last_price = None
        chg_5d_pct = None
        vol_last = None
        try:
            kline = mcp_call("market_quote", "get_kline", {
                "keyword": etf_code,
                "start_date": date_str,
                "end_date": date_str,
                "kline_type": 1,  # 日K
            })
            items = kline if isinstance(kline, list) else []
            if items:
                last_kl = items[-1] if items else {}
                last_price = last_kl.get("close_price") or last_kl.get("close")
                vol_last = last_kl.get("trade_volume") or last_kl.get("volume")
                # 5 日涨跌：需要更长窗口，简化处理
                if len(items) >= 5:
                    try:
                        p5 = items[-5].get("close_price") or items[-5].get("close")
                        if p5 and last_price:
                            chg_5d_pct = round(
                                (float(last_price) - float(p5)) / float(p5) * 100, 2
                            )
                    except (TypeError, ValueError, ZeroDivisionError):
                        pass
                try:
                    last_price = float(last_price) if last_price else None
                except (TypeError, ValueError):
                    last_price = None
        except Exception as e:
            print(f"    ⚠ ETF {etf_code} ({etf_info['name']}) 行情采集失败: {e}")

        etf_data[etf_code] = {
            "code": etf_code,
            "name": etf_info["name"],
            "sector": name,  # 所属申万一级行业
            "sector_rank": sector.get("rank"),
            "sector_stage": sector.get("stage"),
            "composite_score": sector.get("composite_score"),
            "consecutive_top10": sector.get("consecutive_top10", 0),
            "weekly_flow_yi": sector.get("weekly_flow"),
            "daily_flow_yi": sector.get("fund_flow"),
            "last_price": last_price,
            "chg_5d_pct": chg_5d_pct,
            "last_volume": vol_last,
            "tier": "industry_etf",
        }

    # 2. 宽基 ETF（固定 6 只 + 根据概念 TOP10 补充）
    broad_etfs = [
        {"code": "510050", "name": "50ETF", "desc": "上证50"},
        {"code": "510300", "name": "300ETF", "desc": "沪深300"},
        {"code": "510500", "name": "500ETF", "desc": "中证500"},
        {"code": "159915", "name": "创业板ETF", "desc": "创业板"},
        {"code": "588000", "name": "科创50ETF", "desc": "科创50"},
        {"code": "159949", "name": "创业板50ETF", "desc": "创业板50"},
    ]
    for be in broad_etfs:
        if be["code"] in etf_data:
            continue
        # 宽基 ETF 不关联行业，作为市场情绪标的
        etf_data[be["code"]] = {
            "code": be["code"],
            "name": be["name"],
            "sector": be["desc"],
            "tier": "broad_etf",
            "sector_rank": None,
            "sector_stage": None,
            "composite_score": None,
        }

    stats = {
        "industry_etfs": sum(1 for e in etf_data.values() if e["tier"] == "industry_etf"),
        "broad_etfs": sum(1 for e in etf_data.values() if e["tier"] == "broad_etf"),
        "missing_etf_industries": missing,
    }
    print(f"        → 行业ETF {stats['industry_etfs']} 只 / 宽基 {stats['broad_etfs']} 只 "
          f"/ 无ETF映射行业 {len(missing)}")

    return {
        "etf_data": etf_data,
        "stats": stats,
    }


# ══════════════════════════════════════════════════════
# V3.5: 票池进攻性指标
# ══════════════════════════════════════════════════════

def compute_pool_aggressiveness(stock_data: dict) -> dict:
    """量化当日票池的进攻性（0-100），用于风险管理和仓位指导。

    四维度等权：
    - 弹性(elasticity_20d 均值) → 高弹性=高进攻
    - 涨停密度(A2 因子命中率) → 涨停多=进攻
    - 资金流强度(B1 因子命中率) → 资金流入=进攻
    - NX 买点占比 → 买点多=进攻

    Returns:
        {
            "score": 0-100,
            "level": "高/中/低",
            "breakdown": {"elasticity": float, "limit_up": float, "fund_flow": float, "nx_buy": float},
            "n_stocks": int,
        }
    """
    if not stock_data:
        return {"score": 0, "level": "低", "breakdown": {}, "n_stocks": 0}

    elasticities = []
    limit_up_count = 0
    fund_flow_positive = 0
    nx_buy_count = 0
    total = 0

    for code, info in stock_data.items():
        if not isinstance(info, dict):
            continue
        total += 1

        el = info.get("elasticity_20d")
        if isinstance(el, (int, float)):
            elasticities.append(el)

        catalyst = info.get("catalyst_breakdown", {})
        if catalyst.get("nx_score", 0) > 15:
            nx_buy_count += 1

        flow = info.get("flow_4d") or info.get("fund_flow_4d")
        if isinstance(flow, (int, float)) and flow > 0:
            fund_flow_positive += 1

        swing = info.get("swing_position")
        if isinstance(swing, (int, float)) and swing > 70:
            limit_up_count += 1

    if total == 0:
        return {"score": 0, "level": "低", "breakdown": {}, "n_stocks": 0}

    avg_el = sum(elasticities) / len(elasticities) if elasticities else 1.0
    el_score = min(avg_el / 3.0 * 100, 100)
    lu_score = (limit_up_count / total) * 100
    ff_score = (fund_flow_positive / total) * 100
    nx_score = (nx_buy_count / total) * 100

    composite = (el_score + lu_score + ff_score + nx_score) / 4
    level = "高" if composite >= 65 else "中" if composite >= 35 else "低"

    return {
        "score": round(composite, 1),
        "level": level,
        "breakdown": {
            "elasticity": round(el_score, 1),
            "limit_up": round(lu_score, 1),
            "fund_flow": round(ff_score, 1),
            "nx_buy": round(nx_score, 1),
        },
        "n_stocks": total,
    }
