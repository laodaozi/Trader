#!/usr/bin/env python3
"""
热点事件 LLM 增强：从标题生成 thesis（核心观点）+ tickers（相关标的）
用法：
    python3 enrich_hot_events.py                     # 从 wewe-rss.db 读最近48h事件，逐条增强
    python3 enrich_hot_events.py --title "标题"       # 增强单条标题（调试）
    python3 enrich_hot_events.py --file events.json   # 增强 JSON 文件中的事件

缓存策略：data/hot_enrichment.json，key = title 的 hash
成本：~$0.001/条（Haiku 4.5）
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

from anthropic import Anthropic

# ── 路径 ──
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 优先使用 wewe-rss 真实运行目录（/opt/wewe-rss-deploy/data/wewe-rss.db）
# 旧副本 admin/data/wewe-rss.db 停止更新于 2026-06-11，仅作 fallback
_WEWE_DB_PRIMARY = Path("/opt/wewe-rss-deploy/data/wewe-rss.db")
_WEWE_DB_FALLBACK = PROJECT_ROOT / "admin" / "data" / "wewe-rss.db"
WEWE_DB = _WEWE_DB_PRIMARY if _WEWE_DB_PRIMARY.exists() else _WEWE_DB_FALLBACK
CACHE_FILE = PROJECT_ROOT / "data" / "hot_enrichment.json"

# ── API ──
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
MODEL = "claude-sonnet-4-6"  # 当前 token 唯一可用模型

# ── Prompt ──
SYSTEM_PROMPT = """你是一个事件驱动交易解读引擎。你的任务是从微信公众号文章中提炼：这件事对A股意味着什么，交易员现在应该关注什么。

## 核心原则

**先判断是不是市场事件，再解读。** 大多数财经公众号标题偏情绪化/标题党——你的工作不是评价标题好坏，而是穿透措辞，提取文章描述的市场现象。

- 如果文章描述了一个实际发生的市场事件（大跌、政策、数据），解读事件 > 评价标题。
- 如果标题是纯营销/培训/广告，且正文无市场事件信息，标记为「非市场分析内容」。
- 如果文章是泛泛的资讯汇总（如"周末值得关注"），提取其暗示的市场基调。

## 输入格式

你会收到：信源（公众号名）、标题、正文摘要（前 3000 字符）。优先从正文摘要中提取具体数据、政策措辞、公司/行业名称来构建 thesis 和 tickers。标题可能偏标题党，正文才是真实信息所在。

## 解读框架

对每条文章回答三个问题：
1. **这跟上周有什么不同？** 不是人尽皆知的事。找出增量信息——预期差、市场原来怎么想、现在哪里变了。
2. **钱会往哪流？** 事件对资金的含义（政策→板块、数据→风格、海外→A股映射）。
3. **为什么是现在？** 结合时间窗口（季报期/政策窗口/事件催化），说明紧迫性。

## 输出格式

严格输出 JSON，无其他文字：
{
  "thesis": "交易级洞察，30-60字。直接说：什么变了、影响什么方向、持续性如何。用「超预期」「证伪」「price in」「拐点」「抱团松动」「风格切换」等术语。",
  "tickers": [
    {"code": "sh/sz+6位数字", "name": "简称", "reason": "关联逻辑≤15字"}
  ]
}

## 约束

- thesis 必须包含可证伪的判断（涨/跌/轮动/分化），不允许骑墙
- 如果含「传」「据称」「或」，标注不确定性但依然给出基准判断
- 营销/培训/广告类 → thesis="非市场分析内容", tickers=[]
- 泛资讯汇总 → 提取其中最可能影响次日市场的方向
- tickers 最多3只，宁可少推不硬凑，不确定代码就不输出
- **正文中提到具体公司/股票名称的，优先推 ticker；正文无具体标的时再从标题推断**"""

USER_PROMPT_TEMPLATE = """信源：{source}
标题：{title}
正文摘要：{content_snippet}

