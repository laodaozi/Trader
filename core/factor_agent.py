"""
factor_agent.py - industry factor scan and ETF/futures mappings.

This module keeps the industry ETF/futures helpers used by local agents and
provides import-safe stubs for ECS score.py. In --scan-all mode, score.py only
calls scan_all_industries().
"""
from __future__ import annotations

from typing import Any


# Industry -> ETF mapping (A-share mainstream industry ETFs).
INDUSTRY_ETF_MAP: dict[str, dict[str, str]] = {
    # Resources / energy
    "有色金属": {"code": "512400", "name": "有色金属ETF"},
    "贵金属": {"code": "518880", "name": "黄金ETF"},
    "小金属": {"code": "159715", "name": "稀土ETF"},
    "能源金属": {"code": "159840", "name": "锂电池ETF"},
    "工业金属": {"code": "512400", "name": "有色金属ETF"},
    "石油石化": {"code": "159863", "name": "能源ETF"},
    "煤炭采选": {"code": "515220", "name": "煤炭ETF"},
    "钢铁": {"code": "515210", "name": "钢铁ETF"},
    "基础化工": {"code": "159870", "name": "化工ETF"},
    # Technology / TMT
    "计算机": {"code": "159998", "name": "计算机ETF"},
    "电子": {"code": "159997", "name": "电子ETF"},
    "半导体": {"code": "512480", "name": "半导体ETF"},
    "通信": {"code": "515880", "name": "通信ETF"},
    "光学光电子": {"code": "159997", "name": "电子ETF"},
    "电子化学品": {"code": "159997", "name": "电子ETF"},
    # Consumer
    "食品饮料行业": {"code": "515170", "name": "食品饮料ETF"},
    "家用电器": {"code": "159996", "name": "家电ETF"},
    "汽车整车": {"code": "516110", "name": "汽车ETF"},
    "美容护理": {"code": "516130", "name": "消费ETF"},
    "纺服行业": {"code": "159840", "name": "消费ETF"},
    "商贸零售": {"code": "159825", "name": "消费ETF"},
    "轻工制造": {"code": "159825", "name": "消费ETF"},
    "农林牧渔": {"code": "159825", "name": "农业ETF"},
    # Healthcare
    "医药": {"code": "512010", "name": "医药ETF"},
    "化学制药": {"code": "512010", "name": "医药ETF"},
    "生物制品": {"code": "159837", "name": "生物科技ETF"},
    # Financials
    "银行": {"code": "512800", "name": "银行ETF"},
    "非银金融": {"code": "512880", "name": "证券ETF"},
    "证券": {"code": "512880", "name": "证券ETF"},
    "多元金融": {"code": "512880", "name": "证券ETF"},
    # Infrastructure / industrials
    "建筑工程": {"code": "516970", "name": "基建ETF"},
    "建筑材料": {"code": "159745", "name": "建材ETF"},
    "机械设备": {"code": "159886", "name": "机械ETF"},
    "通用设备": {"code": "159886", "name": "机械ETF"},
    "电网设备": {"code": "159840", "name": "新能源ETF"},
    "交运设备": {"code": "516110", "name": "汽车ETF"},
    "电力设备": {"code": "159840", "name": "新能源ETF"},
    "电新行业": {"code": "159840", "name": "新能源ETF"},
    "环保": {"code": "159861", "name": "环保ETF"},
    # Transportation / logistics
    "航运港口": {"code": "159662", "name": "交运ETF"},
    "交通运输": {"code": "159662", "name": "交运ETF"},
    "机场": {"code": "159662", "name": "交运ETF"},
    # Real estate / utilities
    "房地产": {"code": "512200", "name": "房地产ETF"},
    "公用事业": {"code": "159611", "name": "电力ETF"},
    # Defense / media / services
    "国防军工": {"code": "512660", "name": "军工ETF"},
    "文化传媒": {"code": "512980", "name": "传媒ETF"},
    "影视院线": {"code": "159869", "name": "游戏ETF"},
    "出版": {"code": "512980", "name": "传媒ETF"},
    "社会服务": {"code": "159766", "name": "旅游ETF"},
}


# Industry -> related commodity futures mapping.
INDUSTRY_FUTURES_MAP: dict[str, list[dict[str, str]]] = {
    "有色金属": [{"name": "沪铜"}, {"name": "沪铝"}, {"name": "沪锌"}],
    "贵金属": [{"name": "沪金"}, {"name": "沪银"}],
    "小金属": [{"name": "碳酸锂"}, {"name": "工业硅"}],
    "能源金属": [{"name": "碳酸锂"}, {"name": "沪镍"}],
    "工业金属": [{"name": "沪铜"}, {"name": "沪铝"}, {"name": "沪锌"}],
    "石油石化": [{"name": "原油"}, {"name": "PTA"}, {"name": "沥青"}],
    "煤炭采选": [{"name": "焦煤"}, {"name": "焦炭"}, {"name": "动力煤"}],
    "钢铁": [{"name": "螺纹钢"}, {"name": "热卷"}, {"name": "铁矿石"}],
    "基础化工": [{"name": "甲醇"}, {"name": "尿素"}, {"name": "纯碱"}],
    "农林牧渔": [{"name": "豆粕"}, {"name": "玉米"}, {"name": "生猪"}],
    "电力设备": [{"name": "碳酸锂"}, {"name": "工业硅"}],
    "电新行业": [{"name": "碳酸锂"}, {"name": "工业硅"}],
    "公用事业": [{"name": "动力煤"}, {"name": "天然气"}],
    "建材": [{"name": "玻璃"}, {"name": "纯碱"}],
}


