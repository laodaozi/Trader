"""
rotation_factor.py -- 行业轮动因子引擎 V1.1

职责：独立于 Claude API 的行业轮动因子计算与结论生成。
原则：规则引擎先行——因子本身就能产出结论，LLM 只做润色。

因子体系（8因子 覆盖 A/B/C/D/E 五维）:
  A1: 超额收益    industry_chg - hs300_chg >= 3%  → 1分
  A2: 涨停热度    涨停数 >= 3                      → 1分
  B1: 资金确认    主力净流入 > 0                    → 1分
  B2: 融资热度    融资余额排名进TOP30 且 净买入>0   → 1分
  C1: 估值安全    PE历史分位 < 30%                  → 1分
  D1: 产业资本    近5日大宗交易净买入 > 0            → 1分
  D2: 机构持仓    龙虎榜机构净买入 > 0              → 1分
  E1: 舆情共振    ETF中枢突破或情绪共振              → 1分

衍生指标:
  轮动强度  = 当期得分 - 上期得分       (方向+力度)
  轮动持续性 = 连续 TOP10 期数          (耐力)
  轮动质量  = 持续期数 × 当期得分       (耐力×力度)

输出:
  1. 行业排名表（49行业，按得分排序）
  2. 轮动信号（新晋/退出/升温/降温/持续/加速/减速）
  3. 轮动结论文本（规则引擎直接产出，不需要 LLM）
"""

from __future__ import annotations
from typing import Optional


# ── 因子定义（配置表）───────────────────────────────────

FACTOR_DEFINITIONS = {
    "A1": {
        "name": "超额收益",
        "desc": "行业涨幅 - 沪深300涨幅 >= 3%",
        "weight": 1.0,
        "category": "短期动量",
    },
    "A2": {
        "name": "涨停热度",
        "desc": "涨停家数 >= 3",
        "weight": 1.0,
        "category": "短期动量",
    },
    "B1": {
        "name": "资金确认",
        "desc": "主力净流入 > 0",
        "weight": 1.0,
        "category": "中期资金",
    },
    # V1.1 P2新增：C/B/E 三维补充因子
    "B2": {
        "name": "融资热度",
        "desc": "融资余额排名进TOP30 且 净融资买入为正",
        "weight": 1.0,
        "category": "中期资金",
    },
    "C1": {
        "name": "估值安全",
        "desc": "行业PE历史分位 < 30%",
        "weight": 0.5,
        "category": "估值水位",
    },
    "D1": {
        "name": "产业资本",
        "desc": "近5日大宗交易净买入 > 0",
        "weight": 1.0,
        "category": "长期资本",
    },
    "D2": {
        "name": "机构持仓",
        "desc": "龙虎榜机构净买入 > 0",
        "weight": 1.0,
        "category": "长期资本",
    },
    "E1": {
        "name": "舆情共振",
        "desc": "ETF中枢突破 或 微信舆情情绪共振",
        "weight": 1.5,
        "category": "情绪舆情",
    },
}

# 预热区：排名 11-20 且 B1=1 标记为 A3
PREHEAT_RANK_START = 11
PREHEAT_RANK_END = 20

# 轮动信号阈值（V1.1: 适配8因子加权满分8.0）
ROTATION_SIGNAL_THRESHOLDS = {
    "升温": 3,          # 得分 Δ >= +3
    "降温": -2,         # 得分 Δ <= -2
    "持续轮动": 3,      # 连续 TOP10 >= 3 期 且 stage=确认
    "加速": 4,          # 得分 Δ >= +4
    "减速": -3,         # 得分 Δ <= -3
}


# ── 阶段判断 ────────────────────────────────────────────

def score_to_stage(score: float) -> str:
    """得分 → 阶段标签（V1.1: 8因子加权满分 8.0）"""
    if score >= 6.0:
        return "确认"
    elif score >= 3.0:
        return "关注"
    elif score >= 1.0:
        return "观察"
    else:
        return "观望"


def score_to_level(score: float) -> str:
    """得分 → 强度等级（V1.1: 8因子加权满分 8.0）"""
    if score >= 6.0:
        return "强"
    elif score >= 3.0:
        return "中"
    else:
        return "弱"


# ── 轮动结论规则引擎 ─────────────────────────────────────

# 结论模板：基于因子状态直接产出文本，不依赖 LLM

