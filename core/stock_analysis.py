"""
stock_analysis.py — CycleRadar 个股深度分析模块

用法：
  python stock_analysis.py --stocks 688256 300474 --date 2026-03-01
  python stock_analysis.py --stocks 601899 603993 --date 2026-03-01 --from-cache

输出：
  - 终端打印每只股票的分析摘要
  - 底稿/raw/YYYY-MM-DD_stocks.json — 原始数据

依赖：requests, akshare (optional, for NX point calculation)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

from dotenv import load_dotenv
load_dotenv()

# Windows 终端 UTF-8 输出
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── 配置（复用 score.py 同源） ────────────────────────
BASE_URL = "http://fintool-mcp.finstep.cn"
SIGNATURE = os.environ.get("MCP_SIGNATURE", "")
PROJECT_ROOT = Path(__file__).parent
RAW_DIR = PROJECT_ROOT / "底稿" / "raw"

# OHLC 缓存目录（兼容 ECS 数据路径）
_cycleradar_data_dir = os.environ.get("CYCLERADAR_DATA_DIR", "")
if _cycleradar_data_dir:
    OHLC_CACHE_DIR = Path(_cycleradar_data_dir) / "ohlc_cache"
else:
    _default_data_dir = PROJECT_ROOT.parent / "data" if PROJECT_ROOT.name == "core" else PROJECT_ROOT / "data"
    OHLC_CACHE_DIR = _default_data_dir / "ohlc_cache"

# 股票代码 → 名称（龙头 + 微信信源常见标的，按需扩展）
STOCK_NAMES = {
    # ── 行业龙头 ──
    "601899": "紫金矿业", "603993": "洛阳钼业",
    "002415": "海康威视", "688111": "金山办公",
    "601088": "中国神华", "601898": "中煤能源",
    "600019": "宝钢股份", "000709": "河钢股份",
    "002371": "北方华创", "603501": "韦尔股份",
    "600519": "贵州茅台", "000858": "五粮液",
    "600276": "恒瑞医药", "300760": "迈瑞医疗",
    "601318": "中国平安", "600030": "中信证券",
    "601398": "工商银行", "600036": "招商银行",
    "600900": "长江电力", "003816": "中国广核",
    "300750": "宁德时代", "601012": "隆基绿能",
    "688256": "寒武纪",   "300474": "景嘉微",
    "688041": "海光信息", "002230": "科大讯飞",
    "300059": "东方财富", "002236": "大华股份",
    "600489": "中金黄金", "600547": "山东黄金",
    "002460": "赣锋锂业", "600549": "厦门钨业",
    "600028": "中国石化", "601857": "中国石油",
    "600938": "中国海油",
    "600050": "中国联通", "000063": "中兴通讯",
    "600760": "中航沈飞", "601698": "中国卫通",
    "001979": "招商蛇口", "600048": "保利发展",
    "600104": "上汽集团",
    "000651": "格力电器", "000333": "美的集团",
    "300498": "温氏股份", "002714": "牧原股份",
    "600309": "万华化学", "002601": "龙蟒佰利",
    # ── 微信信源常见标的 ──
    "600026": "中远海能", "601872": "招商轮船", "600069": "招商南油",
    "300443": "金雷股份", "002353": "杰瑞股份", "002930": "宏川智慧",
    "002278": "神开股份", "002490": "山东墨龙", "603393": "新天然气",
    "000059": "华锦股份", "600618": "氯碱化工", "000731": "四川美丰",
    "600989": "宝丰能源", "002493": "荣盛石化", "002083": "孚日股份",
    "600722": "金牛化工", "002521": "齐峰新材",
    "002064": "华峰超纤", "002001": "新和成", "600299": "安迪苏",
    "002532": "天山铝业", "000807": "云铝股份",
    "000065": "北方国际", "600583": "海油工程",
    "002466": "天齐锂业", "603799": "华友钴业",
    "600585": "海螺水泥", "601633": "长城汽车",
    "601225": "陕西煤业", "601800": "中国交建",
    "002475": "立讯精密", "002049": "紫光国微",
    "600588": "用友网络", "002555": "三七互娱",
    "601985": "中国核电", "000776": "广发证券",
}


# ── MCP 调用（与 score.py 一致） ─────────────────────
def mcp_call(service: str, tool: str, arguments: dict) -> dict:
    """调用 Finstep MCP，返回解析后的 data 字段。"""
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
    try:
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
                        return parsed.get("data", parsed)
                    except json.JSONDecodeError:
                        return {"_raw_text": text}
                sc = result.get("structuredContent", {})
                if sc:
                    return sc.get("data", sc)
                return result
        return {}
    except Exception as e:
        return {"_error": str(e)}


# ── 个股分析 ─────────────────────────────────────────
def analyze_stock(code: str, date_str: str) -> dict:
    """
    对单只股票进行多维分析。
    返回 dict 包含：估值、资金、新闻、NX趋势、催化剂匹配（待人工）、风险（待人工）
    """
    name = get_stock_name(code)
    result = {
        "code": code,
        "name": name,
        "date": date_str,
    }

    # 1. 估值：PE_TTM, PB
    print(f"  [{code}] 拉取估值数据...")
    val_data = mcp_call("company_info", "get_valuation_metrics_daily", {
        "keyword": code,
        "start_date": date_str,
        "end_date": date_str,
    })
    result["valuation"] = _parse_valuation(val_data)

    # 2. 资金：近 5 日主力净流向
    print(f"  [{code}] 拉取近5日资金流向...")
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    start_5d = (dt - timedelta(days=7)).strftime("%Y-%m-%d")  # 多取2天覆盖周末
    flow_data = mcp_call("market_quote", "get_net_flow_list", {
        "keyword": code,
        "start_date": start_5d,
        "end_date": date_str,
    })
    result["fund_flow"] = _parse_fund_flow(flow_data)

    # 3. 新闻：公司相关近期新闻
    print(f"  [{code}] 搜索公司新闻...")
    # 用股票名称搜索（英文环境下用代码）
    search_kw = name if name != code else code
    news_data = mcp_call("news", "search_news", {
        "query": search_kw,
        "topk": 5,
    })
    result["news"] = _parse_news(news_data)

    # 4. NX 点 + 弹性（AKShare OHLC）
    print(f"  [{code}] 计算 NX 点...")
    ohlc = get_stock_ohlc_akshare(code, date_str)
    result["nx"] = compute_nx_signal(ohlc)
    if ohlc:
        result["close_price"] = round(ohlc[-1]["close"], 2)

    # 5. 催化剂匹配度（人工填写）
    result["catalyst_match"] = "?"  # 需人工确认

    # 6. 风险点（人工填写）
    result["risk"] = "?"  # 需人工确认

    return result


def _parse_valuation(data) -> dict:
    """解析估值数据。"""
    result = {"pe_ttm": None, "pb": None, "ps_ttm": None, "pcf_ttm": None}
    if isinstance(data, list) and data:
        day = data[0]
    elif isinstance(data, dict) and not data.get("_error"):
        day = data
    else:
        if data is not None and isinstance(data, dict):
            result["_error"] = str(data.get("_error", "无数据"))
        else:
            result["_error"] = "无数据"
        return result

    for field in ["pe_ttm", "pb", "ps_ttm", "pcf_ttm"]:
        val = day.get(field)
        if val is not None:
            try:
                result[field] = float(val)
            except (TypeError, ValueError):
                pass
    return result


def _parse_fund_flow(data) -> dict:
    """解析资金流向数据。"""
    result = {
        "days": 0,
        "total_major_net": 0.0,
        "daily": [],
        "trend": "未知",
    }
    records = []
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict) and "data" in data:
        records = data["data"] if isinstance(data["data"], list) else []

    for r in records:
        mf = r.get("major_net_flow_in", 0)
        date = r.get("date", "")
        try:
            if isinstance(mf, str):
                mf_val = float(mf.replace("亿元", "").replace("亿", "")
                               .replace("万元", "").replace("万", "").strip())
            else:
                mf_val = float(mf) if mf else 0.0
        except (TypeError, ValueError):
            mf_val = 0.0
        result["daily"].append({"date": date, "major_net": mf_val})
        result["total_major_net"] += mf_val

    result["days"] = len(result["daily"])
    if result["days"] >= 2:
        recent_half = result["daily"][result["days"] // 2:]
        early_half = result["daily"][:result["days"] // 2]
        recent_avg = sum(d["major_net"] for d in recent_half) / len(recent_half)
        early_avg = sum(d["major_net"] for d in early_half) / len(early_half) if early_half else 0
        if recent_avg > early_avg and result["total_major_net"] > 0:
            result["trend"] = "加速流入"
        elif result["total_major_net"] > 0:
            result["trend"] = "持续流入"
        elif result["total_major_net"] < 0:
            result["trend"] = "净流出"
        else:
            result["trend"] = "平衡"
    elif result["days"] == 1:
        result["trend"] = "流入" if result["total_major_net"] > 0 else "流出"
    return result


def _parse_news(data) -> list[dict]:
    """解析新闻数据。"""
    news_list = []
    records = []
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict) and "data" in data:
        records = data["data"] if isinstance(data["data"], list) else []

    for n in records[:5]:
        title = n.get("title", "")
        source = n.get("source", "")
        pub_time = n.get("time", "") or n.get("publish_time", "")
        if title:
            news_list.append({
                "title": title,
                "source": source,
                "time": pub_time,
            })
    return news_list


# ── NX 点 + 弹性分析 ─────────────────────────────────

def get_stock_ohlc_akshare(code: str, end_date: str, days: int = 40) -> list[dict]:
    """用 AKShare 获取个股日 K 线 OHLC 数据。

    返回 [{date, open, high, low, close}, ...]，按日期升序。
    需要至少 20 根 K 线才能计算 NX 点，多取 40 天覆盖节假日。

    降级链：东方财富(2重试) → 腾讯直接(3退避) → 本地缓存(≤72h)
    """
    try:
        import akshare as ak
    except ImportError:
        print(f"  ⚠ akshare 未安装，改用腾讯/缓存 OHLC 降级源")
        ak = None

    dt = datetime.strptime(end_date, "%Y-%m-%d")
    start_dt = dt - timedelta(days=days + 20)  # 多取20天覆盖节假日/周末

    # ── 主源：东方财富（push2his.eastmoney.com）──
    if ak is not None:
        for attempt in range(2):
            try:
                df = ak.stock_zh_a_hist(
                    symbol=code,
                    period="daily",
                    start_date=start_dt.strftime("%Y%m%d"),
                    end_date=dt.strftime("%Y%m%d"),
                    adjust="qfq",
                )
                if df is not None and not df.empty:
                    rows = []
                    for _, r in df.iterrows():
                        rows.append({
                            "date": str(r["日期"]),
                            "open": float(r["开盘"]),
                            "high": float(r["最高"]),
                            "low": float(r["最低"]),
                            "close": float(r["收盘"]),
                        })
                    _save_ohlc_cache(code, rows, "eastmoney")
                    return rows
            except Exception:
                if attempt == 0:
                    time.sleep(1.0)

    # ── 降级源：腾讯（web.ifzq.gtimg.cn 直连，不走 akshare proxy）──
    result = _get_ohlc_from_tencent_direct(code, start_dt, dt)
    if result:
        _save_ohlc_cache(code, result, "tencent")
        return result

    # ── 终极降级：本地缓存 ──
    cached = _load_ohlc_cache(code, max_age_hours=72)
    if cached:
        print(f"  [{code}] ⚠ 使用缓存 OHLC 数据（{cached['cache_age_hours']:.0f}h 前）")
        return cached["rows"]

    return cached.get("rows", []) if cached else []


def _save_ohlc_cache(code: str, rows: list[dict], source: str) -> None:
    """保存最后一次可用 OHLC 数据，供行情源瞬断时兜底。"""
    if not rows:
        return
    try:
        OHLC_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = OHLC_CACHE_DIR / f"{code}.json"
        payload = {
            "code": code,
            "source": source,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "rows": rows,
        }
        tmp_path = path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        tmp_path.replace(path)
    except OSError:
        pass


def _load_ohlc_cache(code: str, max_age_hours: int = 72) -> dict | None:
    """读取最近可用 OHLC 缓存；超过 max_age_hours 或数据不足则忽略。"""
    path = OHLC_CACHE_DIR / f"{code}.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        fetched_at = datetime.fromisoformat(payload.get("fetched_at", ""))
        age_hours = (datetime.now() - fetched_at).total_seconds() / 3600
        rows = payload.get("rows", [])
        if age_hours > max_age_hours or len(rows) < 20:
            return None
        return {
            "rows": rows,
            "source": payload.get("source", "cache"),
            "cache_age_hours": age_hours,
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _get_ohlc_from_tencent_direct(code: str, start_dt, end_dt) -> list[dict]:
    """直连腾讯行情 API（HTTPS），带 retry + exponential backoff。

    akshare 的 stock_zh_a_hist_tx() 走 proxy.finance.qq.com，ECS 上被限流。
    这里直接用 requests 调 web.ifzq.gtimg.cn，可控重试策略。
    """
    import requests

    if code.startswith(("6", "9")):
        tx_code = "sh" + code
    else:
        tx_code = "sz" + code

    url = (
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={tx_code},day,{start_dt.strftime('%Y-%m-%d')},{end_dt.strftime('%Y-%m-%d')},640,qfq"
    )

    for attempt in range(3):
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()
            if data.get("code") != 0:
                raise ValueError(f"API code={data.get('code')}, msg={data.get('msg')}")

            stock_data = data.get("data", {}).get(tx_code, {})
            klines = stock_data.get("qfqday") or stock_data.get("day") or []
            if not klines:
                if attempt < 2:
                    time.sleep(1.0 * (2 ** attempt))
                    continue
                return []

            rows = []
            for row in klines:
                # 腾讯 K 线格式: [日期, 开盘, 收盘, 最高, 最低, 成交量]
                rows.append({
                    "date": str(row[0]),
                    "open": float(row[1]),
                    "high": float(row[3]),
                    "low": float(row[4]),
                    "close": float(row[2]),
                })
            return rows

        except Exception:
            if attempt < 2:
                wait = 1.0 * (2 ** attempt)
                time.sleep(wait)

    return []


def _sma_series(values: list[float], n: int, m: int = 1) -> list[float]:
    """通达信 SMA(X, N, M) 指数平滑序列。

    SMA(t) = (M * X(t) + (N - M) * SMA(t-1)) / N
    """
    if not values:
        return []
    result = [values[0]]
    for i in range(1, len(values)):
        prev = result[-1]
        cur = (m * values[i] + (n - m) * prev) / n
        result.append(cur)
    return result


def compute_nx_signal(ohlc_data: list[dict]) -> dict:
    """计算 NX 点信号。

    输入: [{date, open, high, low, close}, ...]（至少 20 日数据）
    输出: {
        "nx_value": float,       # 当前 NX 值 (0-100)
        "nx_signal": str,        # "buy" / "sell" / "neutral"
        "nx_trend": str,         # "上攻" / "回调" / "震荡"
        "swing_position": float, # 波段位置 0-1（0=底, 1=顶）
        "elasticity_20d": float, # 20日弹性（平均振幅%）
    }
    """
    if len(ohlc_data) < 20:
        return {
            "nx_value": None, "nx_signal": "unknown",
            "nx_trend": "数据不足", "swing_position": None,
            "elasticity_20d": None,
        }

    closes = [d["close"] for d in ohlc_data]
    highs = [d["high"] for d in ohlc_data]
    lows = [d["low"] for d in ohlc_data]
    opens = [d["open"] for d in ohlc_data]

    # PRINT := (3*CLOSE + HIGH + LOW + OPEN) / 6
    prints = [(3 * c + h + l + o) / 6
              for c, h, l, o in zip(closes, highs, lows, opens)]

    # LC := REF(PRINT, 1)  →  diff = PRINT - LC
    diffs = [prints[i] - prints[i - 1] for i in range(1, len(prints))]

    # RSI 变体: SMA(MAX(diff,0), 6, 1) / SMA(ABS(diff), 6, 1) * 100
    pos_diffs = [max(d, 0) for d in diffs]
    abs_diffs = [abs(d) for d in diffs]

    sma_pos = _sma_series(pos_diffs, 6, 1)
    sma_abs = _sma_series(abs_diffs, 6, 1)

    rsi_series = []
    for sp, sa in zip(sma_pos, sma_abs):
        rsi_series.append(sp / max(sa, 1e-10) * 100)

    # CS := SMA(RSI, 3, 1)
    cs = _sma_series(rsi_series, 3, 1)

    # RSI_P := 3 * CS - 2 * SMA(CS, 3, 1)
    sma_cs = _sma_series(cs, 3, 1)
    rsi_p = [3 * c - 2 * s for c, s in zip(cs, sma_cs)]

    if len(rsi_p) < 3:
        return {
            "nx_value": None, "nx_signal": "unknown",
            "nx_trend": "数据不足", "swing_position": None,
            "elasticity_20d": None,
        }

    # 当前值
    nx_val = rsi_p[-1]
    prev1 = rsi_p[-2]
    prev2 = rsi_p[-3]

    # V型底 = 买入信号（prev1 是局部最低 + 当前上翘）
    # 倒V顶 = 卖出信号（prev1 是局部最高 + 当前下弯）
    if nx_val > prev1 and prev1 < prev2:
        signal = "buy"
        trend = "上攻"
    elif nx_val < prev1 and prev1 > prev2:
        signal = "sell"
        trend = "回调"
    else:
        signal = "neutral"
        # 判断方向
        if nx_val > prev1:
            trend = "上行"
        elif nx_val < prev1:
            trend = "下行"
        else:
            trend = "震荡"

    # 弹性 = 20日平均振幅率 (high-low)/close
    recent_20 = ohlc_data[-20:]
    elasticity = sum(
        (d["high"] - d["low"]) / max(d["close"], 0.01)
        for d in recent_20
    ) / len(recent_20)

    # 波段位置 = 当前价在20日范围中的位置
    h20 = max(d["high"] for d in recent_20)
    l20 = min(d["low"] for d in recent_20)
    swing = (closes[-1] - l20) / max(h20 - l20, 1e-10)

    return {
        "nx_value": round(nx_val, 1),
        "nx_signal": signal,
        "nx_trend": trend,
        "swing_position": round(min(max(swing, 0), 1), 2),
        "elasticity_20d": round(elasticity * 100, 2),
    }


# ── 全量 A 股名称映射 ──────────────────────────────────

_ALL_STOCK_NAMES_CACHE: dict[str, str] | None = None  # code -> name


def _load_all_stock_names() -> dict[str, str]:
    """加载全量 A 股名称映射（~5000条），优先用本地缓存，否则 AKShare 拉取。"""
    global _ALL_STOCK_NAMES_CACHE
    if _ALL_STOCK_NAMES_CACHE is not None:
        return _ALL_STOCK_NAMES_CACHE

    cache_path = RAW_DIR / "stock_names_cache.json"
    # 缓存有效期 7 天
    if cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < 7 * 86400:
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    _ALL_STOCK_NAMES_CACHE = json.load(f)
                return _ALL_STOCK_NAMES_CACHE
            except (json.JSONDecodeError, OSError):
                pass

    # AKShare 拉取全量
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        mapping = {}
        for _, row in df.iterrows():
            mapping[str(row["code"])] = str(row["name"])
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False)
        _ALL_STOCK_NAMES_CACHE = mapping
        return mapping
    except Exception as e:
        print(f"  ⚠ 全量股票名称拉取失败: {e}，降级使用 STOCK_NAMES")
        _ALL_STOCK_NAMES_CACHE = dict(STOCK_NAMES)
        return _ALL_STOCK_NAMES_CACHE


def get_stock_name(code: str) -> str:
    """查股票名称：优先用全量映射，兜底用 STOCK_NAMES。"""
    all_names = _load_all_stock_names()
    return all_names.get(code, STOCK_NAMES.get(code, code))


# ── 微信票池提取 ──────────────────────────────────────

# 6位纯数字正则（排除日期/金额等误匹配）
_CODE_RE = re.compile(r"(?<!\d)([036]\d{5})(?!\d)")

# 短名称排除列表（2字名容易误匹配：如"中信"匹配"中信证券"和"中信建投"）
_SHORT_NAME_MIN_LEN = 3


def extract_stock_codes_from_wechat(wechat_data: dict) -> list[dict]:
    """从微信信源文章中提取提及的个股代码。

    匹配规则：
    1. 全量 A 股名称反查（~5000条，名→代码）
    2. 6位数字代码正则匹配（仅限 0/3/6 开头的 A 股代码）

    返回 [{code, name, source, reason}, ...]，已去重。
    """
    all_names = _load_all_stock_names()
    # 构建名称→代码反向映射（跳过太短的名称避免误匹配）
    name_to_code = {}
    for code, name in all_names.items():
        if len(name) >= _SHORT_NAME_MIN_LEN:
            name_to_code[name] = code

    results = []
    seen = set()

    for article in wechat_data.get("articles", []):
        source = article.get("source", "")
        content = article.get("content", "")
        if not content:
            continue

        # 按段落切分，用于提取上下文作为 reason
        paragraphs = content.split("\n")

        # 1) 名称反查（全量映射）
        for name, code in name_to_code.items():
            if name in content and code not in seen:
                seen.add(code)
                reason = ""
                for p in paragraphs:
                    if name in p and len(p) > 10:
                        reason = p.strip()[:120]
                        break
                results.append({
                    "code": code, "name": name,
                    "source": source, "reason": reason,
                })

        # 2) 正则匹配6位代码
        for m in _CODE_RE.finditer(content):
            code = m.group(1)
            if code not in seen:
                seen.add(code)
                name = all_names.get(code, code)
                reason = ""
                for p in paragraphs:
                    if code in p and len(p) > 10:
                        reason = p.strip()[:120]
                        break
                results.append({
                    "code": code, "name": name,
                    "source": source, "reason": reason,
                })

    return results


# ── 持久化 ───────────────────────────────────────────
def save_stocks(date_str: str, results: list[dict]) -> Path:
    """保存个股分析结果到 底稿/raw/YYYY-MM-DD_stocks.json"""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{date_str}_stocks.json"
    output = {
        "date": date_str,
        "analyzed_at": datetime.now().isoformat(),
        "stocks": results,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    return path


def load_cached_stocks(date_str: str) -> list[dict] | None:
    """读取缓存的个股分析结果。"""
    path = RAW_DIR / f"{date_str}_stocks.json"
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("stocks", [])
        except (json.JSONDecodeError, OSError):
            pass
    return None


# ── 输出格式化 ───────────────────────────────────────
def print_summary(results: list[dict]):
    """终端打印个股分析摘要。"""
    print("\n" + "=" * 60)
    print("  个股深度分析摘要")
    print("=" * 60)

    for r in results:
        name = r.get("name", r.get("code", "?"))
        code = r.get("code", "?")
        val = r.get("valuation", {})
        flow = r.get("fund_flow", {})
        news = r.get("news", [])

        print(f"\n  {name}（{code}）")
        print(f"  {'─' * 40}")

        # 估值
        pe = val.get("pe_ttm")
        pb = val.get("pb")
        pe_str = f"{pe:.2f}" if pe else "N/A"
        pb_str = f"{pb:.2f}" if pb else "N/A"
        print(f"  估值 | PE_TTM: {pe_str}  PB: {pb_str}")

        # 资金
        total = flow.get("total_major_net", 0)
        days = flow.get("days", 0)
        trend = flow.get("trend", "未知")
        # MCP 返回元单位，转换为亿元显示
        total_yi = total / 1e8
        print(f"  资金 | 近{days}日主力净流入: {total_yi:+.2f}亿  趋势: {trend}")

        # NX 点
        nx = r.get("nx", {})
        nx_val = nx.get("nx_value")
        nx_sig = nx.get("nx_signal", "unknown")
        nx_trend = nx.get("nx_trend", "未知")
        swing = nx.get("swing_position")
        elast = nx.get("elasticity_20d")
        sig_map = {"buy": "买入↑", "sell": "卖出↓", "neutral": "中性—", "unknown": "?"}
        nx_str = f"{nx_val:.1f}" if nx_val is not None else "N/A"
        swing_str = f"{swing:.0%}" if swing is not None else "N/A"
        elast_str = f"{elast:.1f}%" if elast is not None else "N/A"
        print(f"  NX点 | 值: {nx_str}  信号: {sig_map.get(nx_sig, nx_sig)}  趋势: {nx_trend}")
        print(f"  结构 | 弹性: {elast_str}  波段位置: {swing_str}")

        # 新闻
        if news:
            print(f"  新闻 | 近期 {len(news)} 条:")
            for i, n in enumerate(news[:3], 1):
                print(f"         {i}. {n['title'][:40]}")
        else:
            print(f"  新闻 | 无近期新闻")

        # 人工待确认
        print(f"  催化 | {r.get('catalyst_match', '?')} (需人工确认)")
        print(f"  风险 | {r.get('risk', '?')} (需人工确认)")

    print(f"\n{'=' * 60}")
    print("  [?] = 需人工判断  数据来源: Finstep MCP")
    print()


# ── 主函数 ───────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="CycleRadar 个股深度分析")
    parser.add_argument("--stocks", nargs="+", required=True,
                        help="股票代码列表（如 688256 300474）")
    parser.add_argument("--date", required=True, help="日期 YYYY-MM-DD")
    parser.add_argument("--from-cache", action="store_true",
                        help="从缓存读取数据")
    args = parser.parse_args()

    date_str = args.date
    stock_codes = args.stocks

    if args.from_cache:
        cached = load_cached_stocks(date_str)
        if cached:
            # 过滤出请求的股票
            results = [s for s in cached if s.get("code") in stock_codes]
            if results:
                print(f"从缓存读取: {RAW_DIR / f'{date_str}_stocks.json'}")
                print_summary(results)
                return
        print("缓存中无匹配数据，重新拉取...")

    print(f"分析 {len(stock_codes)} 只股票 ({date_str})...")
    results = []
    for code in stock_codes:
        print(f"\n{'─' * 40}")
        print(f"分析 {STOCK_NAMES.get(code, code)}（{code}）")
        result = analyze_stock(code, date_str)
        results.append(result)

    # 保存
    path = save_stocks(date_str, results)
    print(f"\n分析结果已保存: {path}")

    # 打印摘要
    print_summary(results)


if __name__ == "__main__":
    main()
