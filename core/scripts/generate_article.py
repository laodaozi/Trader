#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, os, sys, argparse
from datetime import datetime
from anthropic import Anthropic

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENRICHMENT_PATH = os.path.join(BASE_DIR, "data", "hot_enrichment.json")
ARTICLE_DIR = os.path.join(BASE_DIR, "data", "articles")
os.makedirs(ARTICLE_DIR, exist_ok=True)

def get_api_key():
    key = os.getenv("ANTHROPIC_API_KEY")
    if key:
        return key
    env_file = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.startswith("ANTHROPIC_API_KEY="):
                    val = line.strip().split("=", 1)[1]
                    return val.strip('"').strip("'")
    raise ValueError("API key not set")

def get_base_url():
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    if base_url:
        return base_url
    env_file = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.startswith("ANTHROPIC_BASE_URL="):
                    val = line.strip().split("=", 1)[1]
                    return val.strip('"').strip("'")
    return "https://api.anthropic.com"

def load_hot_events():
    try:
        with open(ENRICHMENT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    
    events = []
    bad_kws = ["正文缺失", "无法确认", "信息不完整", "但无正文", "但正文缺失", "无法提取"]
    
    for title_hash, item in data.items():
        if not isinstance(item, dict):
            continue
        thesis = item.get("thesis", "")
        tickers = item.get("tickers", [])
        if not thesis.strip():
            continue
        if thesis == "非市场分析内容":
            continue
        if any(kw in thesis for kw in bad_kws) and len(tickers) == 0:
            continue
        
        events.append({
            "title": item.get("title", ""),
            "time": item.get("time", ""),
            "source": item.get("source", ""),
            "thesis": thesis,
            "tickers": tickers,
        })
    
    events.sort(key=lambda x: x.get("time", ""), reverse=True)
    return events

def generate_article(event, date_str):
    base_url = get_base_url()
    client = Anthropic(api_key=get_api_key(), base_url=base_url)
    tickers = [t.get("name", t.get("code", "")) for t in event.get("tickers", [])]
    ticker_str = "、".join(tickers) if tickers else "暂无标的"
    
    system_prompt = """你是 CycleRadar 内容编辑。
风格：有观点、有节奏、像人写的。三段式：核心信号 → 深度解读 → 双叙事对冲。
开头钩子，结尾思考。1500-2000字。
参考结构：开头钩子 → 三件支撑 → 乐观vs悲观 → 分歧点 → 收尾。
纯Markdown。"""
    
    user_prompt = "今日热点({}):\n标题：{}\n来源：{}\nAI观点：{}\n标的：{}\n\n基于此撰写深度文章。标题吸引人、核心观点清晰、三件支撑、双叙事、思考题。文末：周期雷达 CycleRadar · 仅供参考，不构成投资建议。".format(
        date_str, event["title"], event["source"], event["thesis"], ticker_str
    )
    
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )
    return response.content[0].text

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    args = parser.parse_args()
    
    now_time = datetime.now().strftime("%H:%M:%S")
    print("[{}] Generate article ({})".format(now_time, args.date))
    
    events = load_hot_events()
    if not events:
        print("No valid events")
        sys.exit(0)
    
    print("Top event: {}".format(events[0]["title"][:50]))
    print("Calling Claude API...")
    
    try:
        article = generate_article(events[0], args.date)
    except Exception as e:
        print("Error: {}".format(e))
        sys.exit(1)
    
    output = os.path.join(ARTICLE_DIR, "article_{}.md".format(args.date.replace("-", "")))
    with open(output, "w", encoding="utf-8") as f:
        f.write(article)
    
    print("Saved: {} ({} chars)".format(output, len(article)))
    title = next((l for l in article.split("\n") if l.strip().startswith("#")), "")
    print("Title: {}".format(title.strip("#").strip()))

if __name__ == "__main__":
    main()
