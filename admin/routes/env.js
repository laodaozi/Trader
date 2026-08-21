'use strict';

const express = require('express');
const router = express.Router();

const envConfig = require('../models/env-config');

// ── GET /admin/env ── 环境配置页
router.get('/env', (req, res) => {
  const cfg = envConfig.getConfig();
  res.render('admin/env', {
    title: '环境配置',
    active: 'env',
    config: cfg,
    models: envConfig.MODELS,
    styleGroups: envConfig.STYLE_GROUPS,
    saved: req.query.saved === '1',
  });
});

// ── POST /admin/env ── 保存配置（表单提交）
router.post('/env', (req, res) => {
  try {
    const body = req.body || {};
    const models = {};
    for (const m of envConfig.MODELS) {
      models[m.key] = body.models && body.models[m.key] === 'on';
    }
    const next = envConfig.saveConfig({
      models,
      style_preference: body.style_preference || '',
    });
    if (req.accepts('html') && !req.xhr && !/\/api\//.test(req.path)) {
      return res.redirect('/admin/env?saved=1');
    }
    res.json({ ok: true, config: next });
  } catch (error) {
    res.status(500).json({ ok: false, error: String(error) });
  }
});

// ── GET /admin/api/env ── 配置 JSON（供 /m 及脚本读取）
router.get('/api/env', (req, res) => {
  res.json(envConfig.getConfig());
});

// ── POST /admin/api/env ── JSON 写回（供前端 AJAX / CLI）
router.post('/api/env', (req, res) => {
  try {
    const next = envConfig.saveConfig(req.body || {});
    res.json({ ok: true, config: next });
  } catch (error) {
    res.status(500).json({ ok: false, error: String(error) });
  }
});

module.exports = router;
