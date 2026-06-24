'use strict';

const fs = require('fs/promises');
const path = require('path');

const SIGNALS_FILE = path.join(__dirname, '..', '..', 'data', 'upstream_signals.jsonl');
const LATEST_SIGNAL_LIMIT = 20;

function toTimestamp(value) {
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

function compareSignalsByTimestampDesc(a, b) {
  return b.timestampMs - a.timestampMs;
}

function sortByCountDescThenName(a, b, key) {
  if (b.count !== a.count) return b.count - a.count;
  return String(a[key] || '').localeCompare(String(b[key] || ''));
}

// getDashboardData({ limit }) => 取前 N 个活跃信号，默认 20
// 传 null/Infinity 取全部（供 getCycleradarCategories 使用）
async function getDashboardData(opts = {}) {
  const limit = opts.limit !== undefined ? opts.limit : LATEST_SIGNAL_LIMIT;
  let content;

  try {
    content = await fs.readFile(SIGNALS_FILE, 'utf8');
  } catch (error) {
    if (error && error.code === 'ENOENT') {
      return null;
    }
    throw error;
  }

  if (!content.trim()) {
    return null;
  }

  const latestBySignalId = new Map();

  for (const rawLine of content.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;

    let signal;
    try {
      signal = JSON.parse(line);
    } catch (_) {
      continue;
    }

    if (!signal || !signal.signal_id || !signal.timestamp) {
      continue;
    }

    const timestampMs = toTimestamp(signal.timestamp);
    if (timestampMs === null) {
      continue;
    }

    const existing = latestBySignalId.get(signal.signal_id);
    if (!existing || timestampMs >= existing.timestampMs) {
      latestBySignalId.set(signal.signal_id, { ...signal, timestampMs });
    }
  }

  if (latestBySignalId.size === 0) {
    return null;
  }

  const dedupedSignals = Array.from(latestBySignalId.values()).sort(compareSignalsByTimestampDesc);
  const nowIso = new Date().toISOString();
  const activeSignals = [];
  let expiredCount = 0;

  for (const signal of dedupedSignals) {
    if (!signal.expiry || signal.expiry < nowIso) {
      expiredCount += 1;
      continue;
    }
    activeSignals.push(signal);
  }

  const strategyStats = new Map();
  const assetTypeStats = new Map();
  const directionBreakdown = { long: 0, short: 0 };

  for (const signal of activeSignals) {
    const direction = signal.direction === 'short' ? 'short' : 'long';
    const strategy = signal.strategy || 'unknown';
    const assetType = signal.asset_type || 'unknown';
    const confidence = typeof signal.confidence === 'number' ? signal.confidence : Number(signal.confidence) || 0;

    directionBreakdown[direction] += 1;

    if (!strategyStats.has(strategy)) {
      strategyStats.set(strategy, {
        strategy,
        count: 0,
        direction: { long: 0, short: 0 },
        confidenceSum: 0,
      });
    }
    const strategyEntry = strategyStats.get(strategy);
    strategyEntry.count += 1;
    strategyEntry.direction[direction] += 1;
    strategyEntry.confidenceSum += confidence;

    if (!assetTypeStats.has(assetType)) {
      assetTypeStats.set(assetType, {
        assetType,
        count: 0,
        direction: { long: 0, short: 0 },
      });
    }
    const assetTypeEntry = assetTypeStats.get(assetType);
    assetTypeEntry.count += 1;
    assetTypeEntry.direction[direction] += 1;
  }

  const byStrategy = Array.from(strategyStats.values())
    .map((entry) => ({
      strategy: entry.strategy,
      count: entry.count,
      direction: entry.direction,
      avgConfidence: entry.count > 0 ? entry.confidenceSum / entry.count : 0,
    }))
    .sort((a, b) => sortByCountDescThenName(a, b, 'strategy'));

  const byAssetType = Array.from(assetTypeStats.values())
    .map((entry) => ({
      assetType: entry.assetType,
      count: entry.count,
      direction: entry.direction,
    }))
    .sort((a, b) => sortByCountDescThenName(a, b, 'assetType'));

  return {
    summary: {
      total: dedupedSignals.length,
      active: activeSignals.length,
      expired: expiredCount,
      longCount: directionBreakdown.long,
      shortCount: directionBreakdown.short,
      strategyCount: strategyStats.size,
      avgConfidence: activeSignals.length > 0
        ? activeSignals.reduce((sum, s) => sum + ((typeof s.confidence === 'number' ? s.confidence : Number(s.confidence) || 0)), 0) / activeSignals.length
        : 0,
      newestTime: dedupedSignals[0] ? dedupedSignals[0].timestamp : null,
      oldestTime: dedupedSignals[dedupedSignals.length - 1] ? dedupedSignals[dedupedSignals.length - 1].timestamp : null,
    },
    byStrategy,
    byAssetType,
    signals: activeSignals.slice(0, limit ?? undefined),
    directionBreakdown,
  };
}

// Group signals into V4.0 categories: alpha, ETF, 商品
// 热点事件 handled separately from WeWe RSS
// 策略→分类映射表（显式声明，新增策略必须在此注册，否则归入 unknown 并告警）
const STRATEGY_CATEGORY_MAP = {
  report_agent:    'alpha',    // V4.3: Pipeline A 事件驱动 LLM 推股 (daily.py → /m 主 alpha 源)
  stock_agent:     'alpha',
  ma_signals:      'alpha',
  scanner:         'alpha',
  rotation_factor: 'etf',
  commodity_radar: 'commodity',
  wanjun_models:   'alpha',    // V6.2: scanner 14 模型选股 (scanner.py → scanner_adapter)
};
const ASSET_TYPE_CATEGORY_MAP = {
  stock:     'alpha',
  sector:    'etf',
  commodity: 'commodity',
};

async function getCycleradarCategories() {
  const dashboardData = await getDashboardData({ limit: null }); // 分类时不截断
  if (!dashboardData) {
    return { alpha: [], etf: [], commodity: [], unknown: [] };
  }

  const signals = dashboardData.signals;
  const alpha = [], etf = [], commodity = [], unknown = [];

  for (const signal of signals) {
    const strategy  = signal.strategy  || '';
    const assetType = signal.asset_type || '';

    // 策略优先，asset_type 兜底
    const category =
      STRATEGY_CATEGORY_MAP[strategy] ||
      ASSET_TYPE_CATEGORY_MAP[assetType];

    if (category === 'alpha')     { alpha.push(signal); }
    else if (category === 'etf')       { etf.push(signal); }
    else if (category === 'commodity') { commodity.push(signal); }
    else {
      // 未知策略：显式归入 unknown 并打印告警，便于排查新策略类型
      console.warn(`[signals] unknown category: strategy="${strategy}" assetType="${assetType}" signal_id="${signal.signal_id || ''}"`);
      unknown.push(signal);
    }
  }

  return { alpha, etf, commodity, unknown };
}

module.exports = {
  getDashboardData,
  getCycleradarCategories,
  STRATEGY_CATEGORY_MAP,
};
