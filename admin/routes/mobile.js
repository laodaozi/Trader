'use strict';

const express = require('express');
const os = require('os');
const fs = require('fs/promises');
const fsSync = require('fs');  // V4.1.2: sync ops for enrichment cache
const path = require('path');
const { execSync } = require('child_process');
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
const WEWE_DB_PATH = path.join(__dirname, '..', 'data', 'wewe-rss.db');  // [deprecated] 保留路径定义供 _getRssHealth 兼容
const SOURCE_ARTICLES_DB_PATH = path.join(__dirname, '..', '..', 'data', 'source_articles.db');
const HOTEVENTS_CACHE_PATH = path.join(__dirname, '..', '..', 'data', 'hotevents_cache.json');
const ENRICHMENT_CACHE_PATH = path.join(__dirname, '..', '..', 'data', 'hot_enrichment.json');
const ROTATION_PATH = path.join(__dirname, '..', '..', 'data', 'rotation_snapshot.json');
const UPSTREAM_SIGNALS_PATH = path.join(__dirname, '..', '..', 'data', 'upstream_signals.jsonl');
const TRANSMISSION_SIGNALS_PATH = path.join(__dirname, '..', '..', 'data', 'transmission_signals.jsonl');

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
  // V9.0 扩展：rotation_factor 产出的 ETF 代码
  '518880': '黄金ETF',
  '159869': '游戏ETF',
  '159825': '农业ETF',
  '159766': '旅游ETF',
  '159861': '碳中和ETF',
  '159863': '石油ETF',
  '159745': '建材ETF',
  '159998': '大数据ETF',
  '512200': '地产ETF',
  '512400': '有色ETF',
  '512800': '银行ETF',
  '512980': '传媒ETF',
  '515170': '食品饮料ETF',
  '516110': '汽车ETF',
  '516130': '消费ETF',
};

