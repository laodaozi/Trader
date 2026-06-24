'use strict';

/**
 * 微信读书扫码恢复路由
 * ======================
 * GET  /recover         — 一键扫码恢复页
 * GET  /api/health-check  — 账号健康检查 API（JSON）
 */

const express = require('express');
const router = express.Router();

// 远端 SQLite（通过 SSH 查询，同 wewe_health.py 逻辑）
function remoteSQL(query) {
  const { execSync } = require('child_process');
  try {
    return execSync(
      `ssh -o ConnectTimeout=10 -o BatchMode=yes root@139.196.115.64 "sqlite3 '/opt/wewe-rss-deploy/data/wewe-rss.db' '${query}' 2>/dev/null"`,
      { timeout: 15000, encoding: 'utf8' }
    ).trim();
  } catch {
    return '';
  }
}

function remoteShell(cmd) {
  const { execSync } = require('child_process');
  try {
    return execSync(
      `ssh -o ConnectTimeout=10 -o BatchMode=yes root@139.196.115.64 "${cmd}" 2>/dev/null`,
      { timeout: 15000, encoding: 'utf8' }
    ).trim();
  } catch {
    return '';
  }
}

// GET /recover — 扫码恢复页
router.get('/', (req, res) => {
  let accounts = [];
  let pm2Status = 'unknown';
  let connected = false;

  try {
    const accRaw = remoteSQL('SELECT id, name, status FROM accounts');
    const lines = accRaw.split('\n').filter(Boolean);
    for (const line of lines) {
      const parts = line.split('|');
      if (parts.length >= 3) {
        accounts.push({
          id: parts[0],
          name: parts[1],
          status: parseInt(parts[2], 10) || 0,
        });
      }
    }

    const pm2Raw = remoteShell("pm2 jlist 2>/dev/null");
    if (pm2Raw) {
      const procs = JSON.parse(pm2Raw);
      const wewe = procs.find((p) => p.name === 'wewe-rss');
      pm2Status = wewe?.pm2_env?.status || 'absent';
    }
    connected = true;
  } catch {
    connected = false;
  }

  const hasBad = accounts.some((a) => a.status !== 1);
  const hasAny = accounts.length > 0;

  res.render('recover', {
    title: '扫码恢复 — WeWe RSS',
    active: 'admin',
    connected,
    accounts,
    hasBad,
    hasAny,
    pm2Status,
    dashUrl: 'http://139.196.115.64/dash/',
  });
});

// GET /api/health-check — AJAX 健康检查（JSON）
router.get('/api/health-check', (req, res) => {
  try {
    const total = parseInt(remoteSQL('SELECT COUNT(*) FROM accounts'), 10) || 0;
    const bad = parseInt(
      remoteSQL("SELECT COUNT(*) FROM accounts WHERE status != 1"),
      10
    ) || 0;
    const pm2Raw = remoteShell("pm2 jlist 2>/dev/null");
    let pm2Status = 'unknown';
    try {
      const procs = JSON.parse(pm2Raw);
      const wewe = procs.find((p) => p.name === 'wewe-rss');
      pm2Status = wewe?.pm2_env?.status || 'absent';
    } catch {}

    // 最新文章时间
    const lastTs = remoteSQL(
      "SELECT MAX(publish_time) FROM articles WHERE publish_time > 0"
    );
    const ageH = lastTs
      ? Math.round((Math.floor(Date.now() / 1000) - parseInt(lastTs, 10)) / 3600)
      : -1;

    const ok = total - bad;
    const healthy = ok > 0 && bad === 0 && pm2Status === 'online';

    res.json({
      healthy,
      accounts: { total, ok, bad },
      pm2: pm2Status,
      lastArticleAgeH: ageH,
      ts: new Date().toISOString(),
    });
  } catch (e) {
    res.json({ healthy: false, error: e.message });
  }
});

module.exports = router;
