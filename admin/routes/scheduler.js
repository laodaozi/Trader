'use strict';

/**
 * Phase 1-A: 调度器状态 API 路由
 * 
 * POST /api/scheduler/heartbeat  — Mac cron 汇报阶段状态
 * GET  /api/scheduler/status     — 查看当日调度状态
 * GET  /api/scheduler/history    — 查看最近 N 天历史 (?days=7)
 */

const express = require('express');
const router = express.Router();
const scheduler = require('../scheduler');

// 简单共享密钥，防止公开访问
const SCHEDULER_TOKEN = process.env.SCHEDULER_TOKEN || 'cycleradar-scheduler';

function _checkAuth(req, res) {
  const token = req.headers['x-scheduler-token'] || req.query.token || '';
  if (token !== SCHEDULER_TOKEN) {
    res.status(401).json({ error: 'unauthorized' });
    return false;
  }
  return true;
}

// POST /api/scheduler/heartbeat
router.post('/api/scheduler/heartbeat', (req, res) => {
  if (!_checkAuth(req, res)) return;

  const { stage, status, exit_code } = req.body || {};
  if (!stage || !status) {
    return res.status(400).json({ error: 'missing stage or status' });
  }

  const state = scheduler.heartbeat(stage, status, exit_code);
  res.json({ ok: true, overall: state.overall });
});

// GET /api/scheduler/status
router.get('/api/scheduler/status', (req, res) => {
  if (!_checkAuth(req, res)) return;
  res.json(scheduler.getStatus());
});

// GET /api/scheduler/history?days=7
router.get('/api/scheduler/history', (req, res) => {
  if (!_checkAuth(req, res)) return;
  const days = parseInt(req.query.days, 10) || 7;
  res.json(scheduler.getHistory(days));
});

module.exports = router;
