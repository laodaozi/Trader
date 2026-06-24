'use strict';

const express = require('express');
const os = require('os');
const fs = require('fs/promises');
const fsSync = require('fs');  // V4.1.2: sync ops for enrichment cache
const path = require('path');
const router = express.Router();
const haoyunge = require('../models/haoyunge');  // V5.3: 好运哥策略模块

const strategyModel = require('../models/trader-strategy');
const trackerModel = require('../models/trader-tracker');
const signalsModel = require('../models/signals');

const TIMING_PATH = path.join(__dirname, '..', '..', 'data', 'timing_history.json');
// V4.0.1: 对齐 core/daily.py XDG 标准，告别越级相对路径
// V6.0: 多路径 fallback（Mac dev ~/交易员/，ECS prod project-relative）
let _positionsPathCache = null;
function _getPositionsPath() {
  if (_positionsPathCache) return _positionsPathCache;
  const candidates = [
    path.join(os.homedir(), '交易员', 'data', 'positions.json'),           // Mac dev
    path.join(__dirname, '..', '..', 'data', 'positions.json'),            // ECS prod
    '/opt/cycleradar-trader/data/positions.json',                          // ECS absolute
  ];
  for (const c of candidates) {
    if (fsSync.existsSync(c)) { _positionsPathCache = c; return c; }
  }
  _positionsPathCache = candidates[1]; // fallback: project-relative
  return _positionsPathCache;
}
const POSITIONS_PATH = void 0; // replaced by _getPositionsPath()
const BACKTEST_DIR = path.join(__dirname, '..', '..', 'data', 'backtest_reports');
// V6.5: 优先读 wewe-rss 真实运行目录（9个信源），旧副本作 fallback
const WEWE_DB_PATH = fsSync.existsSync('/opt/wewe-rss-deploy/data/wewe-rss.db')
  ? '/opt/wewe-rss-deploy/data/wewe-rss.db'
  : path.join(__dirname, '..', 'data', 'wewe-rss.db');
const HOTEVENTS_CACHE_PATH = path.join(__dirname, '..', '..', 'data', 'hotevents_cache.json');
const ENRICHMENT_CACHE_PATH = path.join(__dirname, '..', '..', 'data', 'hot_enrichment.json');

// V6.5: ETF 代码→中英文名称映射（rotation_factor 策略不携带 stock_name，此处补全）
const ETF_NAME_MAP = {
  '159662': '交运ETF · Transportation ETF',
  '159715': '稀土ETF · Rare Earth ETF',
  '159837': '生物科技ETF · Biotech ETF',
  '159840': '锂电池ETF · Lithium Battery ETF',
  '159870': '化工ETF · Chemical Industry ETF',
  '159886': '机械ETF · Machinery ETF',
  '159997': '电子ETF · Electronics ETF',
  '512010': '医药ETF · Healthcare ETF',
  '512480': '半导体ETF · Semiconductor ETF',
  '512880': '证券ETF · Securities ETF',
  '515220': '煤炭ETF · Coal ETF',
  '515880': '通信ETF · Communication ETF',
};

// ── V5.0: 契约文件路径解析（3 文件桥）──
// ECS 生产环境：/opt/trader/output/contracts/（cron git pull 同步）
// Mac 开发环境：~/交易员/data/（daily.py 直接产出）
let _contractsPathCache = null;
function _getContractsPath() {
  if (_contractsPathCache) return _contractsPathCache;
  const candidates = [
    '/opt/trader/output/contracts',                          // ECS prod
    path.join(os.homedir(), '交易员', 'data'),               // Mac dev
  ];
  for (const c of candidates) {
    try {
      if (fsSync.existsSync(path.join(c, 'alpha_latest.json'))) {
        _contractsPathCache = c;
        return c;
      }
    } catch (_) {}
  }
  // fallback: 返回 ECS 路径（部署后 cron 自动填充）
  _contractsPathCache = '/opt/trader/output/contracts';
  return _contractsPathCache;
}

function _readAlphaLatest() {
  try {
    const raw = fsSync.readFileSync(path.join(_getContractsPath(), 'alpha_latest.json'), 'utf8');
    return JSON.parse(raw);
  } catch { return null; }
}

function _readEventNarrative() {
  try {
    const raw = fsSync.readFileSync(path.join(_getContractsPath(), 'event_narrative_latest.json'), 'utf8');
    return JSON.parse(raw);
  } catch { return null; }
}

