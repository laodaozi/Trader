"""
param_scan.py — 参数扫描空间生成器（分支 A · backtest CI 配套）

职责：
  从策略模块（ma_signals.py / rotation_factor.py）中提取关键数值参数，
  为每个参数生成 3 档取值（默认值 / 默认值×0.5 / 默认值×2），
  汇总写入 param_space.json，供后续 backtest 参数扫描消费。

设计原则：
  - 纯静态分析（AST），不 import 策略模块，避免触发其副作用 / 依赖。
  - 只抓"模块级数值常量"和"数值阈值字典"，跳过字符串映射表（如 NAME_INDUSTRY_MAP）。
  - 若两个策略文件都抽不出可用数值参数，回退到产品默认参数空间
    （短均线 [5,10,20]、长均线 [20,30,60]、阈值 [0.01,0.02,0.03]）。

用法：
  python3 param_scan.py                 # 扫描默认策略文件，写 param_space.json
  python3 param_scan.py --print         # 同时把结果打印到终端
  python3 param_scan.py --out other.json # 自定义输出路径
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).parent

# 要扫描的策略文件（相对 PROJECT_ROOT；本文件现位于 core/backtest/，策略在 core/）
TARGET_FILES = ["../ma_signals.py", "../rotation_factor.py", "../factor_agent.py"]

# 跳过名单：大写常量里这些是分类/文本映射，不是可调数值参数
SKIP_NAMES = {
    "NAME_INDUSTRY_MAP",
    "MA_RELEVANT_TYPES",
    "MA_KEYWORDS",
    "FACTOR_DEFINITIONS",  # 含权重，但结构是嵌套 dict，单独处理（见 _extract_factor_weights）
    "_FACTOR_WEIGHTS",     # factor_agent.py 因子权重（扁平 dict），单独处理（见 _extract_factor_agent_weights）
    "_INDUSTRY_PE_RANGE", "INDUSTRY_ETF_MAP", "INDUSTRY_FUTURES_MAP",
    "_CROSS_ASSET_RULES", "_SENTIMENT_MAP", "_CORRELATED_GROUPS",
}

# 产品默认参数空间（回退用）：与设计文档一致
DEFAULT_PARAM_SPACE = {
    "ma_short": {"default": 10, "grid": [5, 10, 20], "source": "fallback"},
    "ma_long": {"default": 30, "grid": [20, 30, 60], "source": "fallback"},
    "signal_threshold": {"default": 0.02, "grid": [0.01, 0.02, 0.03], "source": "fallback"},
}


def _is_number(node: ast.AST) -> bool:
    """判断 AST 节点是否为正/负数字面量。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return True
    # 负数：UnaryOp(USub, Constant)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) \
            and isinstance(node.operand, ast.Constant) \
            and isinstance(node.operand.value, (int, float)):
        return True
    return False


def _number_value(node: ast.AST) -> float | int:
    if isinstance(node, ast.UnaryOp):
        return -node.operand.value
    return node.value


def _make_grid(default: float | int) -> list:
    """对单个默认值生成 3 档 [default×0.5, default, default×2]，去重并保留类型。"""
    is_int = isinstance(default, int)

    def _coerce(x: float):
        if is_int:
            return int(round(x))
        return round(x, 6)

    candidates = [_coerce(default * 0.5), _coerce(default), _coerce(default * 2)]
    # 按数值排序去重
    seen = []
    for c in candidates:
        if c not in seen:
            seen.append(c)
    return sorted(seen)


def _scan_module_constants(tree: ast.Module) -> dict[str, dict]:
    """抽取模块级数值常量（标量）和数值阈值字典（值全为数字的 dict）。"""
    found: dict[str, dict] = {}

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        # 只看单目标赋值，且目标是 Name
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        name = node.targets[0].id
        if name in SKIP_NAMES:
            continue

        value = node.value

        # 情形 1：标量数值常量，如 PREHEAT_RANK_START = 11
        if _is_number(value):
            default = _number_value(value)
            found[name] = {
                "default": default,
                "grid": _make_grid(default),
                "kind": "scalar",
            }
            continue

        # 情形 2：值全为数字的阈值字典，如 ROTATION_SIGNAL_THRESHOLDS = {...}
        if isinstance(value, ast.Dict):
            sub: dict[str, dict] = {}
            ok = True
            for k_node, v_node in zip(value.keys, value.values):
                if not isinstance(k_node, ast.Constant) or not _is_number(v_node):
                    ok = False
                    break
                key = str(k_node.value)
                dv = _number_value(v_node)
                sub[key] = {"default": dv, "grid": _make_grid(dv)}
            if ok and sub:
                found[name] = {"kind": "threshold_dict", "params": sub}

    return found


def _extract_factor_weights(tree: ast.Module) -> dict[str, dict]:
    """单独处理 FACTOR_DEFINITIONS：抽每个因子的 weight 作为可调参数。"""
    weights: dict[str, dict] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if node.targets[0].id != "FACTOR_DEFINITIONS":
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for fk_node, fv_node in zip(node.value.keys, node.value.values):
            if not isinstance(fk_node, ast.Constant) or not isinstance(fv_node, ast.Dict):
                continue
            factor = str(fk_node.value)
            for ik_node, iv_node in zip(fv_node.keys, fv_node.values):
                if isinstance(ik_node, ast.Constant) and ik_node.value == "weight" \
                        and _is_number(iv_node):
                    w = _number_value(iv_node)
                    weights[f"{factor}_weight"] = {
                        "default": w,
                        "grid": _make_grid(w),
                    }
    return weights


