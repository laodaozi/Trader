'use strict';

const express = require('express');
const os = require('os');
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
// V4.0.1: 对齐 core/daily.py XDG 标准，告别越级相对路径
const POSITIONS_PATH = path.join(os.homedir(), '交易员', 'data', 'positions.json');

// ── /admin/trader ── 工作台首页：概览仪表盘 ──
router.get('/trader', async (req, res) => {
  try {
    const [strategyDateList, latestStrategy, trackerSummary, backtestReports] = await Promise.all([
      strategyModel.getAvailableDates(),
      strategyModel.getLatestStrategy(),
      trackerModel.getTrackerSummary(),
      backtestModel.listReports(),
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

    if (strategyDateList.length === 0) {
      return res.render('trader/strategy', {
        title: '自选池诊断',
        active: 'trader',
        subTab: 'strategy',
        strategyDateList: [],
        currentDate: null,
        strategy: null,
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
    const trackerSummary = await trackerModel.getTrackerSummary();

    if (!trackerSummary) {
      return res.render('trader/tracker', {
        title: '信号跟踪',
        active: 'trader',
        subTab: 'tracker',
        trackerSummary: null,
        currentDate: null,
        currentHorizon: horizonParam,
        records: [],
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
    res.render('trader/watchlist', {
      title: '自选股管理',
      active: 'trader',
      subTab: 'watchlist',
      stocks,
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

// ── /admin/trader/article-stats ── 微信文章统计 ──
router.get('/trader/article-stats', async (req, res) => {
  try {
    const enrichPath = path.join(__dirname, '..', '..', 'data', 'hot_enrichment.json');
    let enrichment = {};
    try {
      const raw = await fs.readFile(enrichPath, 'utf8');
      enrichment = JSON.parse(raw);
    } catch (_) { /* optional */ }

    const entries = Object.entries(enrichment);
    const totalArticles = entries.length;
    const withTickers = entries.filter(([, e]) => e.tickers && e.tickers.length > 0);
    const articlesWithTickers = withTickers.length;
    const zeroTickerCount = totalArticles - articlesWithTickers;

    const allTickers = [];
    for (const [, e] of entries) {
      if (e.tickers && Array.isArray(e.tickers)) {
        allTickers.push(...e.tickers);
      }
    }
    const totalTickers = allTickers.length;
    const uniqueCodes = new Set(allTickers.map((t) => t.code));
    const uniqueTickers = uniqueCodes.size;
    const avgTickers = totalArticles > 0 ? (totalTickers / totalArticles).toFixed(1) : '0.0';
    const signalRatio = totalArticles > 0 ? Math.round((articlesWithTickers / totalArticles) * 100) + '%' : '0%';

    let earliestEnrich = null;
    let latestEnrich = null;
    for (const [, e] of entries) {
      if (e.enriched_at) {
        if (!earliestEnrich || e.enriched_at < earliestEnrich) earliestEnrich = e.enriched_at;
        if (!latestEnrich || e.enriched_at > latestEnrich) latestEnrich = e.enriched_at;
      }
    }

    const tickerCountMap = {};
    for (const t of allTickers) {
      const key = t.code;
      if (!tickerCountMap[key]) tickerCountMap[key] = { code: t.code, name: t.name, count: 0 };
      tickerCountMap[key].count++;
    }
    const topTickers = Object.values(tickerCountMap)
      .sort((a, b) => b.count - a.count)
      .slice(0, 15);

    const dailyMap = {};
    for (const [, e] of entries) {
      if (!e.enriched_at) continue;
      const date = e.enriched_at.slice(0, 10);
      if (!dailyMap[date]) dailyMap[date] = { total: 0, withTickers: 0, tickerCount: 0, zeroCount: 0 };
      dailyMap[date].total++;
      if (e.tickers && e.tickers.length > 0) {
        dailyMap[date].withTickers++;
        dailyMap[date].tickerCount += e.tickers.length;
      } else {
        dailyMap[date].zeroCount++;
      }
    }
    const dailyCounts = Object.entries(dailyMap)
      .map(([date, d]) => ({ date, ...d }))
      .sort((a, b) => b.date.localeCompare(a.date))
      .slice(0, 30);

    res.render('trader/article-stats', {
      title: '微信文章统计',
      active: 'trader',
      subTab: 'article-stats',
      stats: {
        totalArticles,
        articlesWithTickers,
        signalRatio,
        totalTickers,
        uniqueTickers,
        avgTickers,
        earliestEnrich,
        latestEnrich,
        zeroTickerCount,
        topTickers,
        dailyCounts,
      },
      error: null,
    });
  } catch (error) {
    res.status(500).render('admin/error', {
      title: '500 服务器错误',
      status: 500,
      active: 'trader',
      message: '文章统计数据加载失败',
      error,
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
    const LLM_REFLECTION_PATH = path.join('/opt', 'cycleradar-trader', 'data', 'strategy_reflection.json');
    let llmReflection = null;
    try {
      const raw = await fs.readFile(LLM_REFLECTION_PATH, 'utf8');
      llmReflection = JSON.parse(raw);
    } catch (_) { /* optional — 首次部署前文件可能不存在 */ }

    // 读取 tracker_log.jsonl（反思日志）
    const REFLECTION_PATH = path.join(__dirname, '..', '..', 'data', 'tracker_log.jsonl');
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

    // 汇总统计
    const stats = {
      totalReflections: reflections.length,
      totalStrategies: strategies.length,
      totalScannerRuns: scannerEntries.length,
      latestReflection: reflections.length > 0 ? reflections[reflections.length - 1].date || '未知' : '无',
      latestStrategy: strategies.length > 0 ? strategies[strategies.length - 1].date || '未知' : '无',
      latestScanner: scannerEntries.length > 0 ? '有' : '无',
    };

    res.render('trader/reflection', {
      title: '策略反思',
      active: 'trader',
      subTab: 'reflection',
      stats,
      llmReflection,           // LLM 策略反思（generate_strategy_reflection.py 产出）
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

module.exports = router;
