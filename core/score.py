"""
score.py — CycleRadar 4D8I 确定性打分脚本

用法：
  python score.py --date 2026-02-27                    # 打分（自动拉取 MCP 数据）
  python score.py --date 2026-02-27 --from-cache       # 用缓存 JSON 打分（离线）
  python score.py --date 2026-02-27 --industries 有色金属 计算机  # 指定行业
  python score.py --date 2026-02-27 --scan-all         # 全行业扫描排名（A1/A2/B1）
  python score.py --date 2026-02-27 --scan-all --from-cache  # 用缓存做全行业扫描

输出：
  - 终端打印打分汇总表
  - 底稿/raw/YYYY-MM-DD.json  — MCP 原始数据（自动保存）
  - 底稿/raw/YYYY-MM-DD_score.json — 打分结果
  - 底稿/raw/YYYY-MM-DD_scan.json — 全行业扫描结果（--scan-all 模式）
  - 底稿/history/score_ledger.json — 评分台账（--scan-all 自动追加）

依赖：requests（标准库外唯一依赖）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

from dotenv import load_dotenv
load_dotenv()

# Windows 终端 UTF-8 输出
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── 配置 ──────────────────────────────────────────────
BASE_URL = "http://fintool-mcp.finstep.cn"
SIGNATURE = os.environ.get("MCP_SIGNATURE", "")
PROJECT_ROOT = Path(__file__).parent
RAW_DIR = PROJECT_ROOT / "底稿" / "raw"
HISTORY_DIR = PROJECT_ROOT / "底稿" / "history"
MCP_CACHE_DIR = PROJECT_ROOT / "底稿" / "cache" / "mcp"

# 申万一级行业 → 龙头股代码（用于个股级 MCP 查询）
# key 必须与 scan_all 返回的行业名称精确匹配（49 行业全覆盖）
INDUSTRY_LEADERS = {
    # ── 资源/能源 ──
    "有色金属": ["601899", "603993"],   # 紫金矿业, 洛阳钼业
    "贵金属":   ["600489", "600547"],   # 中金黄金, 山东黄金
    "小金属":   ["002460", "600549"],   # 赣锋锂业, 厦门钨业
    "能源金属": ["002466", "603799"],   # 天齐锂业, 华友钴业
    "工业金属": ["600219", "601677"],   # 南山铝业, 明泰铝业
    "石油石化": ["600028", "601857"],   # 中国石化, 中国石油
    "煤炭采选": ["601088", "601898"],   # 中国神华, 中煤能源
    "钢铁":     ["600019", "000709"],   # 宝钢股份, 河钢股份
    "基础化工": ["600309", "002601"],   # 万华化学, 龙蟒佰利
    # ── 科技/TMT ──
    "计算机":   ["002415", "688111"],   # 海康威视, 金山办公
    "电子":     ["002371", "603501"],   # 北方华创, 韦尔股份
    "半导体":   ["688981", "002049"],   # 中芯国际, 紫光国微
    "通信":     ["600050", "000063"],   # 中国联通, 中兴通讯
    "光学光电子": ["002475", "300433"], # 立讯精密, 蓝思科技
    "电子化学品": ["300236", "603986"], # 上海新阳, 兆易创新
    # ── 消费 ──
    "食品饮料行业": ["600519", "000858"], # 贵州茅台, 五粮液
    "家用电器": ["000651", "000333"],   # 格力电器, 美的集团
    "汽车整车": ["002594", "600104"],   # 比亚迪, 上汽集团
    "美容护理": ["300957", "603605"],   # 贝泰妮, 珀莱雅
    "纺服行业": ["002563", "603337"],   # 森马服饰, 杰克股份
    "商贸零售": ["601933", "002697"],   # 永辉超市, 红旗连锁
    "轻工制造": ["603833", "002831"],   # 欧派家居, 裕同科技
    "农林牧渔": ["300498", "002714"],   # 温氏股份, 牧原股份
    # ── 医药 ──
    "医药":     ["600276", "300760"],   # 恒瑞医药, 迈瑞医疗
    "化学制药": ["000963", "600196"],   # 华东医药, 复星医药
    "生物制品": ["300122", "002007"],   # 智飞生物, 华兰生物
    # ── 金融 ──
    "银行":     ["601398", "600036"],   # 工商银行, 招商银行
    "非银金融": ["601318", "600030"],   # 中国平安, 中信证券
    "证券":     ["600030", "300059"],   # 中信证券, 东方财富
    "多元金融": ["600705", "000166"],   # 中航产融, 申万宏源
    # ── 基建/工业 ──
    "建筑工程": ["601668", "601800"],   # 中国建筑, 中国交建
    "建筑材料": ["600585", "000786"],   # 海螺水泥, 北新建材
    "机械设备": ["002008", "300124"],   # 大族激光, 汇川技术
    "通用设备": ["601100", "002353"],   # 恒立液压, 杰瑞股份
    "电网设备": ["600089", "300750"],   # 特变电工, 宁德时代
    "交运设备": ["601766", "000957"],   # 中国中车, 中通客车
    "电力设备": ["300750", "601012"],   # 宁德时代, 隆基绿能
    "电新行业": ["300274", "300751"],   # 阳光电源, 迈为股份
    "环保":     ["603568", "000967"],   # 伟明环保, 盈峰环境
    # ── 交运/物流 ──
    "航运港口": ["601919", "001872"],   # 中远海控, 招商港口
    "交通运输": ["601111", "002352"],   # 中国国航, 顺丰控股
    "机场":     ["600009", "600004"],   # 上海机场, 白云机场
    # ── 地产/公用 ──
    "房地产":   ["001979", "600048"],   # 招商蛇口, 保利发展
    "公用事业": ["600900", "003816"],   # 长江电力, 中国广核
    # ── 军工/传媒/服务 ──
    "国防军工": ["600760", "601698"],   # 中航沈飞, 中国卫通
    "文化传媒": ["002555", "300413"],   # 三七互娱, 芒果超媒
    "影视院线": ["300251", "002739"],   # 光线传媒, 万达电影
    "出版":     ["601098", "601928"],   # 中南传媒, 凤凰传媒
    "社会服务": ["300015", "600754"],   # 爱尔眼科, 锦江酒店
}

# 英文关键词（规避 Windows curl 中文编码问题）
# key 必须与 INDUSTRY_LEADERS / scan_all 名称一致
INDUSTRY_KEYWORDS_EN = {
    "有色金属": "metals mining copper gold",
    "贵金属":   "gold silver precious metals",
    "小金属":   "lithium tungsten rare metals",
    "能源金属": "lithium cobalt nickel energy metals",
    "工业金属": "aluminum copper zinc industrial metals",
    "石油石化": "oil petroleum refinery",
    "煤炭采选": "coal energy mining",
    "钢铁":     "steel iron",
    "基础化工": "chemical materials",
    "计算机":   "software AI computing",
    "电子":     "semiconductor chip electronics",
    "半导体":   "semiconductor foundry chip",
    "通信":     "telecom 5G communication",
    "光学光电子": "optics LED display panel",
    "电子化学品": "electronic chemicals materials",
    "食品饮料行业": "food beverage liquor",
    "家用电器": "appliance home electronics",
    "汽车整车": "automobile EV vehicle",
    "美容护理": "cosmetics skincare beauty",
    "纺服行业": "textile apparel fashion",
    "商贸零售": "retail ecommerce supermarket",
    "轻工制造": "furniture packaging light industry",
    "农林牧渔": "agriculture livestock farming",
    "医药":     "pharmaceutical biotech medical",
    "化学制药": "pharma drug chemical medicine",
    "生物制品": "biotech vaccine biological products",
    "银行":     "bank finance lending",
    "非银金融": "securities insurance brokerage",
    "证券":     "securities brokerage stock exchange",
    "多元金融": "diversified finance leasing trust",
    "建筑工程": "construction infrastructure engineering",
    "建筑材料": "cement glass building materials",
    "机械设备": "machinery equipment manufacturing",
    "通用设备": "hydraulic pump general equipment",
    "电网设备": "power grid transformer equipment",
    "交运设备": "railway vehicle transport equipment",
    "电力设备": "battery solar new energy",
    "电新行业": "solar inverter new energy equipment",
    "环保":     "environment waste treatment",
    "航运港口": "shipping port container freight",
    "交通运输": "airline logistics transportation",
    "机场":     "airport aviation",
    "房地产":   "real estate property",
    "公用事业": "utility power electricity",
    "国防军工": "defense military aerospace",
    "文化传媒": "media entertainment gaming",
    "影视院线": "film cinema entertainment",
    "出版":     "publishing media print",
    "社会服务": "healthcare hotel tourism service",
}


# ── MCP 缓存层 ───────────────────────────────────────
import hashlib

_mcp_cache_date: str = ""  # 当前采集日期，由 set_mcp_cache_date() 设置

# 不缓存的工具（实时性要求高）
_MCP_NO_CACHE = set()


def set_mcp_cache_date(date_str: str):
    """设置当前采集日期，启用 MCP 缓存。空字符串 = 禁用缓存。"""
    global _mcp_cache_date
    _mcp_cache_date = date_str


def _mcp_cache_key(service: str, tool: str, arguments: dict) -> str:
    params_str = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
    params_hash = hashlib.md5(params_str.encode()).hexdigest()[:8]
    return f"{_mcp_cache_date}_{service}_{tool}_{params_hash}"


def _mcp_cache_get(key: str) -> dict | None:
    path = MCP_CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(path))
        age_hours = (datetime.now() - mtime).total_seconds() / 3600
        if age_hours > 18:
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _mcp_cache_put(key: str, data: dict):
    MCP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = MCP_CACHE_DIR / f"{key}.json"
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError:
        pass


# ── MCP 调用封装 ──────────────────────────────────────
import time as _time

_MCP_MAX_RETRIES = 2
_MCP_BACKOFF_BASE = 1.5  # 秒: 1.5, 3.0


def _mcp_request(url: str, payload: dict, headers: dict) -> dict:
    """单次 MCP 请求，返回解析后的 data 或 raise。"""
    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    for line in resp.text.split("\n"):
        if line.startswith("data: "):
            body = json.loads(line[6:])
            result = body.get("result", {})
            content_list = result.get("content", [])
            if content_list and content_list[0].get("type") == "text":
                text = content_list[0]["text"]
                try:
                    parsed = json.loads(text)
                    data = parsed.get("data", parsed)
                except json.JSONDecodeError:
                    data = {"_raw_text": text}
                if "_error" in data:
                    raise RuntimeError(f"MCP业务错误: {data['_error']}")
                return data
            sc = result.get("structuredContent", {})
            if sc:
                return sc.get("data", sc)
            return result
    return {}


def mcp_call(service: str, tool: str, arguments: dict) -> dict:
    """调用 Finstep MCP，返回解析后的 data 字段。自动缓存 + 重试（最多2次）。"""
    # 缓存读取
    if _mcp_cache_date and tool not in _MCP_NO_CACHE:
        cache_key = _mcp_cache_key(service, tool, arguments)
        cached = _mcp_cache_get(cache_key)
        if cached is not None:
            return cached

    url = f"{BASE_URL}/{service}?signature={SIGNATURE}"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    last_err = None
    for attempt in range(_MCP_MAX_RETRIES + 1):
        try:
            data = _mcp_request(url, payload, headers)
            # 缓存写入
            if _mcp_cache_date and tool not in _MCP_NO_CACHE:
                _mcp_cache_put(cache_key, data)
            return data
        except Exception as e:
            last_err = e
            if attempt < _MCP_MAX_RETRIES:
                wait = _MCP_BACKOFF_BASE * (2 ** attempt)
                _time.sleep(wait)

    return {"_error": str(last_err)}


# ── 数据拉取 ─────────────────────────────────────────
def fetch_all_data(date_str: str) -> dict:
    """拉取指定日期的全部 MCP 原始数据。"""
    data = {"date": date_str, "fetched_at": datetime.now().isoformat()}

    # A1: 行业涨跌幅排名
    print("  [A1] 拉取行业涨跌幅...")
    data["plates_ranking"] = mcp_call("plates", "get_plate_rate_ranking", {
        "sector_type": [1], "num": 50, "trade_date": ""
    })

    # B2: 融资余额排行
    print("  [B2] 拉取融资余额排行...")
    data["margin_rank"] = mcp_call("ms_and_connect",
        "get_margin_trade_balance_surplus_daily_rank",
        {"rank_type": 1, "trading_date": date_str, "top": 30})

    # C2-ref: 沪深300成分股（取部分用于基准 PE）
    print("  [C2] 拉取沪深300成分股...")
    data["hs300_snapshot"] = mcp_call("index", "get_constituent_stock_snapshot", {
        "keyword": "000300", "page": 1, "page_size": 50
    })

    # 按行业拉取个股数据
    data["by_industry"] = {}

    return data


def fetch_industry_data(industry: str, date_str: str, event_keywords: list[str] | None = None) -> dict:
    """拉取某个行业的详细数据。"""
    leaders = INDUSTRY_LEADERS.get(industry, [])
    result = {"industry": industry, "leaders": leaders}

    # B1: 龙头股资金流向
    result["net_flows"] = {}
    for code in leaders[:2]:
        print(f"  [B1] {industry} 龙头 {code} 资金流向...")
        result["net_flows"][code] = mcp_call("market_quote", "get_net_flow_list", {
            "keyword": code, "end_date": date_str
        })

    # C1: 龙头 PE
    result["valuations"] = {}
    for code in leaders[:1]:
        print(f"  [C1] {industry} 龙头 {code} 估值...")
        result["valuations"][code] = mcp_call("company_info",
            "get_valuation_metrics_daily",
            {"keyword": code, "start_date": date_str, "end_date": date_str})

    # D1: 新闻（优先用事件关键词，否则用行业英文关键词）
    if event_keywords:
        # 用事件关键词搜索更精准的新闻
        result["news"] = []
        for kw in event_keywords[:2]:
            print(f"  [D1] 事件关键词搜索: {kw}")
            news = mcp_call("news", "search_news", {"query": kw, "topk": 3})
            if isinstance(news, list):
                result["news"].extend(news)
            elif isinstance(news, dict) and "data" in news:
                result["news"].extend(news["data"])
    else:
        kw = INDUSTRY_KEYWORDS_EN.get(industry, industry)
        print(f"  [D1] {industry} 新闻搜索...")
        result["news"] = mcp_call("news", "search_news", {
            "query": kw, "topk": 5
        })

    return result


# ── 打分逻辑（确定性） ────────────────────────────────
def score_industry(industry: str, global_data: dict, industry_data: dict,
                   prev_data: dict | None = None) -> dict:
    """
    对单个行业进行 4D8I 打分。
    返回 {"A1": 0/1, "A2": 0/1, ..., "total": int, "stage": str, "details": {...}}
    """
    scores = {}
    details = {}

    # ── A1: 行业 vs 沪深300 超额 ≥ 3% ──
    # plates 在 A2/B1 中也会用到，先统一取出
    plates = global_data.get("plates_ranking", [])

    # 优先用历史缓存计算多日累计涨幅，否则用当日数据
    hist_plates = _load_historical_plates(industry, before_date=global_data.get("date"))
    if len(hist_plates) >= 4:
        # 有多日数据：计算累计涨幅
        recent = hist_plates[-min(20, len(hist_plates)):]  # 最多取最近20天
        cumulative = 1.0
        for d in recent:
            cumulative *= (1 + d["change_rate"] / 100)
        cumulative_pct = (cumulative - 1) * 100
        # TODO: 同期 HS300 累计涨幅需要类似的历史积累，暂用 0 作保守基准
        hs300_est = 0.0
        excess = cumulative_pct - hs300_est
        scores["A1"] = 1 if excess >= 3.0 else 0
        details["A1"] = (f"近{len(recent)}日累计涨幅 {cumulative_pct:+.2f}%, "
                         f"HS300基准 ≈ {hs300_est}%, 超额 {excess:+.2f}% "
                         f"({len(recent)}天数据)")
    else:
        # 历史不足：用当日单日数据
        industry_chg = None
        if isinstance(plates, list):
            for p in plates:
                name = p.get("plate_name", "")
                if industry in name or name in industry:
                    raw_chg = p.get("price_change_rate")
                    if raw_chg is not None:
                        industry_chg = str(raw_chg).replace("%", "").strip()
                    break
        if industry_chg is not None:
            try:
                chg = float(industry_chg)
            except (TypeError, ValueError):
                chg = 0.0
            hs300_est = 0.3
            excess = chg - hs300_est
            scores["A1"] = 1 if excess >= 3.0 else 0
            hist_note = f"（{len(hist_plates)}天数据趋势判断）"
            details["A1"] = (f"当日涨幅 {chg:+.2f}%, HS300 ≈ {hs300_est}%, "
                             f"超额 {excess:+.2f}% {hist_note}")
        else:
            scores["A1"] = 0
            details["A1"] = f"未找到 {industry} 涨跌幅数据"

    # ── A2: 行业内涨停数 ≥ 3 只/周 ──
    # 从 plates 数据中取 limit_rise_count
    limit_count = 0
    if isinstance(plates, list):
        for p in plates:
            name = p.get("plate_name", "")
            if industry in name or name in industry:
                limit_count = p.get("limit_rise_count", 0) or 0
                break
    scores["A2"] = 1 if limit_count >= 3 else 0
    details["A2"] = f"涨停数 {limit_count}（阈值 ≥3）"

    # ── B1: 板块主力净流入 > 0 ──
    major_flow = None
    if isinstance(plates, list):
        for p in plates:
            name = p.get("plate_name", "")
            if industry in name or name in industry:
                raw = p.get("major_net_flow_in")
                if raw is not None:
                    try:
                        # 可能是字符串 "100.27亿元" 或数字
                        if isinstance(raw, str):
                            major_flow = float(
                                raw.replace("亿元", "").replace("亿", "")
                                   .replace("万元", "").replace("万", "").strip()
                            )
                        else:
                            major_flow = float(raw)
                    except (TypeError, ValueError):
                        major_flow = None
                break
    if major_flow is not None:
        scores["B1"] = 1 if major_flow > 0 else 0
        details["B1"] = f"板块主力净流入 {major_flow:.2f}亿"
    else:
        # 回退：用龙头股资金流向
        flows = industry_data.get("net_flows", {})
        total_flow = 0.0
        for code, flow_data in flows.items():
            if isinstance(flow_data, list) and flow_data:
                for day in flow_data:
                    mf = day.get("major_net_flow_in", 0)
                    if mf:
                        try:
                            total_flow += float(str(mf).replace("亿", "").replace("万", ""))
                        except (TypeError, ValueError):
                            pass
        scores["B1"] = 1 if total_flow > 0 else 0
        details["B1"] = f"龙头合计净流入 {total_flow:.2f}"

    # ── B2: 融资余额环比 > +3% ──
    margin_data = global_data.get("margin_rank", [])
    current_margin = None
    if isinstance(margin_data, list):
        for m in margin_data:
            # 匹配 first_industry_name 或 second_industry_name
            ind_name = m.get("second_industry_name", "") or ""
            first_name = m.get("first_industry_name", "") or ""
            if (industry in ind_name or ind_name in industry or
                industry in first_name or first_name in industry):
                fv = m.get("finance_value", "")
                # 解析 "421.19亿元" → 421.19
                if isinstance(fv, str):
                    try:
                        current_margin = float(fv.replace("亿元", "").replace("亿", ""))
                    except ValueError:
                        pass
                elif isinstance(fv, (int, float)):
                    current_margin = float(fv)
                break

    # 尝试读取上周数据计算环比
    prev_margin = None
    if prev_data:
        prev_margin_data = prev_data.get("margin_rank", [])
        if isinstance(prev_margin_data, list):
            for m in prev_margin_data:
                ind_name = m.get("second_industry_name", "") or ""
                first_name = m.get("first_industry_name", "") or ""
                if (industry in ind_name or ind_name in industry or
                    industry in first_name or first_name in industry):
                    fv = m.get("finance_value", "")
                    if isinstance(fv, str):
                        try:
                            prev_margin = float(fv.replace("亿元", "").replace("亿", ""))
                        except ValueError:
                            pass
                    elif isinstance(fv, (int, float)):
                        prev_margin = float(fv)
                    break

    if current_margin is not None and prev_margin is not None and prev_margin > 0:
        change_pct = (current_margin - prev_margin) / prev_margin * 100
        scores["B2"] = 1 if change_pct > 3.0 else 0
        details["B2"] = f"融资余额 {current_margin:.1f}亿, 上期 {prev_margin:.1f}亿, 环比 {change_pct:+.1f}%"
    elif current_margin is not None:
        # 从 history/ 查找上期融资余额
        hist_margin = _load_prev_margin(industry, global_data.get("date", ""))
        if hist_margin is not None and hist_margin > 0:
            change_pct = (current_margin - hist_margin) / hist_margin * 100
            scores["B2"] = 1 if change_pct > 3.0 else 0
            details["B2"] = f"融资余额 {current_margin:.1f}亿, 上期 {hist_margin:.1f}亿, 环比 {change_pct:+.1f}%（历史快照）"
        else:
            scores["B2"] = 0
            details["B2"] = f"融资余额 {current_margin:.1f}亿，首期评分待环比数据积累"
    else:
        scores["B2"] = 0
        details["B2"] = f"未找到 {industry} 融资数据"

    # ── C1: PE_TTM 估值判断 ──
    # 优先用历史分位（有足够数据时），否则与 HS300 PE 对比
    valuations = industry_data.get("valuations", {})
    pe_ttm = None
    for code, val_data in valuations.items():
        if isinstance(val_data, list) and val_data:
            pe_ttm = val_data[0].get("pe_ttm")
        elif isinstance(val_data, dict):
            pe_ttm = val_data.get("pe_ttm")
        break

    if pe_ttm is not None:
        try:
            pe = float(pe_ttm)
        except (TypeError, ValueError):
            pe = None
        if pe is not None and pe > 0:
            hist_pe = _load_historical_pe(industry)
            if hist_pe and len(hist_pe) >= 5:
                # 有足够历史：计算分位数
                below = sum(1 for h in hist_pe if h <= pe)
                percentile = below / len(hist_pe) * 100
                scores["C1"] = 1 if percentile < 70 else 0
                details["C1"] = f"PE_TTM={pe:.2f}, 历史分位 {percentile:.0f}%（{len(hist_pe)}天）"
            else:
                # 历史不足：与 HS300 PE 对比作为参考
                hs300_pe = _compute_hs300_pe_avg(global_data)
                if hs300_pe and hs300_pe > 0:
                    premium = pe / hs300_pe
                    # 溢价率 < 3 倍视为未过热（宽松标准，因数据不足）
                    scores["C1"] = 1 if premium < 3.0 else 0
                    details["C1"] = (f"PE_TTM={pe:.2f}, HS300均值PE={hs300_pe:.1f}, "
                                     f"溢价率 {premium:.1f}x（参考值）")
                else:
                    scores["C1"] = 0
                    details["C1"] = f"PE_TTM={pe:.2f}, 估值参考基准待完善"
        else:
            scores["C1"] = 0
            details["C1"] = f"PE_TTM 无效值: {pe_ttm}"
    else:
        scores["C1"] = 0
        details["C1"] = "未获取到估值数据"

    # ── C2: 溢价率本周扩大 ──
    # 对比当前 PE 与上期 PE 的相对变化
    current_date = global_data.get("date", "")
    if pe_ttm is not None:
        try:
            current_pe = float(pe_ttm)
        except (TypeError, ValueError):
            current_pe = None
    else:
        current_pe = None

    hs300_pe = _compute_hs300_pe_avg(global_data)
    prev_pe = _load_prev_premium(industry, current_date)

    if current_pe and current_pe > 0 and hs300_pe and hs300_pe > 0:
        current_premium = current_pe / hs300_pe
        if prev_pe and prev_pe > 0:
            prev_premium = prev_pe / hs300_pe  # 简化：用同一 HS300 基准
            premium_expanding = current_premium > prev_premium
            scores["C2"] = 1 if premium_expanding else 0
            details["C2"] = (f"溢价率 {current_premium:.2f}x → "
                             f"{'扩大' if premium_expanding else '收窄'}（上期 {prev_premium:.2f}x）")
        else:
            scores["C2"] = 0
            details["C2"] = f"溢价率 {current_premium:.2f}x，首期评分待环比数据"
    else:
        scores["C2"] = 0
        details["C2"] = "估值数据不足，溢价率待后续评估"

    # ── D1: 本周重大催化剂 ──
    # 定性指标 — 返回新闻摘要，由人工确认
    news = industry_data.get("news", [])
    news_titles = []
    if isinstance(news, list):
        for n in news[:5]:
            t = n.get("title", "")
            if t:
                news_titles.append(t)
    elif isinstance(news, dict) and "data" in news:
        for n in news["data"][:5]:
            t = n.get("title", "")
            if t:
                news_titles.append(t)

    scores["D1"] = -1  # -1 = 需人工判断
    details["D1"] = f"新闻 {len(news_titles)} 条 → 需人工判断是否有重大催化剂: " + \
                    "; ".join(news_titles[:3]) if news_titles else "未获取到新闻"

    # ── D2: 舆情热度趋势 ──
    scores["D2"] = -1  # -1 = 需人工判断
    details["D2"] = "舆情需人工判断（search_community_forum 数据待接入）"

    # ── 汇总 ──
    auto_total = sum(v for v in scores.values() if v > 0)
    manual_count = sum(1 for v in scores.values() if v == -1)
    max_possible = auto_total + manual_count  # 人工指标全给1的上限

    stage = _determine_stage(auto_total, max_possible, scores)

    return {
        "industry": industry,
        "scores": scores,
        "details": details,
        "auto_total": auto_total,
        "manual_pending": manual_count,
        "total_range": f"{auto_total}-{max_possible}/8",
        "stage": stage,
    }


def _determine_stage(auto_total: int, max_possible: int, scores: dict) -> str:
    """
    根据得分确定阶段。
    保守策略：用确定得分（不含人工待定），避免高估。
    """
    s = auto_total
    if s <= 2:
        return "观望期"
    elif s <= 4:
        return "启动期/衰退期（待趋势确认）"
    elif s <= 6:
        return "加速期"
    else:
        return "过热期"


def _load_historical_pe(industry: str) -> list[float]:
    """从历史缓存中加载 PE 数据列表。"""
    result = []
    if not RAW_DIR.exists():
        return result
    for f in sorted(RAW_DIR.glob("*.json")):
        if f.name.endswith("_score.json") or f.name.endswith("_stocks.json"):
            continue
        try:
            with open(f, "r", encoding="utf-8") as fh:
                day_data = json.load(fh)
            by_ind = day_data.get("by_industry", {}).get(industry, {})
            vals = by_ind.get("valuations", {})
            for code, vdata in vals.items():
                if isinstance(vdata, list) and vdata:
                    pe = vdata[0].get("pe_ttm")
                elif isinstance(vdata, dict):
                    pe = vdata.get("pe_ttm")
                else:
                    pe = None
                if pe is not None:
                    try:
                        result.append(float(pe))
                    except (TypeError, ValueError):
                        pass
                break
        except (json.JSONDecodeError, OSError):
            continue
    return result


def _load_historical_plates(industry: str, before_date: str = None) -> list[dict]:
    """
    从历史缓存加载某行业的每日 plates 数据。
    优先从 HISTORY_DIR（精简快照），再从 RAW_DIR（完整数据）补充。
    返回 [{"date": "YYYY-MM-DD", "change_rate": float, "major_flow": float}, ...]
    按日期升序排列。
    """
    seen_dates = set()
    result = []

    # 1) 从 HISTORY_DIR 读取精简快照
    if HISTORY_DIR.exists():
        for f in sorted(HISTORY_DIR.glob("plates_*.json")):
            date_part = f.stem.replace("plates_", "")
            file_date = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:]}"
            if before_date and file_date > before_date:
                continue
            if file_date in seen_dates:
                continue
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                for p in data.get("plates", []):
                    name = p.get("plate_name", "")
                    if industry in name or name in industry:
                        raw_chg = p.get("price_change_rate")
                        if raw_chg is not None:
                            chg = float(str(raw_chg).replace("%", "").strip())
                            raw_flow = p.get("major_net_flow_in")
                            flow = _parse_flow(raw_flow)
                            result.append({"date": file_date, "change_rate": chg, "major_flow": flow})
                            seen_dates.add(file_date)
                        break
            except (json.JSONDecodeError, OSError, ValueError):
                continue

    # 2) 从 RAW_DIR 补充（避免重复日期）
    if RAW_DIR.exists():
        for f in sorted(RAW_DIR.glob("*.json")):
            if f.name.endswith("_score.json") or f.name.endswith("_stocks.json"):
                continue
            file_date = f.stem
            if before_date and file_date > before_date:
                continue
            if file_date in seen_dates:
                continue
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    day_data = json.load(fh)
                plates = day_data.get("plates_ranking", [])
                if not isinstance(plates, list):
                    continue
                for p in plates:
                    name = p.get("plate_name", "")
                    if industry in name or name in industry:
                        raw_chg = p.get("price_change_rate")
                        if raw_chg is not None:
                            chg = float(str(raw_chg).replace("%", "").strip())
                            raw_flow = p.get("major_net_flow_in")
                            flow = _parse_flow(raw_flow)
                            result.append({"date": file_date, "change_rate": chg, "major_flow": flow})
                            seen_dates.add(file_date)
                        break
            except (json.JSONDecodeError, OSError, ValueError):
                continue

    result.sort(key=lambda x: x["date"])
    return result


def _parse_flow(raw_flow) -> float | None:
    """解析资金流向字段为 float。"""
    if raw_flow is None:
        return None
    try:
        if isinstance(raw_flow, str):
            return float(raw_flow.replace("亿元", "").replace("亿", "")
                         .replace("万元", "").replace("万", "").strip())
        return float(raw_flow)
    except (TypeError, ValueError):
        return None


def _parse_flow_to_yi(raw_flow) -> float | None:
    """解析资金流向字段为 float（统一单位：亿元）。"""
    if raw_flow is None:
        return None
    try:
        if isinstance(raw_flow, str):
            s = raw_flow.strip()
            if "万" in s:
                val = float(s.replace("万元", "").replace("万", "").strip())
                return val / 10000  # 万 → 亿
            else:
                return float(s.replace("亿元", "").replace("亿", "").strip())
        return float(raw_flow)
    except (TypeError, ValueError):
        return None


def _compute_hs300_pe_avg(global_data: dict) -> float | None:
    """从沪深300成分股快照计算加权平均 PE。"""
    snapshot = global_data.get("hs300_snapshot", [])
    if not isinstance(snapshot, list):
        return None
    pe_values = []
    for s in snapshot:
        pe = s.get("ttm_pe_rate") or s.get("pe_rate")
        if pe is not None:
            try:
                pev = float(str(pe).replace("%", "").strip())
                if 0 < pev < 500:  # 过滤异常值
                    pe_values.append(pev)
            except (TypeError, ValueError):
                pass
    if pe_values:
        return sum(pe_values) / len(pe_values)
    return None


def _score_to_stage(score: float) -> str:
    """简版评分→基础阶段。

    V1.1: 8因子加权满分 8.0（A1+A2+B1+B2*1.0+C1*0.5+D1+D2+E1*1.5）。
    ≥3.0 关注，<3.0 观望。
    确认由 enrich_scan_results() 根据持续性升级。
    """
    if score < 3.0:
        return "观望"
    return "关注"


def _load_plates(date_str: str, from_cache: bool) -> list:
    """加载 plates 数据，优先缓存→历史→MCP。"""
    date_compact = date_str.replace("-", "")

    # 1) Raw cache
    cache_path = RAW_DIR / f"{date_str}.json"
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            plates = data.get("plates_ranking", [])
            if isinstance(plates, list) and plates:
                print(f"  从缓存读取 plates: {cache_path}")
                return plates
        except (json.JSONDecodeError, OSError):
            pass

    # 2) History snapshot
    history_path = HISTORY_DIR / f"plates_{date_compact}.json"
    if history_path.exists():
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            plates = data.get("plates", [])
            if isinstance(plates, list) and plates:
                print(f"  从历史快照读取 plates: {history_path}")
                return plates
        except (json.JSONDecodeError, OSError):
            pass

    # 3) Fetch from MCP
    if not from_cache:
        print("  从 MCP 拉取 plates...")
        plates_raw = mcp_call("plates", "get_plate_rate_ranking", {
            "sector_type": [1], "num": 50, "trade_date": date_str
        })
        # get_plate_rate_ranking 不支持历史日期查询（返回 code:2005），
        # 用 trade_date="" 重试拿当日实时数据（daily.py 22:00 跑时市场已收盘）
        if not isinstance(plates_raw, list) or not plates_raw:
            print(f"  trade_date={date_str} 返回空/错误，尝试实时查询...")
            plates_raw = mcp_call("plates", "get_plate_rate_ranking", {
                "sector_type": [1], "num": 50, "trade_date": ""
            })
        if isinstance(plates_raw, list) and plates_raw:
            actual_date = plates_raw[0].get("trade_date", date_str)
            global_data = {
                "date": actual_date,
                "fetched_at": datetime.now().isoformat(),
                "plates_ranking": plates_raw,
                "by_industry": {},
            }
            save_raw(actual_date, global_data)
            save_history(actual_date, global_data)
            if actual_date != date_str:
                print(f"  ⚠ 实时数据日期为 {actual_date}（请求 {date_str}）")
            return plates_raw

    # 4) Fallback: 前一交易日缓存（最多回溯 3 天）
    if not from_cache:
        print("  MCP 当日无数据，尝试前一交易日...")
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        for offset in range(1, 4):
            prev = (dt - timedelta(days=offset)).strftime("%Y-%m-%d")
            prev_plates = _load_plates(prev, from_cache=True)
            if prev_plates:
                print(f"  ⚠ 使用 {prev} 板块数据作为替代（T-{offset}）")
                return prev_plates

    return []


# ══════════════════════════════════════════════════════
# V3.0 新增因子数据采集
# ══════════════════════════════════════════════════════

# fetch_leader_board_institutional → 已迁移至 factor_agent.py


# fetch_block_trade_summary → 已迁移至 factor_agent.py


# scan_all_industries → 已迁移至 factor_agent.py


# _INDUSTRY_PE_RANGE → 已迁移至 factor_agent.py


# compute_valuation_percentile → 已迁移至 factor_agent.py


# _CROSS_ASSET_RULES → 已迁移至 factor_agent.py


# compute_cross_asset_signals → 已迁移至 factor_agent.py


# scan_concept_plates → 已迁移至 factor_agent.py


def _load_concept_plates(date_str: str, from_cache: bool) -> list:
    """加载概念板块数据。优先缓存 → MCP。"""
    date_compact = date_str.replace("-", "")

    # 1) 缓存
    cache_path = RAW_DIR / f"{date_str}_concepts.json"
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            plates = data.get("concept_plates", [])
            if isinstance(plates, list) and plates:
                return plates
        except (json.JSONDecodeError, OSError):
            pass

    # 2) MCP
    if not from_cache:
        print("  从 MCP 拉取概念板块...")
        plates_raw = mcp_call("plates", "get_plate_rate_ranking", {
            "sector_type": [3], "num": 50, "trade_date": date_str,
        })
        if isinstance(plates_raw, list) and plates_raw:
            RAW_DIR.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump({
                    "date": date_str,
                    "fetched_at": datetime.now().isoformat(),
                    "concept_plates": plates_raw,
                }, f, ensure_ascii=False, indent=2)
            return plates_raw

    return []


# ── 周级资金面 + 持续性增强 ─────────────────────────────


# _compute_all_weekly_flows → 已迁移至 factor_agent.py


# compute_market_temperature → 已迁移至 factor_agent.py


# enrich_scan_results → 已迁移至 factor_agent.py


# compute_multi_period_heat → 已迁移至 event_agent.py


def _load_prev_margin(industry: str, before_date: str) -> float | None:
    """从 history/ 加载该行业最近一次的融资余额，用于 B2 环比。"""
    if not HISTORY_DIR.exists():
        return None
    files = sorted(HISTORY_DIR.glob("margin_*.json"), reverse=True)
    for f in files:
        # 文件名: margin_YYYYMMDD.json
        file_date = f.stem.replace("margin_", "")
        file_date_fmt = f"{file_date[:4]}-{file_date[4:6]}-{file_date[6:]}"
        if file_date_fmt >= before_date:
            continue
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            for m in data.get("margin_rank", []):
                ind_name = m.get("second_industry_name", "") or ""
                first_name = m.get("first_industry_name", "") or ""
                if (industry in ind_name or ind_name in industry or
                    industry in first_name or first_name in industry):
                    fv = m.get("finance_value", "")
                    if isinstance(fv, str):
                        return float(fv.replace("亿元", "").replace("亿", ""))
                    elif isinstance(fv, (int, float)):
                        return float(fv)
        except (json.JSONDecodeError, OSError, ValueError):
            continue
    return None


def _load_prev_premium(industry: str, before_date: str) -> float | None:
    """从 history/ 加载该行业上次的 PE 溢价率（vs HS300），用于 C2 环比。"""
    if not HISTORY_DIR.exists():
        return None
    safe_ind = industry.replace("/", "_")
    files = sorted(HISTORY_DIR.glob(f"valuation_{safe_ind}_*.json"), reverse=True)
    for f in files:
        # 文件名: valuation_有色金属_YYYYMMDD.json
        date_part = f.stem.split("_")[-1]
        file_date_fmt = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:]}"
        if file_date_fmt >= before_date:
            continue
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            vals = data.get("valuations", {})
            for code, vdata in vals.items():
                pe = None
                if isinstance(vdata, list) and vdata:
                    pe = vdata[0].get("pe_ttm")
                elif isinstance(vdata, dict):
                    pe = vdata.get("pe_ttm")
                if pe is not None:
                    return float(pe)
        except (json.JSONDecodeError, OSError, ValueError, TypeError):
            continue
    return None


# ── 持久化 ────────────────────────────────────────────
def save_raw(date_str: str, data: dict) -> Path:
    """保存 MCP 原始 JSON 到 底稿/raw/YYYY-MM-DD.json"""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{date_str}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def save_scores(date_str: str, results: list[dict]) -> Path:
    """保存打分结果到 底稿/raw/YYYY-MM-DD_score.json"""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{date_str}_score.json"
    output = {
        "date": date_str,
        "scored_at": datetime.now().isoformat(),
        "results": results,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    return path


def save_history(date_str: str, global_data: dict):
    """保存精简版每日快照到 底稿/history/，供后续环比计算。"""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    date_compact = date_str.replace("-", "")

    # 板块数据快照
    plates = global_data.get("plates_ranking", [])
    if isinstance(plates, list) and plates:
        compact = []
        for p in plates:
            compact.append({
                "plate_name": p.get("plate_name", ""),
                "price_change_rate": p.get("price_change_rate"),
                "major_net_flow_in": p.get("major_net_flow_in"),
                "limit_rise_count": p.get("limit_rise_count"),
                "rise_count": p.get("rise_count"),
                "fall_count": p.get("fall_count"),
            })
        path = HISTORY_DIR / f"plates_{date_compact}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"date": date_str, "plates": compact}, f, ensure_ascii=False, indent=2)

    # 融资数据快照
    margin = global_data.get("margin_rank", [])
    if isinstance(margin, list) and margin:
        path = HISTORY_DIR / f"margin_{date_compact}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"date": date_str, "margin_rank": margin}, f, ensure_ascii=False, indent=2)

    # 估值数据快照（按行业）
    for ind, ind_data in (global_data.get("by_industry") or {}).items():
        vals = ind_data.get("valuations", {})
        if vals:
            safe_ind = ind.replace("/", "_")
            path = HISTORY_DIR / f"valuation_{safe_ind}_{date_compact}.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"date": date_str, "industry": ind, "valuations": vals},
                          f, ensure_ascii=False, indent=2)


def save_scan(date_str: str, scan_results: list[dict],
              rotation_signals: list[dict] | None = None,
              plates_unavailable: bool = False) -> Path:
    """保存全行业扫描结果到 底稿/raw/YYYY-MM-DD_scan.json

    plates_unavailable=True 表示 MCP get_plate_rate_ranking 返 null/空,
    下游(daily.py / report_agent)应在日报里说明"行业排名数据缺失"。
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{date_str}_scan.json"
    output = {
        "date": date_str,
        "scanned_at": datetime.now().isoformat(),
        "industries_scanned": len(scan_results),
        "rankings": scan_results,
    }
    if rotation_signals:
        output["rotation_signals"] = rotation_signals
    if plates_unavailable:
        output["plates_unavailable"] = True
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    return path


