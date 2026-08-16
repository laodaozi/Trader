"""
sector_adapter.py — 行业轮动监控适配器

职责：
  1. 从 akshare 拉取 31 个中信一级行业板块数据
  2. 计算每个行业 6 维指标
  3. 产出 sector snapshot + 行业排名

行业列表：31 个中信一级行业

输出格式：
  {
    "sector": "sector_rotation",
    "label": "行业轮动",
    "industry_snapshot": [ { "code": "...", "name": "...", "change_pct": ..., "dimensions": {...} }, ... ],
    "top_momentum": [...], "top_volume": [...], "top_sentiment": [...],
    "dimensions": { ... aggregated ... },
    "verdict": { "score": 0-100, "label": "...", "detail": "..." }
  }
"""

import math
from datetime import datetime

CITIC_INDUSTRIES = {
    "BK0477": "石油石化", "BK0478": "煤炭", "BK0479": "有色金属",
    "BK0480": "电力及公用事业", "BK0481": "钢铁", "BK0482": "基础化工",
    "BK0483": "建筑", "BK0484": "建材", "BK0485": "轻工制造",
    "BK0486": "机械", "BK0487": "电力设备及新能源", "BK0488": "国防军工",
    "BK0489": "汽车", "BK0490": "商贸零售", "BK0491": "消费者服务",
    "BK0492": "家电", "BK0493": "纺织服装", "BK0494": "医药",
    "BK0495": "食品饮料", "BK0496": "农林牧渔", "BK0497": "银行",
    "BK0498": "非银行金融", "BK0499": "房地产", "BK0500": "交通运输",
    "BK0501": "电子", "BK0502": "通信", "BK0503": "计算机",
    "BK0504": "传媒", "BK0505": "综合", "BK0506": "综合金融",
    "BK0507": "电力设备及新能源",
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


def _short_dim(closes, highs, lows, volumes):
    """单行业 6 维快评（轻量版，适配 31 行业低频扫描）"""
    dims = {}

    if len(closes) < 10:
        return {
            "trend": {"score": 50, "label": "—", "detail": ""},
            "volatility": {"score": 50, "label": "—", "detail": ""},
            "capital_flow": {"score": 50, "label": "—", "detail": ""},
            "sentiment": {"score": 50, "label": "—", "detail": ""},
            "key_levels": {"score": 50, "label": "—", "detail": ""},
            "event_risk": {"score": 50, "label": "—", "detail": ""},
        }

    current = closes[-1]

    # trend: MA5 vs MA10 vs MA20
    ma5 = _compute_ma(closes, 5)
    ma10 = _compute_ma(closes, 10)
    ma20 = _compute_ma(closes, min(20, len(closes)))
    t_score = 50
    t_detail = []
    if ma5 and ma10 and ma20:
        if ma5 > ma10 > ma20:
            t_score = 78
            t_detail.append("多头排列")
        elif ma5 > ma10:
            t_score = 60
            t_detail.append("短多")
        elif ma5 < ma10 < ma20:
            t_score = 22
            t_detail.append("空头排列")
        elif ma5 < ma10:
            t_score = 40
            t_detail.append("短空")
    if len(closes) >= 5:
        ret5 = closes[-1] / closes[-5] - 1
        if ret5 > 0.05:
            t_score = min(t_score + 10, 100)
        elif ret5 < -0.05:
            t_score = max(t_score - 10, 0)
    dims["trend"] = {
        "score": t_score,
        "label": "多头" if t_score >= 55 else "空头",
        "detail": "；".join(t_detail),
    }

    # volatility: 10-day annualized
    rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
    vol10 = _compute_std(rets, min(10, len(rets)))
    if vol10:
        ann_vol = vol10 * math.sqrt(250) * 100
        if ann_vol < 15:
            vol_score, vol_label = 75, "低波"
        elif ann_vol < 25:
            vol_score, vol_label = 55, "正常"
        elif ann_vol < 40:
            vol_score, vol_label = 35, "高波"
        else:
            vol_score, vol_label = 15, "极高波"
        dims["volatility"] = {"score": vol_score, "label": vol_label, "detail": f"年化{ann_vol:.0f}%"}
    else:
        dims["volatility"] = {"score": 50, "label": "—", "detail": ""}

    # capital_flow: 量比
    if len(volumes) >= 10:
        avg_vol = _compute_ma(volumes, min(10, len(volumes)))
        recent_vol = _compute_ma(volumes, 3)
        if avg_vol and avg_vol > 0:
            ratio = recent_vol / avg_vol
            if ratio > 1.5:
                cf_score, cf_label = 75, "放量"
            elif ratio > 1.1:
                cf_score, cf_label = 60, "温和放量"
            elif ratio > 0.8:
                cf_score, cf_label = 50, "正常"
            elif ratio > 0.5:
                cf_score, cf_label = 35, "缩量"
            else:
                cf_score, cf_label = 20, "极度缩量"
            dims["capital_flow"] = {"score": cf_score, "label": cf_label, "detail": f"量比{ratio:.1f}"}
        else:
            dims["capital_flow"] = {"score": 50, "label": "—", "detail": ""}
    else:
        dims["capital_flow"] = {"score": 50, "label": "—", "detail": ""}

    # sentiment: 连涨/连跌 + 5日涨幅
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

    # key_levels: 距20日高低
    k_score = 50
    k_detail = []
    n = min(20, len(closes))
    h20 = max(highs[-n:]) if highs else current
    l20 = min(lows[-n:]) if lows else current
    if h20 > 0:
        d_high = (h20 - current) / h20 * 100
        if d_high < 2:
            k_score -= 15
            k_detail.append("接近高点")
    if l20 > 0:
        d_low = (current - l20) / l20 * 100
        if d_low < 2:
            k_score -= 8
            k_detail.append("接近低点")
    dims["key_levels"] = {
        "score": min(100, max(0, k_score)),
        "label": "安全" if k_score >= 60 else ("风险区" if k_score <= 35 else "关键位"),
        "detail": "；".join(k_detail),
    }

    # event_risk: 极端波动
    e_score = 80
    e_detail = []
    if len(rets) >= 5:
        for i in range(-min(5, len(rets)), 0):
            if abs(rets[i]) > 0.06:
                e_score -= 20
                e_detail.append(f"单日{rets[i]*100:+.1f}%")
                break
    dims["event_risk"] = {
        "score": min(100, max(0, e_score)),
        "label": "低风险" if e_score >= 60 else "中高风险",
        "detail": "；".join(e_detail),
    }

    return dims


def fetch_industry_hist(code, name):
    """拉取行业板块日线数据"""
    import akshare as ak

    try:
        df = ak.stock_board_industry_hist_em(symbol=name, period="日k", start_date="20260101",
                                              end_date=datetime.now().strftime("%Y%m%d"), adjust="")
        if df is None or df.empty:
            return None
        closes = df["收盘"].tolist() if "收盘" in df.columns else df["close"].tolist()
        highs = df["最高"].tolist() if "最高" in df.columns else df["high"].tolist()
        lows = df["最低"].tolist() if "最低" in df.columns else df["low"].tolist()
        volumes = df["成交量"].tolist() if "成交量" in df.columns else df["volume"].tolist()

        last = closes[-1] if closes else 0
        prev = closes[-2] if len(closes) > 1 else last
        change_pct = round((last - prev) / prev * 100, 2) if prev else 0

        return {
            "code": code,
            "name": name,
            "value": round(last, 2),
            "change_pct": change_pct,
            "closes": closes,
            "highs": highs,
            "lows": lows,
            "volumes": volumes,
        }
    except Exception as e:
        print(f"[sector_adapter] 拉取 {name}({code}) 失败: {e}")
        return None


def scan_sectors():
    """入口函数：拉取全行业数据，计算排名，产出 sector snapshot"""
    import akshare as ak

    industries = []

    for code, name in CITIC_INDUSTRIES.items():
        data = fetch_industry_hist(code, name)
        if not data:
            continue
        dims = _short_dim(data["closes"], data["highs"], data["lows"], data["volumes"])
        industries.append({
            "code": code,
            "name": name,
            "value": data["value"],
            "change_pct": data["change_pct"],
            "dimensions": dims,
        })

    if not industries:
        return {"sector": "sector_rotation", "label": "行业轮动", "error": "无数据"}

    # 排名 Top5 板块
    top_momentum = sorted(industries, key=lambda x: -x["dimensions"]["trend"]["score"])[:5]
    top_volume = sorted(industries, key=lambda x: -x["dimensions"]["capital_flow"]["score"])[:5]
    top_sentiment = sorted(industries, key=lambda x: -x["dimensions"]["sentiment"]["score"])[:5]

    # 聚合 6 维
    dim_names = ["trend", "volatility", "capital_flow", "sentiment", "key_levels", "event_risk"]
    aggregated = {}
    for dim in dim_names:
        scores = [ind["dimensions"].get(dim, {}).get("score", 50) for ind in industries]
        avg = round(sum(scores) / len(scores)) if scores else 50
        aggregated[dim] = {"score": avg, "label": industries[0]["dimensions"].get(dim, {}).get("label", "—"), "detail": ""}

    # verdict
    w = {"trend": 0.30, "volatility": 0.10, "capital_flow": 0.25, "sentiment": 0.15, "key_levels": 0.10, "event_risk": 0.10}
    total = round(sum(aggregated[d]["score"] * w[d] for d in dim_names))
    if total >= 70:
        v_label = "轮动活跃 · 机会多"
    elif total >= 55:
        v_label = "轮动温和 · 结构性"
    elif total >= 40:
        v_label = "轮动减速 · 防御"
    else:
        v_label = "轮动停滞 · 低仓位"

    top_names = [ind["name"] for ind in top_momentum[:3]]

    return {
        "sector": "sector_rotation",
        "label": "行业轮动",
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "industry_count": len(industries),
        "industry_snapshot": industries,
        "top_momentum": [{"name": ind["name"], "score": ind["dimensions"]["trend"]["score"]} for ind in top_momentum],
        "top_volume": [{"name": ind["name"], "score": ind["dimensions"]["capital_flow"]["score"]} for ind in top_volume],
        "top_sentiment": [{"name": ind["name"], "score": ind["dimensions"]["sentiment"]["score"]} for ind in top_sentiment],
        "dimensions": aggregated,
        "verdict": {
            "score": total,
            "label": v_label,
            "detail": f"动量领先: {', '.join(top_names)}",
        },
    }


if __name__ == "__main__":
    import json

    print("=" * 60)
    print("  行业轮动监控 · 自检")
    print("=" * 60)

    result = scan_sectors()

    if "error" in result:
        print(f"❌ {result['error']}")
    else:
        print(f"\n📊 行业轮动评分: {result['verdict']['score']}/100 → {result['verdict']['label']}")
        print(f"   覆盖 {result['industry_count']} 个行业\n")

        print("动量 Top5:")
        for item in result["top_momentum"]:
            print(f"  {item['name']:10s} {item['score']:3d}")

        print("\n资金 Top5:")
        for item in result["top_volume"]:
            print(f"  {item['name']:10s} {item['score']:3d}")

        for dim_name, dim in result["dimensions"].items():
            bar = "█" * (dim["score"] // 5) + "░" * (20 - dim["score"] // 5)
            print(f"  {dim_name:15s} {bar} {dim['score']:3d}")

        print(json.dumps(result, ensure_ascii=False, indent=2)[:400])