def generate_rotation_conclusion(
    top3: list[dict],
    trending_up: list[dict],
    trending_down: list[dict],
    persistent: list[dict],
) -> dict:
    """规则引擎：从因子数据直接生成轮动结论。

    返回 {
        "summary": str,           # 一句话结论
        "main_theme": str,        # 主线方向
        "rotation_phase": str,    # 轮动阶段
        "strength": str,          # 强度评估
        "risks": list[str],       # 风险提示
        "raw": dict,              # 原始数据
    }
    """
    # ── 主线判断 ──
    top3_names = [r["name"] for r in top3]
    top3_scores = [r.get("score_auto", 0) for r in top3]

    # 如果有持续轮动行业，优先级最高
    persistent_names = [p["industry"] for p in persistent]
    persistent_in_top3 = [n for n in persistent_names if n in top3_names]

    # ── 轮动阶段判断 ──
    avg_score = sum(top3_scores) / len(top3_scores) if top3_scores else 0
    has_persistent = len(persistent) > 0
    has_trending = len(trending_up) > 0
    has_cooling = len(trending_down) > 0

    if has_persistent and avg_score >= 5.0:
        phase = "主升"
        phase_desc = "持续轮动进行中，主线明确"
    elif has_trending and avg_score >= 3.0:
        phase = "启动"
        phase_desc = "新主线正在形成，关注确认信号"
    elif has_cooling and avg_score < 3.0:
        phase = "退潮"
        phase_desc = "前期主线降温，等待新方向"
    else:
        phase = "混沌"
        phase_desc = "无明确主线，资金分散"

    # ── 主线方向 ──
    if persistent_in_top3:
        main_theme = f"「{' + '.join(persistent_in_top3)}」持续走强"
    elif top3_names and avg_score >= 3.0:
        main_theme = f"「{' + '.join(top3_names[:2])}」领涨"
    else:
        main_theme = "方向不明，等待信号"

    # ── 一句话结论 ──
    if phase == "主升":
        summary = (
            f"轮动处于主升阶段，{main_theme}。"
            f"TOP3行业均分{avg_score:.1f}/8，建议重配主线。"
        )
    elif phase == "启动":
        summary = (
            f"轮动处于启动阶段，{main_theme}。"
            f"新晋TOP10行业{len(trending_up)}个，需关注持续性。"
        )
    elif phase == "退潮":
        summary = (
            f"轮动处于退潮阶段，前期主线降温。"
            f"{len(trending_down)}个行业退出TOP10，建议降低仓位。"
        )
    else:
        summary = (
            f"轮动处于混沌阶段，无明确主线。"
            f"TOP3行业均分仅{avg_score:.1f}/8，建议观望或轻仓试错。"
        )

    # ── 风险提示 ──
    risks = []
    for d in trending_down:
        risks.append(f"「{d['industry']}」退出TOP10，注意风险")
    if avg_score < 2.0:
        risks.append("整体得分偏低，市场偏弱")

    return {
        "summary": summary,
        "main_theme": main_theme,
        "rotation_phase": phase,
        "phase_desc": phase_desc,
        "strength": score_to_level(avg_score),
        "risks": risks,
        "raw": {
            "top3": top3_names,
            "top3_scores": top3_scores,
            "avg_score": round(avg_score, 1),
            "trending_up_count": len(trending_up),
            "trending_down_count": len(trending_down),
            "persistent_count": len(persistent),
        },
    }


# ── 轮动强度计算 ─────────────────────────────────────────

def compute_rotation_intensity(
    current_scan: list[dict],
    prev_scan_map: dict[str, dict],
    top_n: int = 10,
) -> dict:
    """计算轮动强度指标。

    返回 {
        "intensity": float,          # 整体轮动强度 (-100 ~ +100)
        "acceleration": list[dict],  # 加速行业
        "deceleration": list[dict],  # 减速行业
        "turnover": float,           # TOP10换手率 (0-1)
    }
    """
    curr_top = current_scan[:top_n]
    curr_names = {r["name"] for r in curr_top}

    # TOP10 换手率 = 新进入比例
    new_entries = curr_names - set(prev_scan_map.keys())
    turnover = len(new_entries) / top_n if top_n > 0 else 0

    # 加速度：得分变化
    acceleration = []
    deceleration = []
    total_delta = 0

    for r in curr_top:
        name = r["name"]
        curr_score = r.get("score_auto", 0)
        prev = prev_scan_map.get(name, {})
        prev_score = prev.get("score", 0) if prev else 0
        delta = curr_score - prev_score
        total_delta += delta

        if delta >= 4:
            acceleration.append({"industry": name, "delta": delta, "rank": r["rank"]})
        elif delta <= -3:
            deceleration.append({"industry": name, "delta": delta, "rank": r["rank"]})

    # 整体轮动强度（归一化到 -100 ~ +100）
    max_possible = top_n * 8  # 8因子加权满分（V1.1）
    intensity = (total_delta / max_possible) * 100 if max_possible > 0 else 0

    return {
        "intensity": round(intensity, 1),
        "acceleration": acceleration,
        "deceleration": deceleration,
        "turnover": round(turnover, 2),
    }


