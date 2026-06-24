/**
 * reflection.js — 策略反思数据聚合模型
 * 职责: 读 tracker/strategy/scanner JSONL → 出结构化数据 + A1 胜率矩阵 + 信号来源分布
 */
const fs = require('fs').promises;
const fsSync = require('fs');
const path = require('path');

const DATA_DIR = path.join(__dirname, '..', '..', 'data');

// 带容错读 JSONL（不抛异常，缺文件返回 []）
async function readJSONL(filename) {
  try {
    const raw = await fs.readFile(path.join(DATA_DIR, filename), 'utf8');
    return raw.trim().split('\n').filter(Boolean).map(line => {
      try { return JSON.parse(line); } catch (_) { return null; }
    }).filter(Boolean);
  } catch (_) {
    return [];
  }
}

/**
 * A1 胜率矩阵：按 signal_type×strategy 分组统计
 */
function buildA1Matrix(trackerLog) {
  const groups = {};

  for (const row of trackerLog) {
    const key = `${row.signal_type || '未知'}`;
    if (!groups[key]) groups[key] = {
      signal_type: row.signal_type || '未知',
      total: 0,
      hit: 0,
      avg_return: 0,
      max_return: -Infinity,
      max_dd: 0,
    };

    const g = groups[key];
    g.total++;

    const hr = parseFloat(row.final_return) || 0;
    const mr = parseFloat(row.max_return) || 0;
    const dd = parseFloat(row.max_dd) || 0;
    const hit = row.hit_target === true || row.hit_target === 'true';

    g.avg_return += hr;
    if (hit) g.hit++;
    if (mr > g.max_return) g.max_return = mr;
    if (dd < g.max_dd) g.max_dd = dd;  // max_dd is negative
  }

  // 计算均值、命中率
  for (const key of Object.keys(groups)) {
    const g = groups[key];
    g.avg_return = g.total > 0 ? (g.avg_return / g.total) : 0;
    g.hit_rate = g.total > 0 ? (g.hit / g.total) : 0;
    g.max_return = g.max_return === -Infinity ? 0 : g.max_return;
  }

  // 按命中率排序
  return Object.values(groups).sort((a, b) => b.hit_rate - a.hit_rate);
}

/**
 * 按策略分组统计
 */
function buildStrategyStats(trackerLog) {
  const groups = {};
  for (const row of trackerLog) {
    const strat = row.strategy || '未知';
    if (!groups[strat]) groups[strat] = { strategy: strat, total: 0, hit: 0, avg_return: 0 };
    const g = groups[strat];
    g.total++;
    const hr = parseFloat(row.final_return) || 0;
    if (row.hit_target === true || row.hit_target === 'true') g.hit++;
    g.avg_return += hr;
  }
  for (const k of Object.keys(groups)) {
    const g = groups[k];
    g.hit_rate = g.total > 0 ? (g.hit / g.total) : 0;
    g.avg_return = g.total > 0 ? (g.avg_return / g.total) : 0;
  }
  return Object.values(groups).sort((a, b) => b.total - a.total);
}

/**
 * scanner 模型统计: 每个模型触发了几次信号
 */
function buildScannerModelStats(scannerLog) {
  const modelCount = {};
  const modelStocks = {};
  for (const row of scannerLog) {
    const m = row.model || '未知';
    modelCount[m] = (modelCount[m] || 0) + 1;
    if (!modelStocks[m]) modelStocks[m] = new Set();
    if (row.code) modelStocks[m].add(row.code);
  }
  return Object.entries(modelCount)
    .sort((a, b) => b[1] - a[1])
    .map(([model, count]) => ({
      model,
      count,
      uniqueStocks: modelStocks[model] ? modelStocks[model].size : 0,
    }));
}

/**
 * 信号来源分布：读 upstream_signals.jsonl，统计每个策略的活跃/历史信号数
 * 帮助识别"哪些策略在跑、哪些失效了"
 */
