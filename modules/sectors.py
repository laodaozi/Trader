"""
modules/sectors.py — Layer 2: 活跃主线识别

数据来源：
  get_trending_industry  — 热门板块 + 龙头股 + 板块涨幅
  get_leader_board       — 龙虎榜 name→code 映射

API 实际字段（2026-05 验证）：
  trendingTitle              板块名称
  trendingPriceChangeRate    "0.46%"（字符串，含%）
  trendingNetFlowIn          资金净流入（元，int）
  faucet[].securityName      龙头股名称
  faucet[].priceChangeRate   "19.94%"（字符串）
  faucet[].lastPrice         最新价

注：faucet 没有 securityCode，通过 LB name→code 映射补全。

打分逻辑（满分约 12 分）：
  +4/3/2/1/0  排名（rank 0→4, rank 1→3, ...）
  +2          板块涨幅 > 3%
  +1          板块涨幅 > 1%（否则0）
  +2          有龙头股涨停（chg >= 9.5%）
  +1          有龙头股 > 5%
  +3          该板块有股票上了龙虎榜
  +1          板块资金净流入 > 5 亿
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from modules.mcp import mcp_call
from modules.pool import load_upstream_signals   # V3.9.4: 上游共振

# ── 解析工具 ─────────────────────────────────────────────

def _pct(v) -> float:
    """统一转百分比浮点：'19.94%'→19.94, 0.035→3.5, 3.5→3.5"""
    if v is None:
        return 0.0
    if isinstance(v, str):
        is_pct_str = "%" in v
        try:
            v = float(v.replace("%", "").strip())
        except ValueError:
            return 0.0
        return float(v)  # 字符串带% → 已是百分比，不再乘100
    v = float(v)
    # 小数形式(0.035→3.5%)：阈值 0.5 防止 1.5% 被误判为 150%
    return v * 100 if abs(v) < 0.5 else v


def _parse_trending(raw) -> list[dict]:
    """解析 get_trending_industry，返回标准化板块列表。"""
    items: list = []
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = (raw.get("trendingInfo") or raw.get("trending_info")
                 or raw.get("list") or [])

    sectors = []
    for item in items:
        name = (item.get("trendingTitle") or item.get("title")
                or item.get("plate_name") or item.get("name") or "")
        chg  = _pct(item.get("trendingPriceChangeRate") or item.get("changeRate")
                    or item.get("change_rate") or 0)
        flow = float(item.get("trendingNetFlowIn") or 0)

        faucets = item.get("faucet") or []
        leaders = []
        for f in faucets:
            fname = (f.get("securityName") or f.get("security_name") or "")
            fchg  = _pct(f.get("priceChangeRate") or f.get("changeRate")
                         or f.get("change_rate") or 0)
            price = float(f.get("lastPrice") or f.get("last_price") or 0)
            if fname:
                leaders.append({"code": "", "name": fname,
                                 "change_rate": fchg, "price": price})

        if name:
            sectors.append({
                "name": name, "change_rate": chg,
                "net_flow": flow, "leaders": leaders,
            })
    return sectors


def _parse_leaderboard(raw) -> list[dict]:
    """解析 get_leader_board → [{code, name, net_amount}]"""
    items = raw if isinstance(raw, list) else []
    result = []
    for item in items:
        code = item.get("security_code", "")
        name = item.get("security_name", "")
        net  = float(item.get("total_net_amount") or 0)
        if code:
            result.append({"code": code, "name": name, "net_amount": net})
    return result


# ── 核心 ─────────────────────────────────────────────────

def get_active_sectors(date: str, top_n: int = 5) -> dict:
    """
    返回：
    {
      "date": date,
      "themes": [{name, score, change_rate, net_flow, leaders, on_leaderboard, lb_codes}],
      "hot_codes": [code, ...],        # LB 上属于热点板块的代码
      "hot_sector_names": {name, ...}, # score >= 5 的板块名
      "trending_names": {name, ...},   # 所有 faucet 股票名（scanner 兼容）
    }
    """
    trend_raw = mcp_call("market_quote", "get_trending_industry", {"date": date})
    lb_raw    = mcp_call("market_quote", "get_leader_board", {"trade_date": date})

    sectors  = _parse_trending(trend_raw)
    lb_items = _parse_leaderboard(lb_raw)

    # LB name→code 映射（用于补全 faucet 代码）
    lb_by_name = {item["name"]: item["code"] for item in lb_items}
    lb_codes_set = {item["code"] for item in lb_items}

    if not sectors:
        return {
            "date": date, "themes": [],
            "hot_codes": [], "hot_sector_names": set(), "trending_names": set(),
        }

    # ── 打分 ──
    scored = []
    for rank, sec in enumerate(sectors[:10]):
        score = max(4 - rank, 0)  # rank 0→4, 1→3, 2→2, 3→1, 4+→0

        chg = sec["change_rate"]
        if chg > 3:
            score += 2
        elif chg > 1:
            score += 1

        flow = sec["net_flow"]
        if flow > 5e8:
            score += 1

        # faucet 股票分析（补全 code + 检查涨停）
        enriched_leaders = []
        has_limit_up = False
        has_strong   = False
        sec_lb_codes = []

        for ldr in sec["leaders"]:
            code = lb_by_name.get(ldr["name"], "")
            on_lb = code in lb_codes_set and bool(code)
            if on_lb:
                sec_lb_codes.append(code)
            if ldr["change_rate"] >= 9.5:
                has_limit_up = True
            elif ldr["change_rate"] >= 5:
                has_strong = True
            enriched_leaders.append({**ldr, "code": code, "on_lb": on_lb})

        if sec_lb_codes:
            score += 3
        if has_limit_up:
            score += 2
        elif has_strong:
            score += 1

        # V3.9.4: cycleradar 上游共振加分
        # 板块名与上游信号的 sectors 匹配（去后缀模糊匹配）
        upstream = load_upstream_signals(date)
        if upstream["sectors"]:
            sec_name_clean = sec["name"].rstrip()  # 去空格
            resonance_conf = "none"
            for up_signal in upstream["signals"]:
                for up_sec in up_signal.get("sectors", []):
                    # 模糊匹配：上游行业名是板块名的子串，或板块名是上游行业名的子串
                    up_sec_clean = up_sec.rstrip()
                    if up_sec_clean in sec_name_clean or sec_name_clean in up_sec_clean:
                        conf = up_signal.get("confidence", "low")
                        if conf == "high":
                            resonance_conf = "high"
                            score += 2
                        elif conf == "medium" and resonance_conf != "high":
                            resonance_conf = "medium"
                            score += 1
                        elif conf == "low" and resonance_conf == "none":
                            resonance_conf = "low"
                        break  # 一个上游 event 匹配到就够
                if resonance_conf != "none":
                    break

        scored.append({
            "name":          sec["name"],
            "score":         score,
            "change_rate":   chg,
            "net_flow":      flow,
            "leaders":       enriched_leaders,
            "on_leaderboard": bool(sec_lb_codes),
            "lb_codes":      sec_lb_codes,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    top_themes = scored[:top_n]

    # ── 汇总输出 ──
    hot_codes:        list[str] = []
    hot_sector_names: set[str]  = set()
    trending_names:   set[str]  = set()

    for theme in top_themes:
        if theme["score"] >= 5:
            hot_sector_names.add(theme["name"])
        hot_codes.extend(theme["lb_codes"])
        for ldr in theme["leaders"]:
            if ldr["name"]:
                trending_names.add(ldr["name"])

    return {
        "date":             date,
        "themes":           top_themes,
        "hot_codes":        list(dict.fromkeys(hot_codes)),
        "hot_sector_names": hot_sector_names,
        "trending_names":   trending_names,
    }


# ── 命令行 ───────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="活跃主线识别")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    args = parser.parse_args()

    r = get_active_sectors(args.date)
    print(f"\n主线识别  {r['date']}")
    print("=" * 60)
    if not r["themes"]:
        print("  无热点数据（可能是非交易日）")
    else:
        for t in r["themes"]:
            lb_tag   = " 🔥龙虎" if t["on_leaderboard"] else ""
            flow_tag = f"  净流入{t['net_flow']/1e8:.1f}亿" if t["net_flow"] > 0 else ""
            print(f"  [{t['score']:2d}分] {t['name']:<12} 涨{t['change_rate']:+.2f}%{lb_tag}{flow_tag}")
            for ldr in t["leaders"][:3]:
                lb_m = " ★" if ldr.get("on_lb") else ""
                code_s = f"({ldr['code']})" if ldr["code"] else ""
                print(f"         {ldr['name']}{code_s}  {ldr['change_rate']:+.2f}%{lb_m}")
    print("=" * 60)
    print(f"  热点代码 {len(r['hot_codes'])} 只: {r['hot_codes'][:8]}")
    print(f"  热点板块: {', '.join(r['hot_sector_names']) or '无'}")