# ── factor_agent.py 专属参数提取 ─────────────────────────
# factor_agent.py 的可调参数分两类：
#   1) 模块级权重 dict `_FACTOR_WEIGHTS`（扁平 {因子: 权重}）—— AST 抽取
#   2) 写死在函数体内的阈值（hs300 基准 / 超额阈值 / 涨停阈值等）
#      —— 不是模块级常量，AST 扫不到，用下方"已知阈值表"显式登记。

# factor_agent.py 函数体内硬编码阈值（人工登记，含来源行号注释）
# 注：grid_search 方案 B 会在内存中复算，这些值作为扫描档位的"默认中枢"。
FACTOR_AGENT_HARDCODED = {
    "hs300_chg_est":       0.3,   # scan_all_industries / scan_concept_plates 默认基准变化量
    "a1_excess_threshold": 3.0,   # A1: excess >= 3.0
    "a2_limit_threshold":  3,     # A2: limit_count >= 3
    "b1_flow_threshold":   0.0,   # B1: flow > 0
    "c1_pe_pct_threshold": 30.0,  # C1: 估值分位 < 30%
}


def _extract_factor_agent_weights(tree: ast.Module) -> dict[str, dict]:
    """抽取 factor_agent.py 的 `_FACTOR_WEIGHTS` 扁平权重字典。

    形如 `_FACTOR_WEIGHTS = {"A1": 0.13, "A2": 0.12, ...}`，
    每个权重作为一个可调参数 `<因子>_weight`。
    """
    weights: dict[str, dict] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if node.targets[0].id != "_FACTOR_WEIGHTS":
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for k_node, v_node in zip(node.value.keys, node.value.values):
            if isinstance(k_node, ast.Constant) and _is_number(v_node):
                factor = str(k_node.value)
                w = _number_value(v_node)
                weights[f"{factor}_weight"] = {
                    "default": w,
                    "grid": _make_grid(w),
                }
    return weights


def _extract_factor_agent_thresholds() -> dict[str, dict]:
    """登记 factor_agent.py 函数体内硬编码阈值（AST 扫不到）。"""
    out: dict[str, dict] = {}
    for name, dv in FACTOR_AGENT_HARDCODED.items():
        out[name] = {"default": dv, "grid": _make_grid(dv)}
    return out


def scan_file(path: Path) -> dict[str, Any]:
    """扫描单个策略文件，返回 {param_name: {...}}。"""
    if not path.exists():
        return {}
    tree = ast.parse(path.read_text(encoding="utf-8"))

    params: dict[str, Any] = {}

    # 标量 + 阈值字典
    consts = _scan_module_constants(tree)
    for name, info in consts.items():
        if info.get("kind") == "scalar":
            params[name] = {
                "default": info["default"],
                "grid": info["grid"],
                "source": path.name,
            }
        elif info.get("kind") == "threshold_dict":
            for sub_key, sub_info in info["params"].items():
                params[f"{name}.{sub_key}"] = {
                    "default": sub_info["default"],
                    "grid": sub_info["grid"],
                    "source": path.name,
                }

    # 因子权重（rotation_factor.py 专属：FACTOR_DEFINITIONS 嵌套 dict）
    for name, info in _extract_factor_weights(tree).items():
        params[name] = {
            "default": info["default"],
            "grid": info["grid"],
            "source": path.name,
        }

    # factor_agent.py 专属：_FACTOR_WEIGHTS 扁平权重 + 硬编码阈值
    if path.name == "factor_agent.py":
        for name, info in _extract_factor_agent_weights(tree).items():
            params[name] = {
                "default": info["default"],
                "grid": info["grid"],
                "source": path.name,
            }
        for name, info in _extract_factor_agent_thresholds().items():
            params[name] = {
                "default": info["default"],
                "grid": info["grid"],
                "source": "factor_agent.py (hardcoded)",
            }

    return params


def build_param_space() -> dict[str, Any]:
    """扫描所有目标文件，组装完整参数空间。"""
    all_params: dict[str, Any] = {}
    scanned_files = []

    for fname in TARGET_FILES:
        fpath = PROJECT_ROOT / fname
        file_params = scan_file(fpath)
        if file_params:
            scanned_files.append(Path(fname).name)
            # 文件名前缀防止跨文件重名
            prefix = Path(fname).name.replace(".py", "")
            for k, v in file_params.items():
                all_params[f"{prefix}::{k}"] = v

    # 回退：抽不出任何数值参数时用产品默认空间
    used_fallback = False
    if not all_params:
        used_fallback = True
        for k, v in DEFAULT_PARAM_SPACE.items():
            all_params[k] = dict(v)

    return {
        "version": "1.0",
        "generator": "param_scan.py",
        "scanned_files": scanned_files,
        "used_fallback": used_fallback,
        "grid_rule": "每个参数 3 档：默认值×0.5 / 默认值 / 默认值×2（整数四舍五入、去重排序）",
        "param_count": len(all_params),
        "params": all_params,
    }


def main():
    parser = argparse.ArgumentParser(description="CycleRadar 策略参数扫描空间生成器")
    parser.add_argument("--out", default=str(PROJECT_ROOT / "param_space.json"),
                        help="输出 JSON 路径（默认 param_space.json）")
    parser.add_argument("--print", action="store_true", dest="do_print",
                        help="同时打印结果到终端")
    args = parser.parse_args()

    space = build_param_space()
    out_path = Path(args.out)
    out_path.write_text(
        json.dumps(space, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"✓ 参数空间已写入 {out_path}")
    print(f"  扫描文件: {space['scanned_files'] or '(无，使用回退默认空间)'}")
    print(f"  参数数量: {space['param_count']}")
    if args.do_print:
        print(json.dumps(space, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
