'use strict';

const fs = require('fs/promises');
const path = require('path');

// V7.9: 改读 trader_tracker.jsonl（update_tracker_verdicts.py 每日更新，1656条）
// 旧的 tracker_log.jsonl（177条，tracker_closer.py 写，表已空）已弃用
const TRACKER_FILE = path.join(__dirname, '..', '..', 'data', 'trader_tracker.jsonl');

/**
 * 从 trader_tracker.jsonl 解析跟踪记录，按 signal_date + horizon 分组统计。
 *
 * JSONL 字段（update_tracker_verdicts.py 写入）：
 *   code, name, signal_date, horizon(5|10|20),
 *   entry, stop, targets[],
 *   result(HIT|MISS|EXPIRED|PENDING|NEUTRAL) — update_tracker_verdicts 写入
 *   max_return, max_dd, final_return,
 *   hit_target(bool), hit_stop(bool),
 *   days_to_target, days_to_stop, n_bars,
 *   track_date, signal_type, strategy, score
 */

// 统一 result 枚举
function _normalizeResult(r) {
  if (!r) return 'NODATA';
  if (r === 'WIN'  || r === 'HIT')  return 'HIT';
  if (r === 'LOSE' || r === 'MISS') return 'MISS';
  if (r === 'EXPIRED')              return 'EXPIRED';
  if (r === 'HOLD' || r === 'NEUTRAL' || r === 'PENDING') return 'PENDING';
  return 'NODATA';
}

async function getAvailableDates() {
  const records = await _readAll();
  return _uniqSorted(records.map((r) => r.signal_date));
}

async function getTrackerSummary() {
  const records = await _readAll();
  const dates = _uniqSorted(records.map((r) => r.signal_date));
  if (dates.length === 0) return null;

  const latestDate = dates[0];

  // 按 horizon 分组统计
  const byHorizon = {};
  for (const h of [5, 10, 20]) {
    const recs = records.filter((r) => r.signal_date === latestDate && r.horizon === h);
    const verdicts = {};
    for (const r of recs) {
      const v = _normalizeResult(r.result);
      verdicts[v] = (verdicts[v] || 0) + 1;
    }
    byHorizon[h] = {
      total: recs.length,
      verdicts,
      stocks: recs,
    };
  }

  // 全量统计（所有日期）
  const allByHorizon = {};
  for (const h of [5, 10, 20]) {
    const recs = records.filter((r) => r.horizon === h);
    const verdicts = {};
    let totalReturn = 0;
    let returnCount = 0;
    for (const r of recs) {
      const v = _normalizeResult(r.result);
      verdicts[v] = (verdicts[v] || 0) + 1;
      if (r.final_return != null) {
        totalReturn += r.final_return;
        returnCount++;
      }
    }
    allByHorizon[h] = {
      total: recs.length,
      verdicts,
      avgReturn: returnCount > 0 ? Math.round((totalReturn / returnCount) * 10000) / 100 + '%' : 'N/A',
    };
  }

  // 股票级汇总
  const byCode = {};
  for (const r of records) {
    if (!byCode[r.code]) {
      byCode[r.code] = { code: r.code, name: r.name, signal_type: r.signal_type, records: [] };
    }
    byCode[r.code].records.push(r);
  }
  const stockSummary = Object.values(byCode).map((s) => {
    const total = s.records.length;
    const hit = s.records.filter((r) => _normalizeResult(r.result) === 'HIT').length;
    const miss = s.records.filter((r) => _normalizeResult(r.result) === 'MISS').length;
    const pending = s.records.filter((r) => _normalizeResult(r.result) === 'PENDING').length;
    const nodata = s.records.filter((r) => _normalizeResult(r.result) === 'NODATA').length;
    const decided = hit + miss;  // P0-1: 只基于已裁决信号，pending/nodata 不入分母
    return { code: s.code, name: s.name, signal_type: s.signal_type, total, hit, miss, pending, nodata,
      hitRate: decided > 0 ? Math.round((hit / decided) * 100) + '%' : 'N/A' };
  });

  // ── 全量 overall 裁决统计（所有 horizon 汇总，用于首页 KPI 真实数据）──
  const overall = { HIT: 0, MISS: 0, EXPIRED: 0, PENDING: 0, NODATA: 0, total: 0,
    sumReturn: 0, returnCount: 0 };
  for (const r of records) {
    const v = _normalizeResult(r.result);
    overall[v] = (overall[v] || 0) + 1;
    overall.total++;
    if (r.final_return != null) { overall.sumReturn += r.final_return; overall.returnCount++; }
  }
  const adjudicated = overall.HIT + overall.MISS;
  const resolved   = adjudicated + overall.EXPIRED;
  overall.adjudicationRate = overall.total > 0
    ? Math.round(adjudicated / overall.total * 1000) / 10 : null;       // 已裁决占比
  overall.winRateOfAdjudicated = adjudicated > 0
    ? Math.round(overall.HIT / adjudicated * 100) : null;                // 裁决中胜率
  overall.avgReturn = overall.returnCount > 0
    ? Math.round(overall.sumReturn / overall.returnCount * 10000) / 100 + '%' : 'N/A';

  return {
    dates,
    latestDate,
    totalRecords: records.length,
    overall,
    byHorizon,
    allByHorizon,
    stockSummary,
    records: records.filter((r) => r.signal_date === latestDate),
  };
}

