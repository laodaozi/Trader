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
  const rawDay = records
    .filter((r) => r.date === date)
    .sort((a, b) => (b.score || 0) - (a.score || 0));

  if (rawDay.length === 0) return null;

  // V8.0: 当日 code 去重（兜底，前端展示永不重复）
  const seenCodes = new Map();
  for (const r of rawDay) {
    if (!seenCodes.has(r.code) || (r.score || 0) > (seenCodes.get(r.code).score || 0)) {
      seenCodes.set(r.code, r);
    }
  }
  const dayRecords = Array.from(seenCodes.values()).sort((a, b) => (b.score || 0) - (a.score || 0));

  // P1-2: 按 score 分档（上游 signal_type 无区分度：43-63 分全标"买入"）
  //   >=60 进攻 | 50-59 买入 | 40-49 观察 | <40 回避
  function _tierByScore(sc) {
    sc = sc || 0;
    if (sc >= 60) return { key: 'attack', label: '🔥 进攻' };
    if (sc >= 50) return { key: 'buy', label: '✅ 买入' };
    if (sc >= 40) return { key: 'watch', label: '👀 观察' };
    return { key: 'avoid', label: '— 回避' };
  }
  for (const r of dayRecords) {
    const t = _tierByScore(r.score);
    r.tier = t.key;
    r.tier_label = t.label;
  }

  // 统计（基于 score 分档）
  const attack = dayRecords.filter((r) => r.tier === 'attack').length;
  const buy = dayRecords.filter((r) => r.tier === 'buy').length;
  const ambush = 0;
  const errors = dayRecords.filter((r) => r.error).length;
  const watch = dayRecords.filter((r) => r.tier === 'watch').length;
  const avoid = dayRecords.filter((r) => r.tier === 'avoid').length;

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
    avoid,
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