// V5.1: consumer 端字段校验 —— producer 改字段名/新增字段时告警，避免静默丢数据
function _validateEventNarrativeFields(en) {
  if (!en) return { ok: false, warnings: ['file unreadable'] };
  const warnings = [];

  // top-level 必填字段
  ['generated_at','global_conclusion','events'].forEach(f => {
    if (!(f in en)) warnings.push(`missing top-level: ${f}`);
  });

  if (en.events && en.events.length > 0) {
    const e0 = en.events[0];
    const producerKeys = Object.keys(e0);

    // 生产端字段 ≠ consumer 期望字段（兼容映射后的心理模型：title/thesis/sectors/tickers/date/source）
    const expectedProducerFields = ['rank','title','source','event_time','interpretation','sector_impact','stock_impact','commodity_impact'];
    // note: 'date' is top-level in the file, not inside each event
    const missing = expectedProducerFields.filter(f => !producerKeys.includes(f));
    if (missing.length > 0)
      warnings.push(`events[0] missing producer fields: ${missing.join(', ')} (field renamed upstream?)`);

    // 生产端有新字段 consumer 未映射
    const unknown = producerKeys.filter(k => !expectedProducerFields.includes(k));
    if (unknown.length > 0)
      warnings.push(`events[0] unknown fields: ${unknown.join(', ')} (producer added, consumer not mapping)`);
  }

  const ok = warnings.length === 0;
  if (!ok) console.warn('[contracts] event_narrative_latest.json 字段校验 FAIL:', warnings.join('; '));
  return { ok, warnings };
}

// ── 热点事件（WeWe RSS：最新10条，48h内） ──
//   V4.1.1: 添加缓存降级，wewe-rss 失效时返回最近一次成功的缓存
async function _getHotEvents() {
  let events = [];
  let fromCache = false;

  // 尝试从 wewe-rss.db 获取
  try {
    await fs.access(WEWE_DB_PATH);
    events = await _queryHotEventsFromDB();
  } catch {
    // DB 文件不在，走缓存
  }

  // 降级：DB 无数据时读缓存
  if (events.length === 0) {
    try {
      const raw = await fs.readFile(HOTEVENTS_CACHE_PATH, 'utf8');
      const cached = JSON.parse(raw);
      if (cached && cached.events && cached.events.length > 0) {
        events = cached.events;
        fromCache = true;
        console.warn(`[_getHotEvents] wewe-rss 无数据，使用缓存 (${cached.cached_at})`);
      }
    } catch { /* 缓存也不可用 */ }
  } else {
    // DB 有数据 → 更新缓存
    try {
      await fs.writeFile(HOTEVENTS_CACHE_PATH, JSON.stringify({
        events,
        cached_at: new Date().toISOString(),
      }), 'utf8');
    } catch { /* 写缓存失败不阻塞 */ }
  }

  // 附加 stale 标记，供前端区分实时/缓存
  if (fromCache && events.length > 0) {
    events = events.map(e => ({ ...e, _stale: true }));
  }
  return events;
}

// ── 热点事件 · 信号源分级（2026-06-09）──
const HOT_FEED_TIER_S = new Set([
  '叙事平权old',   // 炒股群围观 × 叙事挖掘
  '微策神机',      // 市场宏观解读，有观点
  '财闻私享',      // 周末资讯 + 周度展望
  '财经早餐',      // 核心叙事大号（待 wewe-rss 订阅）
]);
const HOT_FEED_TIER_A = new Map([
  ['数据宝', 3],    // 证券时报数据平台，量大但偏数据搬运，限流 ≤3
  // 其他未列名账号走默认通道（不限量，按时间排序），包括:
  //   台球之门（中短线波段识别）  小马白话期权（商品机会感知）
  //   在下杜牛牛（市场情绪）      低吸波段王（交易节奏）
]);
// ── V4.3: sql.js (WASM, 零原生依赖) 直连 SQLite ──
// better-sqlite3 无法在 ECS CentOS 8 编译（GLIBC 2.29 缺失），
// sql.js 通过 WebAssembly 实现，无需原生编译。

let __SQL = null;

async function _getSQL() {
  if (!__SQL) {
    const initSqlJs = require('sql.js');
    __SQL = await initSqlJs();
  }
  return __SQL;
}

function _rowsFromExec(db, sql) {
  const results = db.exec(sql);
  if (!results.length) return [];
  const { columns, values } = results[0];
  return values.map(vals => {
    const obj = {};
    columns.forEach((col, i) => { obj[col] = vals[i]; });
    return obj;
  });
}

