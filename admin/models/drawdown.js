'use strict';

const fs = require('fs/promises');
const http = require('http');
const path = require('path');
const watchlist = require('./watchlist');

const SIGNALS_PATH = path.join(__dirname, '..', '..', 'data', 'upstream_signals.jsonl');

// ── Sina 行情 ──
function _sinaUrl(code) {
  const prefix = /^[056]/.test(code) ? 'sz' : 'sh';
  return `http://hq.sinajs.cn/list=${prefix}${code}`;
}

function _fetchOne(code) {
  return new Promise((resolve) => {
    const req = http.get(_sinaUrl(code), { headers: { Referer: 'https://finance.sina.com.cn' } }, (res) => {
      let data = '';
      res.on('data', (c) => (data += c));
      res.on('end', () => {
        const m = data.match(/"([^"]+)"/);
        if (!m) return resolve(null);
        const f = m[1].split(',');
        if (f.length < 4 || !f[3] || f[3] === '0.000') return resolve(null);
        resolve({
          name: f[0],
          price: parseFloat(f[3]),
          prevClose: parseFloat(f[2]) || 0,
          changePct: parseFloat(f[2]) ? ((parseFloat(f[3]) - parseFloat(f[2])) / parseFloat(f[2]) * 100).toFixed(2) : null,
        });
      });
    });
    req.on('error', () => resolve(null));
    req.setTimeout(5000, () => { req.destroy(); resolve(null); });
  });
}

async function fetchPrices(codes) {
  const results = {};
  const chunks = [];
  const BATCH = 8;
  for (let i = 0; i < codes.length; i += BATCH) {
    chunks.push(codes.slice(i, i + BATCH));
  }
  for (const batch of chunks) {
    const batchResults = await Promise.all(batch.map((c) => _fetchOne(c)));
    for (let j = 0; j < batch.length; j++) {
      if (batchResults[j]) results[batch[j]] = batchResults[j];
    }
  }
  return results;
}

// ── 自动池：从 signals 提取有 entry_price 的信号 ──
async function getAutoPool() {
  let lines = [];
  try {
    const raw = await fs.readFile(SIGNALS_PATH, 'utf8');
    lines = raw.trim().split('\n').filter(Boolean);
  } catch (_) { return []; }

  const signals = [];
  for (const line of lines) {
    try {
      const s = JSON.parse(line);
      const m = s.metadata;
      if (!m || !m.entry_price) continue;
      const code = s.asset;
      if (!code || !/^\d{6}$/.test(code)) continue;
      signals.push({
        code,
        name: m.stock_name || code,
        entryPrice: m.entry_price,
        targetPrice: m.target_price || null,
        stopLoss: m.stop_loss || null,
        strategy: s.strategy,
        signalDate: (s.signal_id || '').split('-').slice(1, 4).join('-') || s.signal_id,
        thesis: m.thesis || '',
      });
    } catch (_) { /* skip */ }
  }

  // 同 code 保留最新（最后一条）
  const deduped = [];
  const seen = new Set();
  for (let i = signals.length - 1; i >= 0; i--) {
    if (!seen.has(signals[i].code)) {
      seen.add(signals[i].code);
      deduped.unshift(signals[i]);
    }
  }
  return deduped;
}

// ── 自选池：从 watchlist 取，并尝试匹配信号 entry_price ──
async function getWatchlistPool() {
  const stocks = await watchlist.getAll();
  if (!stocks.length) return [];

  // 先取所有信号
  let signalMap = {};
  try {
    const raw = await fs.readFile(SIGNALS_PATH, 'utf8');
    const lines = raw.trim().split('\n').filter(Boolean);
    for (const line of lines) {
      try {
        const s = JSON.parse(line);
        const m = s.metadata;
        if (!m || !m.entry_price) continue;
        if (s.asset && !signalMap[s.asset]) {
          signalMap[s.asset] = {
            entryPrice: m.entry_price,
            strategy: s.strategy,
            signalDate: (s.signal_id || '').split('-').slice(1, 4).join('-'),
          };
        }
      } catch (_) { /* skip */ }
    }
  } catch (_) { /* skip */ }

  return stocks.map((st) => ({
    code: st.code,
    name: st.name || st.code,
    entryPrice: signalMap[st.code] ? signalMap[st.code].entryPrice : null,
    strategy: signalMap[st.code] ? signalMap[st.code].strategy : null,
    signalDate: signalMap[st.code] ? signalMap[st.code].signalDate : null,
    targetPrice: null,
    stopLoss: null,
    source: 'watchlist',
  }));
}

// ── 合并计算回撤 ──
async function buildDrawdownReport() {
  const [auto, wl] = await Promise.all([getAutoPool(), getWatchlistPool()]);

  // 去重：wl 里和 auto 重复的，标 from: both
  const autoCodes = new Set(auto.map((a) => a.code));
  const wlOnly = [];
  const both = [];
  for (const w of wl) {
    if (autoCodes.has(w.code)) {
      both.push({ ...w, source: 'both' });
    } else {
      wlOnly.push(w);
    }
  }

  // 合并需要拉价的 code 集合
  const allStocks = [...auto, ...wlOnly];
  const codes = [...new Set(allStocks.map((s) => s.code))];

  const prices = await fetchPrices(codes);

  // 计算回撤
  const now = new Date();
  function calcDrawdown(stock, price) {
    if (!price || !stock.entryPrice) return { ...stock, ...price, drawdownPct: null, daysHeld: null, status: 'no_data' };
    const dd = ((price.price - stock.entryPrice) / stock.entryPrice * 100).toFixed(2);
    const daysHeld = stock.signalDate
      ? Math.floor((now - new Date(stock.signalDate)) / 86400000)
      : null;
    let status = 'no_signal';
    if (stock.entryPrice) {
      status = parseFloat(dd) >= 0 ? 'profit' : 'loss';
    }
    return { ...stock, ...price, drawdownPct: parseFloat(dd), daysHeld, status };
  }

  const autoRows = auto.map((s) => calcDrawdown(s, prices[s.code]));
  const wlRows = wlOnly.map((s) => calcDrawdown(s, prices[s.code]));
  const bothRows = both.map((s) => calcDrawdown(s, prices[s.code]));

  // 摘要
  const allRows = [...autoRows, ...wlRows, ...bothRows];
  const withDrawdown = allRows.filter((r) => r.drawdownPct !== null);
  const profitCount = withDrawdown.filter((r) => r.drawdownPct >= 0).length;
  const lossCount = withDrawdown.filter((r) => r.drawdownPct < 0).length;
  const avgDrawdown = withDrawdown.length
    ? (withDrawdown.reduce((s, r) => s + r.drawdownPct, 0) / withDrawdown.length).toFixed(2)
    : null;
  const maxDD = withDrawdown.length
    ? Math.min(...withDrawdown.map((r) => r.drawdownPct)).toFixed(2)
    : null;
  const maxGain = withDrawdown.length
    ? Math.max(...withDrawdown.map((r) => r.drawdownPct)).toFixed(2)
    : null;

  return {
    summary: {
      totalAuto: auto.length,
      totalWatchlist: wlOnly.length + both.length,
      withDrawdown: withDrawdown.length,
      profitCount,
      lossCount,
      avgDrawdown,
      maxDD,
      maxGain,
      pricedCount: Object.keys(prices).length,
      requestedCount: codes.length,
    },
    auto: autoRows,
    watchlist: wlRows,
    both: bothRows,
    fetchedAt: now.toISOString(),
  };
}

module.exports = { buildDrawdownReport, fetchPrices };
