#!/usr/bin/env python3
"""
世界监测合约生成器：enriched.json → contracts.json
将 LLM 增强后的结构化数据，转换为 /m/api/world 前端消费的合约格式。

桥接关系：
  enriched.market  → contracts.sectors.a_share_market
  enriched.sector  → contracts.sectors.sector_rotation
  enriched.commodity → contracts.sectors.commodity
  enriched.llm_verdicts.global_summary → contracts.global_summary

裁剪原则：只保留前端渲染所需的字段（name/change_pct/score/label/symbol），
丢弃 detail/closes/volumes/raw 数据，减小 ECS 内存占用（目标 < 8KB）。

用法：
    python3 generate_world_contract.py
    读 data/world_monitor_enriched.json → 写 data/world_monitor_contracts.json
"""

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENRICHED_PATH = PROJECT_ROOT / "data" / "world_monitor_enriched.json"
CONTRACT_PATH = PROJECT_ROOT / "data" / "world_monitor_contracts.json"


def slim_dimensions(dims):
    """dimensions: 只保留 score + label，丢弃 detail"""
    return {
        k: {"score": v.get("score", 50), "label": v.get("label", "—")}
        for k, v in dims.items()
    }


def slim_indices(indices):
    """indices: 只保留 name + change_pct"""
    return {
        k: {
            "name": v.get("name", "—"),
            "change_pct": v.get("change_pct", 0),
        }
        for k, v in indices.items()
    }


def slim_commodities(commodities):
    """commodities: 只保留 symbol + change_pct"""
    return {
        k: {
            "symbol": v.get("symbol", "—"),
            "change_pct": v.get("change_pct", 0),
        }
        for k, v in commodities.items()
    }


def build_contract(enriched):
    """主转换逻辑"""
    en = enriched
    llm = en.get("llm_verdicts") or {}

    market = en.get("market") or {}
    sector = en.get("sector") or {}
    commodity = en.get("commodity") or {}

    sectors = {}

    # ── A股大盘 ──
    if market:
        sectors["a_share_market"] = {
            "label": market.get("label", "A股大盘"),
            "verdict": {
                "score": (market.get("verdict") or {}).get("score", 50),
                "label": (market.get("verdict") or {}).get("label", "—"),
            },
            "llm_verdict": llm.get("market_verdict", "—"),
            "indices": slim_indices(market.get("indices") or {}),
            "dimensions": slim_dimensions(market.get("dimensions") or {}),
        }

    # ── 行业轮动 ──
    if sector:
        sectors["sector_rotation"] = {
            "label": sector.get("label", "行业轮动"),
            "verdict": {
                "score": (sector.get("verdict") or {}).get("score", 50),
                "label": (sector.get("verdict") or {}).get("label", "—"),
            },
            "llm_verdict": llm.get("sector_verdict", "—"),
            "top_momentum": sector.get("top_momentum") or [],
            "dimensions": slim_dimensions(sector.get("dimensions") or {}),
        }

    # ── 商品期货 ──
    if commodity:
        sectors["commodity"] = {
            "label": commodity.get("label", "商品期货"),
            "verdict": {
                "score": (commodity.get("verdict") or {}).get("score", 50),
                "label": (commodity.get("verdict") or {}).get("label", "—"),
            },
            "llm_verdict": llm.get("commodity_verdict", "—"),
            "commodities": slim_commodities(commodity.get("commodities") or {}),
            "dimensions": slim_dimensions(commodity.get("dimensions") or {}),
        }

    return {
        "version": "v8.0",
        "generated_at": en.get("generated_at", datetime.now().isoformat()),
        "global_summary": llm.get("global_summary", "数据暂不可用"),
        "sectors": sectors,
    }


def main():
    if not ENRICHED_PATH.exists():
        print(f"[generate_contract] enriched 文件不存在: {ENRICHED_PATH}", file=sys.stderr)
        # 生成降级合约（前端可正常显示"数据暂不可用"）
        fallback = {
            "version": "v8.0",
            "generated_at": datetime.now().isoformat(),
            "global_summary": "数据暂不可用",
            "sectors": None,
        }
        CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONTRACT_PATH.write_text(json.dumps(fallback, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[generate_contract] 降级合约已写入: {CONTRACT_PATH}")
        return

    try:
        enriched = json.loads(ENRICHED_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[generate_contract] enriched 解析失败: {e}", file=sys.stderr)
        return

    contract = build_contract(enriched)
    CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT_PATH.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")

    size_kb = CONTRACT_PATH.stat().st_size / 1024
    score_str = ", ".join(
        f"{k}:{v.get('verdict',{}).get('score','?')}" 
        for k, v in (contract.get("sectors") or {}).items()
    )
    print(f"[generate_contract] 合约已写入: {CONTRACT_PATH} ({size_kb:.1f}KB, scores={score_str})")

    # 内存告警（ECS 有 1.7Gi 限制，Node 加载 JSON 额外 3-5x）
    if size_kb > 20:
        print(f"[generate_contract] ⚠️ 合约文件 {size_kb:.1f}KB 偏大，检查是否有多余字段", file=sys.stderr)


if __name__ == "__main__":
    main()
