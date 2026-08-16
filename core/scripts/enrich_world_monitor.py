#!/usr/bin/env python3
"""
世界监测 LLM 增强：为 A股大盘/行业轮动/商品期货 三大板块添加自然语言判词

用法：
    python3 enrich_world_monitor.py                # 跑全流程：采集 + LLM 增强 → data/world_monitor_enriched.json
    python3 enrich_world_monitor.py --dry-run      # 只采集不调 LLM
    python3 enrich_world_monitor.py --cache-only   # 只读缓存不调 LLM

输出：data/world_monitor_enriched.json
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from anthropic import Anthropic

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from core.monitors.market_adapter import scan_market
from core.monitors.sector_adapter import scan_sectors
from core.monitors.commodity_adapter import scan_commodities

CACHE_FILE = PROJECT_ROOT / "data" / "world_monitor_enriched.json"

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://new-api.finstep.cn")
MODEL = os.environ.get("ENRICH_MODEL", "claude-haiku-4-5-20251001")

SYSTEM_PROMPT = """你是一个全球市场监测引擎的判词引擎。

输入是三组结构化快照：A股大盘（上证/深证/创业板）、行业轮动（31个中信一级）、商品期货（黄金/原油/铜/螺纹）。
每组包含6维分数（0-100）：trend/volatility/capital_flow/sentiment/key_levels/event_risk + aggregate verdict。

你的任务：为每个 sector 写一条20-30字的中文判词+建议，再写一条40-50字的全局综述。

规则：
- 判词要有方向性（偏强/偏弱/震荡走弱/企稳/加速），不骑墙
- 提到具体维度（如"量能放大但情绪仍偏谨慎"）
- 全球综述要覆盖三板块并点出核心矛盾（如"大盘企稳但商品走弱，防御性板块占优"）
- 不要输出分析过程，只要结论

输出纯JSON：
{
  "market_verdict": "20-30字判词",
  "sector_verdict": "20-30字判词",
  "commodity_verdict": "20-30字判词",
  "global_summary": "40-50字全局综述"
}"""

USER_PROMPT_TEMPLATE = """A股大盘快照：
{market}
行业轮动快照（维度聚合 + Top5动量/资金/情绪）：
{sector}
商品期货快照（4品种）：
{commodity}

为三大板块各写一条判词，再加全局综述。"""


def _sector_snapshot_text(data):
    """将 sector snapshot 压缩为 LLM 友好的文本"""
    parts = []
    parts.append(f"综合评分: {data.get('verdict', {}).get('score', '?')}/100 → {data.get('verdict', {}).get('label', '?')}")
    parts.append(f"判词: {data.get('verdict', {}).get('detail', '')}")

    for dim_name, dim in data.get("dimensions", {}).items():
        parts.append(f"  {dim_name}: {dim['score']}/100 ({dim['label']})")

    if "indices" in data:
        for code, idx in data["indices"].items():
            parts.append(f"  {idx['name']}: {idx['value']} ({idx['change_pct']:+.2f}%)")
            for dn, dd in idx.get("dimensions", {}).items():
                parts.append(f"    {dn}: {dd['score']} {dd['label']}")

    if "top_momentum" in data:
        top = [f"{t['name']}({t['score']})" for t in data["top_momentum"][:3]]
        parts.append(f"  动量Top3: {', '.join(top)}")

    if "commodities" in data:
        for name, comm in data["commodities"].items():
            parts.append(f"  {name}: {comm['value']} ({comm['change_pct']:+.2f}%)")
            for dn, dd in comm.get("dimensions", {}).items():
                parts.append(f"    {dn}: {dd['score']} {dd['label']}")

    return "\n".join(parts)


def _call_llm(market_data, sector_data, commodity_data):
    """调 LLM 生成判词"""
    client = Anthropic(api_key=API_KEY, base_url=BASE_URL)

    user_prompt = USER_PROMPT_TEMPLATE.format(
        market=_sector_snapshot_text(market_data),
        sector=_sector_snapshot_text(sector_data),
        commodity=_sector_snapshot_text(commodity_data),
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=512,
        temperature=0.3,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    text = response.content[0].text.strip()
    # 清洗可能包裹的 ```json ... ```
    text = text.removeprefix("```json").removesuffix("```").strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        print(f"[enrich] LLM 返回非JSON，原始文本:\n{text[:300]}", file=sys.stderr)
        return {
            "market_verdict": "LLM解析失败",
            "sector_verdict": "LLM解析失败",
            "commodity_verdict": "LLM解析失败",
            "global_summary": "LLM返回格式异常",
        }


def run(dry_run=False, cache_only=False):
    """主入口"""
    print(f"[world_monitor] 开始全量采集 {datetime.now().isoformat()}")

    market_data = scan_market()
    sector_data = scan_sectors()
    commodity_data = scan_commodities()

    print(f"[world_monitor] 采集完成: "
          f"market={market_data.get('verdict',{}).get('score','?')}, "
          f"sector={sector_data.get('verdict',{}).get('score','?')}, "
          f"commodity={commodity_data.get('verdict',{}).get('score','?')}")

    if dry_run:
        print("[world_monitor] dry-run, 不调 LLM")
        output = {
            "generated_at": datetime.now().isoformat(),
            "market": market_data,
            "sector": sector_data,
            "commodity": commodity_data,
            "llm_verdicts": None,
        }
        return output

    if cache_only:
        if CACHE_FILE.exists():
            try:
                with open(CACHE_FILE) as f:
                    return json.load(f)
            except Exception:
                pass
        print("[world_monitor] 无缓存，fallback 到非LLM输出")
        return {
            "generated_at": datetime.now().isoformat(),
            "market": market_data,
            "sector": sector_data,
            "commodity": commodity_data,
            "llm_verdicts": None,
        }

    print(f"[world_monitor] 调用 LLM ({MODEL})...")
    t0 = time.time()
    verdicts = _call_llm(market_data, sector_data, commodity_data)
    elapsed = time.time() - t0
    print(f"[world_monitor] LLM 返回 (耗时 {elapsed:.1f}s)")

    output = {
        "generated_at": datetime.now().isoformat(),
        "market": market_data,
        "sector": sector_data,
        "commodity": commodity_data,
        "llm_verdicts": verdicts,
    }

    # 写缓存
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[world_monitor] 已写入 {CACHE_FILE}")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="World Monitor LLM Enrichment")
    parser.add_argument("--dry-run", action="store_true", help="只采集不调 LLM")
    parser.add_argument("--cache-only", action="store_true", help="只读缓存不调 LLM")
    args = parser.parse_args()

    result = run(dry_run=args.dry_run, cache_only=args.cache_only)

    if result.get("llm_verdicts"):
        v = result["llm_verdicts"]
        print(f"\n📊 判词:")
        print(f"  大盘: {v.get('market_verdict', '—')}")
        print(f"  行业: {v.get('sector_verdict', '—')}")
        print(f"  商品: {v.get('commodity_verdict', '—')}")
        print(f"  全局: {v.get('global_summary', '—')}")
