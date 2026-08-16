"""
commodity_adapter.py — 商品期货趋势监控适配器

职责：
  1. 从 akshare 拉取 4 个核心商品：黄金/原油/铜/螺纹钢
  2. 计算 6 维指标（适配期货特性：trend / volatility / open_interest / sentiment / key_levels / event_risk）
  3. 产出 sector snapshot

覆盖品种：
  黄金 AU0 — 上期所 — 避险/实际利率
  原油 SC0 — 上期能源 — 全球需求/地缘
  沪铜 CU0 — 上期所 — 全球经济晴雨表
  螺纹 RB0 — 上期所 — 中国地产/基建

输出格式：
  {
    "sector": "commodity",
    "label": "商品期货",
    "commodities": { "黄金": {...}, ... },
    "dimensions": { ... },
    "verdict": { "score": 0-100, "label": "...", "detail": "..." }
  }
"""

import math
from datetime import datetime

COMMODITIES = {
    "AU0": "黄金",
    "SC0": "原油",
    "CU0": "沪铜",
    "RB0": "螺纹钢",
}


def _compute_ma(values, window):
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def _compute_std(values, window):
    if len(values) < window:
        return None
    avg = sum(values[-window:]) / window
    return math.sqrt(sum((v - avg) ** 2 for v in values[-window:]) / window)


