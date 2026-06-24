'use strict';

/**
 * Phase 1-A: 统一调度器 — 追踪 Mac cron 各阶段执行状态
 * 
 * Mac cron 每完成一个阶段 curl POST 汇报 → 存储到 JSON 文件
 * /m 前端可通过状态 API 查看当日流水线进度
 */

const fs = require('fs');
const path = require('path');

const DATA_DIR = path.join(__dirname, '..', 'data');
const STATE_FILE = path.join(DATA_DIR, 'scheduler_state.json');
const HISTORY_DIR = path.join(DATA_DIR, 'scheduler_history');

// ── 初始化 ──
function _ensureDir() {
  if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
}

function _todayKey() {
  return new Date().toISOString().slice(0, 10);
}

function _loadState() {
  _ensureDir();
  try {
    const raw = fs.readFileSync(STATE_FILE, 'utf8');
    const state = JSON.parse(raw);
    // 跨天自动复位
    if (state.date !== _todayKey()) return _freshState();
    return state;
  } catch (_) {
    return _freshState();
  }
}

function _freshState() {
  return {
    date: _todayKey(),
    stages: {},
    overall: 'pending',
    started_at: null,
    completed_at: null,
  };
}

function _saveState(state) {
  _ensureDir();
  fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2), 'utf8');
}

function _archiveState(state) {
  if (!fs.existsSync(HISTORY_DIR)) fs.mkdirSync(HISTORY_DIR, { recursive: true });
  const file = path.join(HISTORY_DIR, `scheduler_${state.date}.json`);
  fs.writeFileSync(file, JSON.stringify(state, null, 2), 'utf8');
}

// ── API ──

/**
 * 接收 Mac cron 的阶段汇报
 * @param {string} stage - 阶段名 (trader / tracker / git_push / server_sync)
 * @param {string} status - done / failed
 * @param {number} exitCode - 退出码
 */
function heartbeat(stage, status, exitCode) {
  const state = _loadState();
  const now = new Date().toISOString();

  if (!state.started_at) state.started_at = now;

  const existing = state.stages[stage] || {};
  state.stages[stage] = {
    status,
    exit_code: exitCode != null ? exitCode : null,
    start: existing.start || now,
    end: now,
    duration_sec: existing.start
      ? Math.round((new Date(now) - new Date(existing.start)) / 1000)
      : null,
  };

  // 判定整体状态
  const stageNames = Object.keys(state.stages);
  const allDone = stageNames.every(s => state.stages[s].status === 'done');
  const anyFailed = stageNames.some(s => state.stages[s].status === 'failed');

  if (anyFailed) {
    state.overall = 'failed';
  } else if (allDone && stageNames.length >= 4) {
    state.overall = 'complete';
    state.completed_at = now;
  } else {
    state.overall = 'running';
  }

  _saveState(state);

  // 如果全部完成或失败，归档到 history
  if (state.overall === 'complete' || state.overall === 'failed') {
    _archiveState(state);
  }

  return state;
}

/** 返回当前日调度状态 */
function getStatus() {
  return _loadState();
}

/** 返回最近 N 天历史 */
function getHistory(days) {
  days = days || 7;
  const results = [];
  if (!fs.existsSync(HISTORY_DIR)) return results;
  const files = fs.readdirSync(HISTORY_DIR)
    .filter(f => f.endsWith('.json'))
    .sort()
    .reverse()
    .slice(0, days);
  for (const f of files) {
    try {
      const raw = fs.readFileSync(path.join(HISTORY_DIR, f), 'utf8');
      results.push(JSON.parse(raw));
    } catch (_) {}
  }
  return results;
}

module.exports = { heartbeat, getStatus, getHistory };
