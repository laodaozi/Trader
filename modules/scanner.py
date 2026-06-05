"""
modules/scanner.py — Layer 3: 量化选股扫描

11 个模型：
  钱坤寻龙  (qkxl)  — 龙虎榜涨停 × 热点板块 × 资金净流入     强势短线
  主升狙击  (zsji)  — 横盘突破12月新高 × 量价确认             突破波段
  回调狙击  (htji)  — 前期涨停波段 × 回调企稳                低吸短线
  向上缺口  (xsqk)  — 跳空高开未回补 × 顺势                  顺势短线
  中线狙击  (zxji)  — MA60向上 × 站上MA5 × 放量              低吸中线
  波段雄鹰  (bdxy)  — 多头排列缩量休整后放量启动              波段
  弱转强    (rzq)   — 横盘弱势转强放量突破MA20               反转
  缩量地板  (sldb)  — 缩量到极致后放量启动                   底部
  涨停回踩  (ztht)  — 涨停后回踩MA5 不破不阴                 强势
  高位整理  (gwzl)  — 高位横盘后突破创新高                   突破
  均线共振  (jxgz)  — MA5/10/20粘合后金叉共振               趋势

候选池：龙虎榜（~30只）+ 热点行业龙头（~50只），去重后约 80-100 只。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from modules.mcp import mcp_call

# make `from modules.sectors import ...` work when run as __main__
sys.path.insert(0, str(Path(__file__).parent.parent))

# ── 行业龙头（同 CycleRadar，用于候选池）─────────────────
INDUSTRY_LEADERS = {
    "有色金属": ["601899", "603993"], "贵金属": ["600489", "600547"],
    "小金属":   ["002460", "600549"], "能源金属": ["002466", "603799"],
    "工业金属": ["600219", "601677"], "石油石化": ["600028", "601857"],
    "煤炭采选": ["601088", "601898"], "钢铁": ["600019", "000709"],
    "基础化工": ["600309", "002601"], "计算机": ["002415", "688111"],
    "电子":     ["002371", "603501"], "半导体": ["688981", "002049"],
    "通信":     ["600050", "000063"], "食品饮料行业": ["600519", "000858"],
    "家用电器": ["000651", "000333"], "汽车整车": ["002594", "600104"],
    "医药":     ["600276", "300760"], "化学制药": ["000963", "600196"],
    "银行":     ["601398", "600036"], "非银金融": ["601318", "600030"],
    "证券":     ["600030", "300059"], "建筑工程": ["601668", "601800"],
    "机械设备": ["002008", "300124"], "电力设备": ["300750", "601012"],
    "房地产":   ["001979", "600048"], "公用事业": ["600900", "003816"],
    "国防军工": ["600760", "601698"], "交通运输": ["601111", "002352"],
}




# ── K 线获取 ──────────────────────────────────────────────

def _get_kline(code: str, end_date: str, days: int = 70) -> tuple[str, list[dict]]:
    """
    获取个股日K线。
    返回 (stock_name, bars)，bars 按日期升序排列。
    stock_name 从 quote_name 字段提取，获取失败时返回空字符串。
    """
    start = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=days + 30)).strftime("%Y-%m-%d")
    data  = mcp_call("market_quote", "get_kline", {
        "keyword":           code,
        "start_date":        start,
        "end_date":          end_date,
        "kline_type":        1,
        "reinstatement_type": 2,  # 前复权
    })
    raw_bars = data if isinstance(data, list) else data.get("list", [])
    stock_name = raw_bars[0].get("quote_name", "") if raw_bars else ""
    result = []
    for b in raw_bars:
        result.append({
            "date":   b.get("trade_date", ""),
            "open":   float(b.get("open_price") or b.get("open") or 0),
            "high":   float(b.get("high_price") or b.get("high") or 0),
            "low":    float(b.get("low_price")  or b.get("low")  or 0),
            "close":  float(b.get("close_price") or b.get("close") or 0),
            "volume": float(b.get("trade_lots") or b.get("volume") or 0),
            "chg":    float(b.get("price_change_rate") or 0),  # 小数形式 0.05 = 5%
        })
    return stock_name, sorted(result, key=lambda x: x["date"])


# ── 技术指标工具 ───────────────────────────────────────────

def _ma(values: list[float], n: int) -> list[Optional[float]]:
    """简单移动平均，不足 n 期返回 None。"""
    result: list[Optional[float]] = []
    for i in range(len(values)):
        if i < n - 1:
            result.append(None)
        else:
            result.append(sum(values[i - n + 1: i + 1]) / n)
    return result


def _upper_shadow(bar: dict) -> float:
    """上影线占收盘价的比例（0-1）。"""
    top = max(bar["open"], bar["close"])
    return (bar["high"] - top) / max(bar["close"], 0.01)


def _lower_shadow(bar: dict) -> float:
    """下影线占收盘价的比例（0-1）。"""
    bot = min(bar["open"], bar["close"])
    return (bot - bar["low"]) / max(bar["close"], 0.01)


def _is_limit_up(bar: dict) -> bool:
    return bar["chg"] >= 0.097  # ≥9.7% 视为涨停


def _is_yiziboard(bar: dict) -> bool:
    """一字板：开盘即涨停，几乎没有交易区间。"""
    if not _is_limit_up(bar):
        return False
    return (bar["high"] - bar["low"]) / max(bar["close"], 0.01) < 0.005


def _is_st(name: str) -> bool:
    """ST / *ST 股票，涨跌幅限制 ±5%，排除在强势模型之外。"""
    return name.startswith("ST") or name.startswith("*ST")


def _vol_ratio(bars: list[dict], n: int = 5) -> float:
    """今日量比 = 今日成交量 / 近 n 日均量。"""
    if len(bars) < n + 1:
        return 0.0
    today   = bars[-1]["volume"]
    avg_vol = sum(b["volume"] for b in bars[-n - 1:-1]) / n
    return today / max(avg_vol, 1)


# ── 候选池构建 ────────────────────────────────────────────

def _build_candidate_pool(date: str) -> tuple[list[dict], list[str], set[str], dict[str, str]]:
    """
    返回 (leader_board_items, extra_codes, trending_names, hot_code_to_sector)
    leader_board_items 含龙虎榜完整数据（含席位明细）
    extra_codes        热点板块 LB 代码 + 所有行业龙头
    trending_names     热点板块 faucet 股票名（供钱坤寻龙用）
    hot_code_to_sector {code: 板块名}（供命中标注用）
    """
    leader_items = []
    try:
        raw = mcp_call("market_quote", "get_leader_board", {"trade_date": date})
        leader_items = raw if isinstance(raw, list) else []
    except Exception:
        pass

    hot_code_to_sector: dict[str, str] = {}
    trending_names:     set[str]       = set()
    hot_codes:          list[str]      = []

    try:
        from modules.sectors import get_active_sectors
        sectors_data = get_active_sectors(date, top_n=5)
        hot_codes      = sectors_data.get("hot_codes", [])
        trending_names = sectors_data.get("trending_names", set())
        for theme in sectors_data.get("themes", []):
            for code in theme.get("lb_codes", []):
                hot_code_to_sector[code] = theme["name"]
    except Exception:
        pass

    # 所有行业龙头（第一名）作为补充候选
    all_leader_codes = [codes[0] for codes in INDUSTRY_LEADERS.values()]
    extra = list(dict.fromkeys(hot_codes + all_leader_codes))

    return leader_items, extra, trending_names, hot_code_to_sector


# ── 模型 1：钱坤寻龙 ──────────────────────────────────────

def _model_qkxl(
    code: str,
    name: str,
    kline: list[dict],
    leader_item: Optional[dict],
    trending_names: set[str],
) -> Optional[dict]:
    """
    钱坤寻龙 — 强势短线
    条件：
    1. 当日涨停 or 涨幅 > 5%
    2. 热点主线 TOP5 板块（股票名出现在热点板块龙头中）
    3. 龙虎榜资金净流入 or 有游资/机构席位买入
    4. 量比 ≤ 10
    5. 剔除一字板
    """
    if len(kline) < 2:
        return None
    today = kline[-1]

    # 排除 ST 股
    if _is_st(name):
        return None

    # 1. 涨停 or 涨幅>5%
    if today["chg"] < 0.05:
        return None

    # 5. 剔除一字板
    if _is_yiziboard(today):
        return None

    # 4. 量比 ≤ 10
    vr = _vol_ratio(kline, 5)
    if vr > 10:
        return None

    reasons = [f"涨幅 {today['chg']*100:.1f}%", f"量比 {vr:.1f}x"]

    # 2. 热点板块（name 出现在趋势行业龙头中）
    in_hot = name in trending_names
    if in_hot:
        reasons.append("热点板块")

    # 3. 龙虎榜资金 / 游资席位
    net_buy = False
    if leader_item:
        net_amount = leader_item.get("total_net_amount", 0) or 0
        if net_amount > 0:
            net_buy = True
            reasons.append(f"净买入 {net_amount/1e8:.2f}亿")
        # 检查是否有知名游资/机构
        for seat in leader_item.get("list", []):
            firm = seat.get("security_firm_name", "")
            if any(kw in firm for kw in ["机构", "国泰君安", "中信", "华泰", "招商"]):
                if seat.get("buy_amount", 0) > seat.get("sell_amount", 0):
                    reasons.append(f"机构/知名营业部买入({firm})")
                    break

    # 命中判断：热点板块 OR 净买入 至少一项成立
    if not in_hot and not net_buy:
        return None

    return {
        "code": code, "name": name,
        "model": "钱坤寻龙", "label": "强势·短线",
        "entry": f"T+1 开盘附近（参考今收 {today['close']:.2f}）",
        "reasons": reasons,
        "note": "需人工确认：板块排名前5 + 是否为一字板后续涨停",
    }


# ── 模型 2：主升狙击 ──────────────────────────────────────

def _model_zsji(code: str, name: str, kline: list[dict]) -> Optional[dict]:
    """
    主升狙击 — 突破波段
    条件：
    1. 前2个月(42交易日)最高最低差 ≤ 20%（横盘整理）
    2. 今日涨幅 > 5%
    3. 今日收盘突破前240日最高收盘价
    4. 今日成交量超过前42日任意单日成交量
    5. 今日上影线 ≤ 2%
    6. 收盘站在 MA5/10/20/60/120 上方
    7. 近60日无跌停
    """
    if len(kline) < 130:  # 需要足够历史数据
        return None

    today  = kline[-1]
    past42 = kline[-43:-1]   # 前42个交易日
    past240 = kline[:-1]     # 所有历史（最多前240日）

    # 1. 横盘：前42日波幅 ≤ 20%
    if not past42:
        return None
    h42 = max(b["high"] for b in past42)
    l42 = min(b["low"]  for b in past42)
    if (h42 - l42) / max(l42, 0.01) > 0.20:
        return None

    # 2. 今日涨幅 > 5%
    if today["chg"] < 0.05:
        return None

    # 3. 突破前240日最高收盘
    max_close_240 = max(b["close"] for b in past240[-240:])
    if today["close"] <= max_close_240:
        return None

    # 4. 今日量超过前42日任意单日
    max_vol_42 = max(b["volume"] for b in past42)
    if today["volume"] <= max_vol_42:
        return None

    # 5. 上影线 ≤ 2%
    if _upper_shadow(today) > 0.02:
        return None

    # 6. MA 多头（至少 MA5/20/60 均在价格下方）
    closes = [b["close"] for b in kline]
    for n in [5, 20, 60, 120]:
        mas = _ma(closes, n)
        val = mas[-1]
        if val and today["close"] < val:
            return None

    # 7. 近60日无跌停
    for b in kline[-60:]:
        if b["chg"] <= -0.097:
            return None

    vol_ratio = today["volume"] / max_vol_42

    return {
        "code": code, "name": name,
        "model": "主升狙击", "label": "突破·波段",
        "entry": f"T+1 开盘附近（今收 {today['close']:.2f}）",
        "reasons": [
            f"涨幅 {today['chg']*100:.1f}%",
            f"突破前240日新高 {max_close_240:.2f}",
            f"量能 {vol_ratio:.1f}x 前42日最大量",
            f"横盘42日波幅 {(h42-l42)/l42*100:.1f}%",
        ],
        "note": "需人工确认：非ST、上市超6个月",
    }


# ── 模型 3：回调狙击 ──────────────────────────────────────

def _model_htji(code: str, name: str, kline: list[dict]) -> Optional[dict]:
    """
    回调狙击 — 低吸短线（简化版，基于条件一）
    条件：
    1. 近60日内有一波快速上涨：累计涨幅 > 35%，≥5个上涨日，≥2个换手涨停
    2. 上涨波结束后股价已回调
    3. 今日出现企稳小阳线（低开高走 or 平开高走）
       - 有下影线（试探支撑，>0 且 ≤5%），下影线 > 上影线（买回成功）
    4. 今日收盘站上MA5 or 上影线触碰MA5
    5. 今日量 ≤ 1.5× 前一日量
    """
    if len(kline) < 25:
        return None

    closes  = [b["close"] for b in kline]
    ma5_all = _ma(closes, 5)

    today   = kline[-1]
    prev    = kline[-2]
    ma5_t   = ma5_all[-1]
    ma5_p   = ma5_all[-2]

    # 3. 今日小阳线：close > open（假阳线也算：收盘 > 前收）
    if today["close"] <= today["open"] and today["chg"] <= 0:
        return None

    # 3. 企稳信号：下影线试探支撑后被买回
    #    下影线必须存在(>0)且大于上影线，下影线过长(>5%)视为异常波动
    lower_s = _lower_shadow(today)
    upper_s = _upper_shadow(today)
    if lower_s <= 0:
        return None
    if lower_s > 0.05:
        return None
    if upper_s >= lower_s:
        return None

    # 4. 站上MA5 or 上影线触MA5
    if ma5_t is None:
        return None
    touches_ma5 = (today["close"] >= ma5_t * 0.99) or (today["high"] >= ma5_t)
    if not touches_ma5:
        return None

    # 5. 量不放量：今日量 ≤ 1.5× 昨日
    if prev["volume"] > 0 and today["volume"] > prev["volume"] * 1.5:
        return None

    # 1. 寻找近60日的上涨波（向前60日找峰值）
    window = kline[-61:-1]
    if len(window) < 20:
        return None

    # 找到窗口内的高点（peak）和其之前的低点（trough）
    peak_idx = max(range(len(window)), key=lambda i: window[i]["close"])
    trough_idx = min(range(peak_idx + 1), key=lambda i: window[i]["close"])

    surge_bars = window[trough_idx: peak_idx + 1]
    if len(surge_bars) < 5:
        return None

    trough_close = window[trough_idx]["close"]
    peak_close   = window[peak_idx]["close"]
    total_rise   = (peak_close - trough_close) / max(trough_close, 0.01)

    if total_rise < 0.35:
        return None

    # 统计上涨日数和换手涨停数
    up_days    = sum(1 for b in surge_bars if b["chg"] > 0)
    limit_days = sum(1 for b in surge_bars if _is_limit_up(b) and not _is_yiziboard(b))

    if up_days < 5 or limit_days < 2:
        return None

    # 已回调：当前收盘 < 峰值 × 0.95
    if today["close"] >= peak_close * 0.95:
        return None

    pullback_pct = (peak_close - today["close"]) / peak_close * 100

    return {
        "code": code, "name": name,
        "model": "回调狙击", "label": "低吸·短线",
        "entry": f"T+1 开盘附近（今收 {today['close']:.2f}）",
        "reasons": [
            f"前期波段涨幅 {total_rise*100:.0f}%（{up_days}涨{limit_days}涨停）",
            f"已回调 {pullback_pct:.1f}% 从峰值 {peak_close:.2f}",
            "企稳小阳线（下影≤1% + 上影>下影）",
            f"{'站上MA5' if today['close'] >= ma5_t else '上影线触MA5'}",
        ],
        "note": f"峰值: {peak_close:.2f} | 当前MA5: {ma5_t:.2f}",
    }


# ── 模型 4：向上缺口 ──────────────────────────────────────

def _model_xsqk(code: str, name: str, kline: list[dict]) -> Optional[dict]:
    """
    向上缺口 — 顺势短线
    条件：
    1. 今日开盘跳空高开（open > 昨收）
    2. 截至收盘缺口未完全回补（low > 昨收）
    3. 今日盘中最高涨幅 > 5%（high vs 昨收）
    4. 若昨日为阴线 → 今日量 < 昨日量（缩量）
    5. 今日下影线 ≤ 2%
    6. 今日收盘涨幅 ≤ 8%（剔除涨停）
    """
    if len(kline) < 3:
        return None

    today  = kline[-1]
    prev   = kline[-2]

    # 排除 ST 股（其涨跌幅上限 ±5%，高开5%即是涨停）
    if _is_st(name):
        return None

    prev_close = prev["close"]

    # 1. 跳空高开
    gap_pct = (today["open"] - prev_close) / max(prev_close, 0.01)
    if gap_pct <= 0:
        return None

    # 2. 缺口未回补
    if today["low"] <= prev_close:
        return None

    # 3. 盘中最高涨幅 > 5%
    intraday_high_chg = (today["high"] - prev_close) / max(prev_close, 0.01)
    if intraday_high_chg <= 0.05:
        return None

    # 4. 前日为阴线 → 今日缩量
    prev_bearish = prev["close"] < prev["open"]
    if prev_bearish and today["volume"] >= prev["volume"]:
        return None

    # 5. 下影线 ≤ 2%
    if _lower_shadow(today) > 0.02:
        return None

    # 6. 收盘涨幅 ≤ 8%（剔除涨停）
    if today["chg"] > 0.08:
        return None

    return {
        "code": code, "name": name,
        "model": "向上缺口", "label": "顺势·短线",
        "entry": f"T+1 开盘附近（今收 {today['close']:.2f}）",
        "reasons": [
            f"跳空高开 {gap_pct*100:.1f}%（缺口 {prev_close:.2f}→{today['open']:.2f}）",
            f"缺口未回补（今低 {today['low']:.2f} > 昨收 {prev_close:.2f}）",
            f"盘中最高涨幅 {intraday_high_chg*100:.1f}%",
            f"下影线 {_lower_shadow(today)*100:.2f}%",
        ],
        "note": f"收盘涨幅 {today['chg']*100:.1f}% | 缺口支撑: {prev_close:.2f}",
    }


# ── 模型 5：中线狙击 ──────────────────────────────────────

def _model_zxji(code: str, name: str, kline: list[dict]) -> Optional[dict]:
    """
    中线狙击 — 低吸中线
    条件：
    1. MA60 趋势向上（今日MA60 > 30日前MA60）
    2. 今日收盘 > MA60
    3. 昨日收盘 < 昨日MA5（或昨日在MA5下方）
    4. 今日收盘 ≥ MA5（站上MA5）
    5. 今日成交量 ≥ 1.5× 前日（放量，≥2×最佳）
    注：PE/业绩/减持/解禁 条件需人工核查
    """
    if len(kline) < 70:
        return None

    closes   = [b["close"] for b in kline]
    ma5_all  = _ma(closes, 5)
    ma60_all = _ma(closes, 60)

    today = kline[-1]
    prev  = kline[-2]

    ma5_today  = ma5_all[-1]
    ma5_prev   = ma5_all[-2]
    ma60_today = ma60_all[-1]
    ma60_prev30 = ma60_all[-31]  # 30日前

    if ma5_today is None or ma60_today is None or ma60_prev30 is None:
        return None

    # 1. MA60 向上
    if ma60_today <= ma60_prev30:
        return None

    # 2. 收盘 > MA60
    if today["close"] <= ma60_today:
        return None

    # 3. 昨日在MA5下方
    if ma5_prev is None:
        return None
    if prev["close"] >= ma5_prev:
        return None

    # 4. 今日站上MA5
    if today["close"] < ma5_today:
        return None

    # 5. 放量（≥1.5×）
    if prev["volume"] > 0 and today["volume"] < prev["volume"] * 1.5:
        return None

    vol_ratio = today["volume"] / max(prev["volume"], 1)
    ma60_trend = (ma60_today - ma60_prev30) / ma60_prev30 * 100

    return {
        "code": code, "name": name,
        "model": "中线狙击", "label": "低吸·中线",
        "entry": f"T+1 开盘或之后回调低点（今收 {today['close']:.2f}）",
        "reasons": [
            f"MA60趋势向上 +{ma60_trend:.1f}%（30日）",
            f"收盘 {today['close']:.2f} > MA60 {ma60_today:.2f}",
            f"昨日在MA5下方，今日站上MA5 {ma5_today:.2f}",
            f"放量 {vol_ratio:.1f}x 前日",
        ],
        "note": "需人工核查：PE<100、净利润增长、近3月无减持/解禁",
    }


# ── 模型 6：波段雄鹰 ──────────────────────────────────────

def _model_bdxy(code: str, name: str, kline: list[dict]) -> Optional[dict]:
    """
    波段雄鹰 — 主升浪中缩量回踩MA10后放量再启动
    条件：
    1. MA5 > MA10 > MA20（多头排列）
    2. 近5日成交量均值 ≤ 前10日均量的 80%（缩量回调中）
    3. 今日收盘 ≥ MA10（站稳支撑）
    4. 今日成交量 > 昨日量（量能回升）
    5. 今日涨幅在 0.3%~8% 之间
    6. 近5日最多2个上涨日（确认是回调而非上涨途中）
    """
    if len(kline) < 25:
        return None

    closes   = [b["close"] for b in kline]
    ma5_all  = _ma(closes, 5)
    ma10_all = _ma(closes, 10)
    ma20_all = _ma(closes, 20)

    today = kline[-1]
    prev  = kline[-2]
    ma5   = ma5_all[-1]
    ma10  = ma10_all[-1]
    ma20  = ma20_all[-1]

    if ma5 is None or ma10 is None or ma20 is None:
        return None

    if not (ma5 > ma10 > ma20):
        return None

    vol_5d  = sum(b["volume"] for b in kline[-6:-1]) / 5
    vol_10d = sum(b["volume"] for b in kline[-11:-1]) / 10
    if vol_5d > vol_10d * 0.80:
        return None

    if today["close"] < ma10:
        return None
    if today["volume"] <= prev["volume"]:
        return None
    if not (0.003 <= today["chg"] <= 0.08):
        return None

    up_days = sum(1 for b in kline[-6:-1] if b["chg"] > 0)
    if up_days > 2:
        return None

    vol_ratio = today["volume"] / max(vol_5d, 1)
    return {
        "code": code, "name": name,
        "model": "波段雄鹰", "label": "波段·回踩再启",
        "entry": f"今收 {today['close']:.2f} 附近或T+1开盘",
        "reasons": [
            f"多头排列 MA5{ma5:.2f}>MA10{ma10:.2f}>MA20{ma20:.2f}",
            f"近5日缩量回调（5日均量/10日均量={vol_5d/vol_10d:.0%}）",
            f"今日站稳MA10 + 量能回升 {vol_ratio:.1f}x 5日均量",
            f"涨幅 {today['chg']*100:.1f}%，回调期上涨日 {up_days}/5",
        ],
        "note": f"MA10支撑: {ma10:.2f}",
    }


# ── 模型 7：弱转强 ────────────────────────────────────────

def _model_rzq(code: str, name: str, kline: list[dict]) -> Optional[dict]:
    """
    弱转强 — 前期横盘/弱势，今日量价共振突破MA20
    条件：
    1. 近20日累计涨幅 < 5%（相对弱势/横盘）
    2. 今日涨幅 > 3%
    3. 今日成交量 ≥ 5日均量的 2 倍
    4. 今日收盘 > MA20（突破关键压力）
    5. 近10日内曾出现 MA5 < MA10（前弱势确认）
    6. 非ST
    """
    if len(kline) < 25:
        return None
    if _is_st(name):
        return None

    closes   = [b["close"] for b in kline]
    ma5_all  = _ma(closes, 5)
    ma10_all = _ma(closes, 10)
    ma20_all = _ma(closes, 20)

    today = kline[-1]
    ma5   = ma5_all[-1]
    ma10  = ma10_all[-1]
    ma20  = ma20_all[-1]

    if ma20 is None or ma5 is None or ma10 is None:
        return None

    close_20d_ago = kline[-21]["close"] if len(kline) >= 21 else kline[0]["close"]
    gain_20d = (today["close"] - close_20d_ago) / max(close_20d_ago, 0.01)
    if gain_20d >= 0.05:
        return None

    if today["chg"] < 0.03:
        return None

    vol_5d = sum(b["volume"] for b in kline[-6:-1]) / 5
    vol_ratio = today["volume"] / max(vol_5d, 1)
    if vol_ratio < 2.0:
        return None

    if today["close"] <= ma20:
        return None

    had_weak = any(
        ma5_all[i] is not None and ma10_all[i] is not None and ma5_all[i] < ma10_all[i]
        for i in range(-11, -1)
    )
    if not had_weak:
        return None

    return {
        "code": code, "name": name,
        "model": "弱转强", "label": "反转·量价共振",
        "entry": f"T+1 开盘追入或回调 MA20 {ma20:.2f} 附近",
        "reasons": [
            f"近20日横盘弱势 涨幅{gain_20d*100:.1f}%",
            f"今日突破MA20 收盘{today['close']:.2f} > MA20{ma20:.2f}",
            f"放量 {vol_ratio:.1f}x 5日均量，涨幅{today['chg']*100:.1f}%",
            "近10日确认前弱势，今日金叉反转",
        ],
        "note": f"止损参考 MA20: {ma20:.2f}",
    }


# ── 模型 8：缩量地板 ──────────────────────────────────────

def _model_sldb(code: str, name: str, kline: list[dict]) -> Optional[dict]:
    """
    缩量地板 — 成交量萎缩至60日极低后放量大阳，底部能量释放
    条件：
    1. 近5日均量 ≤ 60日均量的 40%（严重缩量）
    2. 今日成交量 ≥ 近5日均量的 2 倍（底部放量）
    3. 今日大阳线（涨幅 > 2%）
    4. MA60 趋势向上（今日MA60 > 20日前MA60）
    5. 今日下影线 ≤ 1%
    """
    if len(kline) < 70:
        return None

    closes   = [b["close"] for b in kline]
    ma60_all = _ma(closes, 60)
    ma60     = ma60_all[-1]
    ma60_20d = ma60_all[-21]

    if ma60 is None or ma60_20d is None:
        return None

    today = kline[-1]

    vol_5d  = sum(b["volume"] for b in kline[-6:-1]) / 5
    vol_60d = sum(b["volume"] for b in kline[-61:-1]) / 60
    if vol_5d > vol_60d * 0.40:
        return None

    vol_ratio = today["volume"] / max(vol_5d, 1)
    if vol_ratio < 2.0:
        return None

    if today["chg"] < 0.02:
        return None
    if ma60 <= ma60_20d:
        return None
    if _lower_shadow(today) > 0.01:
        return None

    return {
        "code": code, "name": name,
        "model": "缩量地板", "label": "底部·能量释放",
        "entry": f"今收 {today['close']:.2f} 当日或T+1开盘",
        "reasons": [
            f"严重缩量：近5日均量仅为60日均量的{vol_5d/vol_60d:.0%}",
            f"今日放量大阳 {vol_ratio:.1f}x 近5日均量，涨幅{today['chg']*100:.1f}%",
            f"MA60向上（+{(ma60-ma60_20d)/ma60_20d*100:.1f}% 20日）",
            "下影线短，底部买盘积极",
        ],
        "note": f"MA60支撑: {ma60:.2f} | 需确认非业绩地雷",
    }


# ── 模型 9：涨停回踩 ──────────────────────────────────────

def _model_ztht(code: str, name: str, kline: list[dict]) -> Optional[dict]:
    """
    涨停回踩 — 近15日换手涨停后回踩MA5企稳，二次启动
    条件：
    1. 近15日内有至少1个换手涨停（非一字板）
    2. 今收 ≤ 涨停日收盘 × 0.95（已回调）
    3. 今日收盘在MA5 ±3% 范围内
    4. 今日下影线 ≥ 1%（支撑有效）
    5. 今日涨幅 ≥ 0%
    6. 非ST
    """
    if len(kline) < 20:
        return None
    if _is_st(name):
        return None

    today  = kline[-1]
    window = kline[-16:-1]

    limit_bar = None
    for b in reversed(window):
        if _is_limit_up(b) and not _is_yiziboard(b):
            limit_bar = b
            break
    if limit_bar is None:
        return None

    if today["close"] > limit_bar["close"] * 0.95:
        return None

    closes  = [b["close"] for b in kline]
    ma5_all = _ma(closes, 5)
    ma5     = ma5_all[-1]
    if ma5 is None:
        return None
    if abs(today["close"] - ma5) / ma5 > 0.03:
        return None
    if _lower_shadow(today) < 0.01:
        return None
    if today["chg"] < 0:
        return None

    pullback = (limit_bar["close"] - today["close"]) / limit_bar["close"]
    return {
        "code": code, "name": name,
        "model": "涨停回踩", "label": "短线·二次启动",
        "entry": f"今收 {today['close']:.2f} 附近，确认MA5支撑后介入",
        "reasons": [
            f"近15日换手涨停（涨停日收盘: {limit_bar['close']:.2f}）",
            f"已回调 {pullback*100:.1f}%，回踩MA5 {ma5:.2f}",
            f"今日下影线 {_lower_shadow(today)*100:.1f}%，支撑有效",
            f"涨幅 {today['chg']*100:.1f}%，止跌企稳",
        ],
        "note": f"止损参考 MA5下方3%: {ma5*0.97:.2f}",
    }


# ── 模型 10：高位整理 ─────────────────────────────────────

def _model_gwzl(code: str, name: str, kline: list[dict]) -> Optional[dict]:
    """
    高位整理 — 大涨后高位横盘10日，今日向上突破整理区间
    条件：
    1. 近30日内有快速拉升（30日高低差 > 25%）
    2. 近10日价格在窄幅区间（波幅 ≤ 8%）
    3. 今日向上突破近10日高点
    4. 今日成交量 ≥ 近10日均量的 1.5 倍
    5. 今日涨幅 > 2%，上影线 ≤ 2%
    """
    if len(kline) < 35:
        return None

    today    = kline[-1]
    window10 = kline[-11:-1]

    if not window10:
        return None

    h10 = max(b["high"] for b in window10)
    l10 = min(b["low"]  for b in window10)
    if (h10 - l10) / max(l10, 0.01) > 0.08:
        return None

    if today["close"] <= h10:
        return None

    vol_10d   = sum(b["volume"] for b in window10) / 10
    vol_ratio = today["volume"] / max(vol_10d, 1)
    if vol_ratio < 1.5:
        return None

    if today["chg"] < 0.02:
        return None
    if _upper_shadow(today) > 0.02:
        return None

    window30 = kline[-31:-11]
    if not window30:
        return None
    low30  = min(b["low"]  for b in window30)
    high30 = max(b["high"] for b in window30)
    surge  = (high30 - low30) / max(low30, 0.01)
    if surge < 0.25:
        return None

    return {
        "code": code, "name": name,
        "model": "高位整理", "label": "突破·整理再加速",
        "entry": f"今日突破收盘 {today['close']:.2f} 或T+1确认追入",
        "reasons": [
            f"前段拉升幅度 {surge*100:.0f}%（近30日高低差）",
            f"高位横盘10日（波幅{(h10-l10)/l10*100:.1f}%，区间 {l10:.2f}~{h10:.2f}）",
            f"今日向上突破 {h10:.2f}，放量 {vol_ratio:.1f}x 10日均量",
            f"涨幅 {today['chg']*100:.1f}%，上影线仅 {_upper_shadow(today)*100:.1f}%",
        ],
        "note": f"止损参考整理区间下沿: {l10:.2f}",
    }


# ── 模型 11：均线共振 ─────────────────────────────────────

def _model_jxgz(code: str, name: str, kline: list[dict]) -> Optional[dict]:
    """
    均线共振 — MA5/10/20 完整多头 + 三线密集 + 近期金叉
    条件：
    1. MA5 > MA10 > MA20（完整多头排列）
    2. MA5 与 MA20 差距 ≤ 5%（均线密集聚拢）
    3. 近8日内 MA5 从 MA10 下方穿越（金叉近期发生）
    4. 今日收盘 > MA5
    5. 今日成交量 ≥ 1.2× 5日均量
    6. 非ST
    """
    if len(kline) < 25:
        return None
    if _is_st(name):
        return None

    closes   = [b["close"] for b in kline]
    ma5_all  = _ma(closes, 5)
    ma10_all = _ma(closes, 10)
    ma20_all = _ma(closes, 20)

    today = kline[-1]
    ma5   = ma5_all[-1]
    ma10  = ma10_all[-1]
    ma20  = ma20_all[-1]

    if ma5 is None or ma10 is None or ma20 is None:
        return None

    if not (ma5 > ma10 > ma20):
        return None
    if (ma5 - ma20) / ma20 > 0.05:
        return None

    had_cross = False
    for i in range(-9, -1):
        if i - 1 < -len(ma5_all):
            continue
        v5_prev  = ma5_all[i - 1]
        v10_prev = ma10_all[i - 1]
        v5_cur   = ma5_all[i]
        v10_cur  = ma10_all[i]
        if None in (v5_prev, v10_prev, v5_cur, v10_cur):
            continue
        if v5_prev <= v10_prev and v5_cur > v10_cur:
            had_cross = True
            break
    if not had_cross:
        return None

    if today["close"] < ma5:
        return None

    vol_5d    = sum(b["volume"] for b in kline[-6:-1]) / 5
    vol_ratio = today["volume"] / max(vol_5d, 1)
    if vol_ratio < 1.2:
        return None

    spread = (ma5 - ma20) / ma20
    return {
        "code": code, "name": name,
        "model": "均线共振", "label": "趋势初启·共振",
        "entry": f"今收 {today['close']:.2f} 或回踩MA10 {ma10:.2f} 附近",
        "reasons": [
            f"MA5{ma5:.2f} > MA10{ma10:.2f} > MA20{ma20:.2f}（完整多头）",
            f"三线密集聚拢（MA5-MA20差距仅{spread*100:.1f}%）",
            "近8日内MA5金叉MA10，趋势刚启动",
            f"量能温和放大 {vol_ratio:.1f}x",
        ],
        "note": f"止损参考MA20: {ma20:.2f}",
    }


# ── 主扫描入口 ────────────────────────────────────────────

MODEL_FUNCS = {
    "qkxl": "钱坤寻龙",
    "zsji": "主升狙击",
    "htji": "回调狙击",
    "xsqk": "向上缺口",
    "zxji": "中线狙击",
    "bdxy": "波段雄鹰",
    "rzq":  "弱转强",
    "sldb": "缩量地板",
    "ztht": "涨停回踩",
    "gwzl": "高位整理",
    "jxgz": "均线共振",
}

# 主升狙击需要更长历史（约270日K线）
_KLINE_DAYS = {
    "zsji": 280,
}
_DEFAULT_KLINE_DAYS = 70


def scan(
    date: str,
    models: Optional[list[str]] = None,
    max_stocks: int = 100,
    verbose: bool = True,
) -> dict:
    """
    主接口：扫描当日候选池，输出各模型命中标的。

    返回：
    {
      "date": "2026-05-17",
      "candidate_count": 87,
      "hits": {
        "钱坤寻龙": [{"code": ..., "name": ..., "reasons": [...], ...}],
        ...
      },
      "summary": "扫描87只，钱坤寻龙3只，向上缺口2只..."
    }
    """
    active_models = models or list(MODEL_FUNCS.keys())

    if verbose:
        print(f"\n[scanner] 扫描 {date} | 模型: {', '.join(active_models)}")

    # ── 1. 构建候选池 ──
    if verbose:
        print("  [1/3] 构建候选池...")
    leader_items, extra_codes, trending_names, hot_code_to_sector = _build_candidate_pool(date)

    # leader board 提取 code+name+item 映射
    lb_map: dict[str, dict] = {}
    lb_codes: list[str] = []
    for item in leader_items:
        code = item.get("security_code", "")
        if code:
            lb_map[code] = item
            lb_codes.append(code)

    # 合并候选，龙虎榜优先
    all_codes = list(dict.fromkeys(lb_codes + extra_codes))[:max_stocks]

    # 过滤票池中已灭·出局的股票，避免无效扫描
    excluded: set[str] = set()
    _pool_path = Path(__file__).parent.parent / "data" / "pool.json"
    if _pool_path.exists():
        try:
            import json as _json
            with open(_pool_path, encoding="utf-8") as _f:
                _pool_data = _json.load(_f)
            excluded = {
                s["code"] for s in _pool_data.get("stocks", [])
                if s.get("lifecycle") == "灭·出局"
            }
        except Exception:
            pass
    if excluded:
        before = len(all_codes)
        all_codes = [c for c in all_codes if c not in excluded]
        if verbose:
            print(f"  候选池: {len(all_codes)} 只 (龙虎榜{len(lb_codes)}+行业龙头{len(extra_codes)}"
                  f" — 已过滤灭·出局 {before - len(all_codes)} 只)")
    if verbose and not excluded:
        print(f"  候选池: {len(all_codes)} 只 (龙虎榜{len(lb_codes)}+行业龙头{len(extra_codes)})")

    # ── 2. 逐只获取 K 线并应用模型 ──
    if verbose:
        print(f"  [2/3] 拉取 K 线并扫描...")

    hits: dict[str, list[dict]] = {MODEL_FUNCS[m]: [] for m in active_models}
    # 需要额外长历史的 codes 先处理
    long_history_models = [m for m in active_models if m in _KLINE_DAYS]
    short_models        = [m for m in active_models if m not in _KLINE_DAYS]

    for idx, code in enumerate(all_codes):
        if verbose and (idx + 1) % 20 == 0:
            print(f"    {idx+1}/{len(all_codes)} ...")

        # 决定需要多少天历史
        max_days = max(
            [_KLINE_DAYS[m] for m in long_history_models] or [0],
            default=0,
        )
        max_days = max(max_days, _DEFAULT_KLINE_DAYS)

        kl_name, kline = _get_kline(code, date, days=max_days)
        if not kline:
            continue

        # 获取名称：优先龙虎榜，其次 kline quote_name，最后用代码
        name = lb_map.get(code, {}).get("security_name") or kl_name or code
        lb_item = lb_map.get(code)

        # 应用各模型
        for m in active_models:
            model_name = MODEL_FUNCS[m]
            hit = None
            if m == "qkxl":
                hit = _model_qkxl(code, name, kline, lb_item, trending_names)
            elif m == "zsji":
                hit = _model_zsji(code, name, kline)
            elif m == "htji":
                hit = _model_htji(code, name, kline)
            elif m == "xsqk":
                hit = _model_xsqk(code, name, kline)
            elif m == "zxji":
                hit = _model_zxji(code, name, kline)
            elif m == "bdxy":
                hit = _model_bdxy(code, name, kline)
            elif m == "rzq":
                hit = _model_rzq(code, name, kline)
            elif m == "sldb":
                hit = _model_sldb(code, name, kline)
            elif m == "ztht":
                hit = _model_ztht(code, name, kline)
            elif m == "gwzl":
                hit = _model_gwzl(code, name, kline)
            elif m == "jxgz":
                hit = _model_jxgz(code, name, kline)

            if hit:
                hits[model_name].append(hit)

    # ── 3. 汇总 + 热点板块标注 ──
    # 为命中股票补充热点板块标签
    for stock_list in hits.values():
        for stock in stock_list:
            sector = hot_code_to_sector.get(stock["code"])
            if sector and "reasons" in stock:
                stock["reasons"].append(f"热点主线:{sector}")

    total_hits = sum(len(v) for v in hits.values())
    summary_parts = [f"{k} {len(v)}只" for k, v in hits.items() if v]
    summary = f"扫描{len(all_codes)}只 | {'、'.join(summary_parts) or '无命中'}"

    if verbose:
        print(f"  [3/3] {summary}")

    # ── 信号留存：追加到 data/scan_history.jsonl ──
    history_file = Path(__file__).parent.parent / "data" / "scan_history.jsonl"
    history_file.parent.mkdir(parents=True, exist_ok=True)
    with open(history_file, "a", encoding="utf-8") as fh:
        for model_key, stocks in hits.items():
            for s in stocks:
                fh.write(json.dumps({
                    "date":    date,
                    "model":   model_key,  # hits key 已是中文名
                    "code":    s["code"],
                    "name":    s["name"],
                    "reasons": s.get("reasons", []),
                }, ensure_ascii=False) + "\n")

    return {
        "date": date,
        "candidate_count": len(all_codes),
        "trending_sectors": list(trending_names)[:20],
        "hits": hits,
        "total_hits": total_hits,
        "summary": summary,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--models", nargs="*", choices=list(MODEL_FUNCS.keys()),
                        help="指定模型（默认全部）: " + " ".join(MODEL_FUNCS.keys()))
    args = parser.parse_args()
    result = scan(args.date, models=args.models)

    print("\n" + "=" * 60)
    for model_name, stocks in result["hits"].items():
        if not stocks:
            continue
        print(f"\n【{model_name}】{len(stocks)} 只")
        for s in stocks:
            print(f"  {s['code']} {s['name']}  {s['label']}")
            for r in s["reasons"]:
                print(f"    · {r}")
            if s.get("note"):
                print(f"    ⚠ {s['note']}")
    print("\n" + "=" * 60)
    print(f"  仅供参考，不构成投资建议")
    print("=" * 60)