async function getTrackerByDateHorizon(date, horizon) {
  const records = await _readAll();
  return records
    .filter((r) => r.signal_date === date && r.horizon === horizon)
    .sort((a, b) => (b.score || 0) - (a.score || 0));
}

async function getStockTrackingHistory(code) {
  const records = await _readAll();
  return records
    .filter((r) => r.code === code)
    .sort((a, b) => {
      if (a.signal_date !== b.signal_date) return a.signal_date.localeCompare(b.signal_date);
      return a.horizon - b.horizon;
    });
}

// ── 内部 ──

async function _readAll() {
  try {
    const raw = await fs.readFile(TRACKER_FILE, 'utf8');
    const records = [];
    for (const line of raw.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        records.push(JSON.parse(trimmed));
      } catch (_) { /* skip */ }
    }
    return records;
  } catch (error) {
    if (error && error.code === 'ENOENT') return [];
    throw error;
  }
}

function _uniqSorted(arr) {
  return [...new Set(arr)].filter(Boolean).sort().reverse();
}

// ── 全局胜率（按策略类型分组，供 Admin tracker 页面诊断卡使用）──
async function globalWinRateByStrategy() {
  const records = await _readAll();
  const groups = {};
  for (const r of records) {
    const strat = r.strategy || '未知';
    if (!groups[strat]) groups[strat] = { win: 0, lose: 0, hold: 0, expired: 0, hitStop: 0,
      maxReturnSum: 0, maxDdSum: 0, finalReturnSum: 0, count: 0,
      total: 0, codes: new Set(), returns: [], barsSum: 0, barsCnt: 0 }; // 档1: 单一源扩展字段
    const g = groups[strat];
    g.total++;
    if (r.code) g.codes.add(r.code);
    if (typeof r.final_return === 'number') g.returns.push(r.final_return);
    if (typeof r.n_bars === 'number') { g.barsSum += r.n_bars; g.barsCnt++; }
    const v = _normalizeResult(r.result);
    if (v === 'HIT')  g.win++;
    else if (v === 'MISS') g.lose++;
    else if (v === 'EXPIRED') g.expired++;
    else g.hold++;
    if (r.hit_stop) g.hitStop++;
    if (r.max_return != null) { g.maxReturnSum += r.max_return; g.count++; }
    if (r.max_dd != null)     { g.maxDdSum += r.max_dd; g.ddCnt = (g.ddCnt||0) + 1; }
    if (r.final_return != null) { g.finalReturnSum += r.final_return; g.frCnt = (g.frCnt||0) + 1; }
  }
  return Object.entries(groups).map(([strat, g]) => {
    const closed = g.win + g.lose;
    const winRate = closed > 0 ? Math.round(g.win / closed * 100) : null;
    const hitStopRate = g.lose > 0 ? Math.round(g.hitStop / g.lose * 100) : null;
    const resolvedRate = (g.win + g.lose + g.expired) > 0
      ? Math.round((g.win + g.lose) / (g.win + g.lose + g.expired + g.hold) * 100) : null;
    return {
      strategy: strat,
      win: g.win, lose: g.lose, hold: g.hold, expired: g.expired, closed,
      winRate,
      hitStopRate,
      resolvedRate,
      avgMaxReturn:   g.count > 0 ? Math.round(g.maxReturnSum   / g.count * 1000) / 10 : null,
      avgMaxDd:       g.ddCnt > 0 ? Math.round(g.maxDdSum       / g.ddCnt * 1000) / 10 : null,
      avgFinalReturn: g.frCnt > 0 ? Math.round(g.finalReturnSum / g.frCnt * 1000) / 10 : null,
      // 档1: strategy-report/insights 消费字段 (单一源, 口径A)
      total: g.total,
      uniqueStocks: g.codes.size,
      avgReturn: g.returns.length ? g.returns.reduce((a,b)=>a+b,0)/g.returns.length : 0,
      bestReturn: g.returns.length ? Math.max(...g.returns) : 0,
      worstReturn: g.returns.length ? Math.min(...g.returns) : 0,
      avgHoldingDays: g.barsCnt > 0 ? Math.round(g.barsSum / g.barsCnt * 10) / 10 : 0,
      stars: (() => { const wr = closed>0 ? g.win/closed*100 : 0; const ar = g.returns.length ? g.returns.reduce((a,b)=>a+b,0)/g.returns.length : 0; return Math.min(5, Math.max(1, Math.round((wr/20) + (ar*100+5)/3))); })(),
    };
  }).sort((a, b) => (b.closed - a.closed));
}

module.exports = { getAvailableDates, getTrackerSummary, getTrackerByDateHorizon, getStockTrackingHistory, globalWinRateByStrategy };
