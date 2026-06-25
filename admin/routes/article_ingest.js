'use strict';
/**
 * article_ingest.js — 信源正文投喂路由 v2
 * DB 操作全部通过 ingest_db.py（Python）执行，绕开 better-sqlite3 编译问题
 */

const express = require('express');
const path = require('path');
const { execFile } = require('child_process');
const router = express.Router();

const PYTHON = '/usr/bin/python3.9';
const DB_PY  = path.join(__dirname, '..', '..', 'core', 'scripts', 'ingest_db.py');
const FETCH_PY = path.join(__dirname, '..', '..', 'core', 'scripts', 'wechat_fetcher.py');

function bjToday() {
  const now = new Date(Date.now() + 8 * 3600 * 1000);
  return now.toISOString().slice(0, 10);
}

function runPy(args, cb) {
  execFile(PYTHON, [DB_PY, ...args], { timeout: 15000 }, (err, stdout, stderr) => {
    if (err) return cb(err, null);
    try { cb(null, JSON.parse(stdout.trim())); }
    catch(e) { cb(new Error('JSON parse: ' + stdout.slice(0, 200)), null); }
  });
}

// ── GET /admin/ingest ── 看板页
router.get('/ingest', (req, res) => {
  const date = req.query.date || bjToday();
  runPy(['status', date], (err, data) => {
    if (err) return res.status(500).send('DB 错误: ' + err.message);
    const missing_critical = data.sources.filter(
      s => ['S','A'].includes(s.tier) && s.status !== 'success'
    );
    res.render('ingest/index', {
      title: '信源正文投喂',
      active: 'ingest',
      date,
      statusList: data.sources,
      missing_critical,
      error: null,
    });
  });
});

// ── GET /admin/ingest/status (JSON) ──
router.get('/ingest/status', (req, res) => {
  const date = req.query.date || bjToday();
  runPy(['status', date], (err, data) => {
    if (err) return res.status(500).json({ error: err.message });
    res.json(data);
  });
});

// ── POST /admin/ingest/url ──
router.post('/ingest/url', (req, res) => {
  const { source_id, url, date } = req.body;
  const publish_date = date || bjToday();

  if (!url || !url.includes('mp.weixin.qq.com')) {
    return res.status(400).json({ ok: false, error: '请输入有效的微信公众号文章 URL' });
  }

  // 1. 写入 pending
  runPy(['upsert', source_id, publish_date, url], (err) => {
    if (err) return res.status(500).json({ ok: false, error: err.message });

    // 立即返回，后台抓取
    res.json({ ok: true, message: '抓取已启动，请稍后刷新' });

    // 2. 异步抓取
    execFile(PYTHON, ['-c', `
import sys, json
sys.path.insert(0, '/opt/cycleradar-trader')
from core.scripts.wechat_fetcher import fetch
r = fetch(sys.argv[1], use_playwright_fallback=False)
print(json.dumps({
  'status': r.status, 'method': r.method,
  'title': r.title, 'content_text': r.content_text,
  'content_len': r.content_len, 'error': r.error, 'elapsed_ms': r.elapsed_ms
}))
    `, url], { timeout: 25000 }, (fetchErr, stdout) => {
      if (fetchErr || !stdout) {
        runPy(['save_result', source_id, publish_date,
               JSON.stringify({ status: 'failed', error: String(fetchErr || 'no output'), method: 'http' })], () => {});
        return;
      }
      try {
        const result = JSON.parse(stdout.trim());
        runPy(['save_result', source_id, publish_date, JSON.stringify(result)], () => {});
      } catch(e) {
        runPy(['save_result', source_id, publish_date,
               JSON.stringify({ status: 'failed', error: 'parse error', method: 'http' })], () => {});
      }
    });
  });
});

// ── POST /admin/ingest/manual ──
router.post('/ingest/manual', (req, res) => {
  const { source_id, content_text, title, date } = req.body;
  const publish_date = date || bjToday();
  if (!content_text || content_text.trim().length < 50) {
    return res.status(400).json({ ok: false, error: '正文至少 50 字' });
  }
  runPy(['manual', source_id, publish_date, title || '', content_text.trim()], (err, data) => {
    if (err) return res.status(500).json({ ok: false, error: err.message });
    res.json(data);
  });
});

// ── POST /admin/ingest/retry ──
router.post('/ingest/retry', (req, res) => {
  const { source_id, date } = req.body;
  runPy(['get_url', source_id, date], (err, data) => {
    if (err || !data.url) return res.status(400).json({ ok: false, error: '没有已保存的 URL' });
    res.json({ ok: true, message: '请重新提交 URL' });
  });
});

module.exports = router;
