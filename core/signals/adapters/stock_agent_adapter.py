"""
stock_agent_adapter.py — 将 stock_agent.py 的输出包装为 signals_contract 格式

用法：
  在 stock_agent.py 完成分析后，调用本适配器的 emit_signals() 将选股结果
  转为标准信号写入 upstream_signals.jsonl。
"""
from __future__ import annotations
from datetime import datetime, timedelta

# 注：upstream_signals 的导入依赖 stock_agent_runner.py 预先设置 sys.path。
# 本 adapter 不自行操作 sys.path（ECS 平铺部署下 Path(__file__).parent.parent
# 解析为 /opt/ 而非 /opt/cycleradar/，自注入反而有害）。
from upstream_signals import write_signal

def stock_pick_to_signal(pick: dict) -> dict:
    """将 stock_agent 的一条选股结果转为标准信号 dict。

    pick 格式（来自 stock_agent.py 输出）：
      {code, name, tier, catalyst_score, resonance_score, reasons, industry, ...}
    """
    tier_to_direction = {"强推": "long", "关注": "long", "观望": "neutral"}
    confidence_map = {"强推": 0.85, "关注": 0.65, "观望": 0.35}

    tier = pick.get("tier", "观望")
    now = datetime.now().astimezone()
    code = str(pick.get("code", ""))
    signal_date = str(pick.get("date") or now.strftime("%Y-%m-%d")).replace("-", "")

    return {
        "signal_id": f"STOCK_AGENT-{signal_date}-{code}",
        "timestamp": now.isoformat(),
        "strategy": "stock_agent",
        "asset": code,
        "asset_type": "stock",
        "direction": tier_to_direction.get(tier, "neutral"),
        "confidence": confidence_map.get(tier, 0.35),
        "expiry": (now + timedelta(days=7)).isoformat(),
        "metadata": {
            "stock_name": pick.get("name", ""),
            "tier": tier,
            "catalyst_score": pick.get("catalyst_score"),
            "resonance_score": pick.get("resonance_score"),
            "reasons": pick.get("reasons", []),
            "entry_price": pick.get("entry_price"),
            "target_price": pick.get("target_price"),
            "stop_loss": pick.get("stop_loss"),
            "industry": pick.get("industry", ""),
        },
    }

def emit_signals(picks: list[dict], *, normalize: bool = True) -> int:
    """将 stock_agent 选股结果列表写入信号流，返回成功条数。"""
    count = 0
    for pick in picks:
        try:
            signal = stock_pick_to_signal(pick)
            write_signal(signal, normalize=normalize)
            count += 1
        except Exception as e:
            print(f"  ⚠ stock_agent 信号写入失败 ({pick.get('name', '?')}): {e}")
    return count
