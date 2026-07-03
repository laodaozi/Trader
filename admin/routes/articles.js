"use strict";

const express = require("express");
const fs = require("fs/promises");
const path = require("path");
const https = require("https");
const http = require("http");
const { spawn } = require("child_process");

// ── 微信/通用文章正文抓取 ──────────────────────────────────────────────────
// 返回 { title, content, error }
// content 是纯文本（保留换行），error 非空表示抓取失败
function fetchArticleContent(url) {
  return new Promise((resolve) => {
    const timeout = 12000;
    const isMp = url.includes("mp.weixin.qq.com");

    const options = {
      headers: {
        "User-Agent": isMp
          ? "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.40"
          : "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": isMp ? "https://mp.weixin.qq.com/" : url,
      },
    };

    const lib = url.startsWith("https") ? https : http;
    const req = lib.get(url, options, (res) => {
      // 跟重定向（最多2跳）
      if ((res.statusCode === 301 || res.statusCode === 302) && res.headers.location) {
        return fetchArticleContent(res.headers.location).then(resolve);
      }
      if (res.statusCode !== 200) {
        return resolve({ title: "", content: "", error: `HTTP ${res.statusCode}` });
      }

      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        const html = Buffer.concat(chunks).toString("utf8");

        let title = "";
        let content = "";

        // 提取标题
        const titleMatch = html.match(/<title[^>]*>([^<]+)<\/title>/i);
        if (titleMatch) title = titleMatch[1].replace(/\s*[-_|].*$/, "").trim();

        if (isMp) {
          // 微信文章：js_content div
          const contentMatch = html.match(/id=["']js_content["'][^>]*>([\s\S]*?)<\/div>\s*<div[^>]+id=["']js_content_copyright/i)
            || html.match(/id=["']js_content["'][^>]*>([\s\S]{200,}?)<\/div>/i);
          if (contentMatch) {
            content = contentMatch[1]
              .replace(/<br\s*\/?>/gi, "\n")
              .replace(/<\/p>/gi, "\n")
              .replace(/<[^>]+>/g, "")
              .replace(/&nbsp;/g, " ")
              .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&")
              .replace(/\n{3,}/g, "\n\n")
              .trim();
          }
          // og:title 通常更干净
          const ogTitle = html.match(/property=["']og:title["']\s+content=["']([^"']+)["']/i);
          if (ogTitle) title = ogTitle[1].trim();
        } else {
          // 通用页面：取 <article> 或 <main> 或 body 文本
          const bodyMatch = html.match(/<article[^>]*>([\s\S]+?)<\/article>/i)
            || html.match(/<main[^>]*>([\s\S]+?)<\/main>/i)
            || html.match(/<body[^>]*>([\s\S]+?)<\/body>/i);
          if (bodyMatch) {
            content = bodyMatch[1]
              .replace(/<script[\s\S]*?<\/script>/gi, "")
              .replace(/<style[\s\S]*?<\/style>/gi, "")
              .replace(/<br\s*\/?>/gi, "\n")
              .replace(/<\/p>/gi, "\n")
              .replace(/<[^>]+>/g, "")
              .replace(/&nbsp;/g, " ")
              .replace(/\n{3,}/g, "\n\n")
              .trim()
              .slice(0, 8000); // 截断防止超大正文
          }
        }

        if (!content || content.length < 50) {
          return resolve({ title, content: "", error: "正文提取失败（可能需要登录或内容为空）" });
        }
        resolve({ title, content, error: null });
      });
    });

    req.setTimeout(timeout, () => {
      req.destroy();
      resolve({ title: "", content: "", error: "抓取超时（12s）" });
    });
    req.on("error", (e) => resolve({ title: "", content: "", error: e.message }));
  });
}

const router = express.Router();
const PROJECT_ROOT = path.join(__dirname, "..", "..");
const articleDir = path.join(PROJECT_ROOT, "output", "article");

router.get("/articles", async (req, res) => {
  try {
    let articles = [];

    try {
      const entries = await fs.readdir(articleDir, { withFileTypes: true });
      const htmlEntries = entries.filter((entry) => entry.isFile() && path.extname(entry.name) === ".html");

      articles = await Promise.all(
        htmlEntries.map(async (entry) => {
          const filePath = path.join(articleDir, entry.name);
          const stats = await fs.stat(filePath);

          return {
            name: path.basename(entry.name, ".html"),
            path: filePath,
            mtime: stats.mtime,
          };
        })
      );

      articles.sort((a, b) => b.mtime - a.mtime);
    } catch (error) {
      if (error.code !== "ENOENT") {
        throw error;
      }
    }

    // 注入标注 + 生成状态供 EJS 模板渲染
    const enrichmentStatus = await _readEnrichmentStatus();
    const pipelineStatus    = await _readPipelineStatus();

    let flash = null;
    if (req.query.generated) {
      flash = { generated: parseInt(req.query.generated) };
    } else if (req.query.submitted) {
      flash = { submitted: true };
    }

    res.render("articles/index", {
      title: "文章看板",
      active: "articles",
      subTab: "index",
      articles,
      enrichmentStatus,
      pipelineStatus,
      flash,
    });
  } catch (error) {
    res.status(500).render("admin/error", {
      title: "500 服务器错误",
      status: 500,
      active: "articles",
      message: "文章目录读取失败",
      error,
    });
  }
});

// ── /admin/articles/stats ── 文章数据统计（合并自 trader/article-stats）──
router.get('/articles/stats', async (req, res) => {
  try {
    const enrichPath = path.join(__dirname, '..', '..', 'data', 'hot_enrichment.json');
    let enrichment = [];
    try {
      const raw = await fs.readFile(enrichPath, 'utf8');
      const parsed = JSON.parse(raw);
      // 兼容 list 或 dict
      enrichment = Array.isArray(parsed) ? parsed : Object.values(parsed);
    } catch (_) { /* optional */ }

    const totalArticles = enrichment.length;
    const withTickers = enrichment.filter((e) => e && e.tickers && e.tickers.length > 0);
    const articlesWithTickers = withTickers.length;
    const zeroTickerCount = totalArticles - articlesWithTickers;

    const allTickers = [];
    for (const e of enrichment) {
      if (e && e.tickers && Array.isArray(e.tickers)) {
        allTickers.push(...e.tickers);
      }
    }
    const totalTickers = allTickers.length;
    const uniqueCodes = new Set(allTickers.map((t) => t.code));
    const uniqueTickers = uniqueCodes.size;
    const avgTickers = totalArticles > 0 ? (totalTickers / totalArticles).toFixed(1) : '0.0';
    const signalRatio = totalArticles > 0 ? Math.round((articlesWithTickers / totalArticles) * 100) + '%' : '0%';

    let earliestEnrich = null, latestEnrich = null;
    for (const e of enrichment) {
      if (e && e.enriched_at) {
        if (!earliestEnrich || e.enriched_at < earliestEnrich) earliestEnrich = e.enriched_at;
        if (!latestEnrich || e.enriched_at > latestEnrich) latestEnrich = e.enriched_at;
      }
    }

    const tickerCountMap = {};
    for (const t of allTickers) {
      const key = t.code;
      if (!tickerCountMap[key]) tickerCountMap[key] = { code: t.code, name: t.name, count: 0 };
      tickerCountMap[key].count++;
    }
    const topTickers = Object.values(tickerCountMap).sort((a, b) => b.count - a.count).slice(0, 15);

    const dailyMap = {};
    for (const e of enrichment) {
      if (!e || !e.enriched_at) continue;
      const date = e.enriched_at.slice(0, 10);
      if (!dailyMap[date]) dailyMap[date] = { total: 0, withTickers: 0, tickerCount: 0, zeroCount: 0 };
      dailyMap[date].total++;
      if (e.tickers && e.tickers.length > 0) {
        dailyMap[date].withTickers++;
        dailyMap[date].tickerCount += e.tickers.length;
      } else {
        dailyMap[date].zeroCount++;
      }
    }
    const dailyCounts = Object.entries(dailyMap).map(([date, d]) => ({ date, ...d })).sort((a, b) => b.date.localeCompare(a.date)).slice(0, 30);

    res.render('articles/stats', {
      title: '文章数据统计',
      active: 'articles',
      subTab: 'stats',
      stats: { totalArticles, articlesWithTickers, signalRatio, totalTickers, uniqueTickers, avgTickers, earliestEnrich, latestEnrich, zeroTickerCount, topTickers, dailyCounts },
      error: null,
    });
  } catch (error) {
    res.status(500).render('admin/error', {
      title: '500 服务器错误',
      status: 500,
      active: 'articles',
      message: '文章统计数据加载失败',
      error,
    });
  }
});

// ── Plan C: POST /articles/generate ── 触发文章生成
router.post("/articles/generate", async (req, res) => {
  try {
    const date = req.body.date || new Date().toISOString().slice(0, 10);
    const scriptPath = path.join(PROJECT_ROOT, "core", "scripts", "run_article_pipeline.py");

    const proc = spawn("python3", [scriptPath, "--date", date], {
      cwd: PROJECT_ROOT,
      stdio: "pipe",
    });

    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (d) => (stdout += d.toString()));
    proc.stderr.on("data", (d) => (stderr += d.toString()));

    await new Promise((resolve, reject) => {
      proc.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`exit ${code}: ${stderr}`))));
      proc.on("error", reject);
    });

    // 读取生成计数
    const status = await _readPipelineStatus();
    res.redirect(`/admin/articles?generated=${status.generated || 0}`);
  } catch (error) {
    res.status(500).render("admin/error", {
      title: "500 生成失败",
      status: 500,
      active: "articles",
      message: "文章生成失败",
      error,
    });
  }
});

