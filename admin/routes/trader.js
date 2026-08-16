'use strict';

const express = require('express');
const fs = require('fs/promises');
const path = require('path');

const router = express.Router();

const strategyModel = require('../models/trader-strategy');
const trackerModel = require('../models/trader-tracker');
const backtestModel = require('../models/trader-backtest');
const watchlistModel = require('../models/watchlist');
const drawdownModel = require('../models/drawdown');
const signalsModel = require('../models/signals');

const TIMING_PATH = path.join(__dirname, '..', '..', 'data', 'timing_history.json');
// V7.6: positions 归位项目 data/（解耦 ~/交易员/ 冻结目录）
const POSITIONS_PATH = path.join(__dirname, '..', '..', 'data', 'positions.json');

const ROTATION_PATH = path.join(__dirname, '..', '..', 'data', 'rotation_snapshot.json');

// ── /admin/trader ── 工作台首页：概览仪表盘 ──
router.get('/trader', async (req, res) => {
  try {
    const [strategyDateList, latestStrategy, trackerSummary, backtestReports, globalWinRates] = await Promise.all([
      strategyModel.getAvailableDates(),
      strategyModel.getLatestStrategy(),
      trackerModel.getTrackerSummary(),
      backtestModel.listReports(),
      trackerModel.globalWinRateByStrategy(),
    ]);

    // 市场体温数据
    let timing = null;
    try {
      const raw = await fs.readFile(TIMING_PATH, 'utf8');
      timing = JSON.parse(raw);
    } catch (_) { /* optional */ }

    // 账户快照
    let account = null;
    try {
      const raw = await fs.readFile(POSITIONS_PATH, 'utf8');
      account = JSON.parse(raw);
    } catch (_) { /* optional */ }

    // 今日运营摘要：各管线日志最近修改时间
    const DATA_DIR = path.join(__dirname, '..', '..', 'data');
    const LOGS_DIR = path.join(DATA_DIR, 'logs');
    const today = new Date().toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai'}).replace(/\//g,'-');
    async function _logStatus(pattern) {
      try {
        const files = await fs.readdir(LOGS_DIR);
        const matched = files.filter(f => f.includes(pattern)).sort().reverse();
        if (!matched.length) return { ran: false };
        const stat = await fs.stat(path.join(LOGS_DIR, matched[0]));
        const ranToday = stat.mtime.toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai'}).replace(/\//g,'-') === today;
        return { ran: ranToday, file: matched[0], mtime: stat.mtime.toISOString() };
      } catch { return { ran: false }; }
    }
    const [scannerLog, maLog, reflectionLog, stockAgentLog] = await Promise.all([
      _logStatus('scanner_signals_cron'),
      _logStatus('ma_signals_cron'),
      _logStatus('strategy_reflection_cron'),
      _logStatus('stock_agent_cron'),
    ]);

    // V7.9: 概览页也展示策略反思摘要
    let llmReflection = null;
    try {
      const reflRaw = await fs.readFile(path.join(DATA_DIR, 'strategy_reflection.json'), 'utf8');
      llmReflection = JSON.parse(reflRaw);
    } catch (_) { /* optional */ }
    const ops = {
      scanner:    { label: 'Scanner 14模型', ...scannerLog },
      ma_signals: { label: '兼并重组信号',   ...maLog },
      reflection: { label: 'LLM策略反思',    ...reflectionLog },
      stock_agent:{ label: 'Stock Agent',    ...stockAgentLog },
    };

    // V8.0: 首页也读取事件主线（去重后 top3）
    const NARRATIVE_PATH_IDX = path.join(__dirname, '..', '..', 'data', 'event_narrative_latest.json');
    let indexNarrativeEvents = [];
    try {
      const d = JSON.parse(await fs.readFile(NARRATIVE_PATH_IDX, 'utf8'));
      const raw = d.events || [];
      const seenPrimary = new Set();
      for (const ev of raw.sort((a, b) => (a.rank || 99) - (b.rank || 99))) {
        const sm = ev.stock_mapping || [];
        const primaryCode = sm[0] ? sm[0].code : null;
        if (primaryCode && seenPrimary.has(primaryCode)) continue;
        if (primaryCode) seenPrimary.add(primaryCode);
        indexNarrativeEvents.push({
          rank: ev.rank,
          title: (ev.title || '').slice(0, 55),
          trading_window: ev.trading_window || '',
          stock_mapping: (ev.stock_mapping || []).slice(0, 3).map(s => ({
            code: s.code, name: s.name, type: s.type,
          })),
        });
        if (indexNarrativeEvents.length >= 4) break;
      }
    } catch (_) {}

    res.render('trader/index', {
      title: '交易员工作台',
      active: 'trader',
      subTab: 'overview',
      strategyDateList,
      latestStrategy,
      trackerSummary,
      backtestReports,
      timing,
      account,
      globalWinRates,
      ops,
      llmReflection,
      narrativeEvents: indexNarrativeEvents,
      error: null,
    });
  } catch (error) {
    res.status(500).render('admin/error', {
      title: '500 服务器错误',
      status: 500,
      active: 'trader',
      message: '交易员数据加载失败',
      error,
    });
  }
});

