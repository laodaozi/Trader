"""
event_monitor.py — CycleRadar 事件驱动信号监测引擎

架构：事件识别 → 三维过滤(relevance/novelty/impact) → 波段窗口判断 → 信号输出
不做截面强弱排名，只捕捉"事件发生 → 确定性波段窗口打开"的机会
"""

from __future__ import annotations

import json
import re
import hashlib
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── 路径 ──────────────────────────────────────────────────────────────────────
_BASE = Path(__file__).resolve().parent.parent.parent
EVENT_LIBRARY_PATH = _BASE / "data" / "event_library.json"
EVENT_SIGNALS_PATH = _BASE / "data" / "event_signals.jsonl"
NOVELTY_CACHE_PATH = _BASE / "data" / "event_novelty_cache.json"


# ── 事件库加载 ────────────────────────────────────────────────────────────────
def load_event_library() -> list[dict]:
    with open(EVENT_LIBRARY_PATH, "r", encoding="utf-8") as f:
        lib = json.load(f)
    return lib["events"]


# ── Novelty 缓存（防重复触发）────────────────────────────────────────────────
def load_novelty_cache() -> dict:
    if NOVELTY_CACHE_PATH.exists():
        with open(NOVELTY_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_novelty_cache(cache: dict):
    with open(NOVELTY_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def novelty_key(event_id: str, content: str) -> str:
    """生成事件唯一指纹，用于去重"""
    fingerprint = f"{event_id}:{content[:100]}"
    return hashlib.md5(fingerprint.encode()).hexdigest()


def check_novelty(event_id: str, content: str, cooldown_hours: int) -> bool:
    """返回 True = 首次触发（新颖），False = 冷却期内重复"""
    cache = load_novelty_cache()
    key = novelty_key(event_id, content)
    now = datetime.now()
    if key in cache:
        last_seen = datetime.fromisoformat(cache[key])
        if now - last_seen < timedelta(hours=cooldown_hours):
            return False  # 冷却期内，不触发
    cache[key] = now.isoformat()
    save_novelty_cache(cache)
    return True  # 新颖，触发


# ── T1: 英伟达/云厂商财报超预期 ──────────────────────────────────────────────
def check_t1_nvda_earnings(price_change_pct: float, keywords: list[str]) -> dict | None:
    """
    price_change_pct: NVDA 或云厂商单日涨幅（百分比，如 12.5 表示 +12.5%）
    keywords: 财报电话会或新闻关键词列表
    """
    TRIGGER_THRESHOLD = 10.0
    RELEVANCE_KEYWORDS = {"AI", "data center", "networking", "optical", "InfiniBand", "capex"}

    if price_change_pct < TRIGGER_THRESHOLD:
        return None

    matched_keywords = [k for k in keywords if any(rk.lower() in k.lower() for rk in RELEVANCE_KEYWORDS)]
    if not matched_keywords:
        return None  # relevance 不足

    content = f"price_change={price_change_pct}%,keywords={matched_keywords[:3]}"
    if not check_novelty("T1_nvda_earnings", content, cooldown_hours=72):
        return None  # novelty 冷却中

    return {
        "event_id": "T1_nvda_earnings",
        "tier": 1,
        "signal_type": "event_driven",
        "trigger_detail": f"美股算力链涨幅 {price_change_pct:.1f}%，关键词命中: {matched_keywords[:3]}",
        "beneficiary_sectors": ["通信设备", "光学光电子", "半导体"],
        "window_hours": 36,
        "window_days": 5,
        "confidence": "very_high",
        "action": "关注次日A股开盘：中际旭创/天孚通信/新易盛/工业富联",
        "decay_watch": ["NVDA回调", "概念板块换手过大", "无业绩验证跟进"],
    }


# ── T2: 大宗商品供给冲击 ──────────────────────────────────────────────────────
def check_t2_commodity_shock(
    commodity: str, price_change_pct: float, cause: str
) -> dict | None:
    """
    commodity: 品种名，如 'copper'/'crude_oil'/'coal'
    price_change_pct: 单日涨幅
    cause: 触发原因描述，需包含供给端关键词
    """
    TRIGGER_THRESHOLD = 5.0
    SUPPLY_KEYWORDS = {"战争", "制裁", "停产", "罢工", "出口禁令", "管道", "矿山", "war", "sanction", "strike", "outage"}
    COMMODITY_MAP = {
        "copper":    {"sectors": ["工业金属"], "stocks": "紫金矿业/洛阳钼业/铜陵有色"},
        "crude_oil": {"sectors": ["石油石化"], "stocks": "中国石油/中国海油/中国石化"},
        "coal":      {"sectors": ["煤炭"],     "stocks": "中国神华/陕西煤业/中煤能源"},
    }

    if price_change_pct < TRIGGER_THRESHOLD:
        return None
    if commodity not in COMMODITY_MAP:
        return None

    cause_lower = cause.lower()
    is_supply_shock = any(kw.lower() in cause_lower for kw in SUPPLY_KEYWORDS)
    if not is_supply_shock:
        return None  # 纯需求拉动，不触发

    content = f"{commodity}:{price_change_pct}%:{cause[:50]}"
    if not check_novelty("T2_commodity_shock", content, cooldown_hours=72):
        return None

    info = COMMODITY_MAP[commodity]
    return {
        "event_id": "T2_commodity_supply_shock",
        "tier": 1,
        "signal_type": "event_driven",
        "trigger_detail": f"{commodity} 单日 +{price_change_pct:.1f}%，供给冲击: {cause[:60]}",
        "beneficiary_sectors": info["sectors"],
        "window_hours": 24,
        "window_days": 14,
        "confidence": "very_high",
        "action": f"关注当日/次日A股开盘：{info['stocks']}",
        "decay_watch": ["大宗价格回落", "保供稳价政策", "冲突缓和/复产"],
    }


# ── T3: 国家级产业政策首发 ────────────────────────────────────────────────────
# 政策主题 → 受益板块映射
_POLICY_MAP = {
    "购置税":    {"sectors": ["乘用车", "汽车零部件"],      "action": "比亚迪/长安汽车/华域汽车"},
    "以旧换新":  {"sectors": ["乘用车", "家电"],            "action": "长城汽车/格力电器/美的集团"},
    "化债":      {"sectors": ["银行", "建筑装饰"],          "action": "工商银行/中国建筑/中国铁建"},
    "低空经济":  {"sectors": ["航空装备", "通信设备"],      "action": "万丰奥威/中信海直/莱斯信息"},
    "大基金":    {"sectors": ["半导体设备", "半导体材料"],  "action": "北方华创/中微公司/华大九天"},
    "储能":      {"sectors": ["电力设备", "电池"],          "action": "宁德时代/阳光电源/天合光能"},
    "新能源":    {"sectors": ["电力设备", "电池"],          "action": "宁德时代/比亚迪/亿纬锂能"},
    # ── 2026 新增 ────────────────────────────────────────────────────────────
    "可再生能源": {"sectors": ["电力设备", "风电", "光伏", "储能"], "action": "宁德时代/阳光电源/金风科技/隆基绿能"},
    "十五五":    {"sectors": ["电力设备", "风电", "光伏", "储能"], "action": "宁德时代/阳光电源/金风科技/天合光能"},
    "风电":      {"sectors": ["风电", "电力设备"],          "action": "金风科技/明阳智能/运达股份"},
    "光伏":      {"sectors": ["光伏", "电力设备"],          "action": "隆基绿能/通威股份/天合光能"},
    "数据中心":  {"sectors": ["通信设备", "半导体", "电力"],  "action": "中兴通讯/工业富联/科华数据"},
    "算力":      {"sectors": ["通信设备", "半导体", "光模块"], "action": "中际旭创/天孚通信/工业富联"},
    "人工智能":  {"sectors": ["计算机", "半导体", "通信设备"], "action": "寒武纪/海光信息/中际旭创"},
    "PCB":       {"sectors": ["电子", "印制电路板"],         "action": "沪电股份/深南电路/生益科技"},
}

# 高权威来源（地方政府不触发）
_HIGH_AUTH_SOURCES = {"国务院", "全国人大", "财政部", "发改委", "工信部", "央行", "证监会"}


def check_t3_policy_catalyst(headline: str, source: str, amount_hint: str = "") -> dict | None:
    """
    headline: 快讯标题
    source:   来源机构
    amount_hint: 金额/规模关键词（可选，增强 relevance）
    """
    # relevance: 权威机构出现在标题或来源中
    is_auth = any(s in source or s in headline for s in _HIGH_AUTH_SOURCES)
    # 也接受权威媒体转载（新华社/人民日报/央广等视为可信二手源）
    _AUTH_MEDIA = {"新华社", "人民日报", "央广网", "经济日报", "中央电视台", "财联社", "证券时报", "上海证券报"}
    is_auth = is_auth or any(m in source for m in _AUTH_MEDIA)
    if not is_auth:
        return None

    # 匹配政策主题
    matched_theme = None
    for theme in _POLICY_MAP:
        if theme in headline:
            matched_theme = theme
            break
    if not matched_theme:
        return None

    # 增强词：有规模/金额/方案/实施才触发
    ENHANCE_WORDS = {"亿", "规模", "方案", "实施", "启动", "落地", "发布", "出台", "补贴", "减免", "支持", "推进", "目标", "力争", "万亿", "%", "规划", "印发", "通知", "部署"}
    has_enhance = any(w in headline + amount_hint for w in ENHANCE_WORDS)
    if not has_enhance:
        return None

    content = f"{matched_theme}:{headline[:80]}"
    if not check_novelty("T3_policy", content, cooldown_hours=168):  # 7天冷却
        return None

    info = _POLICY_MAP[matched_theme]
    return {
        "event_id": "T3_policy_catalyst",
        "tier": 1,
        "signal_type": "event_driven",
        "trigger_detail": f"政策首发: [{source}] {headline[:80]}",
        "policy_theme": matched_theme,
        "beneficiary_sectors": info["sectors"],
        "window_hours": 48,
        "window_days": 7,
        "confidence": "high",
        "action": f"关注首个交易日开盘：{info['action']}",
        "decay_watch": ["细则低于预期", "高频数据未见改善", "板块PE超历史75分位"],
    }


# ── T4: 行政限产/能耗双控 ────────────────────────────────────────────────────
_CURTAIL_PROVINCES = {"云南", "贵州", "四川", "内蒙古", "宁夏", "新疆"}
_CURTAIL_COMMODITIES = {
    "黄磷":  {"sectors": ["磷化工"], "action": "云天化/兴发集团/川恒股份"},
    "工业硅": {"sectors": ["工业硅", "有色金属"], "action": "合盛硅业/东方希望"},
    "电解铝": {"sectors": ["工业金属"], "action": "中国铝业/云铝股份/神火股份"},
    "多晶硅": {"sectors": ["电力设备"], "action": "通威股份/协鑫科技/大全能源"},
}


def check_t4_admin_curtailment(
    headline: str, province: str, commodity: str, price_change_pct: float
) -> dict | None:
    """
    headline:          快讯标题
    province:          限产省份
    commodity:         限产品种
    price_change_pct:  对应商品当日现货涨幅
    """
    if province not in _CURTAIL_PROVINCES:
        return None
    if commodity not in _CURTAIL_COMMODITIES:
        return None
    if price_change_pct < 5.0:
        return None

    CURTAIL_WORDS = {"限产", "停产", "能耗双控", "限电", "错峰"}
    if not any(w in headline for w in CURTAIL_WORDS):
        return None

    content = f"{province}:{commodity}:{headline[:60]}"
    if not check_novelty("T4_curtailment", content, cooldown_hours=48):
        return None

    info = _CURTAIL_COMMODITIES[commodity]
    return {
        "event_id": "T4_admin_curtailment",
        "tier": 1,
        "signal_type": "event_driven",
        "trigger_detail": f"行政限产: {province} {commodity} +{price_change_pct:.1f}%，{headline[:60]}",
        "beneficiary_sectors": info["sectors"],
        "window_hours": 48,
        "window_days": 10,
        "confidence": "high",
        "action": f"关注次日开盘：{info['action']}",
        "decay_watch": ["限产放松公告", "保供政策出台", "期货价格掉头"],
    }


# ── T5: 存储芯片价格止跌 ─────────────────────────────────────────────────────
def check_t5_storage_cycle(
    dram_mom_pct: float, nand_mom_pct: float, vendor_signal: str = ""
) -> dict | None:
    """
    dram_mom_pct: DRAM 合约价月环比变化（%），正数=涨价
    nand_mom_pct: NAND 合约价月环比变化（%）
    vendor_signal: 海外厂商信号描述（减产/涨价/补库）
    """
    dram_positive = dram_mom_pct > 0
    nand_positive = nand_mom_pct > 0
    has_vendor = any(w in vendor_signal for w in {"减产", "涨价", "补库", "cut", "hike"})

    # 需要至少两个维度同时满足
    signals_met = sum([dram_positive, nand_positive, has_vendor])
    if signals_met < 2:
        return None

    content = f"dram:{dram_mom_pct}%,nand:{nand_mom_pct}%,vendor:{vendor_signal[:30]}"
    if not check_novelty("T5_storage", content, cooldown_hours=336):  # 2周冷却
        return None

    return {
        "event_id": "T5_storage_cycle_turn",
        "tier": 2,
        "signal_type": "event_driven",
        "trigger_detail": f"存储价格止跌: DRAM {dram_mom_pct:+.1f}% / NAND {nand_mom_pct:+.1f}%，{vendor_signal[:40]}",
        "beneficiary_sectors": ["半导体", "消费电子"],
        "window_hours": 72,
        "window_days": 30,
        "confidence": "medium_high",
        "action": "关注：兆易创新/北京君正/澜起科技",
        "decay_watch": ["消费电子需求回落", "大厂重新增产", "估值提前透支"],
    }


# ── T6: 碳酸锂价格止跌 ──────────────────────────────────────────────────────
def check_t6_lithium_turn(
    li_5d_mom_pct: float, futures_5d_pct: float, downstream_improving: bool
) -> dict | None:
    """
    li_5d_mom_pct:          碳酸锂现货5日均价环比变化（%）
    futures_5d_pct:         广期所碳酸锂期货主力5日涨幅（%）
    downstream_improving:   下游排产是否改善
    """
    if li_5d_mom_pct <= 0:
        return None
    if futures_5d_pct < 10.0:
        return None
    if not downstream_improving:
        return None  # 纯价格反弹不触发，需要下游确认

    content = f"li:{li_5d_mom_pct}%,fut:{futures_5d_pct}%"
    if not check_novelty("T6_lithium", content, cooldown_hours=336):
        return None

    return {
        "event_id": "T6_lithium_price_turn",
        "tier": 2,
        "signal_type": "event_driven",
        "trigger_detail": f"碳酸锂止跌: 现货5日 {li_5d_mom_pct:+.1f}%，期货5日 {futures_5d_pct:+.1f}%，下游排产改善",
        "beneficiary_sectors": ["能源金属", "电池"],
        "window_hours": 72,
        "window_days": 20,
        "confidence": "medium_high",
        "action": "关注：天齐锂业/赣锋锂业/宁德时代",
        "decay_watch": ["价格跌破前低", "库存重新累积", "新能源车销量低于预期"],
    }


# ── T7: 生猪周期反转 ─────────────────────────────────────────────────────────
def check_t7_pig_cycle(
    pig_price_yuan_per_kg: float,
    consecutive_up_weeks: int,
    grain_ratio: float,
    sow_yoy_pct: float,
) -> dict | None:
    """
    pig_price_yuan_per_kg:  全国生猪均价（元/kg）
    consecutive_up_weeks:   连续上涨周数
    grain_ratio:            猪粮比（5.5以上盈利）
    sow_yoy_pct:            能繁母猪存栏同比变化（负数=去化）
    """
    price_ok = pig_price_yuan_per_kg >= 14.0  # 从亏损区间回升
    weeks_ok = consecutive_up_weeks >= 2
    ratio_ok = grain_ratio >= 5.5
    sow_ok = sow_yoy_pct <= -10.0  # 存栏同比去化>10%

    if not (price_ok and weeks_ok and ratio_ok and sow_ok):
        return None

    content = f"pig:{pig_price_yuan_per_kg},weeks:{consecutive_up_weeks},ratio:{grain_ratio}"
    if not check_novelty("T7_pig", content, cooldown_hours=720):  # 30天冷却
        return None

    return {
        "event_id": "T7_pig_cycle_turn",
        "tier": 2,
        "signal_type": "event_driven",
        "trigger_detail": f"猪周期反转: {pig_price_yuan_per_kg}元/kg，连涨{consecutive_up_weeks}周，猪粮比{grain_ratio:.1f}，母猪存栏{sow_yoy_pct:.1f}%",
        "beneficiary_sectors": ["养殖业", "饲料"],
        "window_hours": 168,
        "window_days": 60,
        "confidence": "medium_high",
        "action": "关注：牧原股份/温氏股份/新希望",
        "decay_watch": ["猪价回落至亏损区间", "二次育肥扰动", "冻品库存释放"],
    }


# ── 信号写出 ─────────────────────────────────────────────────────────────────
def emit_event_signal(signal: dict, run_id: str = ""):
    """写入 event_signals.jsonl，并自动触发传导图谱追踪"""
    record = {
        "ts": datetime.now().isoformat(),
        "run_id": run_id,
        "signal_date": datetime.now().strftime("%Y-%m-%d"),
        **signal,
    }
    with open(EVENT_SIGNALS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ── V8.3: 事件触发后自动追踪传导图谱 ──────────────────────────────────────
    _trace_transmission_for_signal(signal)

    return record


# ── V8.3 dedup: 同 event_id 多次触发只追踪一次传导图谱 ────────────────────────
_TRACED_EVENTS: set[str] = set()


def _trace_transmission_for_signal(signal: dict):
    """内部：加载传导图谱，追踪事件传导路径，写入 transmission_signals.jsonl"""
    event_id = signal["event_id"]
    if event_id in _TRACED_EVENTS:
        print(f"  [trace] {event_id} 已追踪，跳过", file=sys.stderr)
        return
    _TRACED_EVENTS.add(event_id)
    try:
        from core.graph.transmission_graph import TransmissionGraph
        from core.graph.event_evolution import trace_transmission, write_signals

        # 事件 ID 映射：event_monitor 命名 → graph 中的 event node ID
        EVENT_TO_GRAPH = {
            "T1_nvda_earnings":        "earnings_beat",
            "T2_commodity_supply_shock": "commodity_shock",
            "T3_policy_catalyst":      "sector_policy",
            "T4_admin_curtailment":    "supply_disruption",
            "T1_supply_demand_gap":    "price_hike",
            "T2_earnings_beat":        "earnings_beat",
            "T5_storage_cycle_turn":   "price_hike",
            "T6_lithium_price_turn":   "commodity_shock",
        }

        graph = TransmissionGraph.load()
        event_id = signal["event_id"]
        graph_event_id = EVENT_TO_GRAPH.get(event_id, event_id)

        tx_signals = trace_transmission(graph, graph_event_id)
        if tx_signals:
            write_signals(tx_signals)
            print(f"  [trace] {event_id} → {len(tx_signals)} 条传导信号", file=sys.stderr)
        else:
            print(f"  [trace] {event_id} 无匹配传导路径（graph: {graph_event_id}）", file=sys.stderr)
    except Exception as e:
        print(f"  [trace] {signal.get('event_id','?')} 追踪失败: {e}", file=sys.stderr)


# ── 真实数据接入：finstep news MCP ───────────────────────────────────────────
def fetch_news(keyword: str, topk: int = 10) -> list[dict]:
    """
    通过 finstep MCP HTTP API 拉取新闻，返回 [{title, content, time, source, url}]
    环境变量：MCP_SIGNATURE, MCP_BASE_URL（可选，默认 http://fintool-mcp.finstep.cn）
    """
    import os, requests
    from dotenv import load_dotenv
    load_dotenv(_BASE / ".env")

    sig = os.environ.get("MCP_SIGNATURE", "")
    base = os.environ.get("MCP_BASE_URL", "http://fintool-mcp.finstep.cn")
    url = f"{base}/news?signature={sig}"
    payload = {
        "jsonrpc": "2.0", "id": "1", "method": "tools/call",
        "params": {"name": "search_news", "arguments": {"keyword": keyword, "topk": topk}}
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        r.encoding = "utf-8"
        for line in r.text.splitlines():
            if line.startswith("data:"):
                outer = json.loads(line[5:])
                inner = json.loads(outer["result"]["content"][0]["text"])
                if inner.get("code") == 0:
                    return inner.get("data", [])
    except Exception as e:
        print(f"[fetch_news] 失败: {e}")
    return []


# ── 日常自动跑：拉新闻 → 过 T3/T4 检测器 → 输出信号 ─────────────────────────
def run_daily(run_id: str | None = None) -> list[dict]:
    """
    每日调用入口：
    1. 拉取政策/限产相关新闻
    2. 过 T3 政策催化 + T4 行政限产检测器
    3. 触发信号写入 event_signals.jsonl
    返回本次触发的信号列表
    """
    import uuid
    from datetime import date
    if run_id is None:
        run_id = f"daily-{date.today().isoformat()}-{uuid.uuid4().hex[:6]}"

    triggered = []

    # ── T3：国家级产业政策 ────────────────────────────────────────────────────
    T3_KEYWORDS = [
        "国务院 产业政策", "财政部 补贴", "发改委 新能源", "工信部 半导体",
        "购置税 减免", "以旧换新", "家电补贴", "新能源汽车",
        "发改委 可再生能源", "发改委 储能", "发改委 十五五", "能源局 规划",
        "发改委 算力", "工信部 人工智能", "发改委 数据中心",
    ]
    seen_t3 = set()
    for kw in T3_KEYWORDS:
        for item in fetch_news(kw, topk=5):
            title = item.get("title", "")
            source = item.get("source", "")
            url = item.get("url", "")
            key = title[:40]
            if key in seen_t3:
                continue
            seen_t3.add(key)
            sig = check_t3_policy_catalyst(headline=title, source=source)
            if sig:
                sig["news_url"] = url
                sig["news_time"] = item.get("time", "")
                record = emit_event_signal(sig, run_id=run_id)
                triggered.append(record)

    # ── T4：行政限产/能耗双控 ─────────────────────────────────────────────────
    T4_KEYWORDS = [
        "限电 限产", "能耗双控", "电解铝 停产", "煤炭 限产",
        "钢铁 压减产能", "水泥 错峰生产", "黄磷 限产", "工业硅 限产", "多晶硅 限产"
    ]
    # 标题关键词 → _CURTAIL_COMMODITIES 中的准确 key
    _COMMODITY_ALIAS = {
        "电解铝": "电解铝", "铝": "电解铝",
        "煤炭": "煤炭", "焦煤": "煤炭",
        "黄磷": "黄磷",
        "工业硅": "工业硅",
        "多晶硅": "多晶硅",
    }
    # 商品 → 价格新闻搜索关键词
    _COMMODITY_PRICE_KW = {
        "电解铝": "电解铝 现货 涨",
        "煤炭":   "动力煤 价格 涨",
        "黄磷":   "黄磷 价格 涨",
        "工业硅": "工业硅 现货 涨",
        "多晶硅": "多晶硅 现货 涨",
    }
    # 从最近新闻标题提取商品涨幅（正则匹配"涨X%"/"上涨X%"/"涨幅X%"）
    _PCT_RE = re.compile(r"(?:涨|上涨|涨幅|涨超|上涨超|涨逾)[^\d]*(\d+(?:\.\d+)?)\s*[%％]")

    def _fetch_commodity_price_pct(commodity: str) -> float:
        """从新闻标题提取商品近期涨幅，无数据返回 0.0"""
        kw = _COMMODITY_PRICE_KW.get(commodity, f"{commodity} 价格 上涨")
        items = fetch_news(kw, topk=5)
        for it in items:
            title = it.get("title", "")
            # 必须标题含商品名，避免误匹配其他品种
            if commodity not in title:
                continue
            m = _PCT_RE.search(title)
            if m:
                return float(m.group(1))
        return 0.0

    seen_t4 = set()
    _price_cache: dict[str, float] = {}   # 避免同一商品重复拉价格
    for kw in T4_KEYWORDS:
        for item in fetch_news(kw, topk=5):
            title = item.get("title", "")
            source = item.get("source", "")
            url = item.get("url", "")
            key = title[:40]
            if key in seen_t4:
                continue
            seen_t4.add(key)
            province = next((p for p in _CURTAIL_PROVINCES if p in title), "")
            commodity = next((v for k, v in _COMMODITY_ALIAS.items() if k in title), "")
            if not province or not commodity:
                continue  # 省份或品种不明确，跳过
            # 拉真实价格涨幅（带缓存，避免同品种重复 API 调用）
            if commodity not in _price_cache:
                _price_cache[commodity] = _fetch_commodity_price_pct(commodity)
            price_pct = _price_cache[commodity]
            sig = check_t4_admin_curtailment(
                headline=title, province=province, commodity=commodity,
                price_change_pct=price_pct,
            )
            if sig:
                sig["news_url"] = url
                sig["news_time"] = item.get("time", "")
                sig["price_pct_source"] = "news_extracted" if price_pct > 0 else "unavailable"
                record = emit_event_signal(sig, run_id=run_id)
                triggered.append(record)

    # ── T1：供需缺口（PCB/光模块/算力链） ────────────────────────────────────
    T1_KEYWORDS = [
        "PCB 涨价", "PCB 供不应求", "高端PCB 订单", "印制电路板 缺货",
        "光模块 涨价", "光芯片 供给", "算力 供不应求", "HBM 缺货",
    ]
    # 供需关键词：价格/产能/订单方面的信号
    _SUPPLY_DEMAND_WORDS = {"涨价", "缺货", "供不应求", "订单", "价格上涨", "产能不足", "排期", "锁单", "交货期"}
    _T1_SECTOR_MAP = {
        "PCB": {"sectors": ["印制电路板", "电子"],        "action": "沪电股份/深南电路/生益科技"},
        "光模块": {"sectors": ["光学光电子", "通信设备"], "action": "中际旭创/天孚通信/新易盛"},
        "光芯片": {"sectors": ["光学光电子", "半导体"],   "action": "源杰科技/仕佳光子/光迅科技"},
        "HBM":    {"sectors": ["半导体", "存储"],         "action": "长鑫存储/澜起科技/兆易创新"},
    }
    seen_t1 = set()
    for kw in T1_KEYWORDS:
        for item in fetch_news(kw, topk=5):
            title = item.get("title", "")
            source = item.get("source", "")
            url = item.get("url", "")
            key = title[:40]
            if key in seen_t1:
                continue
            seen_t1.add(key)
            # relevance：标题含供需信号词
            if not any(w in title for w in _SUPPLY_DEMAND_WORDS):
                continue
            # 匹配品种：只看标题，不靠搜索词 kw 作弊
            matched = next((k for k in _T1_SECTOR_MAP if k in title), None)
            if not matched:
                continue
            content = f"T1_supply_demand:{matched}:{title[:80]}"
            if not check_novelty("T1_supply_demand", content, cooldown_hours=48):
                continue
            info = _T1_SECTOR_MAP[matched]
            sig = {
                "event_id": "T1_supply_demand_gap",
                "tier": 1,
                "signal_type": "event_driven",
                "trigger_detail": f"供需缺口: [{source}] {title[:80]}",
                "product": matched,
                "beneficiary_sectors": info["sectors"],
                "window_hours": 36,
                "window_days": 14,
                "confidence": "high",
                "action": f"关注次日A股开盘：{info['action']}",
                "decay_watch": ["价格回落", "新产能投放", "需求端放缓"],
                "news_url": url,
                "news_time": item.get("time", ""),
            }
            record = emit_event_signal(sig, run_id=run_id)
            triggered.append(record)

    # ── T2：业绩预告超预期（净利预增 >200%） ─────────────────────────────────
    T2_KEYWORDS = [
        "业绩预告 预增", "净利润 预增", "净利 同比增长", "业绩大增",
        "扭亏为盈", "净利润增长 倍", "预计净利润增长"
    ]
    _T2_HIGH_GROWTH_WORDS = {"预增", "大增", "倍", "翻倍", "扭亏", "同比增长"}
    _GROWTH_RE = re.compile(r"(\d{3,})[%％]")  # 匹配 200%+ 的增幅数字
    seen_t2 = set()
    for kw in T2_KEYWORDS:
        for item in fetch_news(kw, topk=5):
            title = item.get("title", "")
            source = item.get("source", "")
            url = item.get("url", "")
            key = title[:40]
            if key in seen_t2:
                continue
            seen_t2.add(key)
            # 过滤批量公告（"X家公司公布"类标题不是单标的信号）
            if re.search(r"\d+家公司", title):
                continue
            # relevance：标题含高增长词
            if not any(w in title for w in _T2_HIGH_GROWTH_WORDS):
                continue
            # impact：尝试提取增幅数字，要求 ≥200%
            growth_match = _GROWTH_RE.search(title)
            growth_pct = int(growth_match.group(1)) if growth_match else 0
            # 无具体数字但含"倍"/"扭亏"也接受
            is_high_impact = growth_pct >= 200 or any(w in title for w in {"倍", "扭亏为盈", "大幅预增"})
            if not is_high_impact:
                continue
            # 权威来源过滤（财联社/证券时报/交易所公告优先）
            _T2_AUTH = {"财联社", "证券时报", "上海证券报", "中国证券报", "深交所", "上交所", "21世纪"}
            if not any(m in source for m in _T2_AUTH):
                continue
            content = f"T2_earnings:{title[:80]}"
            if not check_novelty("T2_earnings_beat", content, cooldown_hours=72):
                continue
            sig = {
                "event_id": "T2_earnings_beat",
                "tier": 1,
                "signal_type": "event_driven",
                "trigger_detail": f"业绩超预期: [{source}] {title[:80]}" + (f"，增幅 {growth_pct}%" if growth_pct else ""),
                "beneficiary_sectors": ["相关个股"],
                "window_hours": 24,
                "window_days": 5,
                "confidence": "medium_high",
                "action": "关注公告个股次日开盘，需结合涨幅判断是否已透支",
                "decay_watch": ["股价当日已大幅上涨", "增速主要来自非经常性损益", "行业景气无持续性"],
                "growth_pct": growth_pct,
                "news_url": url,
                "news_time": item.get("time", ""),
            }
            record = emit_event_signal(sig, run_id=run_id)
            triggered.append(record)

    return triggered


# ── CLI 入口（手动测试用）────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CycleRadar 事件监测引擎")
    parser.add_argument("--demo", action="store_true", help="运行演示案例")
    parser.add_argument("--list", action="store_true", help="列出事件库")
    parser.add_argument("--run", action="store_true", help="拉取真实新闻跑 T3/T4 检测器")
    args = parser.parse_args()

    if args.list:
        events = load_event_library()
        print(f"\n{'─'*60}")
        print(f"CycleRadar 事件库 — 共 {len(events)} 个事件类型")
        print(f"{'─'*60}")
        for e in events:
            tier_label = "Tier-1 快反" if e["tier"] == 1 else "Tier-2 拐点"
            print(f"  [{tier_label}] {e['id']:30s}  {e['name']}")
        print()

    if args.demo:
        print("\n── 演示：触发 T1 英伟达财报超预期 ──")
        sig = check_t1_nvda_earnings(
            price_change_pct=24.0,
            keywords=["AI data center revenue up 400%", "optical networking demand surge"]
        )
        if sig:
            record = emit_event_signal(sig, run_id="demo-001")
            print(json.dumps(record, ensure_ascii=False, indent=2))

        print("\n── 演示：触发 T3 政策催化 ──")
        sig2 = check_t3_policy_catalyst(
            headline="财政部宣布延续新能源汽车购置税减免政策至2025年，规模约千亿",
            source="财政部",
        )
        if sig2:
            record2 = emit_event_signal(sig2, run_id="demo-001")
            print(json.dumps(record2, ensure_ascii=False, indent=2))

        print("\n── 演示：T2 供给冲击（需求拉动，不触发）──")
        sig3 = check_t2_commodity_shock(
            commodity="copper", price_change_pct=6.0, cause="需求旺盛推动铜价上涨"
        )
        print(f"  结果: {'触发' if sig3 else '不触发（非供给冲击）'}")

        print("\n── 演示：T2 供给冲击（供给端，触发）──")
        sig4 = check_t2_commodity_shock(
            commodity="copper", price_change_pct=6.0,
            cause="巴拿马Cobre Panama铜矿工人罢工停产，供给中断"
        )
        if sig4:
            record4 = emit_event_signal(sig4, run_id="demo-001")
            print(json.dumps(record4, ensure_ascii=False, indent=2))

    if args.run:
        signals = run_daily()
        print(f"\n[run_daily] 触发信号 {len(signals)} 条")
        for s in signals:
            print(f"  [{s['event_id']:30s}] {s['trigger_detail'][:70]}")