async function _queryHotEventsFromDB() {
  try {
    const SQL = await _getSQL();
    const fs = require('fs');
    const buf = fs.readFileSync(WEWE_DB_PATH);
    const db = new SQL.Database(buf);
    const since = Math.floor(Date.now() / 1000) - 172800; // 48h 窗口
    const rows = _rowsFromExec(db,
      `SELECT title, publish_time, COALESCE(f.mp_name, a.mp_id) AS source, a.pic_url, a.content
       FROM articles a LEFT JOIN feeds f ON a.mp_id = f.id
       WHERE a.publish_time >= ${since}
       ORDER BY a.publish_time DESC`);
    db.close();

    // 分级过滤：S级全收 → A级限流 → 其他正常 → 合并取 TOP 10
    const sTier = [], aTier = [], others = [];
    const aCount = {};
    for (const row of rows) {
      const src = row.source;
      if (HOT_FEED_TIER_S.has(src)) {
        sTier.push(row);
      } else if (HOT_FEED_TIER_A.has(src)) {
        const limit = HOT_FEED_TIER_A.get(src);
        aCount[src] = (aCount[src] || 0);
        if (aCount[src] < limit) {
          aTier.push(row);
          aCount[src]++;
        }
      } else {
        others.push(row);
      }
    }
    return [...sTier, ...aTier, ...others]
      .sort((a, b) => b.publish_time - a.publish_time)
      .slice(0, 10)
      .map(row => ({
        title: row.title || '',
        time: new Date(row.publish_time * 1000).toISOString(),
        source: row.source || '',
        pic_url: row.pic_url || '',
        content: row.content || '',
      }));
  } catch (e) {
    console.warn('[_queryHotEventsFromDB] sql.js error:', e.message);
    return [];
  }
}

// ── V4.1.2: LLM 增强热点事件（thesis + tickers）──
// 从 hot_enrichment.json 读取 Claude 生成的 AI 观点，对照标题 hash 匹配
// 缓存文件由 enrich_hot_events.py 独立生成，与 API 服务解耦
// 读取用 fs + 内存缓存（5min TTL），避免 require 永久缓存

const crypto = require('crypto');

function _hashTitle(title) {
  return crypto.createHash('md5').update(title).digest('hex').slice(0, 12);
}

let _enrichCache = null;
let _enrichCacheAt = 0;

function _enrichHotEvents(events) {
  const now = Date.now();
  // 5 分钟 TTL：enrich 脚本跑完后下次请求自动拉新
  if (!_enrichCache || (now - _enrichCacheAt) > 300000) {
    try {
      const raw = fsSync.readFileSync(ENRICHMENT_CACHE_PATH, 'utf8');
      _enrichCache = JSON.parse(raw);
      _enrichCacheAt = now;
    } catch {
      _enrichCache = null;
    }
  }
  if (!_enrichCache) return events;

  return events.map(e => {
    const h = _hashTitle(e.title);
    if (_enrichCache[h]) {
      return {
        ...e,
        thesis: _enrichCache[h].thesis || '',
        tickers: _enrichCache[h].tickers || [],
      };
    }
    return { ...e, thesis: '', tickers: [] };
  });
}

// ── V4.2 RSS: 数据新鲜度检测（L3 降级层） ──
// 查询 articles 表最新时间，返回 freshnessHours / freshnessStatus / lastArticleTime
// 前端据此渲染时效指示器（绿/黄/红），enrich cron 据此决定是否跳过 LLM
async function _getRssHealth() {
  try {
    const SQL = await _getSQL();
    const fs = require('fs');
    const buf = fs.readFileSync(WEWE_DB_PATH);
    const db = new SQL.Database(buf);
    const results = db.exec('SELECT COALESCE(MAX(publish_time), 0) AS ts FROM articles');
    db.close();
    const ts = (results.length && results[0].values.length) ? results[0].values[0][0] : 0;
    if (ts === 0) {
      return { freshnessHours: null, freshnessStatus: 'empty', lastArticleTime: null, note: 'no articles in DB' };
    }
    const now = Math.floor(Date.now() / 1000);
    const ageHours = Math.round((now - ts) / 3600 * 10) / 10;
    let status = 'fresh';
    if (ageHours >= 24) status = 'stale';
    else if (ageHours >= 6) status = 'degraded';
    return {
      freshnessHours: ageHours,
      freshnessStatus: status,
      lastArticleTime: new Date(ts * 1000).toISOString(),
    };
  } catch (e) {
    return { freshnessHours: null, freshnessStatus: 'unknown', lastArticleTime: null, note: 'DB unreadable: ' + e.message };
  }
}

// ── /compare ── 三图对比页 ──
router.get('/compare', (req, res) => {
  res.render('comparison');
});

// ── /m ── V6 三 tab 仪表盘（2026-06-19 切换，原 /m/v6）
router.get('/m', (req, res) => {
  res.render('dashboard', { title: '交易仪表盘 V6', appVersion: 'V6.4' });
});