// ── /admin/trader/strategy ── 自选池诊断 ──
router.get('/trader/strategy', async (req, res) => {
  try {
    const dateParam = req.query.date;
    const strategyDateList = await strategyModel.getAvailableDates();
    // V7.6 融合缺口补齐：模型胜率排名（min_backtest.py 输出 data/backtest_winrate.json）
    const winrateRanking = await backtestModel.getWinrateRanking();

    // V8.0: 读取事件叙事，传给模板做关联展示
    const NARRATIVE_PATH = path.join(__dirname, '..', '..', 'data', 'event_narrative_latest.json');
    let narrativeEvents = [];
    try {
      const d = JSON.parse(await fs.readFile(NARRATIVE_PATH, 'utf8'));
      const raw = d.events || [];
      // 按核心股票去重（取 rank 最小的）
      const seenPrimary = new Set();
      for (const ev of raw.sort((a, b) => (a.rank || 99) - (b.rank || 99))) {
        const sm = ev.stock_mapping || [];
        const primaryCode = sm[0] ? sm[0].code : null;
        if (primaryCode && seenPrimary.has(primaryCode)) continue;
        if (primaryCode) seenPrimary.add(primaryCode);
        narrativeEvents.push({
          rank: ev.rank,
          title: (ev.title || '').slice(0, 60),
          trading_window: ev.trading_window || '',
          stock_mapping: (ev.stock_mapping || []).slice(0, 4).map(s => ({
            code: s.code,
            name: s.name,
            type: s.type,
          })),
        });
      }
    } catch (_) {}

    if (strategyDateList.length === 0) {
      return res.render('trader/strategy', {
        title: '自选池诊断',
        active: 'trader',
        subTab: 'strategy',
        strategyDateList: [],
        currentDate: null,
        strategy: null,
        winrateRanking,
        narrativeEvents,
        error: '暂无策略数据，请先在交易员端运行 strategy.py 生成策略日志。',
      });
    }

    const currentDate = dateParam || strategyDateList[0];
    const strategy = await strategyModel.getStrategyByDate(currentDate);

    res.render('trader/strategy', {
      title: '自选池诊断',
      active: 'trader',
      subTab: 'strategy',
      strategyDateList,
      currentDate,
      strategy,
      winrateRanking,
      narrativeEvents,
      error: strategy ? null : `日期 ${currentDate} 无数据`,
    });
  } catch (error) {
    res.status(500).render('admin/error', {
      title: '500 服务器错误',
      status: 500,
      active: 'trader',
      message: '策略诊断加载失败',
      error,
    });
  }
});

// ── /admin/trader/tracker ── 信号跟踪 ──
router.get('/trader/tracker', async (req, res) => {
  try {
    const dateParam = req.query.date;
    const horizonParam = parseInt(req.query.horizon) || 5;
    const [trackerSummary, globalWinRates] = await Promise.all([
      trackerModel.getTrackerSummary(),
      trackerModel.globalWinRateByStrategy(),
    ]);

    if (!trackerSummary) {
      return res.render('trader/tracker', {
        title: '信号跟踪',
        active: 'trader',
        subTab: 'tracker',
        trackerSummary: null,
        currentDate: null,
        currentHorizon: horizonParam,
        records: [],
        globalWinRates: [],
        error: '暂无跟踪数据',
      });
    }

    const currentDate = dateParam || trackerSummary.latestDate;
    const records = await trackerModel.getTrackerByDateHorizon(currentDate, horizonParam);

    res.render('trader/tracker', {
      title: '信号跟踪',
      active: 'trader',
      subTab: 'tracker',
      trackerSummary,
      currentDate,
      currentHorizon: horizonParam,
      records,
      globalWinRates,
      error: null,
    });
  } catch (error) {
    res.status(500).render('admin/error', {
      title: '500 服务器错误',
      status: 500,
      active: 'trader',
      message: '信号跟踪加载失败',
      error,
    });
  }
});

