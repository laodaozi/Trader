"""
modules/mcp.py — 共享 MCP 客户端

所有模块通过此文件调用 Finstep MCP，统一重试/超时/SSE 解析逻辑。
模块加载时自动读取 .env（MCP_SIGNATURE），各模块无需重复配置。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

BASE_URL  = "http://fintool-mcp.finstep.cn"
SIGNATURE = os.environ.get("MCP_SIGNATURE", "")


def mcp_call(service: str, tool: str, arguments: dict) -> dict:
    """调用 Finstep MCP 工具，最多重试 1 次（2 次尝试），失败返回 {}。"""
    url = f"{BASE_URL}/{service}?signature={SIGNATURE}"
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    for attempt in range(2):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            resp.encoding = "utf-8"
            for line in resp.text.split("\n"):
                if line.startswith("data: "):
                    body   = json.loads(line[6:])
                    result = body.get("result", {})
                    clist  = result.get("content", [])
                    if clist and clist[0].get("type") == "text":
                        text = clist[0]["text"]
                        try:
                            parsed = json.loads(text)
                            return parsed.get("data", parsed)
                        except json.JSONDecodeError:
                            return {"_raw_text": text}
                    sc = result.get("structuredContent", {})
                    if sc:
                        return sc.get("data", sc)
                    return result
            return {}
        except Exception:
            if attempt == 0:
                time.sleep(1.5)
                continue
            return {}
    return {}