function buildSignalSourceStats(upstreamSignals) {
  const now = new Date().toISOString();

  // 按 strategy 分组，取最新一条（去重 signal_id）
  const latestBySignalId = new Map();
  for (const sig of upstreamSignals) {
    const sid = sig.signal_id;
    if (!sid) continue;
    const existing = latestBySignalId.get(sid);
    if (!existing || (sig.timestamp || '') >= (existing.timestamp || '')) {
      latestBySignalId.set(sid, sig);
    }
  }

  const deduped = Array.from(latestBySignalId.values());
  const stratMap = {};

  for (const sig of deduped) {
    const strat = sig.strategy || 'unknown';
    if (!stratMap[strat]) stratMap[strat] = {
      strategy: strat,
      total: 0,
      active: 0,
      expired: 0,
      last_signal: null,
    };
    const g = stratMap[strat];
    g.total++;
    const expiry = sig.expiry || '';
    if (expiry > now) {
      g.active++;
    } else {
      g.expired++;
    }
    const ts = sig.timestamp || '';
    if (!g.last_signal || ts > g.last_signal) g.last_signal = ts ? ts.slice(0, 10) : null;
  }

  // 已知策略列表（保证所有策略都出现，即使信号为 0）
  const KNOWN_STRATEGIES = [
    { key: 'report_agent',    label: '事件驱动 (report_agent)' },
    { key: 'stock_agent',     label: '量化选股 (stock_agent)' },
    { key: 'ma_signals',      label: '兼并重组 (ma_signals)' },
    { key: 'wanjun_models',   label: '万军形态 (wanjun_models)' },
    { key: 'rotation_factor', label: 'ETF 轮动 (rotation_factor)' },
    { key: 'commodity_radar', label: '商品雷达 (commodity_radar)' },
  ];

  return KNOWN_STRATEGIES.map(({ key, label }) => {
    const g = stratMap[key] || { strategy: key, total: 0, active: 0, expired: 0, last_signal: null };
    return {
      strategy: key,
      label,
      total: g.total,
      active: g.active,
      expired: g.expired,
      last_signal: g.last_signal || '从未运行',
      // 健康度：有活跃信号=green，有历史无活跃=yellow，从未运行=red
      health: g.active > 0 ? 'green' : g.total > 0 ? 'yellow' : 'red',
    };
  });
}


async function getReflectionData() {
  const [trackerLog, strategyLog, scannerLog, rotationSnapshots, upstreamSignals] = await Promise.all([
    readJSONL('tracker_log.jsonl'),
    readJSONL('trader_strategy.jsonl'),
    readJSONL('scanner_log.jsonl'),
    readJSONL('rotation_snapshots.jsonl'),
    readJSONL('upstream_signals.jsonl'),
  ]);

  // 最新日期
  const latestReflection = trackerLog.length > 0
    ? trackerLog[trackerLog.length - 1].track_date || trackerLog[trackerLog.length - 1].signal_date || '未知'
    : '无';
  const latestStrategy = strategyLog.length > 0
    ? strategyLog[strategyLog.length - 1].date || '未知'
    : '无';
  const latestScanner = scannerLog.length > 0
    ? scannerLog[scannerLog.length - 1].date || '无'
    : '无';

  return {
    stats: {
      totalReflections: trackerLog.length,
      totalStrategies: strategyLog.length,
      totalScannerRuns: scannerLog.length,
      totalRotationSnapshots: rotationSnapshots.length,
      latestReflection,
      latestStrategy,
      latestScanner,
    },
    // 原始数据（前端渲染）
    reflections: trackerLog.slice(-30).reverse(),
    strategies: strategyLog.slice(-10).reverse(),
    scannerEntries: scannerLog.slice(-10).reverse(),
    rotationSnapshots: rotationSnapshots.slice(-5).reverse(),
    // A1 胜率矩阵
    a1Matrix: buildA1Matrix(trackerLog),
    strategyStats: buildStrategyStats(trackerLog),
    scannerModelStats: buildScannerModelStats(scannerLog),
    // 信号来源健康度（新增）
    signalSourceStats: buildSignalSourceStats(upstreamSignals),
    error: null,
  };
}

module.exports = { getReflectionData };