// ── /admin/trader/tracker/:code ── 个股跟踪历史 ──
router.get('/trader/tracker/stock/:code', async (req, res) => {
  try {
    const { code } = req.params;
    const stockRecords = await trackerModel.getStockTrackingHistory(code);

    if (stockRecords.length === 0) {
      return res.render('trader/stock-tracker', {
        title: `个股跟踪 — ${code}`,
        active: 'trader',
        subTab: 'tracker',
        code,
        name: code,
        records: [],
        error: `股票 ${code} 暂无跟踪记录`,
      });
    }

    res.render('trader/stock-tracker', {
      title: `个股跟踪 — ${stockRecords[0].name || code}`,
      active: 'trader',
      subTab: 'tracker',
      code,
      name: stockRecords[0].name || code,
      records: stockRecords,
      error: null,
    });
  } catch (error) {
    res.status(500).render('admin/error', {
      title: '500 服务器错误',
      status: 500,
      active: 'trader',
      message: '个股跟踪数据加载失败',
      error,
    });
  }
});

// ── /admin/trader/backtest ── 回测报告 ──
router.get('/trader/backtest', async (req, res) => {
  try {
    const reports = await backtestModel.listReports();

    res.render('trader/backtest', {
      title: '策略回测',
      active: 'trader',
      subTab: 'backtest',
      reports,
      error: null,
    });
  } catch (error) {
    res.status(500).render('admin/error', {
      title: '500 服务器错误',
      status: 500,
      active: 'trader',
      message: '回测报告加载失败',
      error,
    });
  }
});

// ── /admin/trader/backtest/:filename ── 查看回测报告内容 ──
router.get('/trader/backtest/view/:filename', async (req, res) => {
  try {
    const { filename } = req.params;
    const html = await backtestModel.readReport(filename);
    if (!html) {
      return res.status(404).render('admin/error', {
        title: '404 报告未找到',
        status: 404,
        active: 'trader',
        message: `回测报告 ${filename} 不存在`,
      });
    }
    res.send(html);
  } catch (error) {
    res.status(500).render('admin/error', {
      title: '500 服务器错误',
      status: 500,
      active: 'trader',
      message: '回测报告读取失败',
      error,
    });
  }
});

// ── /admin/trader/model-library ── 策略模型库 ──
router.get('/trader/model-library', async (req, res) => {
  try {
    const SIGNALS_PATH = path.join(__dirname, '..', '..', 'data', 'upstream_signals.jsonl');

    // 从 CONTEXT.md 策略表提取的描述（硬编码，避免解析 md）
    const modelDescriptions = {
      report_agent:       '事件驱动 LLM 推股（Pipeline A 主 alpha 源），含 entry/target/stop/thesis 完整投资链',
      stock_agent:        '个股 AI 筛选（催化剂+资金+共振），Pipeline B fallback',
      ma_signals:         '并购重组事件驱动',
      wanjun_models:      'V6.1: 万军选股模型 2/8/10（wanjun_screener.py → upstream_signals.jsonl）',
      scanner:            'V6.2: scanner 14 模型全量信号（scanner.py → scanner_adapter → upstream_signals.jsonl）',
      rotation_factor:    '行业轮动因子，带 ETF 代码',
      commodity_radar:    '原油/铜/黄金/白银/铁矿方向信号',
    };

    // 读取上游信号，按策略分桶统计
    const strategyStats = {};
    const STRATEGY_CATEGORY_MAP = signalsModel.STRATEGY_CATEGORY_MAP;
    for (const key of Object.keys(STRATEGY_CATEGORY_MAP)) {
      strategyStats[key] = { total: 0, active: 0, latestDay: 0 };
    }

    let allSignals = [];
    try {
      const raw = await fs.readFile(SIGNALS_PATH, 'utf8');
      allSignals = raw.trim().split('\n').filter(Boolean).map(line => {
        try { return JSON.parse(line); } catch (_) { return null; }
      }).filter(Boolean);
    } catch (_) { /* optional */ }

    const now = new Date().toISOString();
    const today = now.slice(0, 10);
    for (const sig of allSignals) {
      const s = sig.strategy || '';
      if (!strategyStats[s]) strategyStats[s] = { total: 0, active: 0, latestDay: 0 };
      strategyStats[s].total++;
      if (!sig.expiry || sig.expiry >= now) {
        strategyStats[s].active++;
      }
      if (sig.timestamp && sig.timestamp.slice(0, 10) === today) {
        strategyStats[s].latestDay++;
      }
    }

    // 构建模型卡片数组
    const models = Object.keys(STRATEGY_CATEGORY_MAP).map(key => ({
      key,
      category: STRATEGY_CATEGORY_MAP[key],
      description: modelDescriptions[key] || '—',
      total: strategyStats[key]?.total || 0,
      active: strategyStats[key]?.active || 0,
      today: strategyStats[key]?.latestDay || 0,
    })).sort((a, b) => b.total - a.total);

    res.render('trader/model-library', {
      title: '策略模型库',
      active: 'trader',
      subTab: 'model-library',
      models,
      error: null,
    });
  } catch (error) {
    res.status(500).render('admin/error', {
      title: '500 服务器错误',
      status: 500,
      active: 'trader',
      message: '策略模型库加载失败',
      error,
    });
  }
});

