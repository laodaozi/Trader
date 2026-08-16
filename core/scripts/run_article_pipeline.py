#!/usr/bin/env python3
"""
文章生成 CLI — Plan C 桥接器

从 hot_enrichment.json（thesis+tickers）+ WeWe RSS DB（原文）
构建按 mp_id 分组的信号数据，调用写作 Pipeline 生成角色化日报。

用法：
    python3 core/scripts/run_article_pipeline.py --date 2026-06-28
    python3 core/scripts/run_article_pipeline.py --date 2026-06-28 --dry-run

输出：
    output/article/article_YYYYMMDD_<role>_<source>.html  — 生成的文章
    data/pipeline_status.json                            — 状态摘要（供 Admin 读取）
"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# ── 路径（V7.7: 改为 source_articles.db）──
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ARTICLES_DB = PROJECT_ROOT / "data" / "source_articles.db"
ENRICHMENT_PATH = PROJECT_ROOT / "data" / "hot_enrichment.json"
STATUS_PATH = PROJECT_ROOT / "data" / "pipeline_status.json"

sys.path.insert(0, str(PROJECT_ROOT))

from core.writing.pipeline import run_pipeline, save_articles, show_pipeline_summary


def _hash_title(title: str) -> str:
    """与 enrich_hot_events._hash() 完全一致的 hash 算法"""
    return hashlib.md5(title.encode()).hexdigest()[:12]


def _clean_content(html: str) -> str:
    """从 HTML 中提取纯文本（与 enrich_hot_events._clean_content 一致）"""
    text = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def main():
    parser = argparse.ArgumentParser(description="文章生成 CLI（Plan C）")
    parser.add_argument("--date", required=True, help="日期 YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="只打印 prompt，不调 LLM")
    args = parser.parse_args()

    target_date = args.date

    # ── 1. 读 hot_enrichment.json ──
    enrichment = {}
    if ENRICHMENT_PATH.exists():
        try:
            enrichment = json.loads(ENRICHMENT_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    # ── 2. 连 source_articles.db ──
    if not SOURCE_ARTICLES_DB.exists():
        print(f"❌ DB 不存在: {SOURCE_ARTICLES_DB}（先跑 ingest_mcp_news.py）", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(SOURCE_ARTICLES_DB))
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """SELECT source_id, source_name, title, content_text, tier, weight, publish_date
           FROM source_articles
           WHERE fetch_status = 'success'
             AND publish_date = ?
           ORDER BY weight DESC, created_at DESC""",
        (target_date,),
    ).fetchall()
    conn.close()

    # ── 3. 匹配 enrichment → 构建 signals_by_source ──
    # enrichment 结构: {hash: {thesis, tickers, enriched_at}}
    # key = md5(title)[:12]，与 enrich_hot_events._hash() 对齐
    signals_by_source = {}    # {source_id: [signal_dict]}
    seen_sources = {}         # {source_id: source_meta}
    total_matched = 0
    total_articles = len(rows)

    for row in rows:
        source_id = row["source_id"]
        title = row["title"] or ""
        content = row["content_text"] or ""
        source_name = row["source_name"] or source_id
        title_hash = _hash_title(title)

        # 检查是否有标注
        enr = enrichment.get(title_hash)
        if not enr:
            continue

        total_matched += 1

        # 注册信源（第一次遇到时）
        if source_id not in seen_sources:
            seen_sources[source_id] = {
                "mp_id": source_id,
                "mp_name": source_name,
                "category": "",
                "tags": [],
            }

        # 构建 signal（正文摘要取前 1500 字，不塞爆 prompt）
        text_snippet = _clean_content(content)[:1500] if content else ""
        signals_by_source.setdefault(source_id, []).append({
            "title": title,
            "summary": text_snippet,
            "thesis": enr.get("thesis", ""),
            "tickers": enr.get("tickers", []),
        })

    print(f"📊 {target_date}: {total_matched}/{total_articles} 篇有标注", file=sys.stderr)

    # 无标注 → 写状态并优雅退出（不报错）
    if total_matched == 0:
        _write_status(target_date, total_articles, 0, 0, [])
        print("⚠️  本日无已标注文章，跳过生成", file=sys.stderr)
        sys.exit(0)

    # ── 4. 构建 source 列表（按 weight 降序）──
    source_with_weight = []
    for source_id, meta in seen_sources.items():
        weight = 0.5  # default weight
        # 尝试从 rows 中拿 weight
        for r in rows:
            if r["source_id"] == source_id:
                weight = r["weight"] or 0.5
                break
        source_with_weight.append((weight, meta))
    source_with_weight.sort(key=lambda x: x[0], reverse=True)
    sources = [meta for _, meta in source_with_weight]

    # ── 5. 运行 Pipeline ──
    report = run_pipeline(
        date_str=target_date,
        sources=sources,
        signals_by_source=signals_by_source,
        model=None,
        dry_run=args.dry_run,
    )

    saved_files = []
    if not args.dry_run and report.total_articles > 0:
        saved_files = save_articles(report)
        show_pipeline_summary(report)

    # ── 6. 写状态 JSON（供 Admin 读取）──
    _write_status(
        target_date, total_articles, total_matched,
        report.total_articles if not args.dry_run else 0,
        [p.name for p in saved_files] if saved_files else [],
    )


def _write_status(date: str, total: int, enriched: int, generated: int, files: list):
    """写 pipeline_status.json"""
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(
        json.dumps({
            "date": date,
            "total_articles": total,
            "enriched": enriched,
            "generated": generated,
            "files": files,
            "run_at": datetime.now().isoformat(),
            "success": generated > 0,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
