"""
market_adapter.py — A股大盘温度监控适配器

职责：
  1. 从 akshare 拉取上证综指/深证成指/创业板指日线数据
  2. 计算 6 维指标：trend / volatility / capital_flow / sentiment / key_levels / event_risk
  3. 产出一致结构的 sector snapshot dict

输出格式：
  {
    "sector": "a_share_market",
    "label": "A股大盘",
    "indices": { "shanghai": {...}, "shenzhen": {...}, "chinext": {...} },
    "dimensions": { "trend": {...}, ... },
    "verdict": { "score": 0-100, "label": "...", "detail": "..." }
  }
"""

import math
from datetime import datetime, timedelta


def _compute_ma(values, window):
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def _compute_std(values, window):
    if len(values) < window:
        return None
    avg = sum(values[-window:]) / window
    return math.sqrt(sum((v - avg) ** 2 for v in values[-window:]) / window)


def _score_trend(closes, highs, lows):
    """
    趋势维度 (0-100)
    - MA 排列（多头/空头）
    - 价格在 MA 族的相对位置
    - ADX 近似（最近 N 日振幅 vs 方向性）
    """
    if len(closes) < 60:
        return {"score": 50, "label": "数据不足", "detail": ""}

    ma5 = _compute_ma(closes, 5)
    ma10 = _compute_ma(closes, 10)
    ma20 = _compute_ma(closes, 20)
    ma60 = _compute_ma(closes, 60)
    current = closes[-1]

    score = 50
    details = []

    if ma20 and ma60:
        if ma5 > ma10 > ma20 > ma60:
            score = 80
            details.append("均线多头排列")
        elif ma5 < ma10 and ma10 < ma20:
            score = 20
            details.append("均线空头排列")
        elif ma20 > ma60:
            score = 62
            details.append("中长期均线向上")
        else:
            score = 38
            details.append("中长期均线向下")

    # 价格位置调整
    if ma20:
        ratio = current / ma20
        if ratio > 1.05:
            score = min(score + 10, 100)
            details.append("价格高于20日均线5%+")
        elif ratio < 0.95:
            score = max(score - 10, 0)
            details.append("价格低于20日均线5%+")

    # 近期方向性（5日斜率）
    if len(closes) >= 5:
        slope = (closes[-1] - closes[-5]) / closes[-5] * 100
        if slope > 3:
            score = min(score + 8, 100)
        elif slope < -3:
            score = max(score - 8, 0)

    label = "强势" if score >= 70 else ("偏强" if score >= 55 else ("偏弱" if score >= 35 else "弱势"))
    return {"score": min(100, max(0, score)), "label": label, "detail": "；".join(details)}


def _score_volatility(closes):
    """
    波动率维度 (0-100, 高分=低波动/安全)
    - 20日年化波动率
    - 波动率分位（相对自身历史）
    """
    if len(closes) < 60:
        return {"score": 50, "label": "数据不足", "detail": ""}

    returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
    vol20 = _compute_std(returns, 20)
    if not vol20:
        return {"score": 50, "label": "数据不足", "detail": ""}

    annual_vol = vol20 * math.sqrt(250) * 100

    # A股大盘年化波动 15-30% 为正常区间
    if annual_vol < 12:
        score, label = 85, "极低波动"
    elif annual_vol < 18:
        score, label = 70, "低波动"
    elif annual_vol < 25:
        score, label = 55, "正常波动"
    elif annual_vol < 35:
        score, label = 35, "高波动"
    else:
        score, label = 15, "极高波动"

    return {"score": score, "label": label, "detail": f"年化波动率 {annual_vol:.1f}%"}


def _score_capital_flow(volumes, closes):
    """
    资金流向维度 (0-100, 高分=资金流入)
    - 成交量 vs 20日均量
    - 量价配合（放量上涨/缩量下跌）
    """
    if len(volumes) < 20:
        return {"score": 50, "label": "数据不足", "detail": ""}

    avg_vol20 = _compute_ma(volumes, 20)
    if not avg_vol20 or avg_vol20 == 0:
        return {"score": 50, "label": "数据不足", "detail": ""}

    recent_vol = _compute_ma(volumes, 3)
    vol_ratio = recent_vol / avg_vol20

    details = []
    score = 50

    # 量价关系
    price_change = closes[-1] / closes[-3] - 1 if len(closes) >= 3 else 0

    if vol_ratio > 1.3 and price_change > 0.01:
        score = 78
        details.append("放量上涨，资金流入")
    elif vol_ratio > 1.3 and price_change < -0.01:
        score = 28
        details.append("放量下跌，资金出逃")
    elif vol_ratio < 0.7 and price_change > 0.005:
        score = 62
        details.append("缩量上涨，筹码稳定")
    elif vol_ratio < 0.7 and price_change < -0.005:
        score = 40
        details.append("缩量下跌，交投清淡")
    elif vol_ratio > 1.1:
        score = 60
    else:
        score = 48

    label = "大额流入" if score >= 70 else ("小幅流入" if score >= 55 else ("小幅流出" if score >= 35 else "大额流出"))
    return {"score": score, "label": label, "detail": f"量比{vol_ratio:.1f}；" + "；".join(details)}


