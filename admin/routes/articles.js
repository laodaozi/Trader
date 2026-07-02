"use strict";

const express = require("express");
const fs = require("fs/promises");
const path = require("path");
const { spawn } = require("child_process");

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

// ── 信源管理：POST /articles/submit ── 手动提交 URL+内容
router.post("/articles/submit", async (req, res) => {
  try {
    const { url, title, content } = req.body;
    if (!content || !content.trim()) {
      const enrichmentStatus = await _readEnrichmentStatus();
      const pipelineStatus = await _readPipelineStatus();
      return res.render("articles/index", {
        title: "文章看板",
        active: "articles",
        subTab: "index",
        articles: [],
        enrichmentStatus,
        pipelineStatus,
        flash: { error: "内容不能为空" },
      });
    }

    const manualPath = path.join(PROJECT_ROOT, "data", "sources", "manual.jsonl");
    await fs.mkdir(path.join(PROJECT_ROOT, "data", "sources"), { recursive: true });

    const entry = {
      id: `manual_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      url: (url || "").trim(),
      title: (title || "").trim() || "(无标题)",
      content: content.trim(),
      submitted_at: new Date().toISOString(),
      enriched: false,
    };

    const line = JSON.stringify(entry) + "\n";
    await fs.appendFile(manualPath, line, "utf8");

    res.redirect("/admin/articles?submitted=1");
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