# ── 概念板块台账 ──────────────────────────────────────
CONCEPT_LEDGER_PATH = HISTORY_DIR / "concept_ledger.json"


def _load_concept_ledger() -> dict:
    """加载概念板块台账。"""
    if CONCEPT_LEDGER_PATH.exists():
        try:
            with open(CONCEPT_LEDGER_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"days": []}


def append_to_concept_ledger(date_str: str,
                             concept_scan: list[dict]) -> dict:
    """追加当日概念扫描结果到台账，返回台账数据。"""
    ledger = _load_concept_ledger()
    top20_entry = [
        {
            "name": r["name"],
            "score": r["score_auto"],
            "chg_pct": r["price_chg"],
            "fund_flow": r.get("fund_flow"),
            "rise_ratio": r.get("rise_ratio", 0),
            "rank": r["rank"],
        }
        for r in concept_scan[:20]
    ]

    # 防止重复
    updated = False
    for d in ledger["days"]:
        if d["date"] == date_str:
            d["top20"] = top20_entry
            updated = True
            break
    if not updated:
        ledger["days"].append({"date": date_str, "top20": top20_entry})

    ledger["days"].sort(key=lambda d: d["date"])

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONCEPT_LEDGER_PATH, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)

    return ledger


# filter_persistent_concepts → 已迁移至 factor_agent.py


