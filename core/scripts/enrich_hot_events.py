#!/usr/bin/env python3
"""
热点事件 LLM 增强：从标题生成 thesis（核心观点）+ tickers（相关标的）
用法：
    python3 enrich_hot_events.py                     # 从 source_articles.db 读最近24h事件，逐条增强
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
# V7.7: source_articles.db 为唯一数据源（MCP news + URL ingest 写入）
SOURCE_ARTICLES_DB = PROJECT_ROOT / "data" / "source_articles.db"
CACHE_FILE = PROJECT_ROOT / "data" / "hot_enrichment.json"

# ── API ──
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://new-api.finstep.cn")
# V7.7: S1 代理 + 多模型降级链（参照 generate_strategy_reflection.py）
# Sonnet/Haiku 不带 s1- 前缀（代理只有 Opus 系列注册了 s1- 别名）
MODELS = ["claude-sonnet-4-6", "claude-haiku-4-5-20251001", "claude-sonnet-4-5-20250929"]

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
    """调 LLM 生成 thesis + tickers（V7.7: 多模型降级 + 重试 + DeepSeek 兜底）"""
    import anthropic
    client = Anthropic(api_key=API_KEY, base_url=BASE_URL)
    user_prompt = _build_user_prompt(title, source, content)

    last_error = None
    for m in MODELS:
        try:
            attempt = 0
            while True:
                attempt += 1
                try:
                    resp = client.messages.create(
                        model=m,
                        max_tokens=512,
                        system=SYSTEM_PROMPT,
                        messages=[{"role": "user", "content": user_prompt}],
                    )
                    text = resp.content[0].text if resp.content else ""
                    return _parse_llm_response(text)
                except (anthropic.APIConnectionError, anthropic.APITimeoutError, anthropic.RateLimitError) as e:
                    if attempt >= 3:
                        raise
                    wait = min(5 * attempt, 30)
                    print(f"    重试 {m} ({type(e).__name__})，{wait}s后...", file=sys.stderr)
                    time.sleep(wait)
        except Exception as e:
            err_msg = str(e)
            print(f"    {m} 失败: {err_msg[:80]}，降级下一个...", file=sys.stderr)
            last_error = e
            continue

    # DeepSeek 终极兜底 — 先试 S1 代理的 deepseek-v4-pro，再试 DeepSeek 直连
    print(f"    所有 Claude 模型失败，尝试 deepseek 兜底...", file=sys.stderr)

    # 方案1: 通过 S1 代理调 deepseek-v4-pro（复用 ANTHROPIC_API_KEY）
    try:
        import openai
        proxy_ds = openai.OpenAI(api_key=API_KEY, base_url=BASE_URL + "/v1")
        ds_resp = proxy_ds.chat.completions.create(
            model="deepseek-v4-pro",
            max_tokens=512,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        text = ds_resp.choices[0].message.content or ""
        print(f"    deepseek-v4-pro (via proxy) ✓", file=sys.stderr)
        return _parse_llm_response(text)
    except ImportError:
        raise RuntimeError(f"所有 Claude 模型失败且 openai 未安装。最后错误: {last_error}")
    except Exception as proxy_err:
        print(f"    deepseek-v4-pro (proxy) 失败: {proxy_err}", file=sys.stderr)

    # 方案2: DeepSeek 直连（需单独 DEEPSEEK_API_KEY）
    ds_key = os.environ.get("DEEPSEEK_API_KEY")
    if not ds_key:
        raise RuntimeError(f"所有 Claude 模型失败，代理 DeepSeek 也失败，且无 DEEPSEEK_API_KEY。最后错误: {last_error}")
    try:
        ds_client = openai.OpenAI(api_key=ds_key, base_url="https://api.deepseek.com/v1")
        ds_resp = ds_client.chat.completions.create(
            model="deepseek-chat",
            max_tokens=512,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        text = ds_resp.choices[0].message.content or ""
        return _parse_llm_response(text)
    except Exception as e:
        raise RuntimeError(f"DeepSeek 直连也失败: {e}。Claude 最后错误: {last_error}")


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


def enrich_one(title: str, cache: dict, force: bool = False, source: str = "", content: str = "", source_date: str = "") -> dict:
    """增强单条标题，优先读缓存（content 用于 LLM 但不参与缓存 key）"""
    h = _hash(title)
    if not force and h in cache:
        return cache[h]

    try:
        result = _call_llm(title, source, content)
        result["enriched_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        result["source_date"] = source_date or time.strftime("%Y-%m-%d")
        result["title"] = title
        result["source"] = source
        cache[h] = result
        _save_cache(cache)
        print(f"  ✅ [{source}] {title[:40]}... → thesis={result.get('thesis','')[:30]}...", file=sys.stderr)
        return result
    except Exception as e:
        # V7.7: 明确标记失败，不静默返回空 thesis 伪装成功
        print(f"  ❌ [{source}] {title[:40]}... → {type(e).__name__}: {e}", file=sys.stderr)
        return {"thesis": "", "tickers": [], "error": str(e), "failed": True}


def enrich_from_db(db_path: Path, cache: dict, force: bool = False) -> list:
    """从 source_articles.db 读最近24h事件并增强。

    V7.7: 替换 WeWe RSS，改读 source_articles.db（MCP news + URL ingest 写入）。
    按 weight 降序输出，保证高权重信源优先展示。
    """
    today = time.strftime("%Y-%m-%d")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    all_rows = conn.execute(
        """SELECT source_id, source_name, title, url, content_text, content_len,
                  tier, weight, publish_date, created_at
           FROM source_articles
           WHERE fetch_status = 'success'
             AND publish_date >= date(?, '-1 day')
           ORDER BY weight DESC, created_at DESC""",
        (today,),
    ).fetchall()
    conn.close()

    events = []
    for row in all_rows:
        title = row["title"] or ""
        if not title:
            continue
        content = row["content_text"] or ""
        source_name = row["source_name"] or ""
        weight = row["weight"] or 0.5
        pub_date = row["publish_date"] or today
        enrichment = enrich_one(title, cache, force, source=source_name, content=content, source_date=pub_date)
        events.append({
            "title": title,
            "time": row["created_at"] or "",
            "source": source_name,
            "pic_url": "",
            "thesis": enrichment.get("thesis", ""),
            "tickers": enrichment.get("tickers", []),
            "weight": weight,
            "url": row["url"] or "",
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
        # 从文件读事件（含 source/content/source_date 字段）
        with open(args.file, encoding="utf-8") as f:
            raw_events = json.load(f)
        events = []
        for ev in raw_events:
            enrichment = enrich_one(ev.get("title", ""), cache, force=args.force,
                                     source=ev.get("source", ""), content=ev.get("content", ""),
                                     source_date=ev.get("source_date", ""))
            events.append({**ev, "thesis": enrichment.get("thesis", ""), "tickers": enrichment.get("tickers", [])})
        print(json.dumps(events, ensure_ascii=False, indent=2))
        return

    # 默认：从 source_articles.db 读事件
    if not SOURCE_ARTICLES_DB.exists():
        print(f"❌ DB 不存在: {SOURCE_ARTICLES_DB}（先跑 ingest_mcp_news.py 灌数据）", file=sys.stderr)
        sys.exit(1)
    events = enrich_from_db(SOURCE_ARTICLES_DB, cache, force=args.force)

    print(json.dumps(events, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
