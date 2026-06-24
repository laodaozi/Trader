"""
文章生成 Pipeline — V3.9.6

信源采集 → 元数据感知（category/tags → role） → 模板选择 → LLM写作 → HTML草稿

核心流程：
1. 接收信号源列表 + 信号数据
2. 根据信源的 category+tags 解析写作角色
3. 加载角色专属 prompt 模板
4. 调用 LLM 生成角色化文章
5. 输出多角色 HTML 文章草稿
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.writing.prompt_registry import (
    SignalSourceMeta,
    resolve_role,
    get_system_prompt,
    list_templates,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "output" / "article"


@dataclass
class RoleArticle:
    role: str
    slug: str
    source_name: str
    html: str
    word_count: int


@dataclass
class PipelineReport:
    date: str
    articles: List[RoleArticle] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    roles_used: List[str] = field(default_factory=list)

    @property
    def total_articles(self) -> int:
        return len(self.articles)

    @property
    def total_words(self) -> int:
        return sum(a.word_count for a in self.articles)

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "total_articles": self.total_articles,
            "total_words": self.total_words,
            "roles_used": self.roles_used,
            "articles": [
                {"role": a.role, "source": a.source_name, "words": a.word_count}
                for a in self.articles
            ],
            "errors": self.errors,
        }


def resolve_role_for_source(source: Dict[str, Any]) -> str:
    """从信源原始数据解析写作角色。"""
    meta = SignalSourceMeta(
        mp_name=source.get("mp_name", source.get("name", "")),
        mp_id=source.get("mp_id", ""),
        category=source.get("category", ""),
        tags=source.get("tags", source.get("tag_list", [])),
    )
    return resolve_role(meta)


def _strip_markdown_frontmatter(template: str) -> str:
    """去除模板中的 `信源关联` 段和 `## 角色定位` 之前的元数据行（# 标题行）。"""
    lines = template.strip().split("\n")

    start_idx = 0
    for i, line in enumerate(lines):
        if line.startswith("## 角色定位"):
            start_idx = i
            break

    end_idx = len(lines)
    for i in range(len(lines) - 1, max(start_idx, 0), -1):
        if line_start := lines[i].strip():
            if (
                line_start.startswith("## 信源关联")
                or line_start.startswith("- 匹配")
                or line_start.startswith("- 典型信源")
            ):
                end_idx = i
                break

    return "\n".join(lines[start_idx:end_idx])


def _build_user_prompt(date_str: str, source: Dict[str, Any],
                       signals: List[Dict[str, Any]]) -> str:
    """构建传给 LLM 的 user prompt。"""
    parts = [f"日期：{date_str}\n"]
    parts.append(f"信源：{source.get('mp_name', source.get('name', '未知'))}")

    category = source.get("category", "")
    tags = source.get("tags", source.get("tag_list", []))
    if category:
        parts.append(f"分类：{category}")
    if tags:
        parts.append(f"标签：{', '.join(tags)}")

    parts.append("")

    if signals:
        parts.append("信号数据：")
        parts.append("```json")
        parts.append(json.dumps(signals, ensure_ascii=False, indent=2))
        parts.append("```")
    else:
        parts.append('（本日无该信源的结构化信号，请基于信源定位生成一篇「角色综述」。）')

    return "\n".join(parts)


def generate_role_article(
    date_str: str,
    source: Dict[str, Any],
    signals: List[Dict[str, Any]],
    model: Optional[str] = None,
    dry_run: bool = False,
) -> Optional[RoleArticle]:
    """
    为单个信源生成角色化文章。

    Returns:
        RoleArticle or None on failure
    """
    role = resolve_role_for_source(source)
    template = get_system_prompt(role)
    system_prompt = _strip_markdown_frontmatter(template)
    user_prompt = _build_user_prompt(date_str, source, signals)

    if dry_run:
        print(f"\n  [{role}] {source.get('mp_name', '?')}")
        print(f"   System prompt: {len(system_prompt)} chars")
        print(f"   User prompt:   {len(user_prompt)} chars")
        print(f"   User preview:  {user_prompt[:200]}...")
        return None

    try:
        from core.report_agent import call_claude_api
        text = call_claude_api(system_prompt, user_prompt, model=model, tier="premium")
    except Exception as e:
        return None

    html_match = re.search(r"```html\s*\n(.*?)\n```", text, re.DOTALL)
    if html_match:
        html = html_match.group(1).strip()
    elif "<" in text and ">" in text:
        html = text
    else:
        return None

    word_count = len(re.sub(r"<[^>]+>", " ", html).split())

    return RoleArticle(
        role=role,
        slug=source.get("slug", ""),
        source_name=source.get("mp_name", source.get("name", "未知")),
        html=html,
        word_count=word_count,
    )


