"""
wechat_fetcher.py — 微信公众号文章正文抓取器

策略：
  1. httpx + BeautifulSoup 直接抓 #js_content（主路径，快）
  2. 失败时 Playwright 兜底（慢但成功率高）
  3. 返回 FetchResult，fetch_status 明确分类
"""
from __future__ import annotations
import hashlib, re, time
from dataclasses import dataclass, field
from typing import Optional

try:
    import httpx
    from bs4 import BeautifulSoup
except ImportError:
    httpx = None
    BeautifulSoup = None

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://mp.weixin.qq.com/",
}

MIN_CONTENT_LEN = 200  # 少于此字数视为抓取失败

@dataclass
class FetchResult:
    url: str
    status: str          # success / failed / blocked / deleted / timeout
    method: str          # http / playwright / manual
    title: str = ""
    account_name: str = ""
    content_text: str = ""
    content_html: str = ""
    content_len: int = 0
    error: str = ""
    http_code: int = 0
    elapsed_ms: int = 0


def normalize_url(url: str) -> str:
    """统一处理微信文章 URL，去掉无关参数保留核心。"""
    url = url.strip()
    # 支持 https://mp.weixin.qq.com/s/xxx 和 https://mp.weixin.qq.com/s?__biz=...
    if "mp.weixin.qq.com" not in url:
        raise ValueError(f"不是微信公众号 URL: {url}")
    return url


def _extract_from_html(html: str) -> tuple[str, str, str]:
    """从 HTML 提取 (title, account_name, content_text)。"""
    soup = BeautifulSoup(html, "html.parser")

    # 标题
    title = ""
    og_title = soup.find("meta", property="og:title")
    if og_title:
        title = og_title.get("content", "").strip()
    if not title:
        h1 = soup.find("h1", id="activity-name") or soup.find("h1", class_="rich_media_title")
        if h1:
            title = h1.get_text(strip=True)

    # 公众号名
    account_name = ""
    account_tag = soup.find("strong", class_="profile_nickname") or \
                  soup.find("span", id="js_name")
    if account_tag:
        account_name = account_tag.get_text(strip=True)

    # 正文内容
    content_div = soup.find(id="js_content")
    if not content_div:
        content_div = soup.find("div", class_="rich_media_content")

    if content_div:
        # 清理脚本和样式标签
        for tag in content_div.find_all(["script", "style"]):
            tag.decompose()
        content_text = content_div.get_text(separator="\n", strip=True)
        # 去除连续空行
        content_text = re.sub(r"\n{3,}", "\n\n", content_text)
    else:
        content_text = ""

    return title, account_name, content_text


def fetch_http(url: str, timeout: int = 15) -> FetchResult:
    """主路径：httpx 直接抓取。"""
    if httpx is None or BeautifulSoup is None:
        return FetchResult(url=url, status="failed", method="http",
                           error="httpx or beautifulsoup4 not installed")
    t0 = time.time()
    try:
        url = normalize_url(url)
        resp = httpx.get(url, headers=HEADERS, timeout=timeout, follow_redirects=True)
        elapsed = int((time.time() - t0) * 1000)
        http_code = resp.status_code

        if http_code == 404:
            return FetchResult(url=url, status="deleted", method="http",
                               http_code=http_code, elapsed_ms=elapsed,
                               error="文章已删除 (404)")

        if http_code != 200:
            return FetchResult(url=url, status="failed", method="http",
                               http_code=http_code, elapsed_ms=elapsed,
                               error=f"HTTP {http_code}")

        html = resp.text

        # 检测是否被拦截（微信验证页）
        if "环境异常" in html or "访问页面过于频繁" in html or "验证" in html[:500]:
            return FetchResult(url=url, status="blocked", method="http",
                               http_code=http_code, elapsed_ms=elapsed,
                               error="微信风控拦截，需 Playwright 兜底")

        title, account_name, content_text = _extract_from_html(html)

        if len(content_text) < MIN_CONTENT_LEN:
            return FetchResult(url=url, status="failed", method="http",
                               http_code=http_code, elapsed_ms=elapsed,
                               title=title, account_name=account_name,
                               content_text=content_text,
                               content_len=len(content_text),
                               error=f"正文过短 ({len(content_text)}字)，可能未渲染")

        return FetchResult(
            url=url, status="success", method="http",
            http_code=http_code, elapsed_ms=elapsed,
            title=title, account_name=account_name,
            content_text=content_text,
            content_html="",  # 不保存原始 HTML 节省空间
            content_len=len(content_text),
        )

    except httpx.TimeoutException:
        return FetchResult(url=url, status="timeout", method="http",
                           elapsed_ms=int((time.time()-t0)*1000),
                           error="请求超时")
    except ValueError as e:
        return FetchResult(url=url, status="failed", method="http", error=str(e))
    except Exception as e:
        return FetchResult(url=url, status="failed", method="http",
                           elapsed_ms=int((time.time()-t0)*1000),
                           error=f"未知错误: {e}")


def fetch_playwright(url: str, timeout: int = 30) -> FetchResult:
    """兜底路径：Playwright 渲染后抓取。"""
    t0 = time.time()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return FetchResult(url=url, status="failed", method="playwright",
                           error="playwright not installed，运行: pip install playwright && playwright install chromium")

    try:
        url = normalize_url(url)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(extra_http_headers={
                "Accept-Language": "zh-CN,zh;q=0.9",
            })
            page.goto(url, timeout=timeout * 1000, wait_until="networkidle")
            # 等待正文容器加载
            try:
                page.wait_for_selector("#js_content", timeout=10000)
            except Exception:
                pass
            html = page.content()
            browser.close()

        elapsed = int((time.time() - t0) * 1000)
        title, account_name, content_text = _extract_from_html(html)

        if len(content_text) < MIN_CONTENT_LEN:
            return FetchResult(url=url, status="failed", method="playwright",
                               elapsed_ms=elapsed,
                               title=title, account_name=account_name,
                               content_text=content_text,
                               content_len=len(content_text),
                               error=f"Playwright 渲染后正文仍过短 ({len(content_text)}字)")

        return FetchResult(
            url=url, status="success", method="playwright",
            elapsed_ms=elapsed,
            title=title, account_name=account_name,
            content_text=content_text,
            content_len=len(content_text),
        )
    except Exception as e:
        return FetchResult(url=url, status="failed", method="playwright",
                           elapsed_ms=int((time.time()-t0)*1000),
                           error=f"Playwright 失败: {e}")


def fetch(url: str, use_playwright_fallback: bool = True) -> FetchResult:
    """主入口：先 httpx，失败/blocked 时走 Playwright。"""
    result = fetch_http(url)
    if result.status in ("success", "deleted"):
        return result

    if use_playwright_fallback and result.status in ("failed", "blocked", "timeout"):
        pw_result = fetch_playwright(url)
        if pw_result.status == "success":
            return pw_result
        # 两者都失败，返回原始 http 结果（保留更多信息）
        result.error = f"HTTP: {result.error} | Playwright: {pw_result.error}"

    return result


if __name__ == "__main__":
    import sys
    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://mp.weixin.qq.com/s/Y939tozaY0z7ArS44nLzjQ"
    r = fetch(test_url, use_playwright_fallback=False)
    print(f"status={r.status} method={r.method} len={r.content_len} elapsed={r.elapsed_ms}ms")
    print(f"title={r.title}")
    print(f"account={r.account_name}")
    if r.content_text:
        print(f"正文前300字:\n{r.content_text[:300]}")
    if r.error:
        print(f"error={r.error}")