// ── /admin/trader/watchlist ── 自选股管理 ──
router.get('/trader/watchlist', async (req, res) => {
  try {
    const stocks = await watchlistModel.getAll();
    // V7.9: 同时读 watchlist_signals.json（64只，含 NX + P&L）
    let wlSignals = [];
    try {
      const DATA_DIR = path.join(__dirname, '..', '..', 'data');
      const raw = await fs.readFile(path.join(DATA_DIR, 'watchlist_signals.json'), 'utf8');
      const parsed = JSON.parse(raw);
      wlSignals = Array.isArray(parsed) ? parsed : (parsed.signals || []);
      // V7.9 M1: 合并 watchlist.json 的 added_at → hold_days（持仓天数，用于止损评分）
      const wlRaw = await fs.readFile(path.join(DATA_DIR, 'watchlist.json'), 'utf8').catch(() => null);
      if (wlRaw) {
        const wlData = JSON.parse(wlRaw);
        const wlStocks = wlData.stocks || [];
        const addedMap = {};
        for (const s of wlStocks) addedMap[s.code] = s.added_at || null;
        const today = new Date();
        wlSignals = wlSignals.map(s => {
          const addedAt = addedMap[s.code];
          let holdDays = null;
          if (addedAt) {
            const diff = today - new Date(addedAt);
            holdDays = Math.floor(diff / (1000 * 60 * 60 * 24));
          }
          return Object.assign({}, s, { hold_days: holdDays });
        });
      }
    } catch (_) { /* optional */ }
    res.render('trader/watchlist', {
      title: '自选股管理',
      active: 'trader',
      subTab: 'watchlist',
      stocks,
      wlSignals,
      error: null,
      success: req.query.success || null,
    });
  } catch (error) {
    res.status(500).render('admin/error', {
      title: '500 服务器错误',
      status: 500,
      active: 'trader',
      message: '自选股数据加载失败',
      error,
    });
  }
});

router.post('/trader/watchlist', async (req, res) => {
  try {
    const { code, name, notes } = req.body;
    if (!code || !name) {
      const stocks = await watchlistModel.getAll();
      return res.render('trader/watchlist', {
        title: '自选股管理',
        active: 'trader',
        subTab: 'watchlist',
        stocks,
        error: '代码和名称不能为空',
        success: null,
      });
    }
    const result = await watchlistModel.add({ code: code.trim(), name: name.trim(), notes: (notes || '').trim() });
    if (!result.added) {
      const stocks = await watchlistModel.getAll();
      return res.render('trader/watchlist', {
        title: '自选股管理',
        active: 'trader',
        subTab: 'watchlist',
        stocks,
        error: result.reason,
        success: null,
      });
    }
    res.redirect('/admin/trader/watchlist?success=' + encodeURIComponent(`已添加 ${code} ${name}`));
  } catch (error) {
    res.status(500).render('admin/error', {
      title: '500 服务器错误',
      status: 500,
      active: 'trader',
      message: '添加自选股失败',
      error,
    });
  }
});

router.post('/trader/watchlist/delete', async (req, res) => {
  try {
    const { code } = req.body;
    const result = await watchlistModel.remove(code);
    if (!result.removed) {
      const stocks = await watchlistModel.getAll();
      return res.render('trader/watchlist', {
        title: '自选股管理',
        active: 'trader',
        subTab: 'watchlist',
        stocks,
        error: result.reason,
        success: null,
      });
    }
    res.redirect('/admin/trader/watchlist?success=' + encodeURIComponent(`已移除 ${code}`));
  } catch (error) {
    res.status(500).render('admin/error', {
      title: '500 服务器错误',
      status: 500,
      active: 'trader',
      message: '移除自选股失败',
      error,
    });
  }
});