def generate_ma_article(
    date_str: str,
    model: Optional[str] = None,
    dry_run: bool = False,
    ma_data: Optional[Dict[str, Any]] = None,
) -> Optional[RoleArticle]:
    """使用 AKShare 并购重组公告数据生成兼并重组角色文章。

    独立于微信信源 Pipeline，消费 ma_signals.collect_ma_signals()
    产出的结构化 M&A 数据。

    Args:
        date_str: 日期 YYYY-MM-DD
        model: 指定模型
        dry_run: 只打印 prompt 不调用 LLM
        ma_data: 已采集的 M&A 数据（避免重复 API 调用）；为 None 时自动调用 collect_ma_signals()

    Returns:
        RoleArticle or None（当日无 M&A 信号时返回 None）
    """
    if ma_data is None:
        from core.ma_signals import collect_ma_signals
        ma_data = collect_ma_signals(date_str)
    if not ma_data or ma_data.get("count", 0) == 0:
        print(f"\n  [兼并重组] 当日无 M&A 公告数据")
        return None

    role = "兼并重组"
    system_prompt = _strip_markdown_frontmatter(get_system_prompt(role))

    # ── 构建结构化 M&A user prompt ──
    parts = [
        f"日期：{date_str}",
        f"数据来源：{ma_data.get('_source', 'akshare_notice_report')}",
        f"公告摘要：{ma_data.get('summary', '')}",
        "",
    ]

    by_ind = ma_data.get("by_industry", {})
    if by_ind:
        parts.append("## 行业信号强度")
        for ind, sig in by_ind.items():
            cnt = sig.get("count", 0)
            strength = sig.get("strength", "low")
            flag = "★" if strength == "high" else ("☆" if strength == "medium" else "△")
            stocks = "、".join(sig.get("stocks", [])[:5])
            parts.append(f"  {flag} {ind} ({cnt}家): {stocks}")
            for note in sig.get("notable", [])[:3]:
                parts.append(f"    标题: {note}")
        parts.append("")

    # 全量公告清单（供 LLM 挑选 TOP 2 深挖）
    announcements = ma_data.get("announcements", [])
    if announcements:
        parts.append(f"## 全量公告清单（共 {len(announcements)} 条）")
        for i, ann in enumerate(announcements[:30], 1):
            parts.append(
                f"  {i}. [{ann.get('notice_type', '')}] {ann.get('stock_name', '')}"
                f"({ann.get('stock_code', '')}) — {ann.get('title', '')}"
                f" | 行业: {ann.get('industry_hint', '')}"
            )
            if i == 30 and len(announcements) > 30:
                parts.append(f"  ... （共 {len(announcements)} 条，仅展示前 30 条）")
        parts.append("")

    user_prompt = "\n".join(parts)

    if dry_run:
        print(f"\n  [兼并重组] AKShare M&A Signals")
        print(f"   公告数: {ma_data.get('count', 0)}")
        print(f"   行业数: {len(by_ind)}")
        print(f"   System prompt: {len(system_prompt)} chars")
        print(f"   User prompt:   {len(user_prompt)} chars")
        return None

    try:
        from core.report_agent import call_claude_api
        text = call_claude_api(system_prompt, user_prompt, model=model, tier="premium")
    except Exception:
        return None

    html_match = re.search(r"```html\s*\n(.*?)\n```", text, re.DOTALL)
    if html_match:
        html = html_match.group(1).strip()
    elif "<" in text and ">" in text:
        html = text
    else:
        return None

    word_count = len(re.sub(r"<[^>]+>", " ", html).split())

    return RoleArticle(
        role=role,
        slug="ma",
        source_name="AKShare 并购重组公告",
        html=html,
        word_count=word_count,
    )


def run_pipeline(
    date_str: str,
    sources: List[Dict[str, Any]],
    signals_by_source: Dict[str, List[Dict[str, Any]]],
    model: Optional[str] = None,
    dry_run: bool = False,
) -> PipelineReport:
    """
    运行完整文章 Pipeline。

    Args:
        date_str: 日期字符串 YYYY-MM-DD
        sources: 信源列表 [{mp_id, mp_name, category, tags}, ...]
        signals_by_source: 每个信源的信号 {mp_id: [signal, ...]}
        model: 指定模型
        dry_run: 只打印 prompt 不调用 LLM

    Returns:
        PipelineReport with generated articles
    """
    report = PipelineReport(date=date_str)

    for source in sources:
        mp_id = source.get("mp_id", "")
        signals = signals_by_source.get(mp_id, [])
        role = resolve_role_for_source(source)

        print(f"\n  [{role}] {source.get('mp_name', mp_id)} ({len(signals)} signals)")

        article = generate_role_article(date_str, source, signals, model=model, dry_run=dry_run)

        if article:
            report.articles.append(article)
            report.roles_used.append(role)
        else:
            err = f"[{role}] {source.get('mp_name', mp_id)} 生成失败"
            report.errors.append(err)
            print(f"    ✗ {err}")

    print(f"\n  Pipeline 完成: {report.total_articles} 篇文章 / "
          f"{report.total_words} 字 / {report.total_articles}/{len(sources)} 源")

    return report


def save_articles(report: PipelineReport) -> List[Path]:
    """保存所有角色文章到 output/article/ 目录。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    saved = []

    for article in report.articles:
        safe_role = re.sub(r"[^\w\-]", "", article.role)
        safe_name = re.sub(r"[^\w\-]", "", article.source_name)[:20]
        filename = f"article_{report.date.replace('-', '')}_{safe_role}_{safe_name}.html"
        path = OUTPUT_DIR / filename
        path.write_text(article.html, encoding="utf-8")
        saved.append(path)

    report_path = OUTPUT_DIR / f"report_{report.date.replace('-', '')}.json"
    report_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return saved


def show_pipeline_summary(report: PipelineReport) -> None:
    """打印 Pipeline 运行摘要。"""
    print(f"\n{'=' * 60}")
    print(f"  文章 Pipeline 报告 — {report.date}")
    print(f"{'=' * 60}")
    print(f"  生成文章: {report.total_articles}")
    print(f"  总字数:   {report.total_words}")
    print(f"  涉及角色: {', '.join(report.roles_used) if report.roles_used else '（无）'}")

    if report.articles:
        print(f"\n  文章列表:")
        for a in report.articles:
            print(f"    [{a.role}] {a.source_name} ({a.word_count}字)")

    if report.errors:
        print(f"\n  错误:")
        for e in report.errors:
            print(f"    ✗ {e}")
