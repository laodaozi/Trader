'use strict';

/**
 * system.js — 系统状态端点（统一版本源 + 健康检查）
 * 
 * 提供:
 *   GET /admin/api/system/status  — 完整系统状态（版本 + 管线 + 新鲜度）
 *   导出 getVersion()             — 其他路由可 require('./system').getVersion()
 */

const express = require('express');
const fs = require('fs');
const path = require('path');

const router = express.Router();

// ── 路径常量 ──
const ROOT = path.resolve(__dirname, '../..');
const VERSION_FILE = path.join(ROOT, 'VERSION');
const DATA_DIR = path.join(ROOT, 'data');
const LOGS_DIR = path.join(DATA_DIR, 'logs');

// ── 工具函数 ──

/** 读取版本文件，失败返回 null */
function getVersion() {
  try {
    return fs.readFileSync(VERSION_FILE, 'utf-8').trim();
  } catch (_e) {
    return null;
  }
}

/** 检查文件 modify time 是否在今天 */
function ranToday(filePath) {
  try {
    const stat = fs.statSync(filePath);
    const mtime = new Date(stat.mtime);
    const today = new Date();
    return (
      mtime.getFullYear() === today.getFullYear() &&
      mtime.getMonth() === today.getMonth() &&
      mtime.getDate() === today.getDate()
    );
  } catch (_e) {
    return false;
  }
}

/** 返回文件路径和它的新鲜度（从 mtime 算，单位小时） */
function fileFreshness(filePath) {
  try {
    const stat = fs.statSync(filePath);
    const ageHours = (Date.now() - stat.mtime.getTime()) / 3600000;
    const mtime = stat.mtime.toISOString();
    return { exists: true, age_hours: Math.round(ageHours * 10) / 10, mtime };
  } catch (_e) {
    return { exists: false, age_hours: null, mtime: null };
  }
}

// ── 端点 ──

/**
 * GET /admin/api/system/status
 * 
 * 返回完整系统状态，包含:
 *   - version:     应用版本（VERSION 文件内容）
 *   - uptime:      进程运行时长（秒）
 *   - pipeline:    各管线今日是否运行
 *   - freshness:   各数据文件的新鲜度
 *   - server_time: 服务器当前 ISO 时间
 *
 * getStatus() 是纯净的同步函数，无依赖，可被 admin.js 等路由复用。
 */
function getStatus() {
  const version = getVersion();
  const now = new Date().toISOString();

  // ── 管线状态 ──
  const pipeline = {
    scanner_signals: {
      label: 'Scanner 14模型',
      ran: ranToday(path.join(LOGS_DIR, 'scanner_signals_cron.log')),
      file: 'scanner_signals_cron.log',
    },
    ma_signals: {
      label: '兼并重组信号',
      ran: ranToday(path.join(LOGS_DIR, 'ma_signals_cron.log')),
      file: 'ma_signals_cron.log',
    },
    strategy_reflection: {
      label: 'LLM策略反思',
      ran: ranToday(path.join(LOGS_DIR, 'strategy_reflection_cron.log')),
      file: 'strategy_reflection_cron.log',
    },
    stock_agent: {
      label: 'Stock Agent',
      ran: ranToday(path.join(LOGS_DIR, 'stock_agent_cron.log')),
      file: 'stock_agent_cron.log',
    },
  };

  const ranCount = Object.values(pipeline).filter(p => p.ran).length;
  const totalCount = Object.keys(pipeline).length;

  // ── 数据新鲜度 ──
  const freshness = {
    strategy: {
      label: '选股信号',
      ...fileFreshness(path.join(DATA_DIR, 'trader_strategy.jsonl')),
    },
    tracker: {
      label: '信号跟踪',
      ...fileFreshness(path.join(DATA_DIR, 'trader_tracker.jsonl')),
    },
    timing: {
      label: '市场体温',
      ...fileFreshness(path.join(DATA_DIR, 'timing_history.json')),
    },
    positions: {
      label: '账户持仓',
      ...fileFreshness(path.join(DATA_DIR, 'positions.json')),
    },
    reflection: {
      label: '策略反思',
      ...fileFreshness(path.join(DATA_DIR, 'strategy_reflection.json')),
    },
    narrative: {
      label: '事件叙事',
      ...fileFreshness(path.join(DATA_DIR, 'event_narrative_latest.json')),
    },
  };

  // ── 系统健康判定 ──
  const allRan = ranCount === totalCount;
  const staleData = Object.entries(freshness)
    .filter(([, f]) => f.exists && f.age_hours > 24)
    .map(([key, f]) => ({ key, label: f.label, age_hours: f.age_hours }));

  const health = allRan && staleData.length === 0 ? 'healthy'
    : !allRan && staleData.length > 0 ? 'degraded_pipeline_and_data'
    : !allRan ? 'degraded_pipeline'
    : 'degraded_data';

  return {
    version,
    health,
    server_time: now,
    uptime_seconds: Math.floor(process.uptime()),
    pipeline: {
      ran: ranCount,
      total: totalCount,
      items: pipeline,
    },
    freshness,
    stale_items: staleData,
  };
}

// ── 端点包装 ──
router.get('/api/system/status', (_req, res) => {
  res.json(getStatus());
});

// ── 导出 ──
router.getVersion = getVersion;
router.getStatus = getStatus;

module.exports = router;