// ── Plan C: GET /articles/status ── JSON 状态接口
router.get("/articles/status", async (req, res) => {
  try {
    const enrichmentStatus = await _readEnrichmentStatus();
    const pipelineStatus    = await _readPipelineStatus();
    res.json({ enrichment: enrichmentStatus, pipeline: pipelineStatus });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// ── 信源管理：POST /articles/submit ── 手动提交 URL（自动抓取）或直接粘贴正文
router.post("/articles/submit", async (req, res) => {
  try {
    let { url, title, content } = req.body;
    url = (url || "").trim();
    content = (content || "").trim();

    let fetchError = null;

    // URL 有值且 content 为空/只是 URL 本身 → 自动抓取
    if (url && (!content || content === url)) {
      const fetched = await fetchArticleContent(url);
      if (fetched.error) {
        fetchError = fetched.error;
        // 抓取失败：content 存空，记录 fetch_error，不阻断提交
      } else {
        content = fetched.content;
        if (!title && fetched.title) title = fetched.title;
      }
    }

    // URL 和 content 都为空才真正拒绝
    if (!url && !content) {
      const enrichmentStatus = await _readEnrichmentStatus();
      const pipelineStatus = await _readPipelineStatus();
      return res.render("articles/index", {
        title: "文章看板",
        active: "articles",
        subTab: "index",
        articles: [],
        enrichmentStatus,
        pipelineStatus,
        flash: { error: "请填写 URL 或粘贴文章正文" },
      });
    }

    const manualPath = path.join(PROJECT_ROOT, "data", "sources", "manual.jsonl");
    await fs.mkdir(path.join(PROJECT_ROOT, "data", "sources"), { recursive: true });

    const entry = {
      id: `manual_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      url,
      title: (title || "").trim() || "(无标题)",
      content: content || "",
      submitted_at: new Date().toISOString(),
      enriched: false,
    };
    if (fetchError) entry.fetch_error = fetchError;

    const line = JSON.stringify(entry) + "\n";
    await fs.appendFile(manualPath, line, "utf8");

    const flash = fetchError
      ? `submitted_warn:抓取失败(${fetchError})，已保存 URL，请手动粘贴正文`
      : "submitted";
    res.redirect("/admin/articles?submitted=" + encodeURIComponent(flash));
  } catch (error) {
    res.status(500).render("admin/error", {
      title: "500 提交失败",
      status: 500,
      active: "articles",
      message: "信源提交失败",
      error,
    });
  }
});

// ── 信源管理：POST /articles/fetch-url ── AJAX 预览抓取（前端用）
router.post("/articles/fetch-url", async (req, res) => {
  const { url } = req.body;
  if (!url) return res.status(400).json({ ok: false, error: "缺少 url" });
  const result = await fetchArticleContent(url);
  if (result.error) return res.json({ ok: false, error: result.error });
  res.json({ ok: true, title: result.title, content: result.content, length: result.content.length });
});

// ── 信源管理：GET /articles/sources ── 展示原始信源（manual + RSS）
router.get("/articles/sources", async (req, res) => {
  try {
    const sources = await _readSources();
    res.render("articles/sources", {
      title: "信源管理",
      active: "articles",
      subTab: "sources",
      sources,
    });
  } catch (error) {
    res.status(500).render("admin/error", {
      title: "500 信源错误",
      status: 500,
      active: "articles",
      message: "信源数据加载失败",
      error,
    });
  }
});

// ── 辅助：读取原始信源（manual + RSS）
async function _readSources() {
  const manualPath = path.join(PROJECT_ROOT, "data", "sources", "manual.jsonl");
  let manualEntries = [];
  try {
    const raw = await fs.readFile(manualPath, "utf8");
    manualEntries = raw
      .split("\n")
      .filter(Boolean)
      .map((line) => JSON.parse(line))
      .sort((a, b) => b.submitted_at.localeCompare(a.submitted_at));
  } catch (_) {}

  // RSS entries — try WeWe RSS DB on ECS
  let rssEntries = [];
  try {
    const { execSync } = require("child_process");
    const rssJson = execSync(
      `python3.9 -c "
import sqlite3, json
db = sqlite3.connect('/opt/wewe-rss-deploy/data/wewe-rss.db')
cur = db.cursor()
cur.execute('SELECT id, mp_id, title, publish_time, created_at, length(content) as content_len FROM articles ORDER BY publish_time DESC LIMIT 50')
rows = cur.fetchall()
out = []
for r in rows:
    out.append({'id': r[0], 'mp_id': r[1], 'title': r[2], 'publish_time': r[3], 'created_at': r[4], 'content_len': r[5]})
print(json.dumps(out, ensure_ascii=False))
"`,
      { timeout: 5000, encoding: "utf8" }
    );
    rssEntries = JSON.parse(rssJson);
    // Convert publish_time (millis/seconds) to readable date
    for (const e of rssEntries) {
      const ts = e.publish_time;
      if (ts > 1e12) {
        e.publish_date = new Date(ts).toISOString().slice(0, 10);
      } else {
        e.publish_date = new Date(ts * 1000).toISOString().slice(0, 10);
      }
    }
  } catch (_) {}

  return {
    manual: manualEntries,
    rss: rssEntries,
    rss_total: rssEntries.length,
    manual_total: manualEntries.length,
    rss_error: false,
  };
}

// ── 辅助函数 ──
async function _readEnrichmentStatus() {
  const enrichPath = path.join(PROJECT_ROOT, "data", "hot_enrichment.json");
  let items = []; // normalized: array of enrichment objects
  try {
    const raw = await fs.readFile(enrichPath, "utf8");
    const parsed = JSON.parse(raw);
    // hot_enrichment.json 可能是 list 或 dict，统一转为 array
    if (Array.isArray(parsed)) {
      items = parsed;
    } else if (parsed && typeof parsed === "object") {
      items = Object.values(parsed);
    }
  } catch (_) {}

  const today = new Date().toISOString().slice(0, 10);
  const todayItems = items.filter((e) => e && e.enriched_at && e.enriched_at.startsWith(today));
  const todayWithTickers = todayItems.filter((e) => e.tickers && e.tickers.length > 0);

  return {
    total: items.length,
    today: todayItems.length,
    todayWithTickers: todayWithTickers.length,
  };
}

async function _readPipelineStatus() {
   const statusPath = path.join(PROJECT_ROOT, "data", "pipeline_status.json");
  try {
    const raw = await fs.readFile(statusPath, "utf8");
    return JSON.parse(raw);
  } catch (_) {
    return { date: "", generated: 0, enriched: 0 };
  }
}

// ── V7.2: 微信公众号草稿箱推送 ──
// POST /articles/mp-draft  body: { filename }
router.post('/articles/mp-draft', async (req, res) => {
  const { filename } = req.body;
  if (!filename) return res.status(400).json({ ok: false, error: '缺少 filename' });

  const appId     = process.env.WECHAT_APPID;
  const appSecret = process.env.WECHAT_APPSECRET;
  if (!appId || !appSecret) {
    return res.status(503).json({ ok: false, error: '未配置 WECHAT_APPID / WECHAT_APPSECRET，请在 .env 中添加' });
  }

  try {
    const filePath = path.join(articleDir, filename + '.html');
    const html = await fs.readFile(filePath, 'utf8');

    // 1. 获取 access_token
    const tokenUrl = `https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid=${appId}&secret=${appSecret}`;
    const tokenRes = await fetch(tokenUrl);
    const tokenData = await tokenRes.json();
    if (!tokenData.access_token) {
      return res.status(502).json({ ok: false, error: '获取 access_token 失败: ' + JSON.stringify(tokenData) });
    }
    const token = tokenData.access_token;

    // 2. 提取标题（从 <title> 或 filename）
    const titleMatch = html.match(/<title[^>]*>([^<]+)<\/title>/i);
    const title = titleMatch ? titleMatch[1].trim() : filename;

    // 3. 推送草稿箱
    const draftUrl = `https://api.weixin.qq.com/cgi-bin/draft/add?access_token=${token}`;
    const draftRes = await fetch(draftUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        articles: [{
          title,
          content: html,
          author: 'CycleRadar',
          need_open_comment: 0,
          only_fans_can_comment: 0
        }]
      })
    });
    const draftData = await draftRes.json();

    if (draftData.errcode && draftData.errcode !== 0) {
      return res.status(502).json({ ok: false, error: '草稿箱接口错误: ' + draftData.errmsg, code: draftData.errcode });
    }

    return res.json({ ok: true, media_id: draftData.media_id, title });
  } catch (err) {
    return res.status(500).json({ ok: false, error: String(err) });
  }
});

// GET /articles/mp-status — 检查公众号配置状态
router.get('/articles/mp-status', (req, res) => {
  const configured = !!(process.env.WECHAT_APPID && process.env.WECHAT_APPSECRET);
  res.json({ configured, appId: configured ? process.env.WECHAT_APPID.slice(0,8) + '****' : null });
});

module.exports = router;