# ── 全行业评分台账（V3.0新增：存储全部49行业因子明细，供IC验证用）─
FULL_LEDGER_PATH = HISTORY_DIR / "score_ledger_full.json"


def _load_full_ledger() -> dict:
    """加载全行业评分台账。"""
    if FULL_LEDGER_PATH.exists():
        try:
            with open(FULL_LEDGER_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"weeks": []}


def append_to_ledger_full(date_str: str, scan_results: list[dict]) -> dict:
    """追加全部行业的完整因子明细到全行业台账（IC验证核心数据源）。

    与 append_to_ledger 不同：
    - 存储全部行业（不只TOP10）
    - 存储每个因子的独立值（A1/A2/A3/B1/C1/C2/D1/D2）
    - 存储 composite_score
    """
    ledger = _load_full_ledger()

    all_entries = []
    for r in scan_results:
        scores = r.get("scores", {})
        entry = {
            "name": r["name"],
            "rank": r.get("rank", 0),
            "price_chg": r.get("price_chg", 0),
            "fund_flow": r.get("fund_flow"),
            "A1": scores.get("A1", 0),
            "A2": scores.get("A2", 0),
            "A3": scores.get("A3", 0),
            "B1": scores.get("B1", 0),
            "C1": scores.get("C1", 0),
            "C2": scores.get("C2", 0),
            "D1": scores.get("D1", 0),
            "D2": scores.get("D2", 0),
            "score_auto": r.get("score_auto", 0),
            "composite_score": r.get("composite_score"),
            "stage": r.get("stage", "观望"),
            "weekly_flow": r.get("weekly_flow", 0),
            "consecutive_top10": r.get("consecutive_top10", 0),
        }
        all_entries.append(entry)

    # 防止重复：更新或追加
    updated = False
    for w in ledger["weeks"]:
        if w["date"] == date_str:
            w["industries"] = all_entries
            w["count"] = len(all_entries)
            updated = True
            break
    if not updated:
        ledger["weeks"].append({
            "date": date_str,
            "count": len(all_entries),
            "industries": all_entries,
        })

    ledger["weeks"].sort(key=lambda w: w["date"])

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(FULL_LEDGER_PATH, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)

    return ledger


