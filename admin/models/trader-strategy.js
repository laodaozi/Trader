'use strict';

const fs = require('fs/promises');
const path = require('path');

const STRATEGY_FILE = path.join(__dirname, '..', '..', 'data', 'trader_strategy.jsonl');

/**
 * 从 strategy_log.jsonl 解析所有记录，按日期分组，得分降序。
 *
 * JSONL 字段（与 Python strategy.py _log_jsonl 完全对齐）：
 *   date, code, name, nx, ma_align, fib_zone, weekly_dir, capital_dir,
 *   rr, model_hits[], signal_type, strategy, score,
 *   entry_low, entry_high, stop_loss, take_profit[], error
 */

async function getAvailableDates() {
  const records = await _readAll();
  const dates = new Set();
  for (const r of records) dates.add(r.date);
  return Array.from(dates).sort().reverse();
}

async function getStrategyByDate(date) {
  const records = await _readAll();
  const dayRecords = records
    .filter((r) => r.date === date)
    .sort((a, b) => (b.score || 0) - (a.score || 0));

  if (dayRecords.length === 0) return null;

  // 统计
  const attack = dayRecords.filter((r) => (r.signal_type || '').includes('🔥')).length;
  const buy = dayRecords.filter((r) => (r.signal_type || '').includes('✅')).length;
  const ambush = dayRecords.filter((r) => (r.signal_type || '').includes('🕐')).length;
  const errors = dayRecords.filter((r) => r.error).length;
  const watch = dayRecords.length - attack - buy - ambush;

  // 五维打分平均值
  const avgScore = dayRecords.reduce((s, r) => s + (r.score || 0), 0) / dayRecords.length;

  // NX 分布
  const nxDist = { buy: 0, rising: 0, sell: 0 };
  for (const r of dayRecords) {
    if (nxDist[r.nx] !== undefined) nxDist[r.nx]++;
  }

  // 行业 / 方向分布
  const weeklyDirDist = {};
  const capitalDirDist = {};
  for (const r of dayRecords) {
    const wd = r.weekly_dir || '未知';
    const cd = r.capital_dir || '未知';
    weeklyDirDist[wd] = (weeklyDirDist[wd] || 0) + 1;
    capitalDirDist[cd] = (capitalDirDist[cd] || 0) + 1;
  }

  return {
    date,
    count: dayRecords.length,
    attack,
    buy,
    ambush,
    watch,
    errors,
    avgScore: Math.round(avgScore * 10) / 10,
    nxDist,
    weeklyDirDist,
    capitalDirDist,
    stocks: dayRecords,
  };
}

async function getLatestStrategy() {
  const dates = await getAvailableDates();
  if (dates.length === 0) return null;
  return getStrategyByDate(dates[0]);
}

// ── 内部 ──

async function _readAll() {
  try {
    const raw = await fs.readFile(STRATEGY_FILE, 'utf8');
    const records = [];
    for (const line of raw.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        records.push(JSON.parse(trimmed));
      } catch (_) {
        // skip malformed lines
      }
    }
    return records;
  } catch (error) {
    if (error && error.code === 'ENOENT') return [];
    throw error;
  }
}

module.exports = { getAvailableDates, getStrategyByDate, getLatestStrategy };