# ── 因子归因分析 ─────────────────────────────────────────

def attribute_factor_score(
    industry: dict,
    include_details: bool = True,
) -> dict:
    """对单个行业的因子得分做归因。

    返回 {
        "total": float,              # 加权总分（含权重）
        "active_factors": list[str],  # 命中的因子（触发 > 0）
        "missing_factors": list[str], # 未命中的因子（触发 == 0）
        "factor_detail": dict,        # 每个因子的详情
    }
    """
    scores = industry.get("scores", {})
    # V1.1: scores 含权重（C1=0.5, E1=1.5），用 >0 判断是否命中
    active = [k for k, v in scores.items() if v > 0]
    missing = [k for k in FACTOR_DEFINITIONS if scores.get(k, 0) <= 0]

    result = {
        "total": industry.get("score_auto", 0),
        "active_factors": active,
        "missing_factors": missing,
    }

    if include_details:
        result["factor_detail"] = {
            k: {
                "hit": scores.get(k, 0) > 0,
                "label": FACTOR_DEFINITIONS[k]["name"],
                "category": FACTOR_DEFINITIONS[k]["category"],
            }
            for k in FACTOR_DEFINITIONS
        }

    return result


# ── 模块入口（给 daily.py 调用）─────────────────────────

def run_rotation_analysis(
    scan_results: list[dict],
    rotation_signals: list[dict],
    prev_scan_map: dict[str, dict],
) -> dict:
    """一站式轮动分析入口。

    入参:
        scan_results: scan_all_industries() 的返回
        rotation_signals: detect_rotation_signals() 的返回
        prev_scan_map: 上期 scan 结果的 {name: data} 映射

    返回:
        {
            "sector_ranking": [...],        # 行业排名
            "rotation_signals": [...],      # 轮动信号
            "rotation_intensity": {...},    # 轮动强度
            "conclusion": {...},            # 规则引擎结论
            "data_version": "v1.1",
        }
    """
    # 提取 TOP3
    top3 = scan_results[:3]

    # 按信号类型分类
    trending_up = [s for s in rotation_signals if s["type"] == "新晋TOP10"]
    trending_down = [s for s in rotation_signals if s["type"] == "退出TOP10"]
    persistent = [s for s in rotation_signals if s["type"] == "持续轮动"]

    # 计算轮动强度
    intensity = compute_rotation_intensity(scan_results, prev_scan_map)

    # 生成结论
    conclusion = generate_rotation_conclusion(
        top3, trending_up, trending_down, persistent
    )

    # 给每个行业加因子归因
    enriched_ranking = []
    for r in scan_results:
        entry = dict(r)
        entry["attribution"] = attribute_factor_score(r, include_details=False)
        enriched_ranking.append(entry)

    return {
        "sector_ranking": enriched_ranking,
        "rotation_signals": rotation_signals,
        "rotation_intensity": intensity,
        "conclusion": conclusion,
        "data_version": "v1.0",
    }


# ── CLI ──────────────────────────────────────────────────
def _format_expiry(date_str: str, horizon_days: int = 14) -> str:
    """计算信号过期日（盘后 15:05 到期）。"""
    from datetime import datetime as dt, timedelta
    d = dt.strptime(date_str, "%Y-%m-%d")
    return (d + timedelta(days=horizon_days)).strftime("%Y-%m-%d") + "T15:05:00"