# ── 评分台账 ─────────────────────────────────────────
LEDGER_PATH = HISTORY_DIR / "score_ledger.json"


def _load_ledger() -> dict:
    """加载评分台账。"""
    if LEDGER_PATH.exists():
        try:
            with open(LEDGER_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"weeks": []}


def append_to_ledger(date_str: str, scan_results: list[dict]) -> dict:
    """追加本期扫描结果到评分台账，返回台账数据。"""
    ledger = _load_ledger()
    top10_entry = [
        {
            "name": r["name"],
            "score": r["score_auto"],
            "stage": r["stage"],
            "chg_pct": r["price_chg"],
            "fund_flow": r["fund_flow"],
            "weekly_flow": r.get("weekly_flow", 0.0),
            "consecutive_top10": r.get("consecutive_top10", 0),
            "rank": r["rank"],
        }
        for r in scan_results[:10]
    ]

    # 防止重复：更新或追加
    updated = False
    for w in ledger["weeks"]:
        if w["date"] == date_str:
            w["top10"] = top10_entry
            updated = True
            break
    if not updated:
        ledger["weeks"].append({"date": date_str, "top10": top10_entry})

    ledger["weeks"].sort(key=lambda w: w["date"])

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(LEDGER_PATH, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)

    return ledger


# detect_rotation_signals → 已迁移至 event_agent.py