// ── /m/v6 ── 保留30天兼容重定向，之后删除
router.get('/m/v6', (req, res) => {
  res.redirect(301, '/m');
});
// ── /m/api/summary ── JSON API ──
router.get('/m/api/summary', async (req, res) => {
  try {
    const [latestStrategy, trackerSummary] = await Promise.all([
      strategyModel.getLatestStrategy(),
      trackerModel.getTrackerSummary(),
    ]);

    // --- timing ---
    let timing = null;
    try { const raw = await fs.readFile(TIMING_PATH, 'utf8'); timing = JSON.parse(raw); } catch (_) {}

    let timingOut = null;
    if (timing && timing.history && timing.history.length > 0) {
      // V6: filter out entries with temperature === 0 (data still computing)
      const validHistory = timing.history.filter(h => h.temperature > 0);
      const lastT = validHistory.length > 0 ? validHistory[validHistory.length - 1] : timing.history[timing.history.length - 1];
      const dataNote = validHistory.length === 0 || timing.history[timing.history.length - 1].temperature === 0
        ? '今日数据计算中' : null;
      let advice = '';
      const ph = lastT.phase || '';
      const tmp = lastT.temperature || 0;
      if (ph.includes('上涨') || ph.includes('进攻')) advice = '趋势向上，积极操作';
      else if (ph.includes('回调') && tmp > 60) advice = '回调中，控制仓位';
      else if (ph.includes('回调')) advice = '回调较深，观望为主';
      else if (ph.includes('震荡')) advice = '震荡市，高抛低吸';
      else advice = '信号不明，轻仓观望';

      let accountData = null;
      try { const raw = await fs.readFile(_getPositionsPath(), 'utf8'); accountData = JSON.parse(raw); } catch (_) {}

      timingOut = {
        phase: lastT.phase || '—',
        temperature: Math.round(tmp),
        indexDirection: lastT.index_direction || '',
        positionRatio: (accountData && accountData.meta) ? accountData.meta.position_ratio : 0,
        advice,
        date: lastT.date,
        dataNote: dataNote,
      };
    }

    // --- account ---
    let account = null;
    try {
      const raw = await fs.readFile(_getPositionsPath(), 'utf8');
      const posData = JSON.parse(raw);
      const meta = posData.meta || {};
      const holdings = posData.holdings || [];
      const totalCost = holdings.reduce((s, h) => s + (h.cost || 0), 0);
      const totalMV = holdings.reduce((s, h) => s + (h.current_value || 0), 0);
      account = {
        marketValue: Math.round(totalMV * 100) / 100,
        cost: Math.round(totalCost * 100) / 100,
        positionCount: holdings.length,
        cash: Math.round((meta.available_cash || 0) * 100) / 100,
        totalCapital: meta.total_capital || 0,
        accountState: posData.account_state || '',
        lastUpdated: meta.last_updated || '',
      };
    } catch (_) {}

    // --- strategy ---
    let strategy = null;
    if (latestStrategy) {
      strategy = {
        date: latestStrategy.date,
        total: latestStrategy.count,
        signals: {
          '🔥进攻': latestStrategy.attack || 0,
          '✅买入': latestStrategy.buy || 0,
          '🕐埋伏': latestStrategy.ambush || 0,
          '—观望': latestStrategy.watch || 0,
        },
        avgScore: latestStrategy.avgScore,
        stocks: latestStrategy.stocks || [],
      };
    }

    // --- tracker ---
    let tracker = null;
    if (trackerSummary) {
      const stockSum = trackerSummary.stockSummary || [];
      let totalDecisions = 0, totalHits = 0, totalMisses = 0, totalPending = 0;
      for (const s of stockSum) {
        totalDecisions += s.total || 0;
        totalHits += s.hit || 0;
        totalMisses += s.miss || 0;
        totalPending += s.pending || 0;
      }
      const nonNodata = totalDecisions - stockSum.reduce((a,s) => a + (s.nodata||0), 0);
      const hitRate = nonNodata > 0 ? Math.round((totalHits / nonNodata) * 100) : 0;

      const topStocks = (latestStrategy && latestStrategy.stocks)
        ? latestStrategy.stocks.sort((a, b) => (b.score || 0) - (a.score || 0)).slice(0, 8).map(s => ({
            code: s.code, name: s.name, score: s.score, signal: s.signal_type,
          }))
        : [];

      tracker = {
        totalDecisions,
        hits: totalHits,
        misses: totalMisses,
        pending: totalPending,
        nodata: stockSum.reduce((a,s) => a + (s.nodata||0), 0),
        hitRate,
        topStocks,
      };
    }

    res.json({ timing: timingOut, account, strategy, tracker,

      // ── V5.0: 契约桥（event_narrative + global_conclusion）──
      narrative: (() => {
        const en = _readEventNarrative();
        if (!en) return null;
        _validateEventNarrativeFields(en);  // V5.1: field audit, runs first call only
        return en.global_conclusion || null;
      })(),
      event_narrative: (() => {
        const en = _readEventNarrative();
        if (!en) return null;
        return {
          date: en.date,
          source: en.source,
          generated_at: en.generated_at,
          events: en.events || [],
          sector_outlook: en.sector_outlook || [],
          global_conclusion: en.global_conclusion || null,
        };
      })(),
    });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ── /m/api/strategy/all ── 全部策略记录
router.get('/m/api/strategy/all', async (req, res) => {
  try {
    const dates = await strategyModel.getAvailableDates();
    // V5.3: ?days=N 滚动窗口过滤
    const limitDays = parseInt(req.query.days) || 0;
    let filteredDates = dates;
    if (limitDays > 0) {
      const cutoff = new Date();
      cutoff.setDate(cutoff.getDate() - limitDays);
      const cutoffStr = cutoff.toISOString().slice(0, 10);
      filteredDates = dates.filter(d => d >= cutoffStr);
    }
    const byDate = {};
    for (const d of filteredDates) {
      const data = await strategyModel.getStrategyByDate(d);
      if (data) {
        byDate[d] = {
          date: data.date,
          count: data.count,
          attack: data.attack,
          buy: data.buy,
          ambush: data.ambush,
          watch: data.watch,
          avgScore: data.avgScore,
          stocks: (data.stocks || []).map(s => ({
            code: s.code,
            name: s.name,
            signal: s.signal_type || '',
            trend: _scoreDim(s, 'ma_align'),
            volumePrice: _scoreDim(s, 'fib_zone'),
            capitalFlow: _scoreDim(s, 'capital_dir'),
            pattern: _scoreDim(s, 'rr'),
            theme: _scoreDim(s, 'weekly_dir'),
            score: s.score || 0,
            nx: s.nx || '',
            entry_low: s.entry_low,
            entry_high: s.entry_high,
            stop_loss: s.stop_loss,
          })),
        };
      }
    }
    res.json({ dates, byDate });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ── /m/api/tracker/all ── 全部跟踪记录
router.get('/m/api/tracker/all', async (req, res) => {
  try {
    const summary = await trackerModel.getTrackerSummary();
    if (!summary) return res.json({ totalDecisions: 0, hits: 0, misses: 0, hitRate: 0, pending: 0, stocks: [], records: [], backtestReports: [], dates: [] });

    const filterDate = req.query.date || null;
    const allRecords = summary.totalRecords ? await _readAllTracker() : [];

    // Filter by date if requested
    let filteredRecords = allRecords;
    if (filterDate) {
      filteredRecords = allRecords.filter(r => r.signal_date === filterDate);
    }

    // Compute stock-level summary from filtered or all records
    const byCode = {};
    for (const r of filteredRecords) {
      if (!byCode[r.code]) {
        byCode[r.code] = { code: r.code, name: r.name, records: [], verdicts: {} };
      }
      byCode[r.code].records.push(r);
      const v = r.result || 'NODATA';
      byCode[r.code].verdicts[v] = (byCode[r.code].verdicts[v] || 0) + 1;
    }

    const stocks = Object.values(byCode).map(s => {
      const total = s.records.length;
      const hits = s.verdicts.HIT || 0;
      const misses = s.verdicts.MISS || 0;
      const effective = total - (s.verdicts.NODATA || 0);
      const hitRate = effective > 0 ? Math.round((hits / effective) * 100) : 0;
      // Last verdict
      const last = s.records[s.records.length - 1];
      return {
        code: s.code,
        name: s.name,
        totalDecisions: total,
        hits,
        hitRate,
        lastVerdict: last ? (last.result || 'NODATA') : 'NODATA',
      };
    }).sort((a, b) => b.totalDecisions - a.totalDecisions);

    // Aggregate totals
    const totalDecisions = filteredRecords.length;
    const totalHits = filteredRecords.filter(r => r.result === 'HIT').length;
    const totalMisses = filteredRecords.filter(r => r.result === 'MISS').length;
    const totalPending = filteredRecords.filter(r => r.result === 'PENDING').length;
    const totalNodata = filteredRecords.filter(r => r.result === 'NODATA' || !r.result).length;

    const effective = totalDecisions - totalNodata;
    const hitRate = effective > 0 ? Math.round((totalHits / effective) * 100) : 0;

    // Average deviation for HIT/MISS records
    let sumDev = 0, devCount = 0;
    for (const r of filteredRecords) {
      if (r.max_return != null && (r.result === 'HIT' || r.result === 'MISS')) {
        sumDev += Math.abs(r.max_return || 0);
        devCount++;
      }
    }
    const avgDeviation = devCount > 0 ? (sumDev / devCount * 100).toFixed(1) + '%' : '—';

    // Records (sorted by signal_date desc, then score desc)
    const records = filteredRecords
      .sort((a, b) => {
        if (a.signal_date !== b.signal_date) return b.signal_date.localeCompare(a.signal_date);
        return (b.score || 0) - (a.score || 0);
      })
      .map(r => ({
        date: r.signal_date,
        code: r.code,
        signal: r.signal_type || '',
        direction: (r.signal_type || '').includes('多看') ? '做多' : (r.signal_type || '').includes('看空') ? '做空' : '—',
        target: r.targets && r.targets.length > 0 ? r.targets[0] : null,
        actual: r.final_return != null ? (r.final_return * 100).toFixed(1) + '%' : '—',
        deviation: r.max_return != null ? (Math.abs(r.max_return) * 100).toFixed(1) + '%' : '—',
        verdict: r.result || 'NODATA',
        horizon: r.horizon,
      }));

    // Backtest reports
    let backtestReports = [];
    try {
      const files = await fs.readdir(BACKTEST_DIR);
      for (const f of files) {
        if (!f.endsWith('.html')) continue;
        const stat = await fs.stat(path.join(BACKTEST_DIR, f));
        backtestReports.push({
          name: f.replace(/\.html$/, '').replace(/^strategy_/, '策略 ').replace(/-/g, '/'),
          date: stat.mtime.toISOString().slice(0, 10),
          url: '/admin/trader/backtest/view/' + f,
        });
      }
      backtestReports.sort((a, b) => b.date.localeCompare(a.date));
    } catch (_) {}

    res.json({
      totalDecisions,
      hits: totalHits,
      misses: totalMisses,
      pending: totalPending,
      nodata: totalNodata,
      hitRate,
      avgDeviation,
      stocks,
      records,
      backtestReports,
      dates: summary.dates || [],
      filterDate: filterDate || summary.latestDate,
    });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ── /m/api/tracker/stock/:code ── 单个标的跟踪历史
router.get('/m/api/tracker/stock/:code', async (req, res) => {
  try {
    const history = await trackerModel.getStockTrackingHistory(req.params.code);
    if (!history || history.length === 0) {
      return res.json({ code: req.params.code, totalDecisions: 0, hitRate: 0, history: [] });
    }

    const total = history.length;
    const hits = history.filter(r => r.result === 'HIT').length;
    const nondata = total - history.filter(r => r.result === 'NODATA' || !r.result).length;
    const hitRate = nondata > 0 ? Math.round((hits / nondata) * 100) : 0;

    let sumDev = 0, devCount = 0;
    for (const r of history) {
      if (r.max_return != null) { sumDev += Math.abs(r.max_return); devCount++; }
    }
    const avgDeviation = devCount > 0 ? (sumDev / devCount * 100).toFixed(1) + '%' : '—';

    const name = history[0].name || '';
    const records = history.map(r => ({
      date: r.signal_date,
      horizon: r.horizon,
      signal: r.signal_type || '',
      direction: (r.signal_type || '').includes('多') ? '做多' : '做空',
      target: r.targets && r.targets.length > 0 ? r.targets[0] : null,
      actual: r.final_return != null ? (r.final_return * 100).toFixed(1) + '%' : '—',
      deviation: r.max_return != null ? (Math.abs(r.max_return) * 100).toFixed(1) + '%' : '—',
      verdict: r.result || 'NODATA',
    }));

    res.json({
      code: req.params.code,
      name,
      totalDecisions: total,
      hitRate,
      avgDeviation,
      history: records,
    });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ── /m/api/cycleradar ── 周期雷达：V4.2.0 四分类输出
router.get('/m/api/cycleradar', async (req, res) => {
  try {
    const [signalsData, categories, events, rssHealth] = await Promise.all([
      signalsModel.getDashboardData(),
      signalsModel.getCycleradarCategories(),
      _getHotEvents(),
      _getRssHealth(),
    ]);

    const enrichedEvents = _enrichHotEvents(events || []);

    // Q11: 后端过滤——正文缺失且无标的、或纯非市场内容，不下发前端
    const _badKws = ['正文缺失', '无法确认', '信息不完整', '但无正文', '但正文缺失'];
    const filteredEvents = enrichedEvents.filter(e => {
      const thesis = e.thesis || '';
      if (thesis === '非市场分析内容') return false;
      const hasIncomplete = _badKws.some(kw => thesis.includes(kw));
      if (hasIncomplete && (e.tickers || []).length === 0) return false;
      return true;
    });

    let summary = null;
    let byStrategy = [];
    let byAssetType = [];

    if (signalsData) {
      const conf = signalsData.summary.avgConfidence;
      summary = {
        active:    signalsData.summary.active,
        longCount: signalsData.summary.longCount,
        shortCount: signalsData.summary.shortCount,
        strategyCount: signalsData.summary.strategyCount,
        avgConfidence: conf != null ? Math.round(conf * 100) / 100 : null,
        newestTime: signalsData.summary.newestTime || null,
      };
      byStrategy = signalsData.byStrategy.map(s => ({
        strategy: s.strategy,
        count: s.count,
        long: s.direction ? s.direction.long : 0,
        short: s.direction ? s.direction.short : 0,
      }));
      byAssetType = signalsData.byAssetType.map(t => ({
        assetType: t.assetType,
        long: t.direction ? t.direction.long : 0,
        short: t.direction ? t.direction.short : 0,
      }));
    }

    const formatSignal = s => ({
      signal_id: s.signal_id || '',
      strategy: s.strategy || '',
      asset: s.asset || '',
      assetType: s.asset_type || '',
      direction: s.direction || 'long',
      confidence: s.confidence != null ? s.confidence : 0,
      expiry: s.expiry || '',
      metadata: s.metadata || {},
    });

    // V4.3: 信号新鲜度（基于 newestTime，供前端时效条使用）
    let signalFreshness = { freshnessHours: null, freshnessStatus: 'empty' };
    if (signalsData && signalsData.summary.newestTime) {
      const signalTs = new Date(signalsData.summary.newestTime).getTime();
      const signalAgeHours = Math.round(((Date.now() - signalTs) / 3600000) * 10) / 10;
      signalFreshness = {
        freshnessHours: signalAgeHours,
        freshnessStatus: signalAgeHours >= 24 ? 'stale' : signalAgeHours >= 6 ? 'degraded' : 'fresh',
        lastSignalTime: signalsData.summary.newestTime,
      };
    }

    res.json({
      summary,
      byStrategy,
      byAssetType,
      // V4.1.0 四分类 + V4.1.2 LLM 增强；Q11: 已在后端过滤空正文/非市场事件
      hotEvents: filteredEvents || [],
      dataFreshness: rssHealth,  // V4.2: RSS 数据管路健康度（hotEvents 用）
      signalFreshness,           // V4.3: 信号新鲜度（alpha/ETF/commodity 用）
      alpha: (categories.alpha || []).map(formatSignal),
      etf: (categories.etf || []).map(s => {
        const sig = formatSignal(s);
        const name = ETF_NAME_MAP[sig.asset];
        if (name) sig.metadata = { ...sig.metadata, stock_name: name };
        return sig;
      }),
      commodity: (categories.commodity || []).map(formatSignal),
      // keep flat list for backward compat
      signals: signalsData ? signalsData.signals.map(formatSignal) : [],

      // ── V5.0: alpha_latest 契约桥（entry/target/stop/thesis）──
      alpha_latest: (() => {
        const al = _readAlphaLatest();
        if (!al || !al.signals) return null;
        return {
          date: al.date,
          signals: al.signals.map(s => ({
            signal_id: s.signal_id || '',
            code: s.stock ? s.stock.code : (s.code || ''),
            name: s.stock ? s.stock.name : (s.name || ''),
            direction: s.direction || 'long',
            entry_price: s.entry_price || null,
            target_price: s.target_price || null,
            stop_loss: s.stop_loss || null,
            confidence: s.confidence || 0,
            time_window: s.time_window || '',
            event_source: s.event_source || '',
            thesis: s.thesis || '',
            sector_context: s.sector_context || '',
            enhanced_nx: s.enhanced_nx || '',
          })),
        };
      })(),

      // ── V6.4: event_narrative 新契约桥（对接 generate_contracts.py V6.3.2 schema）──
      event_narrative: (() => {
        const en = _readEventNarrative();
        if (!en) return null;
        _validateEventNarrativeFields(en);  // V5.1: consumer field audit, separate call ID for tracing
        return {
          generated_at: en.generated_at || null,
          global_conclusion: en.global_conclusion || null,
          events: (en.events || []).map(e => ({
            rank: e.rank || null,
            title: e.title || '',
            source: e.source || null,
            source_title: e.source_title || '',
            time_dimension: e.time_dimension || '',
            trigger_event: e.trigger_event || '',
            direct_reaction: e.direct_reaction || '',
            sector_transmission: (e.sector_transmission || []).map(s => ({
              name: s.sector || '',
              direction: s.direction || '',
              reason: s.reason || '',
            })),
            valuation_impact: e.valuation_impact || '',
            trading_window: e.trading_window || '',
            stock_mapping: (e.stock_mapping || []).map(t => ({
              code: String(t.code || ''),
              name: t.name || '',
              type: t.type || '',
              logic: t.logic || '',
            })),
          })),
        };
      })(),

      // ── V6.5: 30日胜率数据（positions.json daily_pnl_history，calc_30d_winrate.py 写入）──
      daily_pnl: (() => {
        try {
          const raw = fsSync.readFileSync(_getPositionsPath(), 'utf8');
          const positions = JSON.parse(raw);
          const history = positions.daily_pnl_history || [];
          if (!history.length) return null;
          const latest = history[history.length - 1];
          return {
            date: latest.date || null,
            win_rate: latest.win_rate != null ? Math.round(latest.win_rate * 10) / 10 : null,
            active_signals: latest.active_signals_30d || 0,
            valid_signals: latest.valid_signals || 0,
            win: latest.win || 0,
            loss: latest.loss || 0,
            avg_return_pct: latest.avg_return_pct != null ? Math.round(latest.avg_return_pct * 100) / 100 : null,
          };
        } catch (_) { return null; }
      })(),
    });

  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ── 内部辅助 ──

// 将策略维度转换为 0-5 分
function _scoreDim(record, field) {
  const val = record[field];
  if (val == null) return 0;
  switch (field) {
    case 'ma_align':
      return val.includes('bull') ? 5 : val.includes('bear') ? 1 : 3;
    case 'fib_zone':
      return val === 'above_support' ? 4 : val === 'below_resistance' ? 2 : 3;
    case 'capital_dir':
      return val === '净流入' ? 4 : val === '净流出' ? 1 : val === '流入' ? 3 : 2;
    case 'rr':
      return Math.min(5, Math.round((Number(val) || 0) * 2));
    case 'weekly_dir':
      return val === '上升' ? 5 : val === '下降' ? 1 : val === '横盘' ? 3 : 2;
    default:
      return 3;
  }
}

async function _readAllTracker() {
  const TRACKER_FILE = path.join(__dirname, '..', '..', 'data', 'trader_tracker.jsonl');
  try {
    const raw = await fs.readFile(TRACKER_FILE, 'utf8');
    const records = [];
    for (const line of raw.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try { records.push(JSON.parse(trimmed)); } catch (_) {}
    }
    return records;
  } catch (_) { return []; }
}

// ── /m/api/haoyunge ── V5.3: 好运哥交易纪律（regime → posture 映射）
router.get('/m/api/haoyunge', async (req, res) => {
  try {
    const contractsDir = await _getContractsPath();
    const narrativePath = path.join(contractsDir, 'event_narrative_latest.json');
    const narrativeRaw = await fs.readFile(narrativePath, 'utf8');
    const narrative = JSON.parse(narrativeRaw);
    const gc = narrative.global_conclusion || {};
    const regime = gc.market_regime || '均衡';
    const longShortRatio = parseFloat(gc.long_short_ratio) || 1.0;
    const posture = haoyunge.calculatePosture(regime, longShortRatio, null);
    res.json({ regime, longShortRatio, ...posture });
  } catch (error) {
    res.status(500).json({ error: String(error) });
  }
});

// ── /m/api/watchlist ── V6: 自选池信号快读（contracts 缓存 → fallback watchlist.json）
router.get('/m/api/watchlist', async (req, res) => {
  try {
    // 优先读 contracts 里的预计算信号快照
    const contractsDir = await _getContractsPath();
    const filePath = path.join(contractsDir, 'watchlist_signals.json');
    const raw = await fs.readFile(filePath, 'utf8');
    return res.json(JSON.parse(raw));
  } catch (_) {}
  // fallback: 读 watchlist.json（只有列表，无信号数据）
  try {
    const DATA_DIR = process.env.CYCLERADAR_DATA_DIR || path.join(__dirname, '../../data');
    const wlPath = path.join(DATA_DIR, 'watchlist.json');
    const raw = await fs.readFile(wlPath, 'utf8');
    const parsed = JSON.parse(raw);
    // watchlist.json 结构可能是 {stocks:[...]} 或直接 [...]
    const stocks = Array.isArray(parsed) ? parsed : (parsed.stocks || []);
    return res.json({ stocks, signals: [], source: 'watchlist_fallback' });
  } catch (err) {
    return res.status(500).json({ error: 'watchlist 数据不可用', detail: String(err) });
  }
});

module.exports = router;
