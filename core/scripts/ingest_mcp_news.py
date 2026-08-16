#!/usr/bin/env python3.9
# -*- coding: utf-8 -*-
"""
ingest_mcp_news.py — MCP news 服务 → source_articles.db 灌数适配器

背景：微信/RSS token 失效导致 source_articles.db 6/28 后断供，enrich 无米下锅。
方案：用 MCP news 服务（get_alpha_morning + search_news）作独立数据源，
      不依赖微信登录态/RSS token，直接写入 enrich 的核心优先入口 source_articles.db。

数据流：
  MCP news (get_alpha_morning + search_news 按关键词)
    → 去重（article_id / title_hash）
    → 写 source_articles（fetch_status='success'，enrich 可直接消费）

用法：
  python3.9 ingest_mcp_news.py                    # 默认拉当日 alpha_morning + 主线关键词
  python3.9 ingest_mcp_news.py --keywords A股,并购重组,半导体 --topk 5
  python3.9 ingest_mcp_news.py --date 2026-07-13  # 指定日期（默认今日）
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from core.trader_mcp import mcp_call

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "source_articles.db")

# MCP 新闻作为独立信源：tier=B（补充源，权重低于微信 S/A tier，高于未注册默认）
MCP_TIER = "B"
MCP_WEIGHT = 0.6
MIN_CONTENT_LEN = 60  # 内容太短的丢弃（标题党/空壳）

# 默认主线关键词（覆盖大盘 + 常见板块主线）
DEFAULT_KEYWORDS = ["A股", "并购重组", "半导体", "新能源", "人工智能", "医药", "券商"]


def bj_now():
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def _mcp_list(service, tool, args):
    """调 MCP 并归一化为 list（兼容 {data:[...]} 或直接 [...]）。"""
    try:
        r = mcp_call(service, tool, args)
    except Exception as e:
        print(f"  ⚠ MCP {tool} 调用失败: {e}", file=sys.stderr)
        return []
    if isinstance(r, dict):
        return r.get("data", r.get("items", r.get("news", [])))
    return r if isinstance(r, list) else []


def _title_hash(title):
    return hashlib.md5(title.strip().encode("utf-8")).hexdigest()[:12]


def _collect(keywords, topk):
    """从 MCP 收集新闻，归一化为统一 dict 列表。去重键：title_hash。"""
    seen = set()
    items = []

    def _add(raw, origin):
        title = (raw.get("title") or "").strip()
        content = (raw.get("content") or "").strip()
        if not title or len(content) < MIN_CONTENT_LEN:
            return
        h = _title_hash(title)
        if h in seen:
            return
        seen.add(h)
        # article_id 优先做唯一 source_id，无则用 title_hash
        aid = str(raw.get("article_id") or h)
        items.append({
            "source_id": f"mcp_{origin}_{aid}",
            "title": title,
            "url": raw.get("url", ""),
            "content_text": content,
            "content_len": len(content),
        })

    # 1) 早间 alpha 播报（结构化大盘要闻）
    for raw in _mcp_list("news", "get_alpha_morning", {}):
        if isinstance(raw, dict):
            _add(raw, "alpha")

    # 2) 按关键词搜新闻（主线补充）
    for kw in keywords:
        for raw in _mcp_list("news", "search_news", {"keyword": kw, "topk": topk}):
            if isinstance(raw, dict):
                _add(raw, "search")

    return items


def _write(items, date):
    """写入 source_articles，fetch_status='success' 供 enrich 直接消费。"""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    # 确保表存在（复用 ingest_db.py 相同 schema）
    db.execute("""CREATE TABLE IF NOT EXISTS source_articles (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      source_id TEXT NOT NULL, source_name TEXT NOT NULL,
      tier TEXT NOT NULL, weight REAL NOT NULL,
      publish_date TEXT NOT NULL, title TEXT, url TEXT,
      content_text TEXT, content_len INTEGER DEFAULT 0,
      fetch_status TEXT DEFAULT 'pending', fetch_method TEXT,
      fetch_error TEXT, fetched_at TEXT,
      created_at TEXT DEFAULT (datetime('now','+8 hours')),
      updated_at TEXT DEFAULT (datetime('now','+8 hours')),
      UNIQUE(source_id, publish_date))""")

    now = bj_now()
    inserted = 0
    for it in items:
        try:
            db.execute("""INSERT INTO source_articles
                (source_id, source_name, tier, weight, publish_date, title, url,
                 content_text, content_len, fetch_status, fetch_method, fetched_at)
              VALUES (?,?,?,?,?,?,?,?,?,'success','mcp_news',?)
              ON CONFLICT(source_id, publish_date) DO UPDATE SET
                title=excluded.title, content_text=excluded.content_text,
                content_len=excluded.content_len, fetch_status='success',
                fetch_method='mcp_news', fetched_at=?, updated_at=?""",
              (it["source_id"], "MCP新闻", MCP_TIER, MCP_WEIGHT, date,
               it["title"], it["url"], it["content_text"], it["content_len"],
               now, now, now))
            inserted += 1
        except Exception as e:
            print(f"  ⚠ 写入失败 [{it['title'][:30]}]: {e}", file=sys.stderr)
    db.commit()
    db.close()
    return inserted


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keywords", default=",".join(DEFAULT_KEYWORDS),
                    help="逗号分隔关键词")
    ap.add_argument("--topk", type=int, default=5, help="每个关键词取几条")
    ap.add_argument("--date", default=datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d"),
                    help="publish_date（默认北京今日）")
    args = ap.parse_args()

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    print(f"🚀 ingest_mcp_news 启动 | date={args.date} | 关键词={keywords} | topk={args.topk}")

    items = _collect(keywords, args.topk)
    print(f"  📡 MCP 收集去重后: {len(items)} 条")
    if not items:
        print("  ⚠ 无有效新闻，退出（不影响 enrich，RSS 若有仍会跑）", file=sys.stderr)
        return

    n = _write(items, args.date)
    print(f"  ✅ 写入 source_articles: {n} 条（fetch_status=success，enrich 可消费）")


if __name__ == "__main__":
    main()