def _commodity_dims(closes, highs, lows, volumes, open_interests):
    """商品期货 6 维评分"""
    dims = {}

    if len(closes) < 10:
        defaults = {"score": 50, "label": "—", "detail": ""}
        return {k: defaults for k in ["trend", "volatility", "capital_flow", "sentiment", "key_levels", "event_risk"]}

    current = closes[-1]

    # trend
    ma5 = _compute_ma(closes, 5)
    ma10 = _compute_ma(closes, 10)
    ma20 = _compute_ma(closes, min(20, len(closes)))
    t_score = 50
    t_detail = []
    if ma5 and ma10 and ma20:
        if ma5 > ma10 > ma20:
            t_score, t_label = 78, "多头排列"
            t_detail.append("多头排列")
        elif ma5 > ma10:
            t_score, t_label = 62, "短多"
            t_detail.append("均线偏多")
        elif ma5 < ma10 < ma20:
            t_score, t_label = 22, "空头排列"
            t_detail.append("空头排列")
        elif ma5 < ma10:
            t_score, t_label = 38, "短空"
            t_detail.append("均线偏空")
        else:
            t_score, t_label = 50, "整理"
    else:
        t_label = "数据不足"
    if len(closes) >= 5:
        ret5 = closes[-1] / closes[-5] - 1
        if ret5 > 0.05:
            t_score = min(t_score + 10, 100)
            t_detail.append(f"5日+{ret5*100:.1f}%")
        elif ret5 < -0.05:
            t_score = max(t_score - 10, 0)
            t_detail.append(f"5日{ret5*100:.1f}%")
    dims["trend"] = {"score": min(100, max(0, t_score)), "label": t_label, "detail": "；".join(t_detail)}

    # volatility: 10-day annualized
    rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
    vol10 = _compute_std(rets, min(10, len(rets)))
    if vol10:
        ann_vol = vol10 * math.sqrt(250) * 100
        if ann_vol < 15:
            v_score, v_label = 78, "低波"
        elif ann_vol < 25:
            v_score, v_label = 58, "正常"
        elif ann_vol < 40:
            v_score, v_label = 35, "高波"
        else:
            v_score, v_label = 15, "极高波"
        dims["volatility"] = {"score": v_score, "label": v_label, "detail": f"年化{ann_vol:.0f}%"}
    else:
        dims["volatility"] = {"score": 50, "label": "—", "detail": ""}

    # capital_flow: open interest + volume composite
    cf_score = 50
    cf_detail = []
    if len(volumes) >= 10 and len(open_interests) >= 10:
        avg_v = _compute_ma(volumes, 10)
        recent_v = _compute_ma(volumes, 3)
        if avg_v and avg_v > 0:
            v_ratio = recent_v / avg_v
            if v_ratio > 1.5:
                cf_score += 15
                cf_detail.append(f"量增{v_ratio:.1f}x")
            elif v_ratio > 1.1:
                cf_score += 5
            elif v_ratio < 0.6:
                cf_score -= 10
                cf_detail.append(f"量缩{v_ratio:.1f}x")
        # 持仓趋势
        avg_oi = _compute_ma(open_interests, 10)
        recent_oi = _compute_ma(open_interests, 3)
        if avg_oi and avg_oi > 0:
            oi_ratio = recent_oi / avg_oi
            if oi_ratio > 1.05:
                cf_score += 10
                cf_detail.append("持仓增加")
            elif oi_ratio < 0.95:
                cf_score -= 10
                cf_detail.append("持仓减少")
        # 价量配合
        price_up = closes[-1] > closes[-3] if len(closes) >= 3 else False
        if v_ratio > 1.2 and price_up:
            cf_score += 8
        elif v_ratio > 1.2 and not price_up:
            cf_score -= 8
    dims["capital_flow"] = {
        "score": min(100, max(0, cf_score)),
        "label": "资金流入" if cf_score >= 55 else ("资金流出" if cf_score <= 45 else "中性"),
        "detail": "；".join(cf_detail),
    }

    # sentiment: 动量 + 连涨连跌
    s_score = 50
    s_detail = []
    consecutive = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] > closes[i - 1]:
            if consecutive >= 0:
                consecutive += 1
            else:
                break
        elif closes[i] < closes[i - 1]:
            if consecutive <= 0:
                consecutive -= 1
            else:
                break
        else:
            break
    if consecutive >= 3:
        s_score += 15
        s_detail.append(f"连涨{consecutive}")
    elif consecutive <= -3:
        s_score -= 15
        s_detail.append(f"连跌{-consecutive}")
    if len(closes) >= 5:
        ret5 = closes[-1] / closes[-5] - 1
        if abs(ret5) > 0.03:
            s_score += 10 if ret5 > 0 else -10
            s_detail.append(f"5日{ret5*100:+.1f}%")
    dims["sentiment"] = {
        "score": min(100, max(0, s_score)),
        "label": "乐观" if s_score >= 60 else ("悲观" if s_score <= 40 else "中性"),
        "detail": "；".join(s_detail),
    }

    # key_levels
    k_score = 50
    k_detail = []
    n = min(20, len(closes))
    h20 = max(highs[-n:]) if highs else current
    l20 = min(lows[-n:]) if lows else current
    if h20 > 0:
        d_high = (h20 - current) / h20 * 100
        if d_high < 2:
            k_score -= 15
            k_detail.append("接近20日高")
    if l20 > 0:
        d_low = (current - l20) / l20 * 100
        if d_low < 2:
            k_score -= 8
            k_detail.append("接近20日低")
    dims["key_levels"] = {
        "score": min(100, max(0, k_score)),
        "label": "安全区" if k_score >= 60 else ("风险区" if k_score <= 35 else "关键位"),
        "detail": "；".join(k_detail),
    }

    # event_risk
    e_score = 80
    e_detail = []
    if len(rets) >= 5:
        for i in range(-min(5, len(rets)), 0):
            if abs(rets[i]) > 0.04:
                e_score -= 20
                e_detail.append(f"极端波动{rets[i]*100:+.1f}%")
                break
    # 波动率突变
    if len(rets) >= 20:
        v5 = _compute_std(rets, 5)
        v20 = _compute_std(rets, 20)
        if v5 and v20 and v20 > 0 and v5 / v20 > 2.5:
            e_score -= 12
            e_detail.append("波动率骤升")
    dims["event_risk"] = {
        "score": min(100, max(0, e_score)),
        "label": "低风险" if e_score >= 60 else ("中高风险" if e_score >= 35 else "高风险"),
        "detail": "；".join(e_detail),
    }

    return dims


