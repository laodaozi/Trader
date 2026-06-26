"use strict";

const express = require("express");
const fs = require("fs/promises");
const path = require("path");

const router = express.Router();
const articleDir = path.join(__dirname, "../..", "data", "articles");

router.get("/articles", async (req, res) => {
  try {
    let articles = [];

    try {
      const entries = await fs.readdir(articleDir, { withFileTypes: true });
      const htmlEntries = entries.filter((entry) => entry.isFile() && (path.extname(entry.name) === ".html" || path.extname(entry.name) === ".md"));

      articles = await Promise.all(
        htmlEntries.map(async (entry) => {
          const filePath = path.join(articleDir, entry.name);
          const stats = await fs.stat(filePath);

          return {
            name: path.basename(entry.name, path.extname(entry.name)),
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

    res.render("articles/index", {
      title: "文章看板",
      active: "articles",
      subTab: "index",
      articles,
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
    let enrichment = {};
    try {
      const raw = await fs.readFile(enrichPath, 'utf8');
      enrichment = JSON.parse(raw);
    } catch (_) { /* optional */ }

    const entries = Array.isArray(enrichment) ? enrichment.map((e,i) => [e.title||String(i), e]) : Object.entries(enrichment);
    const totalArticles = entries.length;
    const withTickers = entries.filter(([, e]) => e.tickers && e.tickers.length > 0);
    const articlesWithTickers = withTickers.length;
    const zeroTickerCount = totalArticles - articlesWithTickers;

    const allTickers = [];
    for (const [, e] of entries) {
      if (e.tickers && Array.isArray(e.tickers)) {
        allTickers.push(...e.tickers);
      }
    }
    const totalTickers = allTickers.length;
    const uniqueCodes = new Set(allTickers.map((t) => t.code));
    const uniqueTickers = uniqueCodes.size;
    const avgTickers = totalArticles > 0 ? (totalTickers / totalArticles).toFixed(1) : '0.0';
    const signalRatio = totalArticles > 0 ? Math.round((articlesWithTickers / totalArticles) * 100) + '%' : '0%';

    let earliestEnrich = null, latestEnrich = null;
    for (const [, e] of entries) {
      if (e.enriched_at) {
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
    for (const [, e] of entries) {
      if (!e.enriched_at) continue;
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

module.exports = router;

// ── /admin/articles/:name/preview ── Markdown 文章预览
router.get("/articles/:name/preview", async (req, res) => {
  try {
    const name = req.params.name.replace(/[^a-z0-9_\-]/gi, "");
    const mdPath = path.join(articleDir, name + ".md");
    const raw = await fs.readFile(mdPath, "utf8");
    // Convert markdown to simple HTML
    let html = raw
      .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
      .replace(/^# (.+)$/gm, "<h1>$1</h1>")
      .replace(/^## (.+)$/gm, "<h2>$1</h2>")
      .replace(/^### (.+)$/gm, "<h3>$1</h3>")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.+?)\*/g, "<em>$1</em>")
      .replace(/^> (.+)$/gm, "<blockquote>$1</blockquote>")
      .replace(/^---$/gm, "<hr>")
      .replace(/\n\n/g, "</p><p>")
      .replace(/\n/g, "<br>");
    res.send(`<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${name}</title><style>body{max-width:720px;margin:40px auto;padding:0 20px;font-family:-apple-system,sans-serif;line-height:1.8;color:#1a1a1a}h1{font-size:24px;line-height:1.3;margin-bottom:8px}h2{font-size:18px;margin:28px 0 8px}h3{font-size:15px;color:#555;margin:20px 0 6px}blockquote{border-left:3px solid #3b82f6;padding-left:12px;color:#555;margin:16px 0}hr{border:none;border-top:1px solid #eee;margin:24px 0}strong{color:#111}p{margin:12px 0}.back{display:inline-block;margin-bottom:20px;color:#3b82f6;text-decoration:none;font-size:13px}</style></head><body><a href="/admin/articles" class="back">← 返回文章列表</a><p>${html}</p></body></html>`);
  } catch (err) {
    res.status(404).send("文章不存在");
  }
});