// ── POST /admin/trader/watchlist/import ── 自选股批量导入（CSV/JSON） ──
router.post('/trader/watchlist/import', async (req, res) => {
  try {
    const { data, format } = req.body;
    if (!data || !data.trim()) {
      const stocks = await watchlistModel.getAll();
      return res.render('trader/watchlist', {
        title: '自选股管理',
        active: 'trader',
        subTab: 'watchlist',
        stocks,
        error: '导入数据不能为空',
        success: null,
      });
    }

    let parsed = [];
    if (format === 'json') {
      try {
        parsed = JSON.parse(data);
        if (!Array.isArray(parsed)) {
          throw new Error('JSON 格式要求数组');
        }
      } catch (e) {
        const stocks = await watchlistModel.getAll();
        return res.render('trader/watchlist', {
          title: '自选股管理',
          active: 'trader',
          subTab: 'watchlist',
          stocks,
          error: 'JSON 格式错误：' + e.message,
          success: null,
        });
      }
    } else {
      // CSV: 代码,名称,备注（每行一个）
      const lines = data.trim().split('\n');
      for (const line of lines) {
        const parts = line.split(',').map(s => s.trim());
        if (parts.length >= 1 && parts[0]) {
          parsed.push({
            code: parts[0],
            name: parts[1] || '',
            notes: parts[2] || '',
          });
        }
      }
    }

    let added = 0, skipped = 0;
    for (const item of parsed) {
      if (!item.code) continue;
      const result = await watchlistModel.add({
        code: item.code.trim(),
        name: (item.name || '').trim(),
        notes: (item.notes || '').trim(),
      });
      if (result.added) added++; else skipped++;
    }

    const stocks = await watchlistModel.getAll();
    const msg = `批量导入完成：成功 ${added} 只，跳过 ${skipped} 只（已存在）`;
    res.render('trader/watchlist', {
      title: '自选股管理',
      active: 'trader',
      subTab: 'watchlist',
      stocks,
      error: null,
      success: msg,
    });
  } catch (error) {
    const stocks = await watchlistModel.getAll();
    res.render('trader/watchlist', {
      title: '自选股管理',
      active: 'trader',
      subTab: 'watchlist',
      stocks,
      error: '导入失败：' + error.message,
      success: null,
    });
  }
});

// ── /admin/trader/drawdown ── 回撤统计（双池：自动选股 + 自选股） ──
router.get('/trader/drawdown', async (req, res) => {
  try {
    const report = await drawdownModel.buildDrawdownReport();
    res.render('trader/drawdown', {
      title: '回撤统计',
      active: 'trader',
      subTab: 'drawdown',
      report,
      error: null,
    });
  } catch (error) {
    res.status(500).render('admin/error', {
      title: '500 服务器错误',
      status: 500,
      active: 'trader',
      message: '回撤统计数据加载失败',
      error,
    });
  }
});

