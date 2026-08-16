'use strict';

/**
 * brief.js — 每日简报 API（轻量，独立于 /m/api/summary）
 *
 * GET /api/brief
 *   面向外部消费（Bot/Widget/邮件摘要）的每日结构简报。
 *   相比 /m/api/summary 的全量仪表盘数据，brief 只提取当天的关键信号。
 */

const express = require('express');
const fs = require('fs');
const path = require('path');
const router = express.Router();

const ROOT = path.resolve(__dirname, '../..');
const DATA_DIR = path.join(ROOT, 'data');
const LOGS_DIR = path.join(DATA_DIR, 'logs');

// ── 工具 ──

function readJSON(filePath) {
  try { return JSON.parse(fs.readFileSync(filePath, 'utf8')); } catch (_) { return null; }
}

function ranToday(cronFile) {
  try {
    const st = fs.statSync(path.join(LOGS_DIR, cronFile));
    const m = new Date(st.mtime);
    const t = new Date();
    return m.getFullYear() === t.getFullYear() && m.getMonth() === t.getMonth() && m.getDate() === t.getDate();
  } catch (_) { return false; }
}

function fileAgeHours(filePath) {
  try { return (Date.now() - fs.statSync(filePath).mtimeMs) / 3600_000; } catch (_) { return null; }
}

// ── 端点 ──

router.get('/api/brief', (_req, res) => {
  const now = new Date();
  const today = now.toISOString().slice(0, 10);

  // 1) 市场体温
  let timing = { phase: '—', temperature: 0, date: today, advice: '' };
  const t = readJSON(path.join(DATA_DIR, 'timing_history.json'));
  if (t && t.history) {
    const valid = t.history.filter(h => h.temperature > 0);
    const last = valid.length > 0 ? valid[valid.length - 1] : t.history[t.history.length - 1];
    const ph = last.phase || '';
    const tmp = last.temperature || 0;
    let advice = '信号不明，轻仓观望';
    if (ph.includes('上涨') || ph.includes('进攻')) advice = '趋势向上，积极操作';
    else if (ph.includes('回调') && tmp > 60) advice = '回调中，控制仓位';
    else if (ph.includes('回调')) advice = '回调较深，观望为主';
    else if (ph.includes('震荡')) advice = '震荡市，高抛低吸';
    timing = { phase: last.phase || '—', temperature: Math.round(tmp), date: last.date || today, advice };
  }

  // 2) 今日信号摘要（JSONL 每行一只股票，按 date 字段聚合）
  let signals = { total: 0, attack: 0, buy: 0, ambush: 0, watch: 0, top: [] };
  try {
    const strategyRaw = fs.readFileSync(path.join(DATA_DIR, 'trader_strategy.jsonl'), 'utf8');
    const stockRecords = strategyRaw.split('\n').filter(Boolean).map(l => {
      try { return JSON.parse(l); } catch (_) { return null; }
    }).filter(Boolean);
    // 取最新日期
    const dates = [...new Set(stockRecords.map(r => r.date))].sort().reverse();
    if (dates.length > 0) {
      const latest = dates[0];
      const dayStocks = stockRecords
        .filter(r => r.date === latest && !r.error)
        .sort((a, b) => (b.score || 0) - (a.score || 0));
      signals.total = dayStocks.length;
      signals.attack = dayStocks.filter(r => (r.signal_type || '').includes('🔥')).length;
      signals.buy    = dayStocks.filter(r => (r.signal_type || '').includes('✅')).length;
      signals.ambush = dayStocks.filter(r => (r.signal_type || '').includes('🕐')).length;
      signals.watch  = signals.total - signals.attack - signals.buy - signals.ambush;
      signals.top    = dayStocks.slice(0, 5).map(s => ({
        code: s.code, name: s.name, score: s.score, signal: s.signal_type
      }));
    }
  } catch (_) {}

  // 3) 管线状态
  const pipeline = {
    scanner_signals: { label: 'Scanner 14模型', ran: ranToday('scanner_signals_cron.log') },
    ma_signals:      { label: '兼并重组信号', ran: ranToday('ma_signals_cron.log') },
    strategy_reflection: { label: 'LLM策略反思', ran: ranToday('strategy_reflection_cron.log') },
    stock_agent:     { label: 'Stock Agent', ran: ranToday('stock_agent_cron.log') },
  };
  const ranCount = Object.values(pipeline).filter(p => p.ran).length;

  // 4) 数据新鲜度
  const freshness = {
    strategy:  { label: '选股信号', age_h: fileAgeHours(path.join(DATA_DIR, 'trader_strategy.jsonl')) },
    tracker:   { label: '信号跟踪', age_h: fileAgeHours(path.join(DATA_DIR, 'trader_tracker.jsonl')) },
    timing:    { label: '市场体温', age_h: fileAgeHours(path.join(DATA_DIR, 'timing_history.json')) },
    positions: { label: '账户持仓', age_h: fileAgeHours(path.join(DATA_DIR, 'positions.json')) },
  };

  // 5) 今日叙事事件（取最近 3 天内的事件）
  let narrative = [];
  const en = readJSON(path.join(DATA_DIR, 'event_narrative_latest.json'));
  if (en && en.events) {
    const recentCutoff = new Date(now.getTime() - 3 * 86400_000).toISOString().slice(0, 10);
    narrative = en.events
      .filter(e => {
        const d = e.time_dimension || e.start_date || '';
        return d.slice(0, 10) >= recentCutoff;
      })
      .slice(0, 5)
      .map(e => ({
        rank: e.rank,
        date: (e.time_dimension || e.start_date || '').slice(0, 10),
        title: e.title || '',
        trigger: e.trigger_event || '',
        stock_count: (e.stock_mapping || []).length,
      }));
  }

  // 6) 跟踪胜率（全量已成交信号：HIT / HIT+MISS）
  let hitRate = null;
  try {
    const trackerRaw = fs.readFileSync(path.join(DATA_DIR, 'trader_tracker.jsonl'), 'utf8');
    const records = trackerRaw.split('\n').filter(Boolean).map(l => {
      try { return JSON.parse(l); } catch (_) { return null; }
    }).filter(Boolean);
    let hits = 0, settled = 0;
    records.forEach(r => {
      if (r.result === 'HIT') { hits++; settled++; }
      else if (r.result === 'MISS') { settled++; }
    });
    if (settled > 0) hitRate = Math.round((hits / settled) * 100);
  } catch (_) {}

  // 7) 版本
  let version = '9.1';
  try { version = fs.readFileSync(path.join(ROOT, 'VERSION'), 'utf8').trim(); } catch (_) {}

  res.json({
    date: today,
    server_time: now.toISOString(),
    version,
    timing,
    signals,
    pipeline: { ran: ranCount, total: 4, items: pipeline },
    freshness,
    narrative,
    hit_rate: hitRate,
  });
});

module.exports = router;