def _score_sentiment(closes, highs, lows):
    """
    情绪维度 (0-100, 高分=乐观)
    - 连涨/连跌天数
    - 近期涨幅强度
    - 日内振幅（高振幅=分歧大）
    """
    if len(closes) < 20:
        return {"score": 50, "label": "数据不足", "detail": ""}

    details = []
    score = 50

    # 连涨连跌
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
        score += 15
        details.append(f"连涨{consecutive}日")
    elif consecutive >= 1:
        score += 5
    elif consecutive <= -3:
        score -= 15
        details.append(f"连跌{-consecutive}日")
    elif consecutive <= -1:
        score -= 5

    # 近期涨幅强度
    ret5 = closes[-1] / closes[-min(5, len(closes))] - 1
    ret10 = closes[-1] / closes[-min(10, len(closes))] - 1
    if ret5 > 0.03:
        score += 10
        details.append(f"5日涨{ret5*100:.1f}%")
    elif ret5 < -0.03:
        score -= 10
        details.append(f"5日跌{ret5*100:.1f}%")

    # 日内振幅
    if highs and lows:
        recent_amplitudes = []
        n = min(10, len(highs))
        for i in range(-n, 0):
            if highs[i] and lows[i] and lows[i] > 0:
                recent_amplitudes.append((highs[i] - lows[i]) / lows[i])
        if recent_amplitudes:
            avg_amp = sum(recent_amplitudes) / len(recent_amplitudes) * 100
            if avg_amp > 3:
                score = max(score - 8, 0)
                details.append(f"近期振幅{avg_amp:.1f}%，分歧大")

    score = min(100, max(0, score))
    label = "乐观" if score >= 70 else ("偏乐观" if score >= 55 else ("偏悲观" if score >= 35 else "悲观"))
    return {"score": score, "label": label, "detail": "；".join(details)}


def _score_key_levels(closes, highs, lows):
    """
    关键技术位 (0-100, 高分=远离风险位/在安全区)
    - 距离 20 日高/低
    - 距离 60 日高/低（支撑/压力）
    - 整数关口
    """
    if len(closes) < 60:
        return {"score": 50, "label": "数据不足", "detail": ""}

    current = closes[-1]
    n20 = min(20, len(closes))
    n60 = min(60, len(closes))
    high20 = max(highs[-n20:])
    low20 = min(lows[-n20:])
    high60 = max(highs[-n60:])
    low60 = min(lows[-n60:])

    details = []
    score = 50

    # 距20日高
    dist_high20 = (high20 - current) / current * 100
    if dist_high20 < 1:
        score -= 10
        details.append(f"接近20日高点({high20:.0f})")
    elif dist_high20 > 5:
        score += 8

    # 距20日低
    dist_low20 = (current - low20) / current * 100
    if dist_low20 < 1:
        score -= 5
        details.append(f"接近20日低点({low20:.0f})")

    # 距60日高/低
    dist_high60 = (high60 - current) / current * 100
    dist_low60 = (current - low60) / current * 100
    if dist_high60 < 0.5:
        score -= 12
        details.append(f"接近60日高点({high60:.0f})，压力位")
    if dist_low60 < 0.5:
        details.append(f"接近60日低点({low60:.0f})，支撑位")

    score = min(100, max(0, score))
    label = "安全区" if score >= 70 else ("关键位附近" if score >= 40 else "风险区")
    return {"score": score, "label": label, "detail": "；".join(details)}


def _score_event_risk(closes):
    """
    事件风险 (0-100, 高分=低风险)
    - 近期极端波动
    - 跳空缺口
    - 波动率突变
    """
    if len(closes) < 20:
        return {"score": 50, "label": "数据不足", "detail": ""}

    details = []
    score = 80  # 默认低风险

    # 极端单日波动
    returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
    for i in range(max(-10, -len(returns)), 0):
        if abs(returns[i]) > 0.04:
            score -= 15
            details.append(f"近期单日波动{returns[i]*100:.1f}%")
            break

    # 波动率突变（5日 vs 20日）
    if len(returns) >= 20:
        vol5 = _compute_std(returns, 5)
        vol20 = _compute_std(returns, 20)
        if vol5 and vol20 and vol20 > 0:
            vol_ratio = vol5 / vol20
            if vol_ratio > 2:
                score -= 12
                details.append("波动率骤升")
            elif vol_ratio < 0.3:
                score -= 5
                details.append("波动率骤降（暴风雨前宁静）")

    score = min(100, max(0, score))
    label = "低风险" if score >= 70 else ("中风险" if score >= 40 else "高风险")
    return {"score": score, "label": label, "detail": "；".join(details)}