// ── /admin/trader/reflection ── 策略反思（LLM 反思 + tracker_reflection + scanner + A1 胜率矩阵） ──
router.get('/trader/reflection', async (req, res) => {
  try {
    // 读取 LLM 策略反思（generate_strategy_reflection.py 产出）
    const LLM_REFLECTION_PATH = path.join(__dirname, '..', '..', 'data', 'strategy_reflection.json');
    let llmReflection = null;
    try {
      const raw = await fs.readFile(LLM_REFLECTION_PATH, 'utf8');
      llmReflection = JSON.parse(raw);
    } catch (_) { /* optional — 首次部署前文件可能不存在 */ }

    // V9.8 fix: 旧 tracker_log.jsonl(5-6月/WIN-LOSE枚举/177条)已废弃，改读活文件 trader_tracker.jsonl，与 tracker 页口径统一
    const REFLECTION_PATH = path.join(__dirname, '..', '..', 'data', 'trader_tracker.jsonl');
    let reflections = [];
    try {
      const raw = await fs.readFile(REFLECTION_PATH, 'utf8');
      reflections = raw.trim().split('\n').filter(Boolean).map(line => {
        try { return JSON.parse(line); } catch (_) { return null; }
      }).filter(Boolean);
    } catch (_) { /* optional */ }

    // 读取 trader_strategy.jsonl（策略执行日志）
    const STRATEGY_PATH = path.join(__dirname, '..', '..', 'data', 'trader_strategy.jsonl');
    let strategies = [];
    try {
      const raw = await fs.readFile(STRATEGY_PATH, 'utf8');
      strategies = raw.trim().split('\n').filter(Boolean).map(line => {
        try { return JSON.parse(line); } catch (_) { return null; }
      }).filter(Boolean);
    } catch (_) { /* optional */ }

    // 读取 scanner_log.jsonl（扫描日志）
    const SCANNER_PATH = path.join(__dirname, '..', '..', 'data', 'scanner_log.jsonl');
    let scannerEntries = [];
    try {
      const raw = await fs.readFile(SCANNER_PATH, 'utf8');
      scannerEntries = raw.trim().split('\n').filter(Boolean).map(line => {
        try { return JSON.parse(line); } catch (_) { return null; }
      }).filter(Boolean);
    } catch (_) { /* optional */ }

    // 读取轮动快照
    let rotationSnapshot = null;
    try {
      rotationSnapshot = JSON.parse(await fs.readFile(ROTATION_PATH, 'utf8'));
    } catch (_) { /* optional */ }

    // V7.8: 读取 A1 胜率矩阵真实数据
    const WINRATE_PATH = path.join(__dirname, '..', '..', 'data', 'backtest_winrate.json');
    let winrateData = null;
    try {
      winrateData = JSON.parse(await fs.readFile(WINRATE_PATH, 'utf8'));
    } catch (_) { /* optional */ }

    // V9.8: M3 — 从 trader_tracker.jsonl(活文件) 聚合 策略×horizon 胜率矩阵
    const trackerMatrix = (() => {
      // 去重：同 code+signal_date+horizon 只取一条
      const seen = new Set();
      const uniq = reflections.filter(r => {
        const k = `${r.code}|${r.signal_date||''}|${r.horizon}`;
        if (seen.has(k)) return false;
        seen.add(k);
        return true;
      });
      const horizons = [5, 10, 20];
      const matrix = {};  // key: strategy
      for (const r of uniq) {
        const res = r.result;
        if (!['WIN','HIT','LOSE','MISS','EXPIRE','EXPIRED'].includes(res)) continue;
        const s = r.strategy || '?';
        const h = r.horizon;
        if (!matrix[s]) matrix[s] = {};
        if (!matrix[s][h]) matrix[s][h] = { win:0, lose:0, expire:0, total:0 };
        const cell = matrix[s][h];
        cell.total++;
        if (res === 'WIN' || res === 'HIT')               cell.win++;
        else if (res === 'LOSE' || res === 'MISS')        cell.lose++;
        else if (res === 'EXPIRE' || res === 'EXPIRED')   cell.expire++;
      }
      // 转为模板友好结构
      const strategies = Object.keys(matrix).sort();
      return { strategies, horizons, matrix, totalUniq: uniq.length };
    })();

    // 汇总统计
    const stats = {
      totalReflections: reflections.length,
      totalStrategies: strategies.length,
      totalScannerRuns: scannerEntries.length,
      latestReflection: reflections.length > 0
        ? (reflections[reflections.length - 1].signal_date || reflections[reflections.length - 1].track_date || reflections[reflections.length - 1].date || '未知')
        : '无',
      latestStrategy: strategies.length > 0 ? strategies[strategies.length - 1].date || '未知' : '无',
      latestScanner: scannerEntries.length > 0 ? '有' : '无',
    };

    res.render('trader/reflection', {
      title: '策略反思',
      active: 'trader',
      subTab: 'reflection',
      stats,
      llmReflection,
      rotationSnapshot,
      winrateData,
      trackerMatrix,
      reflections: reflections.slice(-20).reverse(),
      strategies: strategies.slice(-10).reverse(),
      scannerEntries: scannerEntries.slice(-5).reverse(),
      error: null,
    });
  } catch (error) {
    res.status(500).render('admin/error', {
      title: '500 服务器错误',
      status: 500,
      active: 'trader',
      message: '策略反思数据加载失败',
      error,
    });
  }
});

// ── POST /admin/trader/reflection/wechat-draft ── 微信稿 AI 生成 ──
router.post('/trader/reflection/wechat-draft', express.json(), async (req, res) => {
  try {
    const { reflection_text, generated_at } = req.body;
    if (!reflection_text) return res.json({ error: '无反思内容' });

    const Anthropic = require('@anthropic-ai/sdk');
    const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

    const prompt = `你是一位 A 股投资公众号编辑。请根据以下策略反思，写一篇微信公众号推文。

要求：
1. 开头第一句是核心结论（10-20字，观点鲜明）
2. 分 3 段：市场判断 → 事件驱动逻辑 → 操作建议
3. 语言简洁直接，不废话，不用"首先其次最后"
4. 总字数 400-500 字
5. 末尾附【免责声明：本内容仅供参考，不构成投资建议】

策略反思内容：
${reflection_text}

生成时间：${generated_at || new Date().toISOString().slice(0,10)}`;

    const resp = await client.messages.create({
      model: 'claude-sonnet-4-6',
      max_tokens: 1024,
      messages: [{ role: 'user', content: prompt }],
    });
    const draft = resp.content.find(b => b.type === 'text')?.text || '生成失败';
    res.json({ draft });
  } catch (err) {
    res.json({ error: err.message || '生成失败' });
  }
});

