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
from datetime import datetime, timedelta
from pathlib import Path

# ── 路径（与 enrich_hot_events.py 对齐）──
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_WEWE_DB_PRIMARY = Path("/opt/wewe-rss-deploy/data/wewe-rss.db")
_WEWE_DB_FALLBACK = PROJECT_ROOT / "admin" / "data" / "wewe-rss.db"
WEWE_DB = _WEWE_DB_PRIMARY if _WEWE_DB_PRIMARY.exists() else _WEWE_DB_FALLBACK
ENRICHMENT_PATH = PROJECT_ROOT / "data" / "hot_enrichment.json"
STATUS_PATH = PROJECT_ROOT / "data" / "pipeline_status.json"

sys.path.insert(0, str(PROJECT_ROOT))

from core.writing.pipeline import run_pipeline, save_articles, show_pipeline_summary
from core.writing.source_registry import SOURCE_ROLES


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

    # ── 2a. 读 manual.jsonl（手动提交信源，enriched=True 的条目）──
    manual_rows = []
    manual_path = PROJECT_ROOT / "data" / "sources" / "manual.jsonl"
    if manual_path.exists():
        for line in manual_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            # 已增强 且 提交日期在目标日
            submitted = e.get("submitted_at", "")[:10]
            if e.get("enriched") and submitted == target_date:
                manual_rows.append(e)

    # ── 2b. 连 WeWe RSS DB ──
    if not WEWE_DB.exists():
        print(f"❌ DB 不存在: {WEWE_DB}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(WEWE_DB))
    conn.row_factory = sqlite3.Row

    date_start = datetime.strptime(target_date, "%Y-%m-%d")
    date_end = date_start + timedelta(days=1)

    rows = conn.execute(
        """SELECT a.mp_id, a.title, a.publish_time,
                  COALESCE(f.mp_name, a.mp_id) AS source,
                  a.content
           FROM articles a LEFT JOIN feeds f ON a.mp_id = f.id
           WHERE a.publish_time >= ? AND a.publish_time < ?
           ORDER BY a.publish_time DESC""",
        (int(date_start.timestamp()), int(date_end.timestamp())),
    ).fetchall()
    conn.close()

    # ── 3. 匹配 enrichment → 构建 signals_by_source ──
    # enrichment 结构: {hash: {thesis, tickers, enriched_at}}
    # key = md5(title)[:12]，与 enrich_hot_events._hash() 对齐
    signals_by_source = {}    # {mp_id: [signal_dict]}
    seen_sources = {}         # {mp_id: source_meta}
    total_matched = 0
    total_articles = len(rows)

    for row in rows:
        mp_id = row["mp_id"]
        title = row["title"]
        content = row["content"] or ""
        source_name = row["source"] or mp_id
        title_hash = _hash_title(title)

        # 检查是否有标注
        enr = enrichment.get(title_hash)
        if not enr:
            continue

        total_matched += 1

        # 注册信源（第一次遇到时）
        if mp_id not in seen_sources:
            src_cfg = SOURCE_ROLES.get(mp_id, {})
            seen_sources[mp_id] = {
                "mp_id": mp_id,
                "mp_name": source_name,
                "category": src_cfg.get("category", ""),
                "tags": src_cfg.get("tags", []),
            }

        # 构建 signal（正文摘要取前 1500 字，不塞爆 prompt）
        text_snippet = _clean_content(content)[:1500] if content else ""
        signals_by_source.setdefault(mp_id, []).append({
            "title": title,
            "summary": text_snippet,
            "thesis": enr.get("thesis", ""),
            "tickers": enr.get("tickers", []),
        })

    print(f"📊 {target_date}: {total_matched}/{total_articles} 篇有标注（WeWe RSS）", file=sys.stderr)

    # ── 3b. 合并 manual 条目 ──
    MANUAL_SOURCE_ID = "manual_submission"
    for e in manual_rows:
        eid   = e.get("id", "")
        title = e.get("title", "(无标题)")
        content = e.get("content", "")
        # 先用 id_hash 查，再用 title_hash 兜底
        id_hash    = hashlib.md5(eid.encode()).hexdigest()[:12]
        title_hash = _hash_title(title)
        enr = enrichment.get(id_hash) or enrichment.get(title_hash)
        if not enr or not enr.get("thesis"):
            continue

        if MANUAL_SOURCE_ID not in seen_sources:
            seen_sources[MANUAL_SOURCE_ID] = {
                "mp_id":    MANUAL_SOURCE_ID,
                "mp_name":  "手动提交",
                "category": "manual",
                "tags":     [],
            }

        text_snippet = _clean_content(content)[:1500] if content else ""
        signals_by_source.setdefault(MANUAL_SOURCE_ID, []).append({
            "title":   title,
            "summary": text_snippet,
            "thesis":  enr.get("thesis", ""),
            "tickers": enr.get("tickers", []),
        })
        total_matched += 1

    print(f"📊 {target_date}: +{len(manual_rows)} 篇手动提交（合计 {total_matched} 篇有标注）", file=sys.stderr)

    # 无标注 → 写状态并优雅退出（不报错）
    if total_matched == 0:
        _write_status(target_date, total_articles, 0, 0, [])
        print("⚠️  本日无已标注文章，跳过生成", file=sys.stderr)
        sys.exit(0)

    # ── 4. 构建 source 列表（按 weight 降序，与 enrich_hot_events.py 对齐）──
    source_with_weight = []
    for mp_id, meta in seen_sources.items():
        weight = SOURCE_ROLES.get(mp_id, {}).get("weight", 0.5)
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


def _write_status(date: str, total: int, enriched_new: int, generated: int, files: list):
    """写 pipeline_status.json（累加 enriched，防止归零覆盖）"""
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)

    prev_enriched = 0
    if STATUS_PATH.exists():
        try:
            prev = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
            prev_enriched = prev.get("enriched", 0)
        except Exception:
            pass

    STATUS_PATH.write_text(
        json.dumps({
            "date": date,
            "total_articles": total,
            "enriched": prev_enriched + enriched_new,
            "generated": generated,
            "files": files,
            "run_at": datetime.now().isoformat(),
            "success": generated > 0,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