def fetch_commodity_hist(symbol, name):
    """拉取商品期货历史日线"""
    import akshare as ak

    try:
        df = ak.futures_main_sina(symbol=symbol)
        if df is None or df.empty:
            return None
        closes = df["close"].tolist() if "close" in df.columns else []
        highs = df["high"].tolist() if "high" in df.columns else []
        lows = df["low"].tolist() if "low" in df.columns else []
        volumes = df["volume"].tolist() if "volume" in df.columns else []
        open_interests = df["hold"].tolist() if "hold" in df.columns else []

        if not closes:
            return None

        last = closes[-1]
        prev = closes[-2] if len(closes) > 1 else last
        change_pct = round((last - prev) / prev * 100, 2) if prev else 0

        return {
            "symbol": symbol,
            "name": name,
            "value": round(last, 2),
            "change_pct": change_pct,
            "closes": closes,
            "highs": highs,
            "lows": lows,
            "volumes": volumes,
            "open_interests": open_interests,
            "latest_date": str(df["date"].tolist()[-1]) if "date" in df.columns else "",
        }
    except Exception as e:
        print(f"[commodity_adapter] 拉取 {name}({symbol}) 失败: {e}")
        return None


def scan_commodities():
    """入口函数"""
    result = {
        "sector": "commodity",
        "label": "商品期货",
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "commodities": {},
        "dimensions": {},
        "verdict": {},
    }

    dims_list = []

    for symbol, name in COMMODITIES.items():
        data = fetch_commodity_hist(symbol, name)
        if not data:
            continue
        dims = _commodity_dims(
            data["closes"], data["highs"], data["lows"],
            data["volumes"], data["open_interests"],
        )
        dims_list.append(dims)
        result["commodities"][name] = {
            "symbol": symbol,
            "value": data["value"],
            "change_pct": data["change_pct"],
            "latest_date": data["latest_date"],
            "dimensions": dims,
        }

    if not dims_list:
        result["verdict"] = {"score": 50, "label": "数据不足", "detail": ""}
        return result

    dim_names = ["trend", "volatility", "capital_flow", "sentiment", "key_levels", "event_risk"]
    aggregated = {}
    for dim in dim_names:
        scores = [d.get(dim, {}).get("score", 50) for d in dims_list]
        avg = round(sum(scores) / len(scores))
        aggregated[dim] = {
            "score": avg,
            "label": dims_list[0].get(dim, {}).get("label", "—"),
            "detail": "",
        }

    w = {"trend": 0.30, "volatility": 0.10, "capital_flow": 0.20, "sentiment": 0.15, "key_levels": 0.15, "event_risk": 0.10}
    total = round(sum(aggregated[d]["score"] * w[d] for d in dim_names))

    # 方向性判断
    dir_scores = [d["dimensions"]["trend"]["score"] for _, d in result["commodities"].items()]
    up_count = sum(1 for s in dir_scores if s >= 55)
    dn_count = len(dir_scores) - up_count
    directional = f"{up_count}涨{dn_count}跌"

    if total >= 70:
        v_label = "商品偏强 · 通胀预期"
    elif total >= 55:
        v_label = "商品温和 · 结构性"
    elif total >= 40:
        v_label = "商品偏弱 · 需求不足"
    else:
        v_label = "商品弱势 · 通缩压力"

    result["dimensions"] = aggregated
    result["verdict"] = {
        "score": total,
        "label": v_label,
        "detail": directional,
    }

    return result


if __name__ == "__main__":
    import json

    print("=" * 60)
    print("  商品期货监控 · 自检")
    print("=" * 60)

    result = scan_commodities()

    print(f"\n📊 商品综合评分: {result['verdict']['score']}/100 → {result['verdict']['label']}")
    print(f"   {result['verdict']['detail']}\n")

    for name, comm in result["commodities"].items():
        print(f"  {name:6s} {comm['value']:>8.1f}  {comm['change_pct']:+.2f}%")
        for dim_name, dim in comm["dimensions"].items():
            print(f"    {dim_name:12s} {dim['score']:3d} {dim['label']}")

    print(json.dumps(result, ensure_ascii=False, indent=2)[:400])