if __name__ == "__main__":
    import argparse
    import json
    import sys
    from datetime import datetime, timedelta
    from pathlib import Path

    parser = argparse.ArgumentParser(
        description="rotation_factor — 行业轮动八因子引擎（V6.1 独立定时）")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"),
                       help="日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--write", action="store_true",
                       help="写入 upstream_signals.jsonl + rotation_snapshots.jsonl")
    parser.add_argument("--dry-run", action="store_true",
                       help="只打印不写入")
    args = parser.parse_args()

    date_str = args.date

    # 设置项目根路径，使 import 能解析
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    # ── 1. 数据采集（复用 score._load_plates 三级链路）────
    from core.score import _load_plates

    print(f"[rotation_factor] {date_str} · 数据采集...")
    plates = _load_plates(date_str, from_cache=False)
    if not plates:
        print("[rotation_factor] ❌ 无法加载板块数据，退出", file=sys.stderr)
        sys.exit(1)
    print(f"  ✓ 获取 {len(plates)} 个板块")

    # ── 2. 八因子评分 ─────────────────────────────────────
    from core.factor_agent import scan_all_industries, INDUSTRY_ETF_MAP

    print(f"[rotation_factor] 八因子评分...")
    scan_results = scan_all_industries(plates)
    print(f"  ✓ 评分完成，TOP5: {', '.join(r['name'] for r in scan_results[:5])}")

    # ── 3. 前一交易日 scan 快照（计算轮动强度用）───────────
    prev_scan_map: dict[str, dict] = {}
    try:
        RAW_DIR = PROJECT_ROOT / "data" / "raw"
        prev_date = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1))
        for _ in range(5):
            prev_path = RAW_DIR / f"{prev_date.strftime('%Y-%m-%d')}_scan.json"
            if prev_path.exists():
                with open(prev_path, "r", encoding="utf-8") as pf:
                    prev_data = json.load(pf)
                for r in prev_data.get("rankings", []):
                    prev_scan_map[r["name"]] = r
                print(f"  ✓ 加载前一交易日 scan: {prev_date.strftime('%Y-%m-%d')}")
                break
            prev_date -= timedelta(days=1)
    except Exception as e:
        print(f"  ⚠ 无历史 scan（{e}），轮动强度仅用当期数据")

    # ── 4. 跑八因子引擎 ────────────────────────────────────
    print(f"[rotation_factor] 八因子引擎计算...")
    rf_output = run_rotation_analysis(scan_results, [], prev_scan_map)

    ranking = rf_output.get("sector_ranking", [])
    conclusion = rf_output.get("conclusion", {})
    intensity = rf_output.get("rotation_intensity", {})

    phase = conclusion.get("phase", "混沌")
    key_thesis = conclusion.get("key_thesis", "")
    print(f"  轮动阶段: {phase} | 强度: {intensity.get('score', 0)} | {key_thesis}")

    # ── 5. 生成 ETF 信号 ───────────────────────────────────
    etf_signals: list[dict] = []
    timestamp = f"{date_str}T16:00:00"
    expiry = _format_expiry(date_str, 14)

    for seq, r in enumerate(ranking[:10], 1):
        name = r.get("name", "")
        stage = r.get("stage", "观望")
        score_val = r.get("score_auto", 0)
        attribution = r.get("attribution", {})

        etf_info = INDUSTRY_ETF_MAP.get(name)
        if not etf_info:
            continue

        # direction 映射
        if stage == "确认":
            direction = "long"
            conf = min(0.85, 0.55 + score_val * 0.06)
        elif stage == "关注":
            direction = "long"
            conf = min(0.70, 0.40 + score_val * 0.06)
        else:
            direction = "watch"
            conf = 0.30

        sig_id = f"rotation_factor-{date_str.replace('-', '')}-{seq:02d}"
        etf_signals.append({
            "signal_id": sig_id,
            "timestamp": timestamp,
            "strategy": "rotation_factor",
            "asset": etf_info["code"],
            "asset_name": etf_info["name"],
            "asset_type": "etf",
            "direction": direction,
            "confidence": round(conf, 2),
            "expiry": expiry,
            "metadata": {
                "stage": stage,
                "score_auto": score_val,
                "sector": name,
                "attribution": attribution,
                "rotation_phase": phase,
            },
        })

    # ── 6. 输出 ────────────────────────────────────────────
    if etf_signals:
        print(f"\n{'='*60}")
        print(f"  rotation_factor · {date_str}")
        print(f"  ETF 信号: {len(etf_signals)} 条 | 阶段: {phase}")
        print(f"{'='*60}")
        for s in etf_signals[:10]:
            m = s["metadata"]
            print(f"  {m['stage'][:2]:>2} {s['asset_name']:<12} {s['direction']:<6} "
                  f"conf={s['confidence']:.2f}")

        if args.write and not args.dry_run:
            # 写入 upstream_signals.jsonl
            from core.signals.upstream_signals import write_signal as _ws
            written = 0
            for s in etf_signals:
                _ws(s, normalize=True)
                written += 1
            print(f"\n  ✅ 已写入 {written} 条 ETF 信号到 upstream_signals.jsonl")

            # 写入 rotation_snapshots.jsonl
            snap_dir = PROJECT_ROOT / "data"
            snap_dir.mkdir(parents=True, exist_ok=True)
            snap_path = snap_dir / "rotation_snapshots.jsonl"
            snapshot = {
                "date": date_str,
                "phase": phase,
                "intensity": intensity,
                "key_thesis": key_thesis,
                "top3": [
                    {"name": r["name"], "score": r.get("score_auto", 0), "stage": r.get("stage", "")}
                    for r in ranking[:3]
                ],
                "etf_count": len(etf_signals),
            }
            with open(snap_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
            print(f"  ✅ 已写轮动快照到 rotation_snapshots.jsonl")
    else:
        print(f"\n  ⚠ 无 ETF 信号产出（无匹配 ETF 的行业）")

    sys.exit(0)