// ── V5.0: 契约文件路径解析（3 文件桥）──
// V7.6 融合：契约文件（alpha_latest / event_narrative / watchlist_signals）统一在平台 data/，
// 由 core/scripts/generate_contracts.py + update_watchlist_signals.py 生成。
// 不再依赖交易员冻结目录（/opt/trader/output/contracts、~/交易员/data）。
let _contractsPathCache = null;
function _getContractsPath() {
  if (_contractsPathCache) return _contractsPathCache;
  const candidates = [
    path.join(__dirname, '..', '..', 'data'),                // 平台 data/（本地 + ECS 通用）
    '/opt/cycleradar-trader/data',                           // ECS 绝对路径兜底
  ];
  for (const c of candidates) {
    try {
      if (fsSync.existsSync(path.join(c, 'alpha_latest.json'))) {
        _contractsPathCache = c;
        return c;
      }
    } catch (_) {}
  }
  // fallback: 平台 data/ 相对路径（部署后由 cron/脚本填充）
  _contractsPathCache = path.join(__dirname, '..', '..', 'data');
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

// ── 热点事件（V7.7: source_articles.db 优先，MCP news + URL ingest） ──
async function _getHotEvents() {
  let events = [];
  let fromCache = false;

  // 尝试从 source_articles.db 获取
  try {
    await fs.access(SOURCE_ARTICLES_DB_PATH);
    events = await _queryHotEventsFromSourceDB();
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
        console.warn(`[_getHotEvents] source_articles 无数据，使用缓存 (${cached.cached_at})`);
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
  // [deprecated] WeWe RSS path — kept for backward compat, but no longer the primary path
  console.warn('[_queryHotEventsFromDB] deprecated: use _queryHotEventsFromSourceDB instead');
  return [];
}

// V7.7: 从 source_articles.db 读取热点事件（MCP news + URL ingest）
async function _queryHotEventsFromSourceDB() {
  try {
    const SQL = await _getSQL();
    const fs = require('fs');
    const buf = fs.readFileSync(SOURCE_ARTICLES_DB_PATH);
    const db = new SQL.Database(buf);
    const today = new Date().toISOString().slice(0, 10);
    const rows = _rowsFromExec(db,
      `SELECT title, source_name AS source, url, content_text AS content, created_at, weight
       FROM source_articles
       WHERE fetch_status = 'success'
         AND publish_date >= date('${today}', '-1 day')
       ORDER BY weight DESC, created_at DESC
       LIMIT 20`);
    db.close();

    return rows.map(row => ({
      title: row.title || '',
      time: row.created_at || new Date().toISOString(),
      source: row.source || '',
      pic_url: '',
      content: row.content || '',
      url: row.url || '',
    }));
  } catch (e) {
    console.warn('[_queryHotEventsFromSourceDB] sql.js error:', e.message);
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

// ── V7.7: source_articles 数据新鲜度检测 ──
// 查询 source_articles 表最新 created_at，返回 freshnessHours / freshnessStatus
async function _getSourceArticlesHealth() {
  try {
    const SQL = await _getSQL();
    const fs = require('fs');
    const buf = fs.readFileSync(SOURCE_ARTICLES_DB_PATH);
    const db = new SQL.Database(buf);
    const results = db.exec("SELECT MAX(created_at) AS ts FROM source_articles WHERE fetch_status='success'");
    db.close();
    const ts = (results.length && results[0].values.length) ? results[0].values[0][0] : null;
    if (!ts) {
      return { freshnessHours: null, freshnessStatus: 'empty', lastArticleTime: null, note: 'no articles in source_articles.db' };
    }
    const lastTime = new Date(ts);
    const ageHours = Math.round((Date.now() - lastTime.getTime()) / 3600000 * 10) / 10;
    let status = 'fresh';
    if (ageHours >= 24) status = 'stale';
    else if (ageHours >= 6) status = 'degraded';
    return {
      freshnessHours: ageHours,
      freshnessStatus: status,
      lastArticleTime: lastTime.toISOString(),
    };
  } catch (e) {
    return { freshnessHours: null, freshnessStatus: 'unknown', lastArticleTime: null, note: 'source_articles.db unreadable: ' + e.message };
  }
}

// [deprecated] _getRssHealth — 保留供其他路由兼容
async function _getRssHealth() {
  return _getSourceArticlesHealth();
}

// ── /m ── V6 三 tab 仪表盘（2026-06-19 切换，原 /m/v6）
router.get('/m', (req, res) => {
  res.render('dashboard', { title: 'CycleRadar Trader', appVersion: require('../routes/system').getVersion() || 'V10.1' });
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
      if (ph.includes('冲刺') || ph.includes('加速') || ph.includes('启动')) advice = '趋势加速，顺势持有';
      else if (ph.includes('上涨') || ph.includes('进攻')) advice = '趋势向上，积极操作';
      else if (ph.includes('回调') && tmp > 60) advice = '回调中，控制仓位';
      else if (ph.includes('回调')) advice = '回调较深，观望为主';
      else if (ph.includes('震荡')) advice = '震荡市，高抛低吸';
      else if (ph.includes('下跌')) advice = '趋势向下，防守为主';
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
      // P1-1: 账户状态需反映数据真实性，避免过期/空账户误显"良性"
      const lastUpd = meta.last_updated || '';
      let staleDays = null;
      if (lastUpd) {
        const d = Math.floor((Date.now() - new Date(lastUpd).getTime()) / 86400000);
        staleDays = Number.isFinite(d) ? d : null;
      }
      const isEmpty = holdings.length === 0 && totalMV === 0;
      let accountState = posData.account_state || '';
      let accountStatus = 'ok';           // ok | stale | unlinked
      if (isEmpty) {
        accountState = '未接入实盘';
        accountStatus = 'unlinked';
      } else if (staleDays != null && staleDays > 7) {
        accountState = `数据过期 ${staleDays} 天`;
        accountStatus = 'stale';
      }
      account = {
        marketValue: Math.round(totalMV * 100) / 100,
        cost: Math.round(totalCost * 100) / 100,
        positionCount: holdings.length,
        cash: Math.round((meta.available_cash || 0) * 100) / 100,
        totalCapital: meta.total_capital || 0,
        accountState,
        accountStatus,
        staleDays,
        lastUpdated: lastUpd,
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
          '👀观察': latestStrategy.watch || 0,
          '—回避': latestStrategy.avoid || 0,
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
      // P0-1: 命中率 = 命中/(命中+踏空)，pending(信号未到验证期)与 nodata 均不计入分母
      const decided = totalHits + totalMisses;
      const hitRate = decided > 0 ? Math.round((totalHits / decided) * 100) : 0;

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

    // --- rotation snapshot ---
    let rotationSnapshot = null;
    try { const raw = await fs.readFile(ROTATION_PATH, 'utf8'); rotationSnapshot = JSON.parse(raw); } catch (_) {}

    // --- pulse (V10.0) ---
    let pulse = null;
    try {
      const raw = await fs.readFile(path.join(__dirname, "..", "..", "data", "pulse_latest.json"), "utf8");
      pulse = JSON.parse(raw);
    } catch (_) {}

    // P0-2: pulse 是 V10.0 综合裁决引擎(timing+5模型)，为权威源。
    // 若可用，用 pulse.verdict 覆盖 timing.advice，避免 summary 与 pulse 给出相反仓位建议。
    if (pulse && pulse.verdict && timingOut) {
      timingOut.advice = pulse.verdict;
      timingOut.adviceSource = 'pulse';
    } else if (timingOut) {
      timingOut.adviceSource = 'timing';
    }

    res.json({ timing: timingOut, account, strategy, tracker, rotation_snapshot: rotationSnapshot,
      pulse,

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
      // P0-1: 统一口径 命中/(命中+踏空)
      const decided = hits + misses;
      const hitRate = decided > 0 ? Math.round((hits / decided) * 100) : 0;
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
    // P0-1: 统一口径 命中/(命中+踏空)
    const decided = totalHits + totalMisses;
    const hitRate = decided > 0 ? Math.round((totalHits / decided) * 100) : 0;

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
    const misses = history.filter(r => r.result === 'MISS').length;
    const pending = history.filter(r => r.result === 'PENDING').length;
    // P0-1 统一口径：命中率 = 命中/(命中+踏空)，pending 与 nodata 不计入分母
    const decided = hits + misses;
    const hitRate = decided > 0 ? Math.round((hits / decided) * 100) : 0;

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
      hits,
      misses,
      pending,
      decided,
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
    const [signalsData, categories, events, dataHealth] = await Promise.all([
      signalsModel.getDashboardData(),
      signalsModel.getCycleradarCategories(),
      _getHotEvents(),
      _getSourceArticlesHealth(),
    ]);

    const enrichedEvents = _enrichHotEvents(events || []);

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
      // V4.1.0 四分类 + V4.1.2 LLM 增强
      hotEvents: enrichedEvents || [],
      dataFreshness: dataHealth,  // V7.7: source_articles 数据管路健康度
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
  const TRACKER_FILE = path.join(__dirname, '..', '..', 'data', 'trader_tracker.jsonl'); // V9.8 fix: 旧 tracker_log.jsonl 已废弃(WIN/LOSE枚举+空)，改读 trader_tracker.jsonl(HIT/MISS)
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

// ── /m/api/reflection/summary ── 策略反思摘要（loop learning 驱动）──
// 返回最新的 strategy_reflection.json 摘要供 /m 首页 Loop Learning 卡片展示
router.get('/m/api/reflection/summary', async (req, res) => {
  try {
    const DATA_DIR = process.env.CYCLERADAR_DATA_DIR || path.join(__dirname, '../../data');
    const reflPath = path.join(DATA_DIR, 'strategy_reflection.json');
    const raw = await fs.readFile(reflPath, 'utf8');
    const refl = JSON.parse(raw);

    // 提炼摘要：只返回前端需要的字段（不发全量 prompt/tokens）
    const summary = refl.summary || '';
    const generatedAt = refl.generated_at || '';
    const loopLearning = refl.loop_learning || {};
    const actionItems = (refl.action_items || []).slice(0, 3);

    // 失败信号根因（第2个 section）
    const reflections = refl.reflections || [];
    const failureSection = reflections.find(r => r.section === '失败信号根因分析') || null;
    const improveSection = reflections.find(r => r.section === '下期策略改进建议') || null;
    const strategySection = reflections.find(r => r.section === '信号策略质量自评') || null;

    return res.json({
      summary,
      generated_at: generatedAt,
      loop_learning: loopLearning,
      action_items: actionItems,
      failure_analysis: failureSection ? failureSection.content : null,
      improvement_advice: improveSection ? improveSection.content : null,
      strategy_quality: strategySection ? strategySection.content : null,
      section_count: reflections.length,
    });
  } catch (err) {
    return res.status(200).json({ summary: null, generated_at: null, loop_learning: {}, action_items: [] });
  }
});

// ── /m/api/event-hits ── 事件标的回查命中率（verify_event_signals.py 产出）──
router.get('/m/api/event-hits', async (req, res) => {
  try {
    const DATA_DIR = process.env.CYCLERADAR_DATA_DIR || path.join(__dirname, '../../data');
    const hitPath = path.join(DATA_DIR, 'event_hit_log.json');
    const raw = await fs.readFile(hitPath, 'utf8');
    const data = JSON.parse(raw);

    // 构建 hash → 命中率的快速索引，供前端事件卡片查找
    const hitIndex = {};
    for (const ev of (data.events || [])) {
      hitIndex[ev.hash] = {
        hit_rate_5d: ev.hit_rate_5d,
        verdicts_count: ev.verdicts_count,
        hit_tickers: (ev.tickers || []).filter(t => t.verdicts && t.verdicts.d5 === 'HIT').map(t => t.name),
        miss_tickers: (ev.tickers || []).filter(t => t.verdicts && t.verdicts.d5 === 'MISS').map(t => t.name),
      };
    }

    return res.json({
      generated_at: data.generated_at,
      summary: data.summary || {},
      hit_index: hitIndex,
    });
  } catch (err) {
    return res.status(200).json({ generated_at: null, summary: {}, hit_index: {} });
  }
});

// ── /m/api/insights ── V10.0 深度结构化问答（多数据源聚合 + 复盘注入）──
const INSIGHT_TEMPLATES = [
  { id: "strategy_perf",  q: "策略绩效排名？",    icon: "📊" },
  { id: "resonance",     q: "今天信号共振方向？", icon: "🔔" },
  { id: "etf_direction", q: "ETF 资金方向？",     icon: "📈" },
  { id: "market_style",  q: "当前市场风格？",     icon: "🎨" },
  { id: "top_signals",   q: "今日最强信号？",      icon: "⚡" },
];

// ── helpers ──
function safeJsonParse(raw) { try { return JSON.parse(raw); } catch (_) { return null; } }
function loadLines(p) { try { return require("fs").readFileSync(p,"utf8").split("\n").filter(l=>l.trim()); } catch(_) { return []; } }
function loadJson(p) { try { return JSON.parse(require("fs").readFileSync(p,"utf8")); } catch(_) { return null; } }
function loadReflection() { return loadJson(path.join(__dirname,"..","..","data","strategy_reflection.json")); }
function pct(v) { return Math.round((v||0)*100); }
function r2(v) { return Math.round((v||0)*100)/100; }

// Trader tracker: time-series stats by week
function buildTrackerTrend() {
  const lines = loadLines(path.join(__dirname,"..","..","data","trader_tracker.jsonl"));
  const recs = lines.map(safeJsonParse).filter(Boolean);
  const weeks = {};
  for (const r of recs) {
    const d = r.track_date || r.signal_date || "";
    if (!d) continue;
    const w = d.substring(0,7);
    if (!weeks[w]) weeks[w] = { week: w, total:0, wins:0, pnl_sum:0, max_dd_sum:0 };
    weeks[w].total++;
    if (r.result === "WIN" || r.hit_target === "True" || r.hit_target === "true") weeks[w].wins++;
    const ret = parseFloat(r.final_return) || 0;
    weeks[w].pnl_sum += ret;
    const dd = parseFloat(r.max_dd) || 0;
    weeks[w].max_dd_sum += dd;
  }
  return Object.values(weeks).sort((a,b) => a.week.localeCompare(b.week)).map(w => ({
    ...w,
    win_rate: w.total>0 ? r2(w.wins/w.total*100) : 0,
    avg_pnl: w.total>0 ? r2(w.pnl_sum/w.total) : 0,
    avg_dd: w.total>0 ? r2(w.max_dd_sum/w.total) : 0
  }));
}

// 档1: 策略统计 —— 委托单一源 trackerModel.globalWinRateByStrategy() (口径A)
async function buildStrategyStats() {
  const rows = await trackerModel.globalWinRateByStrategy();
  return rows.map(g => ({
    name: g.strategy,
    win_rate: g.winRate == null ? 0 : g.winRate,   // win/(win+lose)
    avg_pnl: r2(g.avgFinalReturn == null ? 0 : g.avgFinalReturn),
    avg_dd: g.avgMaxDd == null ? 0 : g.avgMaxDd,
    avg_bars: Math.round(g.avgHoldingDays || 0),
    total: g.total
  })).sort((a,b) => b.win_rate - a.win_rate);
}

// Worst trades from trader_tracker
function buildWorstTrades(n) {
  const lines = loadLines(path.join(__dirname,"..","..","data","trader_tracker.jsonl"));
  const recs = lines.map(safeJsonParse).filter(r => r && r.result!=="PENDING" && (r.result==="MISS" || parseFloat(r.final_return||0)<0));
  recs.sort((a,b) => ((a.result==="MISS"?-99:parseFloat(a.final_return||0))) - ((b.result==="MISS"?-99:parseFloat(b.final_return||0))));
  return recs.slice(0,n||3).map(r => ({
    code: r.code, name: r.name,
    pnl: r.result==="MISS" ? `得分${r.score||0}` : r2(parseFloat(r.final_return)||0),
    entry: parseFloat(r.entry)||0,
    stop: parseFloat(r.stop)||0,
    target: r.targets ? JSON.parse(r.targets||"[0]")[0] : 0,
    signal_date: r.signal_date||"",
    strategy: r.strategy, score: parseInt(r.score)||0,
    entry: parseFloat(r.entry)||0
  }));
}

router.get("/m/api/insights", async (req, res) => {
  const qid = req.query.q || req.query.id || "";
  try {
    const refl = loadReflection() || {};
    const answers = {};

    // ── 1. 策略绩效 ──
    if (!qid || qid === "strategy_perf") {
      try {
        const rankings = await buildStrategyStats();
        const trend = buildTrackerTrend();
        const worstTrades = buildWorstTrades(3);
        const selfAssessment = refl.reflections?.find(r => r.section==="信号策略质量自评")?.content || "";
        const loopL = refl.loop_learning || {};
        const top = rankings[0] || {};
        const worst = rankings[rankings.length-1] || {};
        const recentWeeks = trend.slice(-4);
        const trendDir = recentWeeks.length>=2 && recentWeeks[recentWeeks.length-1].win_rate >= recentWeeks[0].win_rate ? "↑ 改善" : "↓ 下滑";

        answers.strategy_perf = {
          headline: `${top.name} 胜率最高 (${top.win_rate}%/${top.total}笔)${rankings.length>1 ? "，" + worst.name + " 最低 ("+worst.win_rate+"%)" : ""}`,
          analysis: selfAssessment ? selfAssessment.substring(0,300) : "暂无策略自评数据",
          metrics: {
            rankings,
            trend: trend.slice(-8),
            recent_trend: trendDir,
            loop_weights: loopL.strategy_weights || {},
            key_lesson: loopL.key_lesson || "",
          },
          action_items: worstTrades.length>0
            ? [{ priority:"high", action:`复盘最大亏损标的：${worstTrades.map(t=>t.name+"("+t.pnl+"%)").join("、")}` }]
            : [],
          data_quality: selfAssessment ? "high" : "medium",
          answer: `${top.name} 胜率最高 (${top.win_rate}% / ${top.total}笔)` + (rankings.length>1 ? `，${worst.name} 最低 (${worst.win_rate}%)` : ""),
          detail: rankings,
        };
      } catch (e) { answers.strategy_perf = { answer:"暂无数据", detail:[], data_quality:"low" }; }
    }

    // ── 2. 信号共振 ──
    if (!qid || qid === "resonance") {
      try {
        const upstreamRaw = await fs.readFile(path.join(__dirname,"..","..","data","upstream_signals.jsonl"),"utf8");
        const transRaw = await fs.readFile(path.join(__dirname,"..","..","data","transmission_signals.jsonl"),"utf8");
        const combine = new Map();
        for (const lines of [upstreamRaw, transRaw]) {
          for (const line of lines.split("\n").filter(l=>l.trim())) {
            try {
              const sig = JSON.parse(line);
              const key = sig.code || sig.name || "";
              if (!key) continue;
              if (!combine.has(key)) combine.set(key, { code:key, name:sig.name||key, strategies:[], count:0 });
              const entry = combine.get(key);
              if (sig.strategy && !entry.strategies.includes(sig.strategy)) entry.strategies.push(sig.strategy);
              entry.count++;
            } catch (_) { continue; }
          }
        }
        const multiSignal = [...combine.values()].filter(v=>v.strategies.length>=2).slice(0,5);

        const bt = loadJson(path.join(__dirname,"..","..","data","backtest_winrate.json"));
        const modelStats = bt?.models ? Object.entries(bt.models).filter(([,v])=>v.sample_size>0).map(([k,v])=>({
          id:k, name:v.name, win_rate:v.win_rate, profit_factor:r2(v.profit_factor), sample_size:v.sample_size
        })).sort((a,b)=>b.win_rate-a.win_rate) : [];

        const crossVal = refl.reflections?.find(r=>r.section==="因子与叙事交叉验证")?.content || "";

        answers.resonance = {
          headline: multiSignal.length>0
            ? `${multiSignal.length} 个标的被多策略同时推荐：${multiSignal.map(v=>v.name||v.code).join("、")}`
            : "暂无跨策略共振信号",
          analysis: crossVal ? crossVal.substring(0,250) : "暂无因子-叙事一致性数据",
          metrics: {
            multi_signal: multiSignal,
            model_quality: modelStats,
            best_model: modelStats[0] || null,
          },
          action_items: crossVal ? [{ priority:"medium", action:"关注因子与叙事分歧标的，优先服从叙事判断" }] : [],
          data_quality: crossVal ? "high" : "medium",
          answer: multiSignal.length>0 ? `${multiSignal.length} 个标的被多策略同时推荐` : "暂无跨策略共振信号",
          detail: multiSignal,
        };
      } catch (_) { answers.resonance = { answer:"暂无数据", detail:[], data_quality:"low" }; }
    }

    // ── 3. ETF 方向 ── 使用 rotation_snapshot + world_monitor ──
    if (!qid || qid === "etf_direction") {
      try {
        const rotationRaw = await fs.readFile(path.join(__dirname,"..","..","data","rotation_snapshot.json"),"utf8");
        const rot = JSON.parse(rotationRaw);

        // Parse ETF direction from rotation evidence
        const evidenceMatch = (rot.evidence || "").match(/ETF信号\s*(\d+)\s*条/);
        const etfCount = evidenceMatch ? parseInt(evidenceMatch[1]) : 0;
        const direction = rot.direction || "混沌阶段";
        const leadSignals = rot.lead_signals || "";

        // Try world_monitor for sector ETF directions
        let sectorETF = [];
        try {
          const wm = loadJson(path.join(__dirname,"..","..","data","world_monitor_enriched.json"));
          if (wm?.sector?.industry_snapshot) {
            sectorETF = wm.sector.industry_snapshot.slice(0,5).map(s => ({
              name: s.name, code: s.code, change_pct: s.change_pct, trend: s.change_pct>0?"long":"short"
            }));
          }
        } catch(_) {}

        const rotQuality = refl.reflections?.find(r=>r.section==="行业轮动因子质量")?.content || "";

        answers.etf_direction = {
          headline: leadSignals ? leadSignals.replace("领先板块：","") : (direction || "行业轮动方向待确认"),
          analysis: rotQuality ? rotQuality.substring(0,250) : `${direction}。${rot.evidence||""}。${rot.doubt||""}`,
          metrics: {
            direction,
            confidence: parseInt(rot.confidence)||0,
            etf_signal_count: etfCount,
            lead_sectors: leadSignals.replace("领先板块：","").split("、").filter(Boolean),
            sector_etf: sectorETF,
            catalyst: rot.catalyst || "混沌",
          },
          action_items: rotQuality ? [{ priority:"medium", action:"CXO 方向可小仓位关注医药 ETF（512010）短期情绪修复" }] : [],
          data_quality: rotQuality ? "high" : "medium",
          answer: leadSignals || direction,
          detail: { direction, confidence: rot.confidence, lead_signals: leadSignals, etf_count: etfCount },
        };
      } catch (_) { answers.etf_direction = { answer:"暂无数据", detail:[], data_quality:"low" }; }
    }

    // ── 4. 市场风格 ──
    if (!qid || qid === "market_style") {
      try {
        const rotationRaw = await fs.readFile(path.join(__dirname,"..","..","data","rotation_snapshot.json"),"utf8");
        const rot = JSON.parse(rotationRaw);
        const phase = rot.phase || "中性";
        const posRatio = rot.positionRatio ? Math.round(rot.positionRatio*100) : 50;
        const advice = rot.advice || "观望";

        const loopL = refl.loop_learning || {};
        const weights = loopL.strategy_weights || {};
        const summary = refl.summary || "";

        const rootCause = refl.reflections?.find(r=>r.section==="失败信号根因分析")?.content || "";
        const failWarnings = [];
        if (rootCause.includes("反弹陷阱")) failWarnings.push("警惕下跌趋势中的反弹陷阱");
        if (rootCause.includes("商品价格")) failWarnings.push("有色/锂电：商品价格未确认反转前不依赖技术因子");
        if (rootCause.includes("量能")) failWarnings.push("回踩MA20需量能二次确认（成交量>20日均量1.5x）");

        answers.market_style = {
          headline: `${summary || (phase + " | 仓位：" + posRatio + "% | " + advice)}`,
          analysis: rootCause ? rootCause.substring(0,250) : `${phase} 阶段，建议仓位 ${posRatio}%，${advice}`,
          metrics: {
            phase, positionRatio: posRatio, advice,
            signal_count: rot.signalCount||0,
            strategy_weights: weights,
            next_focus: loopL.next_focus || "",
          },
          action_items: failWarnings.map((w,i) => ({ priority: i===0?"high":"medium", action:w })),
          data_quality: rootCause ? "high" : "medium",
          answer: `风格：${phase} | 仓位：${posRatio}% | ${advice}`,
          detail: { phase, positionRatio: posRatio, advice, signal_count: rot.signalCount||0, strategy_weights: weights },
        };
      } catch (_) { answers.market_style = { answer:"暂无数据", detail:{}, data_quality:"low" }; }
    }

    // ── 5. 最强信号 ── 使用 trader_strategy + reflection action_items ──
    if (!qid || qid === "top_signals") {
      try {
        // Top signals from trader_strategy (sorted by model_hits count)
        const stratLines = loadLines(path.join(__dirname,"..","..","data","trader_strategy.jsonl"));
        const stratRecs = stratLines.map(safeJsonParse).filter(r => r && r.signal_type);
        const scored = stratRecs.map(r => {
          const score = parseInt(r.score) || 0;
          const modelHits = (r.model_hits || "").length;
          return { code: r.code, name: r.name, signal_type: r.signal_type, strategy: r.strategy,
                   score, model_hits: modelHits, nx: r.nx, capital_dir: r.capital_dir };
        }).filter(r => r.signal_type && r.signal_type.includes("买入"));
        scored.sort((a,b) => (b.model_hits||0) - (a.model_hits||0) || b.score - a.score);
        const strong = scored.slice(0,5);

        const actionItems = (refl.action_items || []).map(a => ({
          priority: a.priority || "medium",
          action: a.action,
          trigger: a.trigger || "",
        }));

        answers.top_signals = {
          headline: actionItems.length>0
            ? `${actionItems.length} 条待执行操作，最优先：${actionItems.find(a=>a.priority==="high")?.action?.substring(0,35)||"无"}`
            : (strong.length>0 ? strong.map(s=>`${s.name||s.code}: ${s.signal_type}`).join(" | ") : "今日无高置信信号"),
          analysis: refl.reflections?.find(r=>r.section==="中短期标的调仓清单")?.content?.substring(0,300) || "",
          metrics: {
            signals: strong,
            signal_count: strong.length,
            action_items: actionItems,
          },
          action_items: actionItems,
          data_quality: refl.action_items?.length>0 ? "high" : (strong.length>0 ? "medium" : "low"),
          answer: strong.length>0 ? strong.map(s=>`${s.name||s.code}: ${s.signal_type}`).join(" | ") : "今日无高置信信号",
          detail: strong,
        };
      } catch (_) { answers.top_signals = { answer:"暂无数据", detail:[], data_quality:"low" }; }
    }

    // Response
    if (qid && answers[qid]) {
      return res.json({ version:"v10.0", generated_at: new Date().toISOString(), question: qid, ...answers[qid], templates: INSIGHT_TEMPLATES });
    }
    return res.json({ version:"v10.0", generated_at: new Date().toISOString(), templates: INSIGHT_TEMPLATES, answers });
  } catch (err) {
    return res.status(200).json({ version:"v10.0", insights:[], error: err.message, templates: INSIGHT_TEMPLATES });
  }
});

// ── /m/api/world ── V8.5 世界监测（真实数据源，无 LLM 幻觉）──
// 数据源：rotation_snapshot.json（行业轮动）+ upstream_signals.jsonl（商品）
//        + transmission_signals.jsonl（板块传导），均为日频真实管线产出
// 铁律：任何数据源 >48h 未更新 → 该板块返回 null，不伪造
function _isFresh(mtime, maxHours) {
  return (Date.now() - mtime.getTime()) / 3600000 < maxHours;
}

async function _readJsonlFiltered(filePath, filterFn) {
  try {
    const stat = await fs.stat(filePath);
    if (!_isFresh(stat.mtime, 48)) return { fresh: false, mtime: stat.mtime, data: [] };
    const raw = await fs.readFile(filePath, 'utf8');
    const lines = raw.split('\n').filter(l => l.trim());
    const items = lines.map(l => { try { return JSON.parse(l); } catch (_) { return null; } }).filter(Boolean);
    return { fresh: true, mtime: stat.mtime, data: filterFn ? items.filter(filterFn) : items };
  } catch (_) { return { fresh: false, data: [] }; }
}

router.get('/m/api/world', async (req, res) => {
  try {
    const sectors = {};

    // ── A. 行业轮动（rotation_snapshot.json）──
    try {
      const stat = await fs.stat(ROTATION_PATH);
      if (_isFresh(stat.mtime, 48)) {
        const snap = JSON.parse(await fs.readFile(ROTATION_PATH, 'utf8'));
        const conf = snap.confidence || 0;
        const score = Math.min(100, Math.max(0, Math.round(conf)));
        const label = conf >= 60 ? '趋势明确' : conf >= 40 ? '震荡分化' : '混沌观望';
        const leadText = snap.lead_signals || '';
        const leadMatch = leadText.match(/领先板块[：:]\s*(.+)/);
        const topMomentum = leadMatch
          ? leadMatch[1].split(/[、，,]/).slice(0, 5).map(s => ({ name: s.trim(), direction: 'long', confidence: conf / 100 }))
          : [];
        sectors.sector_rotation = {
          label: '行业轮动',
          verdict: { score, label },
          llm_verdict: null,
          data_freshness: stat.mtime.toISOString(),
          top_momentum: topMomentum,
          catalyst: snap.catalyst || '',
          evidence: snap.evidence || '',
          watchlist: snap.watchlist || '',
          dimensions: {
            trend:        { score: conf >= 60 ? 70 : 40, label },
            capital_flow: { score: topMomentum.length > 0 ? 65 : 35, label: topMomentum.length > 0 ? '有资金方向' : '无明确方向' },
            sentiment:    { score: conf >= 50 ? 60 : 35, label: conf >= 50 ? '中性偏多' : '悲观' },
          },
        };
      }
    } catch (_) {}

    // ── B. 商品期货（upstream_signals.jsonl，只取 commodity_radar 策略信号）──
    const upResult = await _readJsonlFiltered(UPSTREAM_SIGNALS_PATH, s => s.strategy === 'commodity_radar');
    if (upResult.fresh && upResult.data.length > 0) {
      const assetMap = {};
      for (const s of upResult.data) {
        const a = s.asset;
        if (!assetMap[a] || s.timestamp > assetMap[a].timestamp) assetMap[a] = s;
      }
      const commodities = {};
      let longCount = 0, shortCount = 0;
      for (const [asset, s] of Object.entries(assetMap)) {
        const chg = (s.metadata || {}).chg_pct || 0;
        commodities[asset] = { symbol: (s.metadata || {}).symbol || asset, change_pct: chg, direction: s.direction, confidence: s.confidence };
        if (s.direction === 'long') longCount++; else shortCount++;
      }
      const total = longCount + shortCount;
      const crScore = total > 0 ? Math.round((longCount / total) * 100) : 50;
      const crLabel = crScore >= 70 ? '商品偏强' : crScore <= 30 ? '商品走弱' : '商品震荡';
      sectors.commodity = {
        label: '商品期货',
        verdict: { score: crScore, label: crLabel },
        llm_verdict: null,
        data_freshness: upResult.mtime ? upResult.mtime.toISOString() : null,
        commodities,
        dimensions: {
          trend:     { score: crScore, label: crLabel },
          sentiment: { score: crScore, label: longCount > shortCount ? '多头占优' : '空头占优' },
        },
      };
    }

    // ── C. 板块传导（transmission_signals.jsonl）──
    const txResult = await _readJsonlFiltered(TRANSMISSION_SIGNALS_PATH);
    if (txResult.fresh && txResult.data.length > 0) {
      const highConf = txResult.data.filter(s => s.confidence >= 0.8);
      const medConf  = txResult.data.filter(s => s.confidence >= 0.4 && s.confidence < 0.8);
      const activeSectors = [...new Set(txResult.data.map(s => (s.target || {}).name).filter(Boolean))].slice(0, 8);
      const topEvents = [...new Set(txResult.data.map(s => s.event_name).filter(Boolean))].slice(0, 3);
      sectors.transmission_summary = {
        label: '板块传导',
        data_freshness: txResult.mtime ? txResult.mtime.toISOString() : null,
        high_confidence_signals: highConf.length,
        medium_confidence_signals: medConf.length,
        active_sectors: activeSectors,
        top_events: topEvents,
      };
    }

    // ── D. ETF 行业（cycleradar categories → rotation_factor 信号）── V9.0
    try {
      const crCats = await signalsModel.getCycleradarCategories();
      const etfSignals = crCats.etf?.signals || crCats.etf || [];
      if (etfSignals.length > 0) {
        // 按 ETF 名称聚合策略信号
        const etfMap = {};
        for (const s of etfSignals) {
          const code = s.asset || s.code || '';
          // ETF 名称解析优先级：ETF_NAME_MAP > metadata.sector > metadata.stock_name > name > code
          const sectorName = (s.metadata || {}).sector;
          const name = ETF_NAME_MAP[code] || sectorName || (s.metadata || {}).stock_name || s.name || code;
          if (!name) continue;
          if (!etfMap[name]) {
            etfMap[name] = {
              name,
              code,
              signals: [],
              bestDirection: s.direction || 'neutral',
              bestConfidence: s.confidence || 0,
            };
          }
          etfMap[name].signals.push({
            strategy: s.strategy || 'rotation_factor',
            direction: s.direction || 'neutral',
            confidence: Math.round((s.confidence || 0) * 100),
          });
          if ((s.confidence || 0) > etfMap[name].bestConfidence) {
            etfMap[name].bestConfidence = s.confidence || 0;
            etfMap[name].bestDirection = s.direction || 'neutral';
          }
        }

        const etfList = Object.values(etfMap).sort((a, b) => b.bestConfidence - a.bestConfidence);
        const longCount = etfList.filter(e => e.bestDirection === 'long').length;
        const total = etfList.length;
        const etfRatio = total > 0 ? Math.round((longCount / total) * 100) : 50;
        const etfLabel = etfRatio >= 65 ? 'ETF偏多' : etfRatio <= 35 ? 'ETF偏空' : 'ETF分歧';

        sectors.etf = {
          label: 'ETF行业',
          verdict: { score: etfRatio, label: etfLabel },
          data_freshness: new Date().toISOString(),
          etf_count: etfList.length,
          long_ratio: etfRatio,
          top_etfs: etfList.slice(0, 8).map(e => ({
            name: e.name,
            code: e.code,
            direction: e.bestDirection,
            confidence: Math.round(e.bestConfidence * 100),
            signal_count: e.signals.length,
          })),
        };
      }
    } catch (_) { /* ETF 不可用时跳过 */ }

    const hasSectors = Object.keys(sectors).length > 0;
    return res.json({
      version: 'v8.5',
      generated_at: new Date().toISOString(),
      global_summary: null,
      sectors: hasSectors ? sectors : null,
    });
  } catch (err) {
    return res.status(200).json({ version: 'v8.5', sectors: null, global_summary: null, error: err.message });
  }
});

router.get('/m/api/strategy-report', async (req, res) => {
  try {
    // 档1: 单一源 —— 调 model globalWinRateByStrategy(), 不再自读文件/自算胜率
    const rows = await trackerModel.globalWinRateByStrategy();
    const strategies = rows.map(g => ({
      strategy: g.strategy,
      label: g.strategy,
      total_trades: g.total,
      wins: g.win,
      losses: g.lose,
      win_rate_pct: g.winRate == null ? 0 : g.winRate,          // 口径A: win/(win+lose)
      avg_return_pct: Math.round((g.avgReturn || 0) * 10000) / 100,
      best_return_pct: Math.round((g.bestReturn || 0) * 10000) / 100,
      worst_return_pct: Math.round((g.worstReturn || 0) * 10000) / 100,
      avg_max_dd_pct: g.avgMaxDd == null ? 0 : g.avgMaxDd,
      avg_holding_days: g.avgHoldingDays || 0,
      unique_stocks: g.uniqueStocks || 0,
      stars: g.stars || 1,
    }));
    // 按胜率排序
    strategies.sort((a, b) => b.win_rate_pct - a.win_rate_pct);

    // 全局统计 (档1: 基于 model rows 汇总, 口径A)
    const allTrades = rows.reduce((a, g) => a + g.total, 0);
    const totalWins = rows.reduce((a, g) => a + g.win, 0);
    const totalLosses = rows.reduce((a, g) => a + g.lose, 0);
    const overallDecided = totalWins + totalLosses;
    const overallWinRate = overallDecided > 0 ? Math.round((totalWins / overallDecided) * 100) : 0;
    const overallAvgReturn = 0; // 极值/均收益已在策略级提供, 全局均值上游多为空, 置0避免误导

    return res.json({
      version: 'v9.0',
      generated_at: new Date().toISOString(),
      period: await (async () => { // 档1: period 从 model 可用日期取
        try { const ds = await trackerModel.getAvailableDates(); return { from: ds.length ? ds[ds.length-1] : null, to: ds.length ? ds[0] : null }; }
        catch (_) { return { from: null, to: null }; }
      })(),
      overall: {
        total_trades: allTrades,
        win_rate_pct: overallWinRate,
        avg_return_pct: Math.round(overallAvgReturn * 10000) / 100,
      },
      strategies,
    });
  } catch (err) {
    return res.status(200).json({ version: 'v9.0', strategies: [], error: err.message });
  }
});

// ── /m/api/graph ── V8.2 传导图谱可视化（事件→板块→个股级联）──
const GRAPH_BRIDGE_PATH = path.join(__dirname, '..', '..', 'core', 'graph', 'api_bridge.py');
router.get('/m/api/graph', async (req, res) => {
  try {
    const eventId = req.query.event_id || '';
    const python3 = 'python3.9';
    const cmd = `${python3} ${GRAPH_BRIDGE_PATH} ${eventId}`.trim();
    const stdout = execSync(cmd, { timeout: 10000, cwd: path.join(__dirname, '..', '..') });
    const data = JSON.parse(stdout.toString());
    return res.json(data);
  } catch (err) {
    return res.status(200).json({ error: '传导数据暂不可用', detail: err.message });
  }
});

// ── /m/api/transmission-summary ── V8.3 信号 Tab 传导摘要（BFS top-3 跨事件最强路径）──
router.get('/m/api/transmission-summary', async (req, res) => {
  try {
    const n = parseInt(req.query.n) || 3;
    const python3 = 'python3.9';
    const cmd = `${python3} ${GRAPH_BRIDGE_PATH} --top-paths ${n}`;
    const stdout = execSync(cmd, { timeout: 15000, cwd: path.join(__dirname, '..', '..') });
    const data = JSON.parse(stdout.toString());
    return res.json(data);
  } catch (err) {
    return res.status(200).json({ error: '传导摘要暂不可用', detail: err.message, top_paths: [] });
  }
});

// ── /m/api/watchlist-tiers ── V8.3 C: 自选池 tier 分级摘要（信号 Tab 嵌入）──
router.get('/m/api/watchlist-tiers', async (req, res) => {
  try {
    const WATCHLIST_SIGNALS_PATH = path.join(__dirname, '..', '..', 'data', 'watchlist_signals.json');
    if (!fsSync.existsSync(WATCHLIST_SIGNALS_PATH)) {
      return res.json({ tiers: {}, distribution: [] });
    }
    const raw = fsSync.readFileSync(WATCHLIST_SIGNALS_PATH, 'utf-8');
    const data = JSON.parse(raw);
    const signals = data.signals || [];

    const tierMap = {};
    for (const s of signals) {
      const t = s.tier || '未分级';
      if (!tierMap[t]) tierMap[t] = [];
      tierMap[t].push({
        code: s.code,
        name: s.name,
        score: s.score || 0,
        resonance_count: s.resonance_count || 0,
        signal_sources: s.signal_sources || [],
      });
    }

    // 对 T1/T2 按 score 降序排列（仅展示前 5 详细）
    for (const tierKey of Object.keys(tierMap)) {
      tierMap[tierKey].sort((a, b) => b.score - a.score);
      if (tierKey.startsWith('T1') || tierKey.startsWith('T2')) {
        tierMap[tierKey] = tierMap[tierKey].slice(0, 5);
      } else {
        // T3/T4 只存数量
        tierMap[tierKey] = { count: tierMap[tierKey].length, stocks: [] };
      }
    }

    // 确保所有 4 个 tier 都在
    const order = ['T1·强推', 'T2·关注', 'T3·观察', 'T4·冷门'];
    const clean = {};
    for (const t of order) {
      if (tierMap[t]) {
        clean[t] = Array.isArray(tierMap[t]) ? { count: tierMap[t].length, stocks: tierMap[t] } : tierMap[t];
      } else {
        clean[t] = { count: 0, stocks: [] };
      }
    }

    const distribution = order.map(t => ({ tier: t, count: clean[t].count }));

    return res.json({
      updated: data.last_updated || '',
      tiers: clean,
      distribution: distribution,
    });
  } catch (err) {
    return res.status(200).json({ tiers: {}, distribution: [] });
  }
});

// ── /m/api/resonance ── P0: 跨策略共振信号（stock_agent × cycleradar alpha/etf/commodity × sector rotation）──
// V9.0: 多策略底层信号交叉验证，找出 ≥2 策略同时看多/看空的标的
// 数据源：
//   - trader_strategy.jsonl → stock_agent signals (latest date only)
//   - cycleradar categories → alpha / etf / commodity signals (from signalsModel)
//   - rotation_snapshot.json → sector momentum keywords for sector-context boost

// 策略名称→中文缩写映射（与前端 app.js V7.1 STRAT_LABEL 对齐）
const RES_STRAT_LABEL = {
  'stock_agent': 'AI',
  'scanner': '形态',
  'wanjun_models': '量化',
  'report_agent': '事件',
  'rotation_factor': '轮动',
  'ma_signals': '并购',
  'commodity_radar': '商品',
};

// 策略权重（用于 resonance_score 加总）
const RES_STRAT_WEIGHT = {
  'stock_agent': 1.0,
  'scanner': 1.2,
  'wanjun_models': 1.1,
  'report_agent': 1.4,
  'rotation_factor': 1.2,
  'ma_signals': 1.3,
};

router.get('/m/api/resonance', async (req, res) => {
  try {
    // ── 1. 读 stock_agent latest signals ──
    let saSignals = [];
    try {
      const sa = await strategyModel.getLatestStrategy();
      if (sa && sa.stocks) {
        saSignals = sa.stocks.filter(s => s.signal_type && (s.signal_type.includes('买入') || s.signal_type.includes('进攻')));
      }
    } catch (_) { /* stock_agent 不可用时降级 */ }

    // ── 2. 读 cycleradar categories ──
    let crCats = { alpha: [], etf: [], commodity: [] };
    try {
      crCats = await signalsModel.getCycleradarCategories();
    } catch (_) { /* cycleradar 不可用时降级 */ }

    // ── 3. 读 rotation snapshot（sector momentum 上下文）──
    let rotationCtx = null;
    try {
      const rotRaw = await fs.readFile(ROTATION_PATH, 'utf8');
      rotationCtx = JSON.parse(rotRaw);
    } catch (_) {}

    // ── 4. 按标的名称聚合所有信号 ──
    const byName = {};
    // 多源映射：code ↔ name，处理不同数据源用不同字段的情况
    const codeToName = {};  // 600519 → 贵州茅台
    const nameToCode = {};  // 贵州茅台 → 600519

    const registerStock = (code, name) => {
      if (code && name && name !== code) {
        if (!codeToName[code]) codeToName[code] = name;
        if (!nameToCode[name]) nameToCode[name] = code;
      }
    };

    // stock_agent signals: grouped by code (primary), augmented with name
    for (const s of saSignals) {
      const code = s.code || '';
      const stockName = s.name || s.code;
      if (!code) continue;
      registerStock(code, stockName);
      const key = code;  // use code as primary key for stock_agent
      if (!byName[key]) byName[key] = { name: stockName, code, strategies: {}, signalCount: 0, totalConfidence: 0 };
      const c = Math.min(1, (s.score || 0) / 100);  // normalize score to 0-1
      byName[key].strategies['stock_agent'] = {
        label: RES_STRAT_LABEL['stock_agent'],
        direction: (s.signal_type || '').includes('看空') ? 'short' : 'long',
        confidence: c,
        score: s.score || 0,
        detail: s.signal_type || '',
      };
      byName[key].signalCount++;
      byName[key].totalConfidence += c;
      // keep name fresh
      if (stockName && stockName !== byName[key].name) byName[key].name = stockName;
    }

    // alpha signals: scanner/wanjun_models/report_agent/ma_signals
    const alphaSignals = crCats.alpha?.signals || crCats.alpha || [];
    for (const s of alphaSignals) {
      const asset = s.asset || '';
      const stockName = (s.metadata || {}).stock_name || '';
      const code = s.code || asset;
      const stratKey = s.strategy || 'unknown';
      if (!asset && !code) continue;

      // Resolve name: prefer metadata.stock_name > name > asset
      const resolvedName = stockName || s.name || asset;
      registerStock(code, resolvedName);

      // Group by code if available, else by resolvedName
      const key = code || resolvedName;
      if (!byName[key]) byName[key] = { name: resolvedName, code: code || asset, strategies: {}, signalCount: 0, totalConfidence: 0 };
      if (resolvedName && resolvedName !== byName[key].name) byName[key].name = resolvedName;

      const c = (s.confidence || 0);
      if (!byName[key].strategies[stratKey]) {
        byName[key].strategies[stratKey] = {
          label: RES_STRAT_LABEL[stratKey] || stratKey,
          direction: s.direction || 'long',
          confidence: c,
          score: Math.round(c * 100),
          detail: resolvedName || code,
        };
        byName[key].signalCount++;
        byName[key].totalConfidence += c;
      } else if (c > byName[key].strategies[stratKey].confidence) {
        byName[key].totalConfidence += (c - byName[key].strategies[stratKey].confidence);
        byName[key].strategies[stratKey].confidence = c;
        byName[key].strategies[stratKey].score = Math.round(c * 100);
      }
    }

    // ETF signals (rotation_factor / sector rotation)
    const etfSignals = crCats.etf?.signals || crCats.etf || [];
    for (const s of etfSignals) {
      const asset = s.asset || '';
      const code = s.code || asset;
      const stratKey = s.strategy || 'rotation_factor';
      if (!asset && !code) continue;
      const resolvedName = ETF_NAME_MAP[asset] || s.name || asset;
      registerStock(code || asset, resolvedName);
      const key = code || asset;
      if (!byName[key]) byName[key] = { name: resolvedName, code: code || asset, strategies: {}, signalCount: 0, totalConfidence: 0 };
      const c = (s.confidence || 0);
      if (!byName[key].strategies[stratKey]) {
        byName[key].strategies[stratKey] = {
          label: RES_STRAT_LABEL[stratKey] || stratKey,
          direction: s.direction || 'long',
          confidence: c,
          score: Math.round(c * 100),
          detail: 'ETF ' + (asset || ''),
        };
        byName[key].signalCount++;
        byName[key].totalConfidence += c;
      }
    }

    // ── 4b. Cross-reference: merge records that share same code or name ──
    const mergeRecords = [];
    const processed = new Set();
    for (const [key, rec] of Object.entries(byName)) {
      if (processed.has(key)) continue;
      // Find all related keys (same code or same name)
      const related = [key];
      const altCode = rec.code === key ? null : rec.code;
      const altName = rec.name !== key ? rec.name : null;
      for (const [otherKey, otherRec] of Object.entries(byName)) {
        if (otherKey === key || processed.has(otherKey)) continue;
        if (otherKey === altCode || otherKey === altName ||
            (altName && otherRec.name === altName) ||
            (altCode && otherRec.code === altCode)) {
          related.push(otherKey);
          processed.add(otherKey);
        }
      }
      processed.add(key);
      if (related.length > 1) {
        // Merge: combine strategies from all related records
        const merged = { ...rec, strategies: { ...rec.strategies }, mergedFrom: related.slice(1) };
        for (const relKey of related.slice(1)) {
          const rel = byName[relKey];
          if (!rel) continue;
          for (const [sk, sv] of Object.entries(rel.strategies)) {
            if (!merged.strategies[sk]) {
              merged.strategies[sk] = sv;
              merged.signalCount++;
              merged.totalConfidence += sv.confidence;
            } else if (sv.confidence > merged.strategies[sk].confidence) {
              merged.totalConfidence += (sv.confidence - merged.strategies[sk].confidence);
              merged.strategies[sk] = sv;
            }
          }
          if (rel.name && rel.name !== rel.code && rel.name !== merged.name) merged.name = rel.name;
          if (rel.code && rel.code !== merged.code) merged.code = rel.code;
        }
        mergeRecords.push(merged);
      } else {
        mergeRecords.push(rec);
      }
    }
    // Replace byName with merged records
    const mergedByName = {};
    for (const r of mergeRecords) {
      if (r.code && r.name) mergedByName[r.code] = r;
      else mergedByName[r.name || r.code] = r;
    }

    // ── 5. Sector momentum boost（rotation snapshot keywords match）──
    const sectorKw = rotationCtx
      ? ((rotationCtx.lead_signals || '') + ' ' + (rotationCtx.catalyst || ''))
          .replace(/领先板块[：:]/g, '').trim()
      : '';
    const kwList = sectorKw.split(/[、，,\s]+/).filter(Boolean);

    // ── 6. Compute resonance score & filter (≥2 strategies) ──
    const resonances = Object.values(mergedByName)
      .filter(v => Object.keys(v.strategies).length >= 2)  // 至少 2 个策略
      .map(v => {
        const strats = Object.entries(v.strategies);
        const uniqueStrats = strats.length;
        const avgConf = v.signalCount > 0 ? v.totalConfidence / v.signalCount : 0;

        // base resonance score = sum of individual strategy weights + confidence
        const rawScore = strats.reduce((sum, [key, val]) => {
          return sum + (RES_STRAT_WEIGHT[key] || 1.0) * val.confidence;
        }, 0);

        // sector momentum bonus: if stock name matches rotation keywords
        let sectorBonus = 0;
        for (const kw of kwList) {
          if (kw && v.name.includes(kw)) { sectorBonus = 0.15; break; }
        }

        // diversity bonus: each additional strategy beyond 2 adds 0.1
        const diversityBonus = Math.max(0, (uniqueStrats - 2) * 0.1);

        const resonanceScore = Math.min(1.0, (rawScore / uniqueStrats) * 0.85 + sectorBonus + diversityBonus);

        // Check direction consensus
        const longs = strats.filter(([_, s]) => s.direction === 'long').length;
        const shorts = strats.filter(([_, s]) => s.direction === 'short').length;
        const hasConsensus = longs === uniqueStrats || shorts === uniqueStrats;
        const consensusDir = longs >= shorts ? 'long' : 'short';

        return {
          name: v.name,
          code: v.code,
          resonance_score: Math.round(resonanceScore * 100),
          strategy_count: uniqueStrats,
          avg_confidence: Math.round(avgConf * 100),
          direction_consensus: hasConsensus,
          consensus_direction: consensusDir,
          strategies: strats.map(([key, val]) => ({
            key,
            label: val.label,
            direction: val.direction,
            confidence: Math.round(val.confidence * 100),
            score: val.score,
            detail: val.detail,
          })),
          sector_boost: sectorBonus > 0,
        };
      })
      .sort((a, b) => b.resonance_score - a.resonance_score);

    return res.json({
      version: 'v9.0',
      generated_at: new Date().toISOString(),
      total_resonances: resonances.length,
      input_counts: {
        stock_agent: saSignals.length,
        alpha: (crCats.alpha || []).length,
        etf: (crCats.etf || []).length,
        commodity: (crCats.commodity || []).length,
      },
      resonance_list: resonances.slice(0, 15),
      sector_keywords: kwList.slice(0, 5),
    });
  } catch (err) {
    return res.status(200).json({ version: 'v9.0', error: String(err), total_resonances: 0, resonance_list: [] });
  }
});

module.exports = router;

// ── GET /m/api/pulse ── 统一市场脉搏 (V10.0) ──
router.get('/m/api/pulse', async (req, res) => {
  try {
    const raw = await fs.readFile(
      path.join(__dirname, '..', '..', 'data', 'pulse_latest.json'), 'utf-8'
    );
    res.json(JSON.parse(raw));
  } catch (err) {
    res.status(200).json({ error: String(err), verdict: '不可用' });
  }
});

// ── GET /m/api/health ── 管线健康 (V10.0) ──
router.get('/m/api/health', async (req, res) => {
  try {
    const raw = await fs.readFile(
      path.join(__dirname, '..', 'health', 'summary_latest.json'), 'utf-8'
    );
    res.json(JSON.parse(raw));
  } catch (err) {
    // fallback to pipeline health script
    try {
      execSync('python3.9 /opt/cycleradar-trader/scripts/check_pipeline_health.py', { timeout: 15000 });
      const raw2 = await fs.readFile(
        path.join(__dirname, '..', 'health', 'summary_latest.json'), 'utf-8'
      );
      return res.json(JSON.parse(raw2));
    } catch (e2) {
      res.status(200).json({ overall: 'UNKNOWN', error: String(err) });
    }
  }
});
