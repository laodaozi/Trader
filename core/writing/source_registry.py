"""
信源注册表 — V6.4

定义每个 wewe-rss 订阅号的写作角色映射。
与 admin/models/account.js 保持同步。

分级体系：
  S — 全量抓取，权重最高（叙事平权：热点事件核心信源）
  A — 关键事件，≤5条（微策神机/财闻私享：政策/催化事件；在下杜牛牛：情绪周期）
  B — 补全事件，≤3条（财经早餐：宏观早报；数据宝：催化数据；小马白话期权：波动率）
  C — 轮动/周期，≤3条（台球之门/低吸波段王：行业趋势/周期策略）
"""

from __future__ import annotations

SOURCE_ROLES: dict = {
    # ── S 全量核心 ──
    "MP_WXS_3988180000": {
        "name": "叙事平权old", "tier": "S", "weight": 1.0, "limit": None,
        "role": "反转信号", "category": "行业", "tags": ["量化", "策略"],
        "note": "热点事件核心信源，全量抓取，权重最高",
    },
    # ── A 关键事件 ──
    "MP_WXS_3242358265": {
        "name": "微策神机", "tier": "A", "weight": 0.9, "limit": 5,
        "role": "催化事件", "category": "公司", "tags": ["量化", "策略"],
        "note": "关键事件，催化驱动",
    },
    "MP_WXS_3233243226": {
        "name": "财闻私享", "tier": "A", "weight": 0.9, "limit": 5,
        "role": "政策分析", "category": "宏观", "tags": ["财经", "私享"],
        "note": "关键事件，政策/宏观驱动",
    },
    "MP_WXS_3583532298": {
        "name": "在下杜牛牛", "tier": "A", "weight": 0.85, "limit": 5,
        "role": "情绪周期", "category": "宏观", "tags": ["情绪周期"],
        "note": "情绪周期判断，关键信源",
    },
    # ── B 补全事件 ──
    "MP_WXS_2398512110": {
        "name": "财经早餐", "tier": "B", "weight": 0.6, "limit": 3,
        "role": "宏观资讯", "category": "宏观", "tags": ["早报", "宏观"],
        "note": "宏观早报补全，辅助定性",
    },
    "MP_WXS_3080543482": {
        "name": "数据宝", "tier": "B", "weight": 0.6, "limit": 3,
        "role": "催化事件", "category": "行业", "tags": ["数据", "公告"],
        "note": "数据/公告补全，催化辅助",
    },
    "MP_WXS_3521606446": {
        "name": "小马白话期权", "tier": "B", "weight": 0.5, "limit": 3,
        "role": "波动率套利", "category": "行业", "tags": ["期权", "衍生品"],
        "note": "波动率/期权视角补全",
    },
    # ── C 轮动/周期 ──
    "MP_WXS_3191151316": {
        "name": "台球之门", "tier": "C", "weight": 0.4, "limit": 3,
        "role": "趋势跟踪", "category": "行业", "tags": ["轮动", "周期"],
        "note": "行业轮动/周期策略视角",
    },
    "MP_WXS_3901470107": {
        "name": "低吸波段王", "tier": "C", "weight": 0.4, "limit": 3,
        "role": "趋势跟踪", "category": "公司", "tags": ["轮动", "波段"],
        "note": "短线轮动/波段策略视角",
    },
}

# 各 tier 默认配额（limit=None 表示全量）
TIER_LIMIT: dict[str, int | None] = {
    "S": None,
    "A": 5,
    "B": 3,
    "C": 3,
}


def get_source_meta(mp_id: str) -> dict | None:
    """获取信源元数据。"""
    return SOURCE_ROLES.get(mp_id)


def get_sources_by_role() -> dict[str, list[str]]:
    """按角色分组：{role: [mp_id, ...]}。"""
    result: dict[str, list[str]] = {}
    for mp_id, meta in SOURCE_ROLES.items():
        role = meta["role"]
        result.setdefault(role, []).append(mp_id)
    return result


def get_sources_by_tier() -> dict[str, list[str]]:
    """按 tier 分组：{tier: [mp_id, ...]}。"""
    result: dict[str, list[str]] = {}
    for mp_id, meta in SOURCE_ROLES.items():
        tier = meta["tier"]
        result.setdefault(tier, []).append(mp_id)
    return result
