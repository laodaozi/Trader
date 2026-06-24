"""
写作模板注册表 — V3.9.6

从 docs/prompt-templates/ 加载 7 个角色模板，
提供按 category + tags 匹配合适模板的能力。

角色映射：
  政策分析   ← category: 政策 or tags: 政策解读
  情绪周期   ← tags: 情绪周期
  趋势跟踪   ← tags: 趋势跟踪
  反转信号   ← tags: 反转信号
  波动率套利 ← tags: 波动率
  催化事件   ← tags: 催化事件
  兼并重组   ← 独立数据源（AKShare公告），不依赖微信信源
"""

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "prompt-templates")

ROLE_MAP = {
    "政策分析": ["policy"],
    "情绪周期": ["sentiment"],
    "趋势跟踪": ["trend"],
    "反转信号": ["reversal"],
    "波动率套利": ["volatility"],
    "催化事件": ["catalyst"],
    "兼并重组": ["ma"],
}

ROLE_ORDER = list(ROLE_MAP.keys())

TAG_TO_ROLE = {
    "政策解读": "政策分析",
    "情绪周期": "情绪周期",
    "趋势跟踪": "趋势跟踪",
    "反转信号": "反转信号",
    "波动率": "波动率套利",
    "催化事件": "催化事件",
    "兼并重组": "兼并重组",
    "并购重组": "兼并重组",
    "并购": "兼并重组",
}

CATEGORY_TO_ROLE = {
    "政策": "政策分析",
    "宏观": "政策分析",
}

TEMPLATE_FILES = {
    "政策分析": "01-政策分析.md",
    "情绪周期": "02-情绪周期.md",
    "趋势跟踪": "03-趋势跟踪.md",
    "反转信号": "04-反转信号.md",
    "波动率套利": "05-波动率套利.md",
    "催化事件": "06-催化事件.md",
    "兼并重组": "07-兼并重组.md",
}


@dataclass
class SignalSourceMeta:
    mp_name: str
    mp_id: str
    category: str = ""
    tags: List[str] = field(default_factory=list)


def _read_template(filename: str) -> str:
    path = os.path.join(TEMPLATES_DIR, filename)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def resolve_role(source: SignalSourceMeta) -> str:
    """根据信源的 category 和 tags 决定使用哪个写作角色。"""
    category = source.category.strip()
    tags = [t.strip() for t in source.tags]

    if category in CATEGORY_TO_ROLE:
        return CATEGORY_TO_ROLE[category]

    for tag in tags:
        if tag in TAG_TO_ROLE:
            return TAG_TO_ROLE[tag]

    return "趋势跟踪"


def get_template(role: str) -> str:
    """加载指定角色的 prompt 模板全文。"""
    filename = TEMPLATE_FILES.get(role, TEMPLATE_FILES["趋势跟踪"])
    content = _read_template(filename)
    if not content:
        content = _read_template(TEMPLATE_FILES["趋势跟踪"])
    return content


def extract_prompt_body(template: str) -> str:
    """从完整模板中提取角色定位+写作人格+分析框架+输出结构+风格约束（去掉元数据行）。"""
    lines = template.strip().split("\n")
    body_start = 2
    for i, line in enumerate(lines):
        if line.startswith("## 角色定位"):
            body_start = i
            break
    return "\n".join(lines[body_start:])


def get_system_prompt(role: str) -> str:
    """获取角色对应的系统 prompt（角色定位→写作人格→分析框架→输出结构→风格约束）。"""
    template = get_template(role)
    return extract_prompt_body(template)


def list_templates() -> List[Dict]:
    """列出所有模板（角色名+文件名+预览首行）。"""
    result = []
    for role in ROLE_ORDER:
        filename = TEMPLATE_FILES.get(role, "")
        path = os.path.join(TEMPLATES_DIR, filename) if filename else ""
        preview = ""
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                first_line = f.readline().strip()
                preview = first_line.lstrip("#").strip()
        result.append({
            "role": role,
            "slug": ROLE_MAP.get(role, [""])[0],
            "filename": filename,
            "preview": preview,
        })
    return result


def save_template(role: str, content: str) -> bool:
    """保存模板内容到文件。"""
    filename = TEMPLATE_FILES.get(role)
    if not filename:
        return False
    path = os.path.join(TEMPLATES_DIR, filename)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except OSError:
        return False
