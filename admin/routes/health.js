'use strict';

const express = require('express');
const { execFile } = require('child_process');
const path = require('path');

const router = express.Router();

// health_check.sh 路径（ECS 部署后在 /opt/cycleradar-trader/scripts/health_check.sh）
const HEALTH_SCRIPT = path.join(__dirname, '..', '..', 'scripts', 'health_check.sh');

/**
 * 运行 health_check.sh --json，返回解析后的 JSON 对象。
 * 超时 15s，兼容退出码 0/1。
 */
function runHealthCheck() {
  return new Promise((resolve) => {
    execFile('bash', [HEALTH_SCRIPT, '--json'], { timeout: 15000 }, (err, stdout, stderr) => {
      try {
        const data = JSON.parse(stdout || '{}');
        resolve({ ok: true, data });
      } catch (_parseErr) {
        resolve({
          ok: false,
          data: {
            status: 'critical',
            summary: { pass: 0, warn: 0, fail: 1 },
            checks: [],
            _error: stderr || (err && err.message) || 'Failed to parse health_check.sh output',
          },
        });
      }
    });
  });
}

// ── GET /admin/health ── 健康面板 HTML ──
router.get('/health', async (req, res) => {
  const { data } = await runHealthCheck();
  res.render('admin/health', {
    title: '生产健康',
    active: 'health',
    health: data,
  });
});

// ── GET /admin/api/health ── 机器可读 JSON（用于面板轮询刷新） ──
router.get('/api/health', async (req, res) => {
  const { data } = await runHealthCheck();
  res.json(data);
});

module.exports = router;
