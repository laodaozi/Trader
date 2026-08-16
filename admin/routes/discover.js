'use strict';

/**
 * discover.js — 发现 feed API
 *
 * GET /api/discover
 *   返回今日新出现的信号、首次出现的股票、板块轮动变化。
 *   对比今天 vs 昨天的 strategy JSONL，找出"新发现"。
 */

const express = require('express');
const fs = require('fs');
const path = require('path');
const router = express.Router();

const DATA_DIR = path.resolve(__dirname, '../../data');

// ── 工具 ──

function readJSONL(filePath) {
  try {
    return fs.readFileSync(filePath, 'utf8')
      .split('\n').filter(Boolean)
      .map(l => { try { return JSON.parse(l); } catch (_) { return null; } })
      .filter(Boolean);
  } catch (_) { return []; }
}

// ── 端点 ──

router.get('/api/discover', (_req, res) => {
  const records = readJSONL(path.join(DATA_DIR, 'trader_strategy.jsonl'));

  // 按日期分组
  const byDate = {};
  records.forEach(r => {
    if (!r.date || r.error) return;
    if (!byDate[r.date]) byDate[r.date] = [];
    byDate[r.date].push(r);
  });

  const dates = Object.keys(byDate).sort().reverse();
  const today = dates[0] || '';
  const yesterday = dates[1] || '';

  const todayStocks = byDate[today] || [];
  const yesterdayCodes = new Set((byDate[yesterday] || []).map(s => s.code));

  // 1) 今日新出现的股票
  const newStocks = todayStocks
    .filter(s => !yesterdayCodes.has(s.code))
    .sort((a, b) => (b.score || 0) - (a.score || 0))
    .slice(0, 10)
    .map(s => ({ code: s.code, name: s.name, score: s.score, signal: s.signal_type, sector: s.weekly_dir || '' }));

  // 2) 信号变化（昨天有，今天信号类型或分数显著变化）
  const yesterdayMap = {};
  (byDate[yesterday] || []).forEach(s => { yesterdayMap[s.code] = s; });

  const signalChanges = todayStocks
    .filter(s => {
      const y = yesterdayMap[s.code];
      return y && (y.signal_type !== s.signal_type || Math.abs((y.score || 0) - (s.score || 0)) >= 10);
    })
    .sort((a, b) => (b.score || 0) - (a.score || 0))
    .slice(0, 10)
    .map(s => {
      const y = yesterdayMap[s.code];
      return {
        code: s.code, name: s.name,
        score: s.score, prevScore: y.score,
        signal: s.signal_type, prevSignal: y.signal_type,
        change: (s.score || 0) - (y.score || 0),
      };
    });

  // 3) 板块热度变化
  const todaySectors = {};
  const yesterdaySectors = {};
  todayStocks.forEach(s => { const d = s.weekly_dir || '未知'; todaySectors[d] = (todaySectors[d] || 0) + 1; });
  (byDate[yesterday] || []).forEach(s => { const d = s.weekly_dir || '未知'; yesterdaySectors[d] = (yesterdaySectors[d] || 0) + 1; });

  const sectorChanges = Object.entries(todaySectors)
    .map(([sector, count]) => ({
      sector,
      today: count,
      yesterday: yesterdaySectors[sector] || 0,
      delta: count - (yesterdaySectors[sector] || 0),
    }))
    .filter(s => s.delta !== 0)
    .sort((a, b) => b.delta - a.delta)
    .slice(0, 8);

  // 4) 今日高分信号一览
  const hotToday = todayStocks
    .sort((a, b) => (b.score || 0) - (a.score || 0))
    .slice(0, 6)
    .map(s => ({ code: s.code, name: s.name, score: s.score, signal: s.signal_type }));

  res.json({
    date: today,
    compare_date: yesterday,
    new_stocks: newStocks,
    signal_changes: signalChanges,
    sector_changes: sectorChanges,
    hot_today: hotToday,
    stats: {
      total_today: todayStocks.length,
      total_yesterday: (byDate[yesterday] || []).length,
      new_count: newStocks.length,
      changed_count: signalChanges.length,
    },
  });
});

module.exports = router;