def _parse_float(value: Any) -> float | None:
    """Parse market data values like '+3.45' or '10.27亿元'."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None

    for token in ("亿元", "亿", "%", ",", " "):
        text = text.replace(token, "")
    if text.startswith("+"):
        text = text[1:]

    try:
        return float(text)
    except ValueError:
        return None


def _parse_int(value: Any) -> int:
    """Parse count-like values and fall back to zero for bad input."""
    parsed = _parse_float(value)
    if parsed is None:
        return 0
    return int(parsed)


def scan_all_industries(plates: list) -> list[dict]:
    """Score all unique industry plates with momentum, limit-up, and fund-flow factors."""
    unique_plates: dict[str, dict] = {}
    for plate in plates or []:
        if not isinstance(plate, dict):
            continue
        name = str(plate.get("plate_name") or "").strip()
        if not name or name in unique_plates:
            continue
        unique_plates[name] = plate

    results: list[dict] = []
    for name, plate in unique_plates.items():
        price_chg = _parse_float(plate.get("price_change_rate")) or 0.0
        fund_flow = _parse_float(plate.get("major_net_flow_in"))
        limit_count = _parse_int(plate.get("limit_rise_count"))

        a1 = 1 if price_chg > 3.0 else 0
        a2 = 1 if limit_count >= 3 else 0
        b1 = 1 if fund_flow is not None and fund_flow > 0 else 0
        score_auto = float(a1 + a2 + b1)

        results.append(
            {
                "name": name,
                "score_auto": score_auto,
                "stage": "关注" if score_auto >= 2.0 else "观望",
                "price_chg": price_chg,
                "fund_flow": fund_flow,
                "limit_rise": limit_count,
                "rank": 0,
                "scores": {"A1": a1, "A2": a2, "B1": b1},
                "weekly_flow": 0.0,
                "consecutive_top10": 0,
            }
        )

    results.sort(key=lambda item: (item["score_auto"], item["scores"]["A1"]), reverse=True)
    for index, result in enumerate(results, start=1):
        result["rank"] = index

    return results


def fetch_leader_board_institutional(date_str: str) -> dict:
    """Stub for institutional leaderboard fetch; returns an empty mapping."""
    return {}


def fetch_block_trade_summary(date_str: str) -> dict:
    """Stub for block trade summary fetch; returns an empty mapping."""
    return {}


def fetch_margin_balance_surplus(date_str: str) -> dict:
    """Stub for margin balance surplus fetch; returns an empty mapping."""
    return {}


def compute_valuation_percentile(industry: str, pe: float) -> float | None:
    """Stub for valuation percentile calculation; returns no percentile."""
    return None


def compute_cross_asset_signals(*args, **kwargs) -> list:
    """Stub for cross-asset signal calculation; returns no signals."""
    return []


def scan_concept_plates(plates: list) -> list:
    """Stub for concept plate scanning; returns no concepts."""
    return []


def compute_market_temperature(plates: list) -> dict:
    """Stub for market temperature calculation; returns a neutral reading."""
    return {"temperature": "normal", "score": 50}


def enrich_scan_results(scan_results: list, ledger: dict) -> list:
    """Stub for scan enrichment; returns scan results unchanged."""
    return scan_results


def filter_persistent_concepts(concepts: list, ledger: dict) -> list:
    """Stub for persistent concept filtering; returns concepts unchanged."""
    return concepts


def get_etf_for_industry(name: str) -> dict[str, str] | None:
    """Return ETF mapping {code, name} for an industry, or None if absent."""
    return INDUSTRY_ETF_MAP.get(name)


def get_futures_for_industry(name: str) -> list[dict[str, str]]:
    """Return related futures [{name}, ...] for an industry, or an empty list."""
    return INDUSTRY_FUTURES_MAP.get(name, [])


# ── V4.2 stub: daily.py 直接导入但未实现 ──────────────────
def compose_score(scan_results: list) -> list:
    """复合因子分合成 (stub: 直通)."""
    return scan_results


def enrich_with_composite(scan_results: list) -> list:
    """复合因子加权增强 (stub: 直通)."""
    return scan_results


def dedup_correlated_industries(scan_results: list, top_n: int = 10) -> list:
    """同风格行业相关性去重，保留排名最高者 (stub: 直通)."""
    return scan_results[:top_n]