穿透标题+正文，提取市场现象并给出交易级解读。"""


def _build_user_prompt(title: str, source: str = "", content: str = "") -> str:
    """构造带信源+正文的 user prompt"""
    snippet = _clean_content(content)[:3000] if content else ""
    return USER_PROMPT_TEMPLATE.format(source=source or "未知", title=title, content_snippet=snippet or "（无正文）")


def _clean_content(html: str) -> str:
    """从 HTML 中提取纯文本，保留关键结构"""
    import re as _re
    # 移除 style/script 标签
    text = _re.sub(r'<style[^>]*>.*?</style>', '', html, flags=_re.DOTALL | _re.IGNORECASE)
    text = _re.sub(r'<script[^>]*>.*?</script>', '', text, flags=_re.DOTALL | _re.IGNORECASE)
    # 移除 HTML 标签
    text = _re.sub(r'<[^>]+>', ' ', text)
    # 合并空白
    text = _re.sub(r'\s+', ' ', text).strip()
    return text


def _hash(title: str) -> str:
    """标题 → 短 hash（缓存 key），同一标题不重复调 LLM"""
    return hashlib.md5(title.encode()).hexdigest()[:12]


def _load_cache() -> dict:
    """加载增强缓存 {hash: {thesis, tickers, enriched_at}}"""
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_cache(cache: dict):
    """保存增强缓存"""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _call_llm(title: str, source: str = "", content: str = "") -> dict:
    """调 Claude Sonnet 生成 thesis + tickers"""
    client = Anthropic(api_key=API_KEY, base_url=BASE_URL)
    user_prompt = _build_user_prompt(title, source, content)

    resp = client.messages.create(
        model=MODEL,
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    text = resp.content[0].text if resp.content else ""
    # 尝试从响应中提取 JSON
    return _parse_llm_response(text)


def _parse_llm_response(text: str) -> dict:
    """从 LLM 文本响应中提取 JSON（处理代码围栏、截断等）"""
    if not text:
        return {"thesis": "", "tickers": []}

    # 1) 剥离 ```json ... ``` 围栏
    cleaned = re.sub(r'^```(?:json)?\s*\n?', '', text.strip())
    cleaned = re.sub(r'\n?```\s*$', '', cleaned)
    cleaned = cleaned.strip()

    # 2) 直接解析
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 3) 提取 JSON 对象块（处理前导文字）
    m = re.search(r'\{[\s\S]*\}', cleaned)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            # 4) 尝试修复截断的 JSON：补全缺失的括号
            try:
                repaired = _repair_truncated_json(m.group())
                if repaired:
                    return json.loads(repaired)
            except (json.JSONDecodeError, Exception):
                pass

    return {"thesis": "", "tickers": []}


def _repair_truncated_json(text: str) -> str:
    """补全被截断的 JSON：尝试关闭未闭合的数组/对象/字符串"""
    # 统计未闭合的括号
    stack = []
    in_str = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == '\\' and in_str:
            escape = True
            continue
        if ch == '"' and not escape:
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in '{[':
            stack.append(ch)
        elif ch == '}':
            if stack and stack[-1] == '{':
                stack.pop()
        elif ch == ']':
            if stack and stack[-1] == '[':
                stack.pop()

    closes = []
    for opener in reversed(stack):
        closes.append('}' if opener == '{' else ']')

    repaired = text.rstrip()
    # 如果最后一个未闭合的是字符串，加上引号
    if in_str:
        repaired += '"'
    # 如果截断在一个值的中间，加逗号修复很困难，尝试直接关括号
    if repaired.rstrip().endswith(','):
        repaired = repaired.rstrip().rstrip(',')
    repaired += ''.join(closes)
    return repaired


def enrich_one(title: str, cache: dict, force: bool = False, source: str = "", content: str = "") -> dict:
    """增强单条标题，优先读缓存（content 用于 LLM 但不参与缓存 key）"""
    h = _hash(title)
    if not force and h in cache:
        return cache[h]

    try:
        result = _call_llm(title, source, content)
        result["enriched_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        cache[h] = result
        _save_cache(cache)
        print(f"  ✅ [{source}] {title[:40]}... → thesis={result.get('thesis','')[:30]}...", file=sys.stderr)
        return result
    except Exception as e:
        print(f"  ❌ [{source}] {title[:40]}... → {e}", file=sys.stderr)
        return {"thesis": "", "tickers": [], "error": str(e)}


def enrich_from_db(db_path: Path, cache: dict, force: bool = False) -> list:
    """从 wewe-rss.db 读最近24h事件，按信源 tier 分配配额后增强。

    配额规则（来自 source_registry.py）：
      S — 全量抓（叙事平权）
      A — ≤5条/源（微策神机/财闻私享/在下杜牛牛）
      B — ≤3条/源（财经早餐/数据宝/小马白话期权）
      C — ≤3条/源（台球之门/低吸波段王）
    最终按 weight 降序输出，保证高权重信源优先展示。
    """
    try:
        from core.writing.source_registry import SOURCE_ROLES
    except ImportError:
        SOURCE_ROLES = {}

    since = int(time.time()) - 86400  # 24h

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # 拉取24h内全部文章，带信源名
    all_rows = conn.execute(
        """SELECT a.mp_id, a.title, a.publish_time,
                  COALESCE(f.mp_name, a.mp_id) AS source,
                  a.pic_url, a.content
           FROM articles a LEFT JOIN feeds f ON a.mp_id = f.id
           WHERE a.publish_time >= ?
           ORDER BY a.publish_time DESC""",
        (since,),
    ).fetchall()
    conn.close()

    # 按信源分组，按 tier 配额截断
    from collections import defaultdict
    by_source: dict = defaultdict(list)
    for row in all_rows:
        by_source[row["mp_id"]].append(row)

    selected = []
    for mp_id, rows in by_source.items():
        meta = SOURCE_ROLES.get(mp_id, {})
        limit = meta.get("limit", 3)  # 未注册信源默认3条
        weight = meta.get("weight", 0.5)
        # limit=None 表示全量（S tier）
        batch = rows if limit is None else rows[:limit]
        for row in batch:
            selected.append((weight, row))

    # 按 weight 降序，同 weight 按时间降序
    selected.sort(key=lambda x: (x[0], x[1]["publish_time"]), reverse=True)

    events = []
    for weight, row in selected:
        title = row["title"]
        content = row["content"] or ""
        source_name = row["source"] or ""
        enrichment = enrich_one(title, cache, force, source=source_name, content=content)
        events.append({
            "title": title,
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(row["publish_time"])),
            "source": source_name,
            "pic_url": row["pic_url"] or "",
            "thesis": enrichment.get("thesis", ""),
            "tickers": enrichment.get("tickers", []),
            "weight": weight,
        })

    return events


def main():
    parser = argparse.ArgumentParser(description="热点事件 LLM 增强")
    parser.add_argument("--title", help="增强单条标题（调试）")
    parser.add_argument("--source", help="标题来源（调试，配合--title用）")
    parser.add_argument("--content", help="正文内容（调试，配合--title用）")
    parser.add_argument("--file", help="增强 JSON 文件中的事件")
    parser.add_argument("--force", action="store_true", help="强制重新生成，忽略缓存")
    args = parser.parse_args()

    if not API_KEY:
        print("❌ ANTHROPIC_API_KEY 未设置", file=sys.stderr)
        sys.exit(1)

    cache = _load_cache()

    if args.title:
        # 单条增强（--source --content 可选）
        result = enrich_one(args.title, cache, force=args.force, source=args.source or "", content=args.content or "")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.file:
        # 从文件读事件（含 source/content 字段）
        with open(args.file, encoding="utf-8") as f:
            raw_events = json.load(f)
        events = []
        for ev in raw_events:
            enrichment = enrich_one(ev.get("title", ""), cache, force=args.force,
                                     source=ev.get("source", ""), content=ev.get("content", ""))
            events.append({**ev, "thesis": enrichment.get("thesis", ""), "tickers": enrichment.get("tickers", [])})
        print(json.dumps(events, ensure_ascii=False, indent=2))
        return

    # 默认：从 DB 读事件
    if not WEWE_DB.exists():
        print(f"❌ DB 不存在: {WEWE_DB}", file=sys.stderr)
        sys.exit(1)
    events = enrich_from_db(WEWE_DB, cache, force=args.force)

    print(json.dumps(events, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