# detect_exit_warnings → 已迁移至 event_agent.py


def load_prev_data(date_str: str) -> dict | None:
    """尝试加载前一个交易日的缓存数据。"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    for days_back in range(1, 8):
        prev_dt = dt - timedelta(days=days_back)
        prev_path = RAW_DIR / f"{prev_dt.strftime('%Y-%m-%d')}.json"
        if prev_path.exists():
            try:
                with open(prev_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
    return None


# ── 输出格式化 ─────────────────────────────────────────
def print_summary(results: list[dict]):
    """终端打印打分汇总。"""
    print("\n" + "=" * 72)
    print("  4D8I 打分汇总")
    print("=" * 72)

    header = f"{'行业':<10} {'A1':>3} {'A2':>3} {'B1':>3} {'B2':>3} {'C1':>3} {'C2':>3} {'D1':>3} {'D2':>3} {'得分':>6} {'阶段'}"
    print(header)
    print("-" * 72)

    for r in results:
        s = r["scores"]
        row = f"{r['industry']:<10}"
        for k in ["A1", "A2", "B1", "B2", "C1", "C2", "D1", "D2"]:
            v = s.get(k, 0)
            if v == -1:
                row += f" {'?':>3}"
            else:
                row += f" {v:>3}"
        row += f" {r['total_range']:>6}  {r['stage']}"
        print(row)

    print("-" * 72)

    # 打印详细信息
    print("\n指标明细：")
    for r in results:
        print(f"\n  [{r['industry']}]  {r['total_range']}  {r['stage']}")
        for k in ["A1", "A2", "B1", "B2", "C1", "C2", "D1", "D2"]:
            v = r["scores"].get(k, 0)
            d = r["details"].get(k, "")
            mark = "[+]" if v == 1 else ("[?]" if v == -1 else "[-]")
            print(f"    {mark} {k}: {d}")

    print("\n  说明: [+]=得分  [-]=未得分  [?]=需人工判断")
    print("  严格模式: 数据不足的指标一律记0，不做替代判断\n")


def print_scan_summary(scan_results: list[dict], signals: list[dict] | None = None):
    """终端打印全行业扫描汇总。"""
    print("\n" + "=" * 72)
    print("  全行业 3I 扫描排名（A1 动量 + A2 涨停 + B1 资金）")
    print("=" * 72)

    header = (f"{'#':>3} {'行业':<12} {'涨幅':>7} {'资金流':>10} "
              f"{'涨停':>4} {'A1':>3} {'A2':>3} {'B1':>3} {'得分':>4} {'阶段'}")
    print(header)
    print("-" * 72)

    for r in scan_results[:20]:
        flow_str = f"{r['fund_flow']:.2f}亿" if r["fund_flow"] is not None else "N/A"
        s = r["scores"]
        print(f"{r['rank']:>3} {r['name']:<12} {r['price_chg']:>+6.2f}% "
              f"{flow_str:>10} {r['limit_rise']:>4}"
              f" {s['A1']:>3} {s['A2']:>3} {s['B1']:>3} "
              f"{r['score_auto']:>4.1f}  {r['stage']}")

    if len(scan_results) > 20:
        print(f"  ... 共 {len(scan_results)} 个行业（显示 TOP20）")

    print("-" * 72)

    if signals:
        print("\n  轮动信号：")
        for sig in signals:
            print(f"    [{sig['type']}] {sig['industry']}: {sig['detail']}")
        print()
    else:
        print("\n  暂无轮动信号（需 ≥2 期数据）\n")


# ── V3.0 S2: 从 factor_agent / event_agent 回填，保持 from score import 兼容 ──
from factor_agent import (  # noqa: E402
    fetch_leader_board_institutional,
    fetch_block_trade_summary,
    fetch_margin_balance_surplus,
    scan_all_industries,
    compute_valuation_percentile,
    compute_cross_asset_signals,
    scan_concept_plates,
    compute_market_temperature,
    enrich_scan_results,
    filter_persistent_concepts,
)
from event_agent import (  # noqa: E402
    compute_multi_period_heat,
    detect_rotation_signals,
    detect_exit_warnings,
)


# ── 主函数 ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="CycleRadar 4D8I 确定性打分")
    parser.add_argument("--date", required=True, help="日期 YYYY-MM-DD")
    parser.add_argument("--from-cache", action="store_true", help="从缓存读取数据")
    parser.add_argument("--industries", nargs="+", default=["有色金属"],
                        help="要打分的行业列表")
    parser.add_argument("--event-keywords", nargs="*", default=None,
                        help="事件关键词（替代默认行业关键词搜新闻），如 '金价 5000' '有色金属 暴涨'")
    parser.add_argument("--scan-all", action="store_true",
                        help="全行业扫描模式（仅用 plates 数据的 A1/A2/B1）")
    args = parser.parse_args()

    date_str = args.date
    industries = args.industries
    event_keywords = args.event_keywords

    # ── scan-all 全行业扫描模式 ──
    if args.scan_all:
        plates = _load_plates(date_str, args.from_cache)
        if not plates:
            print("错误：未获取到行业数据")
            return

        scan_results = scan_all_industries(plates)
        ledger = append_to_ledger(date_str, scan_results)
        signals = detect_rotation_signals(ledger, date_str, scan_results)
        scan_path = save_scan(date_str, scan_results, signals)

        print(f"\n扫描结果已保存: {scan_path}")
        print(f"台账已更新: {LEDGER_PATH}")
        print_scan_summary(scan_results, signals)
        return

    # ── 标准模式：指定行业深度评分 ──

    # 加载或拉取全局数据
    cache_path = RAW_DIR / f"{date_str}.json"
    if args.from_cache and cache_path.exists():
        print(f"从缓存读取: {cache_path}")
        with open(cache_path, "r", encoding="utf-8") as f:
            global_data = json.load(f)
        # 确保历史快照也存在
        date_compact = date_str.replace("-", "")
        if not (HISTORY_DIR / f"plates_{date_compact}.json").exists():
            save_history(date_str, global_data)
            print(f"历史快照已补存: {HISTORY_DIR}")
    else:
        print(f"拉取 {date_str} MCP 数据...")
        global_data = fetch_all_data(date_str)

        # 拉取各行业详细数据
        for ind in industries:
            print(f"\n拉取 {ind} 详细数据...")
            global_data["by_industry"][ind] = fetch_industry_data(ind, date_str, event_keywords)

        # 保存原始数据
        raw_path = save_raw(date_str, global_data)
        print(f"\n原始数据已保存: {raw_path}")

        # 保存精简历史快照
        save_history(date_str, global_data)
        print(f"历史快照已保存: {HISTORY_DIR}")

    # 加载前期数据（用于环比）
    prev_data = load_prev_data(date_str)
    if prev_data:
        print(f"找到历史数据用于环比计算")
    else:
        print(f"无历史数据，环比指标将记 0（严格模式）")

    # 打分
    results = []
    for ind in industries:
        ind_data = global_data.get("by_industry", {}).get(ind, {})
        if not ind_data and not args.from_cache:
            print(f"\n拉取 {ind} 详细数据...")
            ind_data = fetch_industry_data(ind, date_str, event_keywords)
            global_data.setdefault("by_industry", {})[ind] = ind_data
            save_raw(date_str, global_data)

        result = score_industry(ind, global_data, ind_data, prev_data)
        results.append(result)

    # 保存打分结果
    score_path = save_scores(date_str, results)
    print(f"打分结果已保存: {score_path}")

    # 打印汇总
    print_summary(results)


if __name__ == "__main__":
    main()