// ── POST /admin/trader/reflection/rotation-snapshot ── 轮动快照写入 ──
router.post('/trader/reflection/rotation-snapshot', async (req, res) => {
  try {
    const { direction, confidence, catalyst, evidence, lead_signals, watchlist, doubt } = req.body;
    const snapshot = {
      updated_at: new Date().toISOString(),
      direction: (direction || '').trim(),
      confidence: parseInt(confidence) || 0,
      catalyst: (catalyst || '').trim(),
      evidence: (evidence || '').trim(),
      lead_signals: (lead_signals || '').trim(),
      watchlist: (watchlist || '').trim(),
      doubt: (doubt || '').trim(),
    };
    await fs.writeFile(ROTATION_PATH, JSON.stringify(snapshot, null, 2), 'utf8');
    res.redirect('/admin/trader/reflection#rotation-snapshot');
  } catch (error) {
    res.status(500).render('admin/error', {
      title: '500 服务器错误',
      status: 500,
      active: 'trader',
      message: '轮动快照写入失败',
      error,
    });
  }
});

// ── GET /admin/trader/rotation ── 市场状态 + 轮动因子 + 胜率校准 ──
router.get('/trader/rotation', async (req, res) => {
  try {
    const ROTATION_SNAPSHOTS_PATH = path.join(__dirname, '..', '..', 'data', 'rotation_snapshots.jsonl');
    const MORNING_PATH = path.join(__dirname, '..', '..', 'data', 'morning.json');

    // 29 日轮动历史
    let rotationHistory = [];
    try {
      const raw = await fs.readFile(ROTATION_SNAPSHOTS_PATH, 'utf8');
      rotationHistory = raw.trim().split('\n').filter(Boolean).map(l => {
        try { return JSON.parse(l); } catch (_) { return null; }
      }).filter(Boolean).reverse(); // 最新在前
    } catch (_) {}

    // 手写轮动快照（rotation_snapshot.json）
    let rotationSnapshot = null;
    try {
      rotationSnapshot = JSON.parse(await fs.readFile(ROTATION_PATH, 'utf8'));
    } catch (_) {}

    // 今日 global_conclusion
    let morningConclusion = null;
    try {
      const m = JSON.parse(await fs.readFile(MORNING_PATH, 'utf8'));
      morningConclusion = m.global_conclusion || null;
    } catch (_) {}

    // 策略胜率（tracker model 已改读 trader_tracker.jsonl）
    const [globalWinRates] = await Promise.all([
      trackerModel.globalWinRateByStrategy(),
    ]);

    res.render('trader/rotation', {
      title: '市场状态',
      active: 'trader',
      subTab: 'rotation',
      rotationHistory,
      rotationSnapshot,
      morningConclusion,
      globalWinRates,
      error: null,
    });
  } catch (error) {
    res.status(500).render('admin/error', {
      title: '500 服务器错误',
      status: 500,
      active: 'trader',
      message: '市场状态数据加载失败',
      error,
    });
  }
});

// ── GET /admin/trader/event-feed ── 事件录入 ──
router.get('/trader/event-feed', async (req, res) => {
  const EVENT_CATALOG_PATH = path.join(__dirname, '..', '..', 'data', 'event_catalog.json');
  const NARRATIVE_PATH = path.join(__dirname, '..', '..', 'data', 'event_narrative_latest.json');
  const TX_SIGNALS_PATH = path.join(__dirname, '..', '..', 'data', 'transmission_signals.jsonl');
  let eventCatalog = [];
  let narrativeEvents = [];
  let transmissionSignals = [];
  try {
    const raw = await fs.readFile(EVENT_CATALOG_PATH, 'utf8');
    const d = JSON.parse(raw);
    eventCatalog = Array.isArray(d) ? d : (d.events || []);
  } catch (_) {}
  try {
    const d = JSON.parse(await fs.readFile(NARRATIVE_PATH, 'utf8'));
    narrativeEvents = d.events || [];
  } catch (_) {}
  // V8.3: 读传导信号，筛选今天的
  try {
    const today = new Date().toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai'}).replace(/\//g,'-');
    const raw = await fs.readFile(TX_SIGNALS_PATH, 'utf8');
    const all = raw.trim().split('\n').filter(Boolean).map(l => JSON.parse(l));
    transmissionSignals = all.filter(s => (s.triggered_at || '').startsWith(today));
    // 按事件 ID 分组
    const grouped = {};
    transmissionSignals.forEach(s => {
      const eid = s.event_id || 'unknown';
      if (!grouped[eid]) grouped[eid] = { event_name: s.event_name || eid, signals: [] };
      grouped[eid].signals.push(s);
    });
    transmissionSignals = Object.values(grouped);
  } catch (_) {}
  res.render('trader/event-feed', {
    title: '事件录入',
    active: 'trader',
    subTab: 'event-feed',
    eventCatalog,
    narrativeEvents,
    transmissionSignals,
    success: req.query.success || null,
    error: null,
  });
});

