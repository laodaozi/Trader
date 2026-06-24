'use strict';

const fs = require('fs/promises');
const path = require('path');

const TRACKER_FILE = path.join(__dirname, '..', '..', 'data', 'tracker_log.jsonl');

/**
 * 从 tracker_log.jsonl 解析跟踪记录，按 signal_date + horizon 分组统计。
 *
 * JSONL 字段（tracker_closer.py 写入）：
 *   code, name, signal_date, horizon(5|10|20),
 *   entry, stop, targets[],
 *   result(WIN|LOSE|HOLD|NODATA) — tracker_closer 写入
 *   兼容旧枚举: HIT=WIN, MISS=LOSE, NEUTRAL/PENDING=HOLD
 *   max_return, max_dd, final_return,
 *   hit_target(bool), hit_stop(bool),
 *   days_to_target, days_to_stop, n_bars,
 *   track_date, signal_type, strategy, score
 */

// 统一 result 枚举：tracker_closer 用 WIN/LOSE/HOLD，旧代码用 HIT/MISS/PENDING，全部归一
function _normalizeResult(r) {
  if (!r) return 'NODATA';
  if (r === 'WIN' || r === 'HIT') return 'HIT';
  if (r === 'LOSE' || r === 'MISS') return 'MISS';
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
    return { code: s.code, name: s.name, signal_type: s.signal_type, total, hit, miss, pending, nodata,
      hitRate: total > 0 ? Math.round((hit / total) * 100) + '%' : 'N/A' };
  });

  return {
    dates,
    latestDate,
    totalRecords: records.length,
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

module.exports = { getAvailableDates, getTrackerSummary, getTrackerByDateHorizon, getStockTrackingHistory };
