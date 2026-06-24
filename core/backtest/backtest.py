"""
backtest.py — CycleRadar 轮动因子回测验证（v2: 多周期 + 事件催化）

用法：
  python backtest.py                    # 拉取历史数据 + 多周期回测
  python backtest.py --from-cache       # 仅用缓存数据回测
  python backtest.py --fetch-only       # 仅拉取数据不回测
  python backtest.py --fetch-events     # 回溯采集历史事件（Phase 2a）
  python backtest.py --with-events      # 含事件条件分组验证（Phase 2b）

验证问题：
  P9: 纯量化因子在不同持仓周期下是否有效？（多周期 IC）
  P10: 事件催化是否能区分"可持续动量"和"均值回归陷阱"？

输出：
  - 底稿/backtest/plates_YYYYMMDD.json       每周板块数据缓存
  - 底稿/backtest/weekly_scores.json         各周评分结果
  - 底稿/backtest/factor_validation_v2.json  多周期 + 条件分组结果
  - 底稿/backtest/weekly_events.json         回溯事件标注（Phase 2）
  - 终端打印验证报告
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# 复用 score.py 的核心能力
from score import (
    mcp_call,
    scan_all_industries,
    _parse_flow_to_yi,
    HISTORY_DIR,
)

# Windows 终端 UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── 配置 ──────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent
BACKTEST_DIR = PROJECT_ROOT / "底稿" / "backtest"

# 8 周交易日（Step 0 验证结果）
# 请求日期 → 实际返回的交易日
TARGET_DATES = [
    "2025-12-31",  # W0 (元旦假期，API 返回 12-31)
    "2026-01-09",  # W1
    "2026-01-16",  # W2
    "2026-01-23",  # W3
    "2026-01-30",  # W4
    "2026-02-06",  # W5
    "2026-02-13",  # W6 (春节前最后交易日)
    "2026-02-27",  # W7 (春节后)
]


# ── Step 2: 批量拉取历史数据 ────────────────────────────
def fetch_plates(date_str: str) -> list:
    """拉取指定日期的板块排名，带缓存。"""
    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    date_compact = date_str.replace("-", "")
    cache_path = BACKTEST_DIR / f"plates_{date_compact}.json"

    # 优先读缓存
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            plates = data.get("plates", [])
            if plates:
                return plates
        except (json.JSONDecodeError, OSError):
            pass

    # 也检查 history 目录的已有快照
    history_path = HISTORY_DIR / f"plates_{date_compact}.json"
    if history_path.exists():
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            plates = data.get("plates", [])
            if plates:
                # 同步到 backtest 目录
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                return plates
        except (json.JSONDecodeError, OSError):
            pass

    # MCP 拉取
    print(f"  MCP 拉取 {date_str}...")
    raw = mcp_call("plates", "get_plate_rate_ranking", {
        "sector_type": [1], "num": 50, "trade_date": date_str,
    })
    if not isinstance(raw, list) or not raw:
        print(f"  ✗ {date_str} 无数据")
        return []

    # 精简保存
    compact = []
    for p in raw:
        compact.append({
            "plate_name": p.get("plate_name", ""),
            "price_change_rate": p.get("price_change_rate"),
            "major_net_flow_in": p.get("major_net_flow_in"),
            "limit_rise_count": p.get("limit_rise_count"),
        })

    out = {"date": date_str, "plates": compact}
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"  ✓ {date_str}: {len(compact)} 行业")
    return compact


def batch_fetch(from_cache: bool = False) -> dict:
    """批量拉取所有目标日期的数据。返回 {date: plates_list}。"""
    all_data = {}
    for date_str in TARGET_DATES:
        if from_cache:
            # 仅读缓存
            date_compact = date_str.replace("-", "")
            cache_path = BACKTEST_DIR / f"plates_{date_compact}.json"
            history_path = HISTORY_DIR / f"plates_{date_compact}.json"
            plates = []
            for p in [cache_path, history_path]:
                if p.exists():
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        plates = data.get("plates", [])
                        if plates:
                            break
                    except (json.JSONDecodeError, OSError):
                        pass
            if plates:
                all_data[date_str] = plates
            else:
                print(f"  ⚠ {date_str} 缓存不存在，跳过")
        else:
            plates = fetch_plates(date_str)
            if plates:
                all_data[date_str] = plates
            time.sleep(1)  # 避免频率限制

    print(f"\n有效数据: {len(all_data)}/{len(TARGET_DATES)} 周")
    return all_data


# ── Step 3: 逐周评分 ───────────────────────────────────
def score_all_weeks(all_data: dict) -> list[dict]:
    """对每周数据评分，返回按日期排序的评分列表。"""
    weeks = []
    for date_str in sorted(all_data.keys()):
        plates = all_data[date_str]
        scored = scan_all_industries(plates)
        weeks.append({
            "date": date_str,
            "scored": scored,  # 完整评分列表
        })
    return weeks


# ── Step 4-5: 计算前瞻收益 + 验证指标 ───────────────────

def _build_return_map(plates: list) -> dict:
    """从 plates 数据构建 {行业名: 涨跌幅%} 映射。"""
    m = {}
    for p in plates:
        name = p.get("plate_name", "") or p.get("name", "")
        raw_chg = p.get("price_change_rate") or p.get("price_chg")
        if not name:
            continue
        try:
            chg = float(str(raw_chg).replace("%", "").strip())
        except (TypeError, ValueError):
            chg = 0.0
        m[name] = chg
    return m


def spearman_rank_corr(x: list, y: list) -> float | None:
    """计算 Spearman 秩相关系数。x, y 为等长数值列表。"""
    n = len(x)
    if n < 3:
        return None

    def _rank(vals):
        indexed = sorted(enumerate(vals), key=lambda t: t[1])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n and indexed[j][1] == indexed[i][1]:
                j += 1
            avg_rank = (i + j - 1) / 2.0 + 1
            for k in range(i, j):
                ranks[indexed[k][0]] = avg_rank
            i = j
        return ranks

    rx = _rank(x)
    ry = _rank(y)
    d_sq_sum = sum((a - b) ** 2 for a, b in zip(rx, ry))
    rho = 1 - (6 * d_sq_sum) / (n * (n * n - 1))
    return rho


def compute_validation(weeks: list[dict], all_data: dict,
                       horizon: int = 1) -> dict:
    """
    核心验证：Week N 的评分 → Week N+horizon 的涨跌幅（前瞻收益）。
    horizon=1 为下周，horizon=2 为隔周，horizon=4 为月度。
    返回完整的验证结果。
    """
    sorted_dates = sorted(all_data.keys())

    # ── 逐周配对：score[W_n] vs return[W_{n+horizon}] ──
    weekly_details = []
    weekly_ics = []
    factor_ics = {"A1": [], "A2": [], "B1": []}
    top5_returns = []
    bot5_returns = []
    spreads = []
    stage_returns = {"启动": [], "关注": [], "观望": []}

    for i in range(len(weeks) - horizon):
        w_score = weeks[i]
        w_next_date = weeks[i + horizon]["date"]
        next_plates = all_data[w_next_date]
        ret_map = _build_return_map(next_plates)

        scored = w_score["scored"]

        # 配对: 只取两周都存在的行业
        paired_names = []
        paired_scores = []
        paired_returns = []
        paired_a1 = []
        paired_a2 = []
        paired_b1 = []

        for s in scored:
            name = s["name"]
            if name in ret_map:
                paired_names.append(name)
                paired_scores.append(s["score_auto"])
                paired_returns.append(ret_map[name])
                paired_a1.append(s["scores"]["A1"])
                paired_a2.append(s["scores"]["A2"])
                paired_b1.append(s["scores"]["B1"])

        if len(paired_names) < 5:
            continue

        # IC (综合因子)
        ic = spearman_rank_corr(paired_scores, paired_returns)
        if ic is not None:
            weekly_ics.append(ic)

        # 单因子 IC
        for fname, fvals in [("A1", paired_a1), ("A2", paired_a2), ("B1", paired_b1)]:
            fic = spearman_rank_corr(fvals, paired_returns)
            if fic is not None:
                factor_ics[fname].append(fic)

        # Top5 vs Bottom5
        # scored 已经按 score desc, price_chg desc 排序
        top5_names = [s["name"] for s in scored[:5]]
        bot5_names = [s["name"] for s in scored[-5:]]

        top5_ret_vals = [ret_map[n] for n in top5_names if n in ret_map]
        bot5_ret_vals = [ret_map[n] for n in bot5_names if n in ret_map]

        if top5_ret_vals and bot5_ret_vals:
            top_avg = sum(top5_ret_vals) / len(top5_ret_vals)
            bot_avg = sum(bot5_ret_vals) / len(bot5_ret_vals)
            top5_returns.append(top_avg)
            bot5_returns.append(bot_avg)
            spreads.append(top_avg - bot_avg)

        # 阶段信号验证
        for s in scored:
            stage = s["stage"]
            if stage in stage_returns and s["name"] in ret_map:
                stage_returns[stage].append(ret_map[s["name"]])

        # 记录详情
        weekly_details.append({
            "score_date": w_score["date"],
            "return_date": w_next_date,
            "n_paired": len(paired_names),
            "ic": round(ic, 4) if ic is not None else None,
            "top5": top5_names,
            "bot5": bot5_names,
            "top5_avg_ret": round(top_avg, 3) if top5_ret_vals else None,
            "bot5_avg_ret": round(bot_avg, 3) if bot5_ret_vals else None,
            "spread": round(top_avg - bot_avg, 3) if (top5_ret_vals and bot5_ret_vals) else None,
        })

    # ── 汇总统计 ──
    def _safe_mean(lst):
        return sum(lst) / len(lst) if lst else None

    def _safe_std(lst):
        if len(lst) < 2:
            return None
        m = sum(lst) / len(lst)
        var = sum((x - m) ** 2 for x in lst) / (len(lst) - 1)
        return math.sqrt(var)

    avg_ic = _safe_mean(weekly_ics)
    std_ic = _safe_std(weekly_ics)
    icir = (avg_ic / std_ic) if (avg_ic is not None and std_ic and std_ic > 0) else None

    hit_count = sum(1 for s in spreads if s > 0)
    hit_rate = hit_count / len(spreads) if spreads else None

    # 单因子汇总
    factor_summary = {}
    for fname in ["A1", "A2", "B1"]:
        fics = factor_ics[fname]
        factor_summary[fname] = {
            "avg_ic": round(_safe_mean(fics), 4) if _safe_mean(fics) is not None else None,
            "n_weeks": len(fics),
        }

    # 阶段信号汇总
    stage_summary = {}
    for stage_name, rets in stage_returns.items():
        stage_summary[stage_name] = {
            "avg_next_chg": round(_safe_mean(rets), 3) if _safe_mean(rets) is not None else None,
            "n_samples": len(rets),
        }

    result = {
        "horizon": horizon,
        "backtest_range": f"{sorted_dates[0]} → {sorted_dates[-1]}",
        "n_weeks_scored": len(weeks),
        "n_weeks_paired": len(weekly_details),
        "composite_factor": {
            "avg_ic": round(avg_ic, 4) if avg_ic is not None else None,
            "std_ic": round(std_ic, 4) if std_ic is not None else None,
            "icir": round(icir, 4) if icir is not None else None,
            "avg_top5_ret": round(_safe_mean(top5_returns), 3) if top5_returns else None,
            "avg_bot5_ret": round(_safe_mean(bot5_returns), 3) if bot5_returns else None,
            "avg_spread": round(_safe_mean(spreads), 3) if spreads else None,
            "hit_rate": round(hit_rate, 3) if hit_rate is not None else None,
            "hit_detail": f"{hit_count}/{len(spreads)}",
        },
        "single_factors": factor_summary,
        "stage_signal": stage_summary,
        "weekly_details": weekly_details,
    }

    return result


# ── Step 6: 输出报告 ──────────────────────────────────

def _fmt(val, fmt_str):
    """安全格式化，None → 'N/A'。"""
    if val is None:
        return "N/A"
    return format(val, fmt_str)


def print_report(result: dict):
    """终端打印单周期验证报告。"""
    horizon = result.get("horizon", 1)
    cf = result["composite_factor"]
    sf = result["single_factors"]
    ss = result["stage_signal"]

    print(f"\n{'━' * 50}")
    print(f"  Horizon = {horizon} 周")
    print(f"{'━' * 50}")
    print(f"回测区间: {result['backtest_range']}")
    print(f"有效配对: {result['n_weeks_paired']} 周")

    print(f"\n综合因子 (A1+A2+B1):")
    print(f"  平均 IC:     {_fmt(cf['avg_ic'], '+.4f')}")
    print(f"  ICIR:        {_fmt(cf['icir'], '.3f')}")
    print(f"  TOP5 均收益: {_fmt(cf['avg_top5_ret'], '+.3f')}%")
    print(f"  BOT5 均收益: {_fmt(cf['avg_bot5_ret'], '+.3f')}%")
    print(f"  多空价差:    {_fmt(cf['avg_spread'], '+.3f')}%")
    print(f"  Hit Rate:    {_fmt(cf['hit_rate'], '.1%')} ({cf['hit_detail']})")

    print(f"\n单因子 IC:")
    for fname in ["A1", "A2", "B1"]:
        fi = sf[fname]
        label = {"A1": "价格动量", "A2": "涨停计数", "B1": "资金流向"}[fname]
        print(f"  {fname} {label}: {_fmt(fi['avg_ic'], '+.4f')}")

    print(f"\n阶段信号:")
    for stage_name in ["启动", "关注", "观望"]:
        si = ss.get(stage_name, {})
        print(f"  \"{stage_name}\": {_fmt(si.get('avg_next_chg'), '+.3f')}% (N={si.get('n_samples', 0)})")


def print_multi_horizon_report(results: dict):
    """打印多周期对比报告。"""
    print("\n" + "=" * 60)
    print("  多周期因子回测对比")
    print("=" * 60)

    # 汇总表
    print(f"\n{'Horizon':>8s}  {'配对':>4s}  {'IC':>7s}  {'ICIR':>6s}  "
          f"{'TOP5':>7s}  {'BOT5':>7s}  {'价差':>7s}  {'HitRate':>8s}")
    print(f"{'─'*8}  {'─'*4}  {'─'*7}  {'─'*6}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*8}")

    for h in sorted(results.keys()):
        r = results[h]
        cf = r["composite_factor"]
        print(f"{h:>6d}周  {r['n_weeks_paired']:>4d}  "
              f"{_fmt(cf['avg_ic'], '+.4f'):>7s}  "
              f"{_fmt(cf['icir'], '.3f'):>6s}  "
              f"{_fmt(cf['avg_top5_ret'], '+.2f'):>6s}%  "
              f"{_fmt(cf['avg_bot5_ret'], '+.2f'):>6s}%  "
              f"{_fmt(cf['avg_spread'], '+.2f'):>6s}%  "
              f"{_fmt(cf['hit_rate'], '.1%'):>8s}")

    # 单因子对比
    print(f"\n单因子 IC 多周期对比:")
    print(f"{'Horizon':>8s}  {'A1 动量':>9s}  {'A2 涨停':>9s}  {'B1 资金':>9s}")
    print(f"{'─'*8}  {'─'*9}  {'─'*9}  {'─'*9}")
    for h in sorted(results.keys()):
        sf = results[h]["single_factors"]
        print(f"{h:>6d}周  "
              f"{_fmt(sf['A1']['avg_ic'], '+.4f'):>9s}  "
              f"{_fmt(sf['A2']['avg_ic'], '+.4f'):>9s}  "
              f"{_fmt(sf['B1']['avg_ic'], '+.4f'):>9s}")

    # 阶段信号对比
    print(f"\n阶段信号 多周期对比:")
    print(f"{'Horizon':>8s}  {'启动':>9s}  {'关注':>9s}  {'观望':>9s}")
    print(f"{'─'*8}  {'─'*9}  {'─'*9}  {'─'*9}")
    for h in sorted(results.keys()):
        ss = results[h]["stage_signal"]
        print(f"{h:>6d}周  "
              f"{_fmt(ss.get('启动', {}).get('avg_next_chg'), '+.3f'):>8s}%  "
              f"{_fmt(ss.get('关注', {}).get('avg_next_chg'), '+.3f'):>8s}%  "
              f"{_fmt(ss.get('观望', {}).get('avg_next_chg'), '+.3f'):>8s}%")

    # 诊断结论
    print(f"\n诊断结论:")
    any_positive_spread = any(
        (results[h]["composite_factor"]["avg_spread"] or 0) > 0
        for h in results
    )
    if any_positive_spread:
        best_h = max(results.keys(),
                     key=lambda h: results[h]["composite_factor"]["avg_spread"] or -999)
        print(f"  ✓ 因子在 {best_h} 周 horizon 下 spread 翻正 → 调整持仓周期即可")
    else:
        print(f"  ✗ 所有周期 spread 均为负 → 纯量化因子无正向预测力")
        print(f"    → 需要事件催化信息来区分\"可持续动量\"和\"均值回归陷阱\"")

    print(f"\n⚠ 限制: price_change_rate 为单日数据，非真实周/月收益；春节假期数据不连续")
    print("=" * 60)


# ── Phase 2: 事件催化验证 ─────────────────────────────

def fetch_weekly_events(from_cache: bool = False) -> dict:
    """回溯采集历史周事件，标注受影响行业。"""
    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    events_path = BACKTEST_DIR / "weekly_events.json"

    # 读缓存
    if from_cache and events_path.exists():
        with open(events_path, "r", encoding="utf-8") as f:
            return json.load(f)

    # 行业关键词映射（用于事件→行业匹配）
    SECTOR_KEYWORDS = {
        "计算机": ["AI", "人工智能", "DeepSeek", "算力", "大模型", "数据中心"],
        "通信": ["5G", "6G", "光模块", "算力", "通信"],
        "半导体": ["芯片", "半导体", "光刻", "国产替代", "EDA"],
        "电子": ["消费电子", "面板", "手机", "苹果"],
        "贵金属": ["金价", "黄金", "避险", "中东", "伊朗", "地缘"],
        "石油石化": ["油价", "原油", "中东", "OPEC", "伊朗", "石油"],
        "国防军工": ["军工", "国防", "导弹", "中东", "冲突", "军事"],
        "有色金属": ["铜价", "有色", "铝价", "锂价"],
        "小金属": ["锂", "稀土", "钨", "锗", "新能源金属"],
        "能源金属": ["锂电", "碳酸锂", "新能源车", "电池"],
        "煤炭采选": ["煤炭", "煤价", "能源"],
        "钢铁": ["钢铁", "钢价", "基建"],
        "电力设备": ["光伏", "风电", "储能", "新能源"],
        "农林牧渔": ["猪价", "农业", "粮食", "种业"],
        "医药": ["医药", "创新药", "集采", "医疗"],
        "银行": ["银行", "降息", "LPR", "信贷"],
        "非银金融": ["券商", "保险", "牛市"],
        "房地产": ["房地产", "楼市", "地产", "限购"],
        "汽车整车": ["汽车", "新能源车", "比亚迪", "特斯拉"],
        "食品饮料行业": ["白酒", "消费", "茅台"],
        "家用电器": ["家电", "空调", "消费"],
        "文化传媒": ["传媒", "游戏", "影视", "AI应用"],
        "出版": ["AI应用", "数字出版", "教育"],
        "影视院线": ["电影", "院线", "春节档"],
        "公用事业": ["电力", "水务", "高股息"],
    }

    weeks_data = []
    for date_str in TARGET_DATES:
        print(f"\n  搜索 {date_str} 周事件...")

        # 搜索当周新闻（加日期过滤）
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        week_start = (dt - timedelta(days=4)).strftime("%Y-%m-%d")  # Mon
        news_items = mcp_call("news", "search_news", {
            "query": "A股 行业 板块 利好 政策",
            "topk": 15,
            "start_date": week_start, "end_date": date_str,
        })

        events = []
        catalyst_industries = set()

        if isinstance(news_items, list):
            for item in news_items:
                title = item.get("title", "")
                content = item.get("content", "")
                text = f"{title} {content}"

                # 匹配受影响行业
                matched_sectors = []
                for sector, keywords in SECTOR_KEYWORDS.items():
                    if any(kw in text for kw in keywords):
                        matched_sectors.append(sector)

                if matched_sectors:
                    events.append({
                        "title": title[:80],
                        "sectors": matched_sectors,
                    })
                    catalyst_industries.update(matched_sectors)

        weeks_data.append({
            "date": date_str,
            "events": events[:5],  # 最多保留 5 条
            "catalyst_industries": sorted(catalyst_industries),
        })
        print(f"    {len(events)} 条事件, {len(catalyst_industries)} 个行业有催化")
        time.sleep(1)

    result = {"weeks": weeks_data}
    with open(events_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n  → {events_path}")
    return result


def compute_event_conditional(weeks: list[dict], all_data: dict,
                              events_data: dict) -> dict:
    """
    Phase 2b: 条件分组验证。
    按 HH(高分+催化) / HL(高分+无催化) / LH(低分+催化) / LL(低分+无催化) 分组，
    计算各组下周平均收益。
    """
    # 构建 {date: set(catalyst_industries)} 映射
    catalyst_map = {}
    for w in events_data.get("weeks", []):
        catalyst_map[w["date"]] = set(w.get("catalyst_industries", []))

    groups = {"HH": [], "HL": [], "LH": [], "LL": []}
    weekly_group_details = []

    for i in range(len(weeks) - 1):
        w_score = weeks[i]
        w_next_date = weeks[i + 1]["date"]
        next_plates = all_data[w_next_date]
        ret_map = _build_return_map(next_plates)
        catalysts = catalyst_map.get(w_score["date"], set())

        week_groups = {"HH": [], "HL": [], "LH": [], "LL": []}

        for s in w_score["scored"]:
            name = s["name"]
            if name not in ret_map:
                continue
            fwd_ret = ret_map[name]
            high_score = s["score_auto"] >= 1  # score >= 1 为"高分"
            has_catalyst = name in catalysts

            if high_score and has_catalyst:
                groups["HH"].append(fwd_ret)
                week_groups["HH"].append(fwd_ret)
            elif high_score and not has_catalyst:
                groups["HL"].append(fwd_ret)
                week_groups["HL"].append(fwd_ret)
            elif not high_score and has_catalyst:
                groups["LH"].append(fwd_ret)
                week_groups["LH"].append(fwd_ret)
            else:
                groups["LL"].append(fwd_ret)
                week_groups["LL"].append(fwd_ret)

        def _g_avg(lst):
            return round(sum(lst) / len(lst), 3) if lst else None

        weekly_group_details.append({
            "score_date": w_score["date"],
            "return_date": w_next_date,
            "n_catalyst": len(catalysts),
            "HH": {"n": len(week_groups["HH"]), "avg": _g_avg(week_groups["HH"])},
            "HL": {"n": len(week_groups["HL"]), "avg": _g_avg(week_groups["HL"])},
            "LH": {"n": len(week_groups["LH"]), "avg": _g_avg(week_groups["LH"])},
            "LL": {"n": len(week_groups["LL"]), "avg": _g_avg(week_groups["LL"])},
        })

    def _g_avg(lst):
        return round(sum(lst) / len(lst), 3) if lst else None

    result = {
        "group_summary": {
            g: {"n": len(vals), "avg_ret": _g_avg(vals)}
            for g, vals in groups.items()
        },
        "weekly_details": weekly_group_details,
    }

    # 核心验证指标
    hh_avg = _g_avg(groups["HH"])
    hl_avg = _g_avg(groups["HL"])
    result["key_tests"] = {
        "HH_minus_HL": round(hh_avg - hl_avg, 3) if (hh_avg is not None and hl_avg is not None) else None,
        "event_adds_alpha": (hh_avg is not None and hl_avg is not None and hh_avg > hl_avg),
        "HL_is_worst": (hl_avg is not None and all(
            (_g_avg(groups[g]) is None or hl_avg <= _g_avg(groups[g]))
            for g in ["HH", "LH", "LL"]
        )),
    }

    return result


def print_event_report(event_result: dict):
    """打印事件条件分组验证报告。"""
    print(f"\n{'=' * 60}")
    print("  事件催化 × 量化信号 条件分组验证")
    print(f"{'=' * 60}")

    gs = event_result["group_summary"]
    print(f"\n{'分组':>6s}  {'定义':>20s}  {'样本':>4s}  {'平均收益':>8s}")
    print(f"{'─'*6}  {'─'*20}  {'─'*4}  {'─'*8}")
    labels = {
        "HH": "高分 + 有事件催化",
        "HL": "高分 + 无事件催化",
        "LH": "低分 + 有事件催化",
        "LL": "低分 + 无事件催化",
    }
    for g in ["HH", "HL", "LH", "LL"]:
        info = gs[g]
        print(f"  {g:>4s}  {labels[g]:>20s}  {info['n']:>4d}  "
              f"{_fmt(info['avg_ret'], '+.3f'):>7s}%")

    kt = event_result["key_tests"]
    print(f"\n关键验证:")
    hh_hl = kt["HH_minus_HL"]
    print(f"  HH - HL spread: {_fmt(hh_hl, '+.3f')}%  "
          f"{'✓ 事件催化有增量 alpha' if kt['event_adds_alpha'] else '✗ 事件催化无增量'}")
    hl_msg = '✓ 确认"无催化高分=回撤陷阱"' if kt['HL_is_worst'] else '✗ 未确认'
    print(f"  HL 是否最差:    {hl_msg}")

    # 逐周
    print(f"\n逐周分组详情:")
    print(f"  {'评分周':>12s}  {'收益周':>12s}  {'催化':>4s}  "
          f"{'HH':>8s}  {'HL':>8s}  {'LH':>8s}  {'LL':>8s}")
    print(f"  {'─'*12}  {'─'*12}  {'─'*4}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*8}")
    for wd in event_result["weekly_details"]:
        def _g(g):
            info = wd[g]
            if info["n"] == 0:
                return "   N/A  "
            return f"{_fmt(info['avg'], '+.2f'):>6s}%"
        print(f"  {wd['score_date']:>12s}  {wd['return_date']:>12s}  "
              f"{wd['n_catalyst']:>4d}  "
              f"{_g('HH'):>8s}  {_g('HL'):>8s}  {_g('LH'):>8s}  {_g('LL'):>8s}")

    print(f"{'=' * 60}")


# ── 保存 ──────────────────────────────────────────────

# ── S7: V3.0 回测扩展 ────────────────────────────────────

# ── 统计显著性框架（V3.0新增）────────────────────────────

def compute_ic_significance(ic_list: list[float]) -> dict:
    """计算IC序列的统计显著性。

    Returns:
        {
            "n": int,
            "mean": float,
            "std": float,
            "t_stat": float,
            "p_value": float,
            "ci_95_low": float,
            "ci_95_high": float,
            "significant_at_10": bool,
            "significant_at_05": bool,
            "required_weeks_for_p05": int | None,
        }
    """
    ic_clean = [v for v in ic_list if v is not None]
    n = len(ic_clean)
    if n < 2:
        return {"n": n, "mean": None, "std": None, "t_stat": None,
                "p_value": None, "significant_at_10": False,
                "significant_at_05": False, "required_weeks_for_p05": None}

    mean = sum(ic_clean) / n
    var = sum((x - mean) ** 2 for x in ic_clean) / (n - 1)
    std = var ** 0.5

    if std < 1e-9:
        return {"n": n, "mean": round(mean, 4), "std": 0.0,
                "t_stat": None, "p_value": None,
                "significant_at_10": False, "significant_at_05": False,
                "required_weeks_for_p05": None}

    se = std / (n ** 0.5)
    t_stat = mean / se

    # 近似 p 值（双尾，使用正态近似，n>10 较准确）
    # 对于小样本这是保守估计
    z = abs(t_stat)
    # 标准正态分布近似 CDF（Abramowitz & Stegun）
    a1, a2, a3 = 0.254829592, -0.284496736, 1.421413741
    a4, a5, p_const = -1.453152027, 1.061405429, 0.3275911
    t_approx = 1.0 / (1.0 + p_const * z)
    phi = 1.0 - (a1*t_approx + a2*t_approx**2 + a3*t_approx**3 +
                  a4*t_approx**4 + a5*t_approx**5) * math.exp(-z*z/2)
    p_value = 2 * (1 - phi)  # 双尾

    # 95% CI（使用 t 分布近似值 ~1.96 for large n, ~2.26 for n=10）
    # 简化：使用 z=1.96
    t_crit = 1.96 if n >= 30 else (2.26 if n >= 10 else 2.78)
    ci_low = mean - t_crit * se
    ci_high = mean + t_crit * se

    # 需要多少周才能达到 p<0.05
    # t = mean / (std / sqrt(n)) >= 1.96 → n >= (1.96 * std / mean)^2
    required_weeks = None
    if abs(mean) > 1e-6:
        required_n = (1.96 * std / abs(mean)) ** 2
        required_weeks = max(int(math.ceil(required_n)), n)

    return {
        "n": n,
        "mean": round(mean, 4),
        "std": round(std, 4),
        "t_stat": round(t_stat, 4),
        "p_value": round(p_value, 4),
        "ci_95_low": round(ci_low, 4),
        "ci_95_high": round(ci_high, 4),
        "significant_at_10": p_value < 0.10,
        "significant_at_05": p_value < 0.05,
        "required_weeks_for_p05": required_weeks,
    }


def estimate_required_weeks(ic_mean: float, ic_std: float,
                            target_p: float = 0.05) -> int | None:
    """按当前效应量估算需要多少周数据才能达到统计显著性。"""
    if ic_std < 1e-6 or abs(ic_mean) < 1e-6:
        return None
    # z 值对应 target_p（双尾）
    z_map = {0.10: 1.645, 0.05: 1.96, 0.01: 2.576}
    z = z_map.get(target_p, 1.96)
    n = (z * ic_std / abs(ic_mean)) ** 2
    return int(math.ceil(n))


def compute_full_ic_validation(from_cache: bool = True) -> dict:
    """基于全行业台账（score_ledger_full.json）计算全截面IC。

    与 compute_v3_validation 不同：
    - 使用全部49行业的因子数据（不只TOP10）
    - IC统计更可靠（n=49 vs n=10）
    - 包含统计显著性检验

    Returns:
        {
            "periods": int,
            "by_factor": {factor: {ic_list, significance}},
            "composite": {ic_list, significance},
            "go_nogo": {指标: {current, threshold, pass}},
        }
    """
    from score import FULL_LEDGER_PATH

    if not FULL_LEDGER_PATH.exists():
        return {"error": "score_ledger_full.json 不存在，请先运行 daily.py 积累数据",
                "periods": 0}

    with open(FULL_LEDGER_PATH, "r", encoding="utf-8") as f:
        ledger = json.load(f)

    weeks = sorted(ledger.get("weeks", []), key=lambda w: w["date"])
    if len(weeks) < 2:
        return {"error": f"仅有 {len(weeks)} 期数据，需至少2期", "periods": len(weeks)}

    # 加载 plates_history 用于 T+1 收益
    plates_by_date = {}
    for src_dir in [HISTORY_DIR, BACKTEST_DIR]:
        if not src_dir.exists():
            continue
        for fp in sorted(src_dir.glob("plates_*.json")):
            date_part = fp.stem.replace("plates_", "")
            d = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:]}"
            if d in plates_by_date:
                continue
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                plates = data if isinstance(data, list) else data.get("plates", [])
                chg_map = {}
                for p in plates:
                    name = p.get("plate_name", "")
                    raw = p.get("price_change_rate")
                    if name and raw is not None:
                        try:
                            chg_map[name] = float(str(raw).replace("%", ""))
                        except (TypeError, ValueError):
                            pass
                if chg_map:
                    plates_by_date[d] = chg_map
            except (json.JSONDecodeError, OSError):
                pass

    all_dates = sorted(plates_by_date.keys())

    def _next_date(d):
        for nd in all_dates:
            if nd > d:
                return nd
        return None

    factor_names = ["A1", "A2", "A3", "B1", "C1", "C2", "D1", "D2"]
    ic_series = {f: [] for f in factor_names}
    ic_series["composite"] = []
    spread_series = []
    top5_returns = []
    bot5_returns = []

    for w in weeks:
        date = w["date"]
        next_d = _next_date(date)
        if not next_d or next_d not in plates_by_date:
            continue

        next_chg = plates_by_date[next_d]
        industries = w.get("industries", [])
        if not industries:
            continue

        # 构建配对（行业在两期都存在）
        paired_names = []
        paired_composite = []
        paired_returns = []
        factor_vals = {f: [] for f in factor_names}

        for ind in industries:
            name = ind["name"]
            if name not in next_chg:
                continue
            paired_names.append(name)
            paired_composite.append(ind.get("composite_score") or ind.get("score_auto", 0))
            paired_returns.append(next_chg[name])
            for f in factor_names:
                factor_vals[f].append(ind.get(f, 0))

        if len(paired_names) < 10:  # 至少10个行业才有意义
            continue

        # 综合因子 IC
        ic = spearman_rank_corr(paired_composite, paired_returns)
        if ic is not None:
            ic_series["composite"].append(ic)

        # 单因子 IC
        for f in factor_names:
            fic = spearman_rank_corr(factor_vals[f], paired_returns)
            if fic is not None:
                ic_series[f].append(fic)

        # Top5 vs Bot5 spread（按 composite_score 排序）
        ranked = sorted(zip(paired_names, paired_composite, paired_returns),
                        key=lambda x: -x[1])
        n5 = min(5, len(ranked) // 5)
        if n5 >= 3:
            top_ret = [r[2] for r in ranked[:n5]]
            bot_ret = [r[2] for r in ranked[-n5:]]
            top_avg = sum(top_ret) / len(top_ret)
            bot_avg = sum(bot_ret) / len(bot_ret)
            top5_returns.append(top_avg)
            bot5_returns.append(bot_avg)
            spread_series.append(top_avg - bot_avg)

    # 汇总
    result = {
        "periods": len(weeks),
        "paired_periods": len(ic_series["composite"]),
        "avg_industries_per_period": (
            round(sum(len(w.get("industries", []))
                      for w in weeks) / max(len(weeks), 1), 0)
        ),
    }

    # 单因子
    by_factor = {}
    for f in factor_names:
        sig = compute_ic_significance(ic_series[f])
        by_factor[f] = sig
    result["by_factor"] = by_factor

    # 综合因子
    comp_sig = compute_ic_significance(ic_series["composite"])
    result["composite"] = comp_sig

    # Spread
    if spread_series:
        avg_spread = sum(spread_series) / len(spread_series)
        hit_count = sum(1 for s in spread_series if s > 0)
        result["spread"] = {
            "avg": round(avg_spread, 3),
            "hit_rate": round(hit_count / len(spread_series), 3),
            "hit_detail": f"{hit_count}/{len(spread_series)}",
            "avg_top5": round(sum(top5_returns) / len(top5_returns), 3) if top5_returns else None,
            "avg_bot5": round(sum(bot5_returns) / len(bot5_returns), 3) if bot5_returns else None,
        }
    else:
        result["spread"] = {"avg": None, "hit_rate": None}

    # Go/No-Go 检查
    go_nogo = {}
    ic_mean = comp_sig.get("mean")
    ic_p = comp_sig.get("p_value")
    spread_avg = result["spread"].get("avg")
    hit_rate = result["spread"].get("hit_rate")
    n_periods = result["paired_periods"]

    go_nogo["cross_sectional_ic"] = {
        "current": ic_mean, "threshold": "> 0.05, p < 0.10",
        "pass": (ic_mean is not None and ic_mean > 0.05 and
                 ic_p is not None and ic_p < 0.10),
    }
    go_nogo["spread"] = {
        "current": spread_avg, "threshold": "> 0%",
        "pass": spread_avg is not None and spread_avg > 0,
    }
    go_nogo["hit_rate"] = {
        "current": hit_rate, "threshold": "> 50%",
        "pass": hit_rate is not None and hit_rate > 0.50,
    }
    go_nogo["min_periods"] = {
        "current": n_periods, "threshold": ">= 20",
        "pass": n_periods >= 20,
    }
    go_nogo["all_pass"] = all(v["pass"] for v in go_nogo.values())
    result["go_nogo"] = go_nogo

    # 估算所需数据量
    if comp_sig.get("mean") and comp_sig.get("std"):
        result["estimated_weeks_for_significance"] = estimate_required_weeks(
            comp_sig["mean"], comp_sig["std"])
    else:
        result["estimated_weeks_for_significance"] = None

    return result


def print_full_ic_report(result: dict):
    """打印全行业IC验证报告。"""
    print(f"\n{'=' * 60}")
    print(f"  全行业因子IC验证（全截面 n≈49）")
    print(f"{'=' * 60}")

    if result.get("error"):
        print(f"  ⚠ {result['error']}")
        return

    print(f"  数据期数: {result['periods']}期, "
          f"有效配对: {result['paired_periods']}期, "
          f"平均{result.get('avg_industries_per_period', '?')}个行业/期")

    # 综合因子
    comp = result.get("composite", {})
    print(f"\n  综合因子:")
    print(f"    IC均值:  {_fmt(comp.get('mean'), '+.4f')}")
    print(f"    IC标准差: {_fmt(comp.get('std'), '.4f')}")
    print(f"    t统计量: {_fmt(comp.get('t_stat'), '.3f')}")
    print(f"    p值:     {_fmt(comp.get('p_value'), '.4f')}")
    print(f"    95% CI:  [{_fmt(comp.get('ci_95_low'), '+.4f')}, "
          f"{_fmt(comp.get('ci_95_high'), '+.4f')}]")
    print(f"    显著(p<0.10): {'✓' if comp.get('significant_at_10') else '✗'}")
    print(f"    显著(p<0.05): {'✓' if comp.get('significant_at_05') else '✗'}")

    est = result.get("estimated_weeks_for_significance")
    if est:
        print(f"    达到p<0.05预计需要: {est} 周")

    # 单因子
    print(f"\n  {'因子':<6} {'IC均值':>8} {'p值':>8} {'显著':>4} {'样本':>4}")
    print(f"  {'-'*6} {'-'*8} {'-'*8} {'-'*4} {'-'*4}")
    for f in ["A1", "A2", "A3", "B1", "C1", "C2", "D1", "D2"]:
        fi = result.get("by_factor", {}).get(f, {})
        sig_mark = "✓" if fi.get("significant_at_10") else "✗"
        print(f"  {f:<6} {_fmt(fi.get('mean'), '+.4f'):>8} "
              f"{_fmt(fi.get('p_value'), '.4f'):>8} {sig_mark:>4} {fi.get('n', 0):>4}")

    # Spread
    sp = result.get("spread", {})
    if sp.get("avg") is not None:
        print(f"\n  Top5 vs Bot5:")
        print(f"    平均spread: {sp['avg']:+.3f}%")
        print(f"    命中率: {sp['hit_detail']}")
        print(f"    Top5均收益: {_fmt(sp.get('avg_top5'), '+.3f')}%")
        print(f"    Bot5均收益: {_fmt(sp.get('avg_bot5'), '+.3f')}%")

    # Go/No-Go
    gng = result.get("go_nogo", {})
    print(f"\n  生产准入 Go/No-Go:")
    for k, v in gng.items():
        if k == "all_pass":
            continue
        mark = "✓" if v["pass"] else "✗"
        print(f"    {mark} {k}: 当前={v['current']}, 门槛={v['threshold']}")
    all_pass = gng.get("all_pass", False)
    print(f"\n  {'✓ 全部通过，可用于生产' if all_pass else '✗ 未达标，继续积累数据'}")
    print(f"{'=' * 60}")

def compute_v3_validation(from_cache: bool = True) -> dict:
    """S7.1 V3.0 因子 IC 回测：基于 score_ledger + plates_history。

    读取本地台账和板块历史数据，调用 factor_agent.compute_factor_ic。
    不需要 MCP 调用（纯本地计算）。

    Returns:
        factor_agent.compute_factor_ic 的输出 + 元数据
    """
    from factor_agent import compute_factor_ic

    # 加载 score_ledger
    ledger_path = HISTORY_DIR / "score_ledger.json"
    if not ledger_path.exists():
        return {"error": "score_ledger.json 不存在", "periods": 0}

    with open(ledger_path, "r", encoding="utf-8") as f:
        ledger = json.load(f)

    # 加载 plates_history（从 history/ 和 backtest/ 目录汇总）
    plates_history = []
    seen_dates = set()

    # 1) history/ 目录
    if HISTORY_DIR.exists():
        for fp in sorted(HISTORY_DIR.glob("plates_*.json")):
            date_part = fp.stem.replace("plates_", "")
            d = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:]}"
            if d in seen_dates:
                continue
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                plates = data.get("plates", [])
                if plates:
                    plates_history.append({"date": d, "plates": plates})
                    seen_dates.add(d)
            except (json.JSONDecodeError, OSError):
                pass

    # 2) backtest/ 目录补充
    if BACKTEST_DIR.exists():
        for fp in sorted(BACKTEST_DIR.glob("plates_*.json")):
            date_part = fp.stem.replace("plates_", "")
            d = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:]}"
            if d in seen_dates:
                continue
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                plates = data if isinstance(data, list) else data.get("plates", [])
                if plates:
                    plates_history.append({"date": d, "plates": plates})
                    seen_dates.add(d)
            except (json.JSONDecodeError, OSError):
                pass

    plates_history.sort(key=lambda x: x["date"])

    ic_result = compute_factor_ic(ledger, plates_history)
    ic_result["plates_dates"] = len(plates_history)
    ic_result["ledger_weeks"] = len(ledger.get("weeks", []))

    return ic_result


def compute_signal_pnl() -> dict:
    """S7.2 信号 P&L 回测：扫描所有 rotation.json 中带 signal 的个股。

    V3.2 扩展：增加 by_horizon / by_strength / by_event_rank 分桶统计 + R:R 分布。

    Returns:
        {
            "total_signals": int,
            "verified": int,
            "results": [{code, name, date, signal, pnl_result}],
            "summary": {avg_pnl, win_rate, max_win, max_loss} or None,
            "by_horizon": {"1w": {...}, "2w": {...}, "1m": {...}},
            "by_strength": {"strong": {...}, "medium": {...}, "weak": {...}},
            "by_event_rank": {1: {...}, 2: {...}, ...},
            "rr_distribution": {"high(>=2)": N, "mid(1.5-2)": N, "low(1-1.5)": N, "bad(<1)": N},
            "rr_median": float | None,
        }
    """
    from verify import _check_price_targets

    raw_dir = Path(__file__).parent / "底稿" / "raw"
    results = []

    for fp in sorted(raw_dir.glob("*_rotation.json")):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                rot = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        date = rot.get("date", fp.stem.split("_")[0])

        for ev in rot.get("events", []):
            event_rank = ev.get("rank")
            event_title = ev.get("title", "")
            for stk in ev.get("stocks", []):
                signal = stk.get("signal")
                if not signal or not stk.get("code"):
                    continue
                if not signal.get("entry_price"):
                    continue

                pnl_result = _check_price_targets(
                    stk["code"], date, signal, from_cache=True)

                results.append({
                    "code": stk["code"],
                    "name": stk.get("name", ""),
                    "date": date,
                    "signal": signal,
                    "pnl_result": pnl_result,
                    "event_rank": event_rank,
                    "event_title": event_title,
                })

    # 汇总
    verified = [r for r in results
                if r["pnl_result"]["exit_type"] not in ("pending", "no_data")]
    pnls = [r["pnl_result"]["pnl_pct"] for r in verified
            if r["pnl_result"].get("pnl_pct") is not None]

    def _bucket_stats(records):
        """计算单一分桶的统计。records 为原始 results 子集。"""
        v = [r for r in records
             if r["pnl_result"]["exit_type"] not in ("pending", "no_data")]
        ps = [r["pnl_result"]["pnl_pct"] for r in v
              if r["pnl_result"].get("pnl_pct") is not None]
        if not ps:
            return {"total": len(records), "verified": 0, "win_rate": None,
                    "avg_pnl": None, "max_win": None, "max_loss": None}
        wins = [p for p in ps if p > 0]
        return {
            "total": len(records),
            "verified": len(ps),
            "win_rate": round(len(wins) / len(ps), 3),
            "avg_pnl": round(sum(ps) / len(ps), 2),
            "max_win": round(max(ps), 2),
            "max_loss": round(min(ps), 2),
        }

    summary = None
    if pnls:
        wins = [p for p in pnls if p > 0]
        summary = {
            "avg_pnl": round(sum(pnls) / len(pnls), 2),
            "win_rate": round(len(wins) / len(pnls), 3),
            "max_win": round(max(pnls), 2),
            "max_loss": round(min(pnls), 2),
            "total_pnl": round(sum(pnls), 2),
        }

    # V3.2: 分桶统计
    by_horizon = {}
    for h in ["1w", "2w", "1m"]:
        sub = [r for r in results
               if r["signal"].get("time_horizon", "2w") == h]
        if sub:
            by_horizon[h] = _bucket_stats(sub)

    by_strength = {}
    for s in ["strong", "medium", "weak"]:
        sub = [r for r in results if r["signal"].get("strength") == s]
        if sub:
            by_strength[s] = _bucket_stats(sub)

    by_event_rank = {}
    ranks = sorted({r["event_rank"] for r in results
                    if r["event_rank"] is not None})
    for rk in ranks:
        sub = [r for r in results if r["event_rank"] == rk]
        if sub:
            by_event_rank[rk] = _bucket_stats(sub)

    # V3.2: R:R 分布
    rr_distribution = {"high(>=2)": 0, "mid(1.5-2)": 0,
                       "low(1-1.5)": 0, "bad(<1)": 0, "invalid": 0}
    rr_values = []
    for r in results:
        tier = r["pnl_result"].get("rr_tier", "invalid")
        rr_distribution[tier] = rr_distribution.get(tier, 0) + 1
        rr = r["pnl_result"].get("rr_ratio")
        if rr is not None:
            rr_values.append(rr)
    rr_median = None
    if rr_values:
        sorted_rr = sorted(rr_values)
        mid = len(sorted_rr) // 2
        if len(sorted_rr) % 2 == 0:
            rr_median = round((sorted_rr[mid - 1] + sorted_rr[mid]) / 2, 2)
        else:
            rr_median = round(sorted_rr[mid], 2)

    return {
        "total_signals": len(results),
        "verified": len(verified),
        "results": results,
        "summary": summary,
        "by_horizon": by_horizon,
        "by_strength": by_strength,
        "by_event_rank": by_event_rank,
        "rr_distribution": rr_distribution,
        "rr_median": rr_median,
    }


def compare_v22_v30(v22_result: dict, v30_result: dict) -> dict:
    """S7.3 V2.2 vs V3.0 对比报告。

    Parameters:
        v22_result: compute_validation(horizon=1) 的输出
        v30_result: compute_v3_validation() 的输出

    Returns:
        对比报告 dict，含改善/恶化判定
    """
    comparison = {
        "v22": {},
        "v30": {},
        "delta": {},
        "verdict": "",
    }

    # V2.2 指标
    v22_ic = v22_result.get("ic_mean")
    v22_icir = v22_result.get("icir")
    v22_spread = v22_result.get("spread")
    v22_hit = v22_result.get("hit_rate")
    comparison["v22"] = {
        "ic_mean": v22_ic, "icir": v22_icir,
        "spread": v22_spread, "hit_rate": v22_hit,
    }

    # V3.0 指标
    v30_comp = v30_result.get("v30_composite", {})
    v30_ic = v30_comp.get("ic_mean")
    v30_icir = v30_comp.get("icir")
    comparison["v30"] = {
        "ic_mean": v30_ic, "icir": v30_icir,
        "v22_baseline_ic": v30_result.get("v22_baseline", {}).get("ic_mean"),
    }

    # Delta
    if v22_ic is not None and v30_ic is not None:
        comparison["delta"]["ic_delta"] = round(v30_ic - v22_ic, 4)
    if v22_icir is not None and v30_icir is not None:
        comparison["delta"]["icir_delta"] = round(v30_icir - v22_icir, 4)

    # 综合判定
    ic_better = (comparison["delta"].get("ic_delta", 0) > 0)
    icir_better = (comparison["delta"].get("icir_delta", 0) > 0)

    if ic_better and icir_better:
        comparison["verdict"] = "V3.0 全面优于 V2.2"
    elif ic_better or icir_better:
        comparison["verdict"] = "V3.0 部分改善"
    else:
        comparison["verdict"] = "V3.0 需更多数据验证"

    comparison["note"] = v30_result.get("note", "")
    comparison["periods"] = v30_result.get("periods", 0)

    return comparison


def print_comparison_report(comp: dict):
    """打印 V2.2 vs V3.0 对比报告。"""
    print(f"\n{'=' * 60}")
    print(f"  V2.2 vs V3.0 因子效力对比")
    print(f"{'=' * 60}")

    v22 = comp.get("v22", {})
    v30 = comp.get("v30", {})
    delta = comp.get("delta", {})

    print(f"\n  {'指标':<15} {'V2.2':>10} {'V3.0':>10} {'Δ':>10}")
    print(f"  {'-' * 45}")

    def _fmt(v):
        return f"{v:.4f}" if v is not None else "N/A"

    print(f"  {'IC均值':<15} {_fmt(v22.get('ic_mean')):>10} {_fmt(v30.get('ic_mean')):>10} {_fmt(delta.get('ic_delta')):>10}")
    print(f"  {'ICIR':<15} {_fmt(v22.get('icir')):>10} {_fmt(v30.get('icir')):>10} {_fmt(delta.get('icir_delta')):>10}")

    if v22.get("spread") is not None:
        print(f"  {'Spread':<15} {_fmt(v22.get('spread')):>10} {'—':>10} {'—':>10}")
    if v22.get("hit_rate") is not None:
        print(f"  {'命中率':<15} {_fmt(v22.get('hit_rate')):>10} {'—':>10} {'—':>10}")

    print(f"\n  判定: {comp.get('verdict', '—')}")
    print(f"  数据: {comp.get('periods', 0)} 期")
    if comp.get("note"):
        print(f"  备注: {comp['note'][:80]}")
    print()


# ── 持久化 ──────────────────────────────────────────────

def save_results(weeks: list[dict], multi_results: dict,
                 event_result: dict | None = None):
    """保存回测结果到文件。"""
    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)

    # weekly_scores.json
    weekly_out = {
        "weeks": [
            {
                "date": w["date"],
                "top5": [
                    {"name": s["name"], "score": s["score_auto"], "stage": s["stage"],
                     "price_chg": s["price_chg"], "fund_flow": s["fund_flow"]}
                    for s in w["scored"][:5]
                ],
                "bottom5": [
                    {"name": s["name"], "score": s["score_auto"], "stage": s["stage"],
                     "price_chg": s["price_chg"], "fund_flow": s["fund_flow"]}
                    for s in w["scored"][-5:]
                ],
            }
            for w in weeks
        ]
    }
    p1 = BACKTEST_DIR / "weekly_scores.json"
    with open(p1, "w", encoding="utf-8") as f:
        json.dump(weekly_out, f, ensure_ascii=False, indent=2)
    print(f"  → {p1}")

    # factor_validation_v2.json (多周期)
    output = {"multi_horizon": {str(k): v for k, v in multi_results.items()}}
    if event_result:
        output["event_conditional"] = event_result

    p2 = BACKTEST_DIR / "factor_validation_v2.json"
    with open(p2, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"  → {p2}")


# ══════════════════════════════════════════════════════
# V3.2 北极星指标：触发及时性
# ══════════════════════════════════════════════════════

def compute_trigger_coverage(date_str: str) -> dict:
    """V3.2 北极星指标：当日热点触发及时性。

    热点 ground truth 定义（用户 4/18 确认）：
      涨幅 TOP3 板块/概念 ∪ 热度TOP30 前10只个股所属主题

    V3.3 修复：sector 名称模糊匹配（双向 substring + 扩展别名表），
    解决 LLM 输出"医药"/"电力设备" vs scan "医药生物"/"电网设备" 不匹配问题。

    Returns:
        {
            "date": str,
            "ground_truth": {
                "top3_sectors": [str],     # 申万一级涨幅前3
                "top3_concepts": [str],    # 概念涨幅前3
                "hot_stocks": [str],       # 热度TOP10 (name 或 code)
            },
            "covered": {
                "sectors": [str],          # 被 rotation/morning 覆盖的板块
                "concepts": [str],
                "stocks": [str],
            },
            "missed": {
                "sectors": [str],
                "concepts": [str],
                "stocks": [str],
            },
            "coverage_rate": float,        # 总覆盖率 (0-1)
            "sector_coverage": float,      # 分项覆盖率
            "concept_coverage": float,
            "stock_coverage": float,
        }
    """
    raw_dir = Path(__file__).parent / "底稿" / "raw"

    # 扩展 sector 别名（V3.3 G1 修复）：LLM 自然语言名 → 申万一级名
    # 支持"LLM 名 → 申万名"和"申万名 → LLM 常用名"双向
    _SECTOR_ALIAS_BIDIR = {
        # 医药相关
        "医药生物": ["医药", "生物制品", "化学制药", "中药", "创新药", "医疗器械", "医药商业"],
        # 电力/新能源
        "电网设备": ["电力设备", "电气设备", "电网"],
        "电力设备": ["电网设备", "电气设备", "光伏", "风电", "储能", "新能源", "电新行业"],
        "公用事业": ["电力", "燃气", "公用事业（天然气）"],
        # 金属
        "贵金属": ["黄金", "白银", "贵金属/黄金"],
        "工业金属": ["有色金属", "铜", "铝"],
        "小金属": ["稀土", "锂", "钴"],
        # 半导体/科技
        "半导体": ["芯片", "存储芯片", "CPU", "半导体（存储）"],
        "通信设备": ["通信", "光通信", "CPO", "光模块", "AI硬件"],
        "计算机": ["软件", "云计算", "算力"],
        # 军工/航天
        "国防军工": ["军工", "航空航天", "商业航天", "军工/国防"],
        # 交运
        "航运港口": ["航运", "港口", "海运", "航运/油运"],
        "交通运输": ["物流", "快递"],
        # 化工/材料
        "基础化工": ["化工", "化学品", "新材料"],
        "非金属材料": ["玻璃", "陶瓷", "MLCC"],
        # 汽车/机器人
        "汽车整车": ["新能源汽车", "整车", "智能驾驶/自动驾驶", "智能驾驶"],
        "汽车零部件": ["机器人概念", "机器人"],
        # 能源
        "石油石化": ["石油", "油气", "炼化"],
        "煤炭采选": ["煤炭", "动力煤"],
    }

    def _sector_match(ground: str, covered_set: set) -> bool:
        """模糊匹配：ground sector 是否在 covered_set 中（含别名/双向 substring）。"""
        if not ground:
            return False
        if ground in covered_set:
            return True
        # 别名匹配
        aliases = set(_SECTOR_ALIAS_BIDIR.get(ground, []))
        # 反向：如果 covered 中某项 → ground 是它的别名
        for cv in covered_set:
            if cv in _SECTOR_ALIAS_BIDIR.get(ground, []):
                return True
            if ground in _SECTOR_ALIAS_BIDIR.get(cv, []):
                return True
            # 双向 substring（≥2字符避免误匹配）
            if len(ground) >= 2 and len(cv) >= 2:
                if ground in cv or cv in ground:
                    return True
            if cv in aliases:
                return True
        return False

    # === 1. 构造 ground truth ===
    # 1a. 行业涨幅TOP3（申万一级）
    plates_path = raw_dir / f"{date_str}.json"
    top3_sectors = []
    if plates_path.exists():
        try:
            with open(plates_path, "r", encoding="utf-8") as f:
                pdata = json.load(f)
            plates = pdata.get("plates", pdata.get("plates_ranking", []))
            # 按 price_change_rate 降序
            plates_sorted = sorted(
                plates,
                key=lambda p: float(p.get("price_change_rate", 0) or 0),
                reverse=True)
            # V3.3 G1：ground truth 加资金流过滤
            # 旧规则：涨幅 TOP3 → 容易把"单日脉冲无资金跟进"的板块算入 ground truth
            # 新规则：涨幅 TOP5 中取主力资金净流入 > 0 的前 3 个
            #         如果不够 3 个，用主力资金净流入 TOP 补位
            top5_with_flow = plates_sorted[:5]
            qualified = [p for p in top5_with_flow
                         if float(p.get("major_net_flow_in", 0) or 0) > 0]
            if len(qualified) >= 3:
                top3_sectors = [p.get("plate_name", "") for p in qualified[:3]
                                if p.get("plate_name")]
            else:
                # 不够 3 个：用资金流入排序前 3 补全
                flow_sorted = sorted(
                    plates,
                    key=lambda p: float(p.get("major_net_flow_in", 0) or 0),
                    reverse=True)
                seen = set()
                merged = []
                for p in qualified + flow_sorted:
                    nm = p.get("plate_name", "")
                    if nm and nm not in seen:
                        seen.add(nm)
                        merged.append(nm)
                    if len(merged) >= 3:
                        break
                top3_sectors = merged
        except (json.JSONDecodeError, OSError):
            pass

    # 1b. 概念板块涨幅TOP3
    concepts_path = raw_dir / f"{date_str}_concepts.json"
    top3_concepts = []
    if concepts_path.exists():
        try:
            with open(concepts_path, "r", encoding="utf-8") as f:
                cdata = json.load(f)
            concepts = cdata.get("concept_plates", cdata.get("plates", []))
            concepts_sorted = sorted(
                concepts,
                key=lambda p: float(p.get("price_change_rate", 0) or 0),
                reverse=True)
            top3_concepts = [p.get("plate_name", "") for p in concepts_sorted[:3]
                             if p.get("plate_name")]
        except (json.JSONDecodeError, OSError):
            pass

    # 1c. 热度TOP30 前10只个股
    daily_data_path = raw_dir / f"{date_str}_daily_data.json"
    hot_stocks = []
    if daily_data_path.exists():
        try:
            with open(daily_data_path, "r", encoding="utf-8") as f:
                ddata = json.load(f)
            hot = ddata.get("hot_top30_raw") or []
            for it in (hot if isinstance(hot, list) else [])[:10]:
                name = it.get("security_name", "") or it.get("name", "")
                code = it.get("security_code", "") or it.get("code", "")
                if name or code:
                    hot_stocks.append({"name": name, "code": code})
        except (json.JSONDecodeError, OSError):
            pass

    # === 2. 构造覆盖集：rotation + morning 中提到的所有板块/个股 ===
    covered_sectors: set = set()
    covered_concepts: set = set()
    covered_stocks: set = set()  # 用 code 做 key

    def _ingest(path: Path):
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                obj = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        # 兼容 rotation.json (events) 和 morning.json (themes)
        for ev in obj.get("events", []):
            for sec in ev.get("sectors", []):
                nm = sec.get("name", "")
                if nm:
                    covered_sectors.add(nm)
            for stk in ev.get("stocks", []):
                code = stk.get("code", "")
                if code:
                    covered_stocks.add(code)
        for th in obj.get("themes", []):
            for sec in th.get("related_sectors", []):
                if sec:
                    covered_sectors.add(sec)
            for stk in th.get("key_stocks", []):
                code = stk.get("code", "") if isinstance(stk, dict) else ""
                if code:
                    covered_stocks.add(code)

    _ingest(raw_dir / f"{date_str}_rotation.json")
    _ingest(raw_dir / f"{date_str}_morning.json")

    # concept 是子集常见于 sectors (申万) 或 theme 文本提及；做宽松匹配
    # 把 theme 名字与 concept 名字做 substring 匹配
    covered_concept_set = set()
    # 收集所有 theme/event title 用于概念匹配
    all_text = []
    for fn in [f"{date_str}_rotation.json", f"{date_str}_morning.json"]:
        fp = raw_dir / fn
        if fp.exists():
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                for ev in obj.get("events", []):
                    all_text.append(ev.get("title", ""))
                    for s in ev.get("sectors", []):
                        all_text.append(s.get("name", ""))
                for th in obj.get("themes", []):
                    all_text.append(th.get("theme", "") or th.get("name", ""))
                    all_text.append(th.get("catalyst", ""))
            except (json.JSONDecodeError, OSError):
                pass
    text_blob = " ".join(all_text)
    for c in top3_concepts:
        if not c:
            continue
        # V3.3 G1：双向 substring 匹配（"航天系" ↔ "商业航天" / "MLCC" ↔ "MLCC陶瓷"）
        if c in text_blob:
            covered_concept_set.add(c)
            continue
        # 反向：去掉常见后缀再试
        c_core = c.replace("系", "").replace("概念", "").strip()
        if c_core and len(c_core) >= 2 and c_core in text_blob:
            covered_concept_set.add(c)

    # === 3. 计算覆盖率 ===
    def _cov(ground: list, covered: set) -> tuple:
        if not ground:
            return None, [], []  # 无 ground truth 时返回 None（不算满分）
        hits = [g for g in ground if g in covered]
        misses = [g for g in ground if g not in covered]
        return len(hits) / len(ground), hits, misses

    def _cov_fuzzy(ground: list, covered: set) -> tuple:
        """V3.3 G1 修复：sector 模糊匹配（别名 + 双向 substring）。"""
        if not ground:
            return None, [], []
        hits = [g for g in ground if _sector_match(g, covered)]
        misses = [g for g in ground if not _sector_match(g, covered)]
        return len(hits) / len(ground), hits, misses

    # 板块用模糊匹配（名称差异大）；个股按 code 精确匹配
    sec_rate, sec_hits, sec_misses = _cov_fuzzy(top3_sectors, covered_sectors)
    concept_rate, concept_hits, concept_misses = _cov(top3_concepts, covered_concept_set)

    # 个股覆盖（按 code 匹配）
    if hot_stocks:
        hot_codes = [s["code"] for s in hot_stocks if s.get("code")]
        hits = [c for c in hot_codes if c in covered_stocks]
        misses = [c for c in hot_codes if c not in covered_stocks]
        stk_rate = len(hits) / len(hot_codes) if hot_codes else None
        stk_hits_display = [s for s in hot_stocks if s.get("code") in covered_stocks]
        stk_misses_display = [s for s in hot_stocks if s.get("code") not in covered_stocks]
    else:
        stk_rate, stk_hits_display, stk_misses_display = None, [], []

    # 综合覆盖率：只对"有数据"的维度加权（None 跳过）
    weights = []
    values = []
    if sec_rate is not None:
        weights.append(0.4); values.append(sec_rate)
    if concept_rate is not None:
        weights.append(0.3); values.append(concept_rate)
    if stk_rate is not None:
        weights.append(0.3); values.append(stk_rate)
    overall = None
    if weights:
        overall = sum(w * v for w, v in zip(weights, values)) / sum(weights)

    return {
        "date": date_str,
        "ground_truth": {
            "top3_sectors": top3_sectors,
            "top3_concepts": top3_concepts,
            "hot_stocks": [f"{s['name']}({s['code']})" for s in hot_stocks],
        },
        "covered": {
            "sectors": sec_hits,
            "concepts": concept_hits,
            "stocks": [f"{s['name']}({s['code']})" for s in stk_hits_display],
        },
        "missed": {
            "sectors": sec_misses,
            "concepts": concept_misses,
            "stocks": [f"{s['name']}({s['code']})" for s in stk_misses_display],
        },
        "coverage_rate": round(overall, 3) if overall is not None else None,
        "sector_coverage": round(sec_rate, 3) if sec_rate is not None else None,
        "concept_coverage": round(concept_rate, 3) if concept_rate is not None else None,
        "stock_coverage": round(stk_rate, 3) if stk_rate is not None else None,
        "data_complete": all(x is not None for x in [sec_rate, concept_rate, stk_rate]),
    }


# ══════════════════════════════════════════════════════
# V3.2 Sprint 3: 验证-学习闭环
# ══════════════════════════════════════════════════════

def compute_learning_feedback(days: int = 30) -> dict:
    """V3.2 Sprint 3: 聚合近 N 天策略表现，生成可注入 prompt 的反馈。

    关键输出：
      - 分桶统计（strength / event_rank / horizon）
      - R:R 分布
      - 信号演化比例
      - 自动生成 2-4 条"反直觉"洞察（如 medium 胜率高于 strong）

    用途：注入 SYSTEM_PROMPT，让 LLM 看到自身历史表现，自我校准。

    Args:
        days: 回看天数（默认 30）

    Returns:
        {
            "period": "2026-03-21 ~ 2026-04-20 (30天)",
            "sample_size": int,
            "overall": {win_rate, avg_pnl, sample},
            "by_strength": {...},
            "by_event_rank": {...},
            "by_horizon": {...},
            "rr_stats": {...},
            "evolution_stats": {...},
            "insights": [str, ...],   # 2-4 条自动洞察
            "calibration": [str, ...], # 本期校准建议
        }
    """
    from datetime import datetime, timedelta
    from verify import _check_price_targets

    raw_dir = Path(__file__).parent / "底稿" / "raw"
    cutoff_dt = datetime.now() - timedelta(days=days)

    # 1. 收集近 N 天的 signal 和 event 元数据
    results = []
    for fp in sorted(raw_dir.glob("*_rotation.json")):
        date_str = fp.stem.split("_")[0]
        try:
            date_dt = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if date_dt < cutoff_dt:
            continue
        try:
            with open(fp, "r", encoding="utf-8") as f:
                rot = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        for ev in rot.get("events", []):
            event_rank = ev.get("rank")
            event_title = ev.get("title", "")
            for stk in ev.get("stocks", []) or []:
                signal = stk.get("signal")
                if not signal or not stk.get("code"):
                    continue
                if not isinstance(signal.get("entry_price"), (int, float)):
                    continue
                pnl = _check_price_targets(
                    stk["code"], date_str, signal, from_cache=True)
                results.append({
                    "code": stk["code"],
                    "name": stk.get("name", ""),
                    "date": date_str,
                    "signal": signal,
                    "pnl_result": pnl,
                    "event_rank": event_rank,
                    "event_title": event_title,
                })

    def _bucket(records):
        verified = [r for r in records
                    if r["pnl_result"]["exit_type"] not in ("pending", "no_data")]
        pnls = [r["pnl_result"]["pnl_pct"] for r in verified
                if r["pnl_result"].get("pnl_pct") is not None]
        if not pnls:
            return {"sample": len(records), "verified": 0, "win_rate": None,
                    "avg_pnl": None}
        wins = [p for p in pnls if p > 0]
        return {
            "sample": len(records),
            "verified": len(pnls),
            "win_rate": round(len(wins) / len(pnls), 3),
            "avg_pnl": round(sum(pnls) / len(pnls), 2),
        }

    # 2. 分桶
    by_strength = {}
    for s in ["strong", "medium", "weak"]:
        sub = [r for r in results if r["signal"].get("strength") == s]
        if sub:
            by_strength[s] = _bucket(sub)

    by_event_rank = {}
    for rk in sorted({r["event_rank"] for r in results if r["event_rank"] is not None}):
        sub = [r for r in results if r["event_rank"] == rk]
        if sub:
            by_event_rank[rk] = _bucket(sub)

    by_horizon = {}
    for h in ["1w", "2w", "1m"]:
        sub = [r for r in results if r["signal"].get("time_horizon") == h]
        if sub:
            by_horizon[h] = _bucket(sub)

    # 3. R:R 统计
    rr_values = [r["pnl_result"].get("rr_ratio") for r in results
                 if r["pnl_result"].get("rr_ratio") is not None]
    rr_dist = {"high(>=2)": 0, "mid(1.5-2)": 0, "low(1-1.5)": 0,
               "bad(<1)": 0, "invalid": 0}
    for r in results:
        tier = r["pnl_result"].get("rr_tier", "invalid")
        rr_dist[tier] = rr_dist.get(tier, 0) + 1
    rr_median = None
    if rr_values:
        sorted_rr = sorted(rr_values)
        mid = len(sorted_rr) // 2
        rr_median = round(
            (sorted_rr[mid - 1] + sorted_rr[mid]) / 2
            if len(sorted_rr) % 2 == 0 else sorted_rr[mid], 2)

    # 4. 信号演化统计（从 track_record.json）
    evolution_stats = {"strengthened": 0, "weakened": 0,
                       "falsified": 0, "pending": 0}
    track_path = raw_dir.parent / "history" / "track_record.json"
    if track_path.exists():
        try:
            with open(track_path, "r", encoding="utf-8") as f:
                track = json.load(f)
            for rec in track.get("recommendations", []):
                rec_date = rec.get("date", "")
                try:
                    if datetime.strptime(rec_date, "%Y-%m-%d") < cutoff_dt:
                        continue
                except ValueError:
                    continue
                ce = rec.get("current_evolution")
                if ce in evolution_stats:
                    evolution_stats[ce] += 1
        except (json.JSONDecodeError, OSError):
            pass

    # 5. 整体统计
    overall = _bucket(results)

    # 6. 自动洞察（样本量标注）
    insights = []
    calibration = []

    # 洞察 A: strength 反直觉
    if "strong" in by_strength and "medium" in by_strength:
        s_wr = by_strength["strong"].get("win_rate")
        m_wr = by_strength["medium"].get("win_rate")
        if s_wr is not None and m_wr is not None and m_wr > s_wr + 0.1:
            s_n = by_strength["strong"]["verified"]
            m_n = by_strength["medium"]["verified"]
            if s_n >= 3 and m_n >= 3:  # 至少 3 个样本
                insights.append(
                    f"strong 胜率 {s_wr*100:.0f}%（{s_n}条）低于 medium 胜率 "
                    f"{m_wr*100:.0f}%（{m_n}条），strong 标签过于自信"
                )
                calibration.append(
                    "谨慎使用 'strong' 标签，除非催化剂已兑现且多信号共振"
                )

    # 洞察 B: rank 反直觉
    if by_event_rank:
        rank1 = by_event_rank.get(1, {})
        higher_ranks = [by_event_rank[rk] for rk in by_event_rank if rk >= 3]
        if rank1 and higher_ranks:
            r1_wr = rank1.get("win_rate")
            if r1_wr is not None and higher_ranks[0].get("verified", 0) >= 3:
                higher_wr = [b.get("win_rate") for b in higher_ranks
                             if b.get("win_rate") is not None]
                if higher_wr:
                    avg_higher = sum(higher_wr) / len(higher_wr)
                    if avg_higher > r1_wr + 0.1:
                        insights.append(
                            f"次线事件 rank3+ 胜率 {avg_higher*100:.0f}% "
                            f"高于主线 rank1 {r1_wr*100:.0f}%，次线值得给更多弹性个股"
                        )
                        calibration.append(
                            "次线事件 rank3-5 可多给 flex 弹性标的（历史表现优于主线）"
                        )

    # 洞察 C: R:R 分布
    if rr_median is not None:
        total_valid = rr_dist["high(>=2)"] + rr_dist["mid(1.5-2)"] + rr_dist["low(1-1.5)"]
        if total_valid > 0:
            high_pct = rr_dist["high(>=2)"] / total_valid
            if high_pct >= 0.5:
                insights.append(
                    f"R:R ≥2 信号占比 {high_pct*100:.0f}%，中位数 {rr_median}，"
                    f"赔率已达标无需进一步提升"
                )
            elif rr_median < 1.8:
                calibration.append(
                    f"R:R 中位数仅 {rr_median}，建议 target 再放宽至 entry × 1.08-1.15"
                )

    # 洞察 D: 信号演化
    evo_total = sum(evolution_stats.values())
    if evo_total > 0:
        f_rate = evolution_stats["falsified"] / evo_total
        s_rate = evolution_stats["strengthened"] / evo_total
        if f_rate > 0.3:
            insights.append(
                f"信号证伪率 {f_rate*100:.0f}%（{evolution_stats['falsified']}/{evo_total}），"
                f"止损设置可能过于激进"
            )
            calibration.append("考虑放宽 stop_loss（当前 -8% 可放至 -10%）")
        if s_rate > 0.5:
            insights.append(
                f"信号强化率 {s_rate*100:.0f}%，事件选取+individual picking 有效"
            )

    # 时间窗口
    period = f"{cutoff_dt.strftime('%Y-%m-%d')} ~ {datetime.now().strftime('%Y-%m-%d')} ({days}天)"

    return {
        "period": period,
        "sample_size": len(results),
        "overall": overall,
        "by_strength": by_strength,
        "by_event_rank": by_event_rank,
        "by_horizon": by_horizon,
        "rr_stats": {
            "median": rr_median,
            "distribution": rr_dist,
        },
        "evolution_stats": evolution_stats,
        "insights": insights,
        "calibration": calibration,
    }


# ── main ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="CycleRadar 轮动因子回测验证 v2")
    parser.add_argument("--from-cache", action="store_true",
                        help="仅用缓存数据回测（不调用 MCP）")
    parser.add_argument("--fetch-only", action="store_true",
                        help="仅拉取板块数据不回测")
    parser.add_argument("--fetch-events", action="store_true",
                        help="回溯采集历史事件（Phase 2a）")
    parser.add_argument("--with-events", action="store_true",
                        help="含事件条件分组验证（Phase 2b）")
    args = parser.parse_args()

    print("=" * 60)
    print("  CycleRadar 轮动因子回测 v2（多周期 + 事件催化）")
    print("=" * 60)

    # Step 1: 批量拉取板块数据
    print(f"\n[1] 拉取历史数据 ({len(TARGET_DATES)} 周)...")
    all_data = batch_fetch(from_cache=args.from_cache)

    if not all_data:
        print("✗ 无有效数据，退出")
        sys.exit(1)

    if args.fetch_only:
        print("\n--fetch-only 模式，数据已缓存，退出")
        return

    # Step 2: 逐周评分
    print(f"\n[2] 逐周评分...")
    weeks = score_all_weeks(all_data)
    for w in weeks:
        top = w["scored"][0] if w["scored"] else None
        n_startup = sum(1 for s in w["scored"] if s["stage"] == "启动")
        print(f"  {w['date']}: {len(w['scored'])} 行业, "
              f"{n_startup} 个启动, "
              f"TOP1={top['name']}({top['score_auto']}分)" if top else f"  {w['date']}: 无数据")

    # Step 3: 多周期验证 (Phase 1)
    print(f"\n[3] 多周期因子验证...")
    multi_results = {}
    for horizon in [1, 2, 4]:
        result = compute_validation(weeks, all_data, horizon=horizon)
        multi_results[horizon] = result
        if result["n_weeks_paired"] > 0:
            print(f"  horizon={horizon}: {result['n_weeks_paired']} 对, "
                  f"IC={_fmt(result['composite_factor']['avg_ic'], '+.4f')}, "
                  f"spread={_fmt(result['composite_factor']['avg_spread'], '+.3f')}%")

    # Step 4: 事件采集 (Phase 2a, optional)
    event_result = None
    if args.fetch_events or args.with_events:
        print(f"\n[4] 回溯事件采集...")
        events_data = fetch_weekly_events(from_cache=not args.fetch_events)

        if args.with_events:
            print(f"\n[5] 事件条件分组验证...")
            event_result = compute_event_conditional(weeks, all_data, events_data)

    # 输出
    print(f"\n保存结果...")
    save_results(weeks, multi_results, event_result)

    # 打印报告
    print_multi_horizon_report(multi_results)
    if event_result:
        print_event_report(event_result)


if __name__ == "__main__":
    main()