// ── POST /admin/trader/event-feed/analyze ── URL 解析 + LLM 分析 ──
router.post('/trader/event-feed/analyze', async (req, res) => {
  const { url, manual_title, manual_summary } = req.body;
  const EVENT_CATALOG_PATH = path.join(__dirname, '..', '..', 'data', 'event_catalog.json');
  try {
    // 如果有 URL，用 Node 的 https 抓取标题（简单 fetch，不引入依赖）
    let title = (manual_title || '').trim();
    let summary = (manual_summary || '').trim();
    if (url && url.trim()) {
      try {
        const https = require('https');
        const http = require('http');
        const fetchUrl = (u) => new Promise((resolve, reject) => {
          const mod = u.startsWith('https') ? https : http;
          mod.get(u, { headers: { 'User-Agent': 'Mozilla/5.0' }, timeout: 8000 }, (r) => {
            let data = '';
            r.on('data', chunk => { data += chunk; });
            r.on('end', () => resolve(data));
          }).on('error', reject);
        });
        const html = await fetchUrl(url.trim());
        const titleMatch = html.match(/<title[^>]*>([^<]+)<\/title>/i);
        if (titleMatch && !title) title = titleMatch[1].trim().slice(0, 100);
        // 抓正文关键段（og:description 或前500字）
        const ogDesc = html.match(/<meta[^>]+property="og:description"[^>]+content="([^"]+)"/i);
        if (ogDesc && !summary) summary = ogDesc[1].trim().slice(0, 300);
      } catch (_) { /* fetch 失败不影响手动录入 */ }
    }

    if (!title && !summary) {
      return res.redirect('/admin/trader/event-feed?error=请填写标题或摘要');
    }

    // 写入 event_catalog.json
    let catalog = [];
    try {
      const raw = await fs.readFile(EVENT_CATALOG_PATH, 'utf8');
      const d = JSON.parse(raw);
      catalog = Array.isArray(d) ? d : (d.events || []);
    } catch (_) {}

    catalog.unshift({
      id: `USER-${Date.now()}`,
      source: url ? 'url' : 'manual',
      url: url || null,
      title,
      summary,
      added_at: new Date().toISOString().slice(0, 19),
      analyzed: false,
    });
    await fs.writeFile(EVENT_CATALOG_PATH, JSON.stringify(catalog, null, 2), 'utf8');
    res.redirect('/admin/trader/event-feed?success=事件已录入，下次反思将自动消费');
  } catch (error) {
    res.status(500).render('admin/error', {
      title: '500 服务器错误', status: 500, active: 'trader',
      message: '事件录入失败', error,
    });
  }
});

// ── POST /admin/trader/event-feed/delete ── 删除事件 ──
router.post('/trader/event-feed/delete', async (req, res) => {
  const EVENT_CATALOG_PATH = path.join(__dirname, '..', '..', 'data', 'event_catalog.json');
  try {
    const { id } = req.body;
    let catalog = [];
    try {
      const raw = await fs.readFile(EVENT_CATALOG_PATH, 'utf8');
      const d = JSON.parse(raw);
      catalog = Array.isArray(d) ? d : (d.events || []);
    } catch (_) {}
    catalog = catalog.filter(e => e.id !== id);
    await fs.writeFile(EVENT_CATALOG_PATH, JSON.stringify(catalog, null, 2), 'utf8');
    res.redirect('/admin/trader/event-feed?success=已删除');
  } catch (_) {
    res.redirect('/admin/trader/event-feed');
  }
});

// ── POST /admin/trader/strategy/feedback ── 选股交互反馈 ──
router.post('/trader/strategy/feedback', async (req, res) => {
  const FEEDBACK_PATH = path.join(__dirname, '..', '..', 'data', 'strategy_feedback.jsonl');
  try {
    const { code, name, action, date } = req.body;
    if (!code || !action) return res.json({ ok: false, error: 'missing params' });
    const entry = JSON.stringify({
      ts: new Date().toISOString(),
      date: date || new Date().toISOString().slice(0, 10),
      code,
      name: name || '',
      action,  // 'follow' | 'skip' | 'watch'
    });
    await fs.appendFile(FEEDBACK_PATH, entry + '\n', 'utf8');
    res.json({ ok: true });
  } catch (error) {
    res.json({ ok: false, error: String(error) });
  }
});

module.exports = router;