def fetch_index_data(code, name):
    """从 akshare 拉取指数日线数据"""
    import akshare as ak

    symbol_map = {
        "sh000001": "sh000001",
        "sz399001": "sz399001",
        "sz399006": "sz399006",
    }
    symbol = symbol_map.get(code, code)

    try:
        df = ak.stock_zh_index_daily_em(symbol=symbol)
        if df is None or df.empty:
            return None
        closes = df["close"].tolist()
        highs = df["high"].tolist()
        lows = df["low"].tolist()
        volumes = df["volume"].tolist()
        dates = df["date"].tolist()

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
            "latest_date": str(dates[-1]) if dates else "",
        }
    except Exception as e:
        print(f"[market_adapter] 拉取 {name}({code}) 失败: {e}")
        return None


def analyze_index(data):
    """对单个指数计算 6 维指标"""
    closes = data["closes"]
    highs = data["highs"]
    lows = data["lows"]
    volumes = data["volumes"]

    return {
        "trend": _score_trend(closes, highs, lows),
        "volatility": _score_volatility(closes),
        "capital_flow": _score_capital_flow(volumes, closes),
        "sentiment": _score_sentiment(closes, highs, lows),
        "key_levels": _score_key_levels(closes, highs, lows),
        "event_risk": _score_event_risk(closes),
    }


def aggregate_dimensions(index_dims_list):
    """将多个指数的 6 维指标聚合为大盘综合评分"""
    dim_names = ["trend", "volatility", "capital_flow", "sentiment", "key_levels", "event_risk"]
    aggregated = {}

    for dim in dim_names:
        scores = [d[dim]["score"] for d in index_dims_list if dim in d]
        if not scores:
            aggregated[dim] = {"score": 50, "label": "—", "detail": ""}
            continue
        avg_score = round(sum(scores) / len(scores))
        # 取第一个指数的 label 和 detail 为代表
        rep = index_dims_list[0][dim]
        aggregated[dim] = {
            "score": avg_score,
            "label": rep["label"],
            "detail": rep["detail"],
        }

    # 综合 verdict：6 维加权
    weights = {
        "trend": 0.30,
        "volatility": 0.10,
        "capital_flow": 0.25,
        "sentiment": 0.15,
        "key_levels": 0.10,
        "event_risk": 0.10,
    }
    total_score = round(sum(aggregated[d]["score"] * weights[d] for d in dim_names))

    if total_score >= 70:
        label = "强势 · 积极"
    elif total_score >= 55:
        label = "偏强 · 可操作"
    elif total_score >= 45:
        label = "中性 · 观望"
    elif total_score >= 30:
        label = "偏弱 · 谨慎"
    else:
        label = "弱势 · 避险"

    dim_summary = "；".join(
        f"{aggregated[d]['label']}" for d in ["trend", "capital_flow", "sentiment"]
    )

    return {
        "dimensions": aggregated,
        "verdict": {
            "score": total_score,
            "label": label,
            "detail": dim_summary,
        },
    }


def scan_market():
    """
    入口函数：拉取三大指数，分析 6 维，产出 sector snapshot

    Returns:
        dict: 大盘监控快照，可直接序列化为 JSON
    """
    indices_config = [
        ("sh000001", "上证综指"),
        ("sz399001", "深证成指"),
        ("sz399006", "创业板指"),
    ]

    indices = {}
    index_dims = []

    for code, name in indices_config:
        data = fetch_index_data(code, name)
        if not data:
            continue
        dims = analyze_index(data)
        index_dims.append(dims)
        indices[code] = {
            "code": data["code"],
            "name": data["name"],
            "value": data["value"],
            "change_pct": data["change_pct"],
            "latest_date": data["latest_date"],
            "dimensions": dims,
        }

    agg = aggregate_dimensions(index_dims)

    return {
        "sector": "a_share_market",
        "label": "A股大盘",
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "indices": indices,
        "dimensions": agg["dimensions"],
        "verdict": agg["verdict"],
    }


if __name__ == "__main__":
    import json

    print("=" * 60)
    print("  A股大盘温度监控 · 自检")
    print("=" * 60)

    result = scan_market()

    print(f"\n📊 综合评分: {result['verdict']['score']}/100 → {result['verdict']['label']}")
    print(f"   {result['verdict']['detail']}\n")

    for dim_name, dim in result["dimensions"].items():
        bar = "█" * (dim["score"] // 5) + "░" * (20 - dim["score"] // 5)
        print(f"  {dim_name:15s} {bar} {dim['score']:3d} {dim['label']}")

    print(f"\n各指数:")
    for code, idx in result["indices"].items():
        print(f"  {idx['name']:6s} {idx['value']:>8.1f}  {idx['change_pct']:+.2f}%")
        for dim_name, dim in idx["dimensions"].items():
            print(f"    {dim_name:12s} {dim['score']:3d} {dim['label']}")

    # 输出完整 JSON
    print(f"\n完整 JSON (前300字符):")
    print(json.dumps(result, ensure_ascii=False, indent=2)[:300])
