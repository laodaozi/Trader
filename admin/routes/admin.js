'use strict';

const express = require('express');
const router = express.Router();
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');
const Account = require('../models/account');

// ────────────────────────────────────────────────────────────
// 动态架构页 helper（实时生成，不落盘，与 docs/architecture.md 占位符一一对应）
// ────────────────────────────────────────────────────────────

const ROOT = path.resolve(__dirname, '../..');
const DATA_DIR = path.join(ROOT, 'data');

/** 数据契约文件 → 中文标签 */
const CONTRACT_LABELS = {
  'trader_strategy.jsonl': '选股信号',
  'trader_tracker.jsonl': '信号跟踪',
  'timing_history.json': '市场体温',
  'positions.json': '账户持仓',
  'strategy_reflection.json': '策略反思',
  'event_library.json': '事件库',
  'event_catalog.json': '事件目录',
  'event_signals.jsonl': '事件信号',
  'event_narrative_latest.json': '事件叙事',
  'transmission_graph.json': '传导图谱',
  'transmission_signals.jsonl': '传导信号',
  'upstream_signals.jsonl': '上游信号总线',
  'scanner_log.jsonl': '14模型命中',
  'rotation_snapshots.jsonl': '八因子快照',
  'world_monitor_contracts.json': '世界监控合约',
  'world_monitor_enriched.json': '世界监控增强',
  'pulse_latest.json': '市场脉搏',
  'decision_log.jsonl': '决策日志',
  'watchlist.json': '自选池',
  'watchlist_signals.json': '自选池信号',
  'alpha_latest.json': 'Alpha 信号',
  'backtest_winrate.json': '回测胜率',
  'event_hit_log.json': '事件命中日志',
  'event_novelty_cache.json': '事件新颖度缓存',
  'hot_enrichment.json': '热点增强',
  'hotevents_cache.json': '热点事件缓存',
  'morning.json': '晨报',
  'pipeline_status.json': '管线状态',
  'reinforce_checkpoint.json': '强化学习检查点',
  'rotation_snapshot.json': '八因子单次快照',
  'scheduler_state.json': '调度状态',
  'trade_log.json': '交易日志',
  'wanjun_signals.jsonl': '万骏信号',
};

function ageText(ageHours) {
  if (ageHours == null) return '缺失';
  if (ageHours < 1) return Math.round(ageHours * 60) + 'm';
  if (ageHours < 24) return Math.round(ageHours * 10) / 10 + 'h';
  return Math.round(ageHours / 24 * 10) / 10 + 'd';
}

function ageBadge(ageHours) {
  if (ageHours == null) {
    return '<span style="display:inline-block;background:#fee2e2;color:#991b1b;padding:1px 7px;border-radius:10px;font-size:10px;font-weight:600">缺失</span>';
  }
  if (ageHours > 24) {
    return `<span style="display:inline-block;background:#fef3c7;color:#92400e;padding:1px 7px;border-radius:10px;font-size:10px;font-weight:600">${ageText(ageHours)}</span>`;
  }
  return `<span style="display:inline-block;background:#dcfce7;color:#166534;padding:1px 7px;border-radius:10px;font-size:10px;font-weight:600">${ageText(ageHours)}</span>`;
}

/** 实时解析 crontab -l，生成调度表 */
function buildCronTable() {
  let lines = [];
  try {
    lines = execSync('crontab -l 2>/dev/null', { encoding: 'utf-8' }).split('\n');
  } catch (_e) {
    lines = [];
  }

  const rows = [];
  for (const line of lines) {
    const t = line.trim();
    if (!t || t.startsWith('#') || /^@/.test(t)) continue;
    if (/^(PATH|SHELL|MAILTO|HOME|LD_LIBRARY_PATH)\s*=/.test(t)) continue;
    const parts = t.split(/\s+/);
    if (parts.length < 6) continue;
    const [M, H, , , DOW] = parts;
    const cmd = parts.slice(5).join(' ');

    let time;
    if (H === '*') {
      time = M === '*' ? '每分钟' : `每 ${M.replace('*/', '')} 分钟`;
    } else if (M === '*') {
      time = H.startsWith('*/') ? `每 ${H.replace('*/', '')} 小时` : `每时第 ${H} 分`;
    } else {
      time = `${H.padStart(2, '0')}:${M.padStart(2, '0')}`;
    }

    const sched = /^1-5$/.test(DOW) ? '交易日' : /^\d$/.test(DOW) ? `周${DOW}` : '每日';
    const sm = cmd.match(/([\w-]+\.(?:py|sh|js))/);
    const script = sm ? sm[1] : '—';
    const lm = cmd.match(/([\w.-]+\.log)/);
    const log = lm ? lm[1] : '';

    let sortKey = 99999;
    if (/^\d+$/.test(H) && /^\d+$/.test(M)) sortKey = parseInt(H) * 60 + parseInt(M);
    else if (/^\d+$/.test(H)) sortKey = parseInt(H) * 60;

    rows.push({ sortKey, time, sched, script, log });
  }
  rows.sort((a, b) => a.sortKey - b.sortKey);

  let html = '<table><thead><tr><th>时间</th><th>频率</th><th>脚本</th><th>输出日志</th></tr></thead><tbody>';
  for (const r of rows) {
    html += `<tr><td style="white-space:nowrap">${r.time}</td><td>${r.sched}</td><td><code>${r.script}</code></td><td style="font-size:10px;color:#5A6B7C">${r.log}</td></tr>`;
  }
  html += '</tbody></table>';
  html += `<p style="font-size:11px;color:#5A6B7C">共 ${rows.length} 条 cron 任务 · 实时读取 <code>crontab -l</code> · 生成于 ${new Date().toISOString()}</p>`;
  return html;
}

/** 实时扫描 data/ 目录，生成数据契约表 */
function buildContractsTable() {
  let names = [];
  try {
    names = fs.readdirSync(DATA_DIR).filter(n => /\.(json|jsonl)$/.test(n)).sort();
  } catch (_e) {
    names = [];
  }

  const rows = [];
  for (const name of names) {
    const fp = path.join(DATA_DIR, name);
    let stat;
    try { stat = fs.statSync(fp); } catch (_e) { continue; }

    const size = stat.size;
    const age = (Date.now() - stat.mtimeMs) / 3600000;
    let lines = 0;
    if (/\.jsonl$/.test(name)) {
      try { lines = fs.readFileSync(fp, 'utf-8').split('\n').filter(l => l.trim()).length; } catch (_e) {}
    }
    const label = CONTRACT_LABELS[name] || '';
    rows.push({ name, label, size, lines, age });
  }

  let html = '<table><thead><tr><th>文件</th><th>用途</th><th>记录数</th><th>大小</th><th>新鲜度</th></tr></thead><tbody>';
  for (const r of rows) {
    const recs = r.lines ? r.lines.toLocaleString() : '—';
    const sz = r.size > 1024 ? (r.size / 1024).toFixed(1) + ' KB' : r.size + ' B';
    html += `<tr><td><code>${r.name}</code></td><td>${r.label}</td><td style="text-align:right">${recs}</td><td style="text-align:right">${sz}</td><td>${ageBadge(r.age)}</td></tr>`;
  }
  html += '</tbody></table>';
  html += `<p style="font-size:11px;color:#5A6B7C">共 ${rows.length} 个数据文件 · 实时扫描 <code>data/</code> 目录 · 生成于 ${new Date().toISOString()}</p>`;
  return html;
}

/** 实时检测可量化技术债 */
function buildTechDebtTable() {
  let expiredRatio = null, winRate = null, hit = 0, miss = 0, total = 0, expired = 0;
  try {
    const lines = fs.readFileSync(path.join(DATA_DIR, 'trader_tracker.jsonl'), 'utf-8').split('\n');
    for (const l of lines) {
      if (!l.trim()) continue;
      const d = JSON.parse(l);
      total++;
      if (d.result === 'EXPIRED') expired++;
      else if (d.result === 'HIT') hit++;
      else if (d.result === 'MISS') miss++;
    }
    if (total > 0) expiredRatio = Math.round(expired / total * 1000) / 10;
    if (hit + miss > 0) winRate = Math.round(hit / (hit + miss) * 1000) / 10;
  } catch (_e) {}

  let timingAge = null;
  try {
    const s = fs.statSync(path.join(DATA_DIR, 'timing_history.json'));
    timingAge = (Date.now() - s.mtimeMs) / 3600000;
  } catch (_e) {}

  const staleTiming = timingAge != null && timingAge > 24;

  let html = '<table><thead><tr><th>债项</th><th>描述</th><th>实时检测</th></tr></thead><tbody>';

  html += '<tr><td>tb-1</td><td>EXPIRED 占比过高（多数信号因 OHLC 回填失败无法裁决，损害回测可信度）</td><td>';
  if (expiredRatio != null) {
    html += `EXPIRED <strong>${expiredRatio}%</strong>（${expired}/${total}）· 口径A胜率 <strong>${winRate}%</strong>（HIT ${hit} / MISS ${miss}）`;
  } else {
    html += '<span style="color:#5A6B7C">无法读取 tracker</span>';
  }
  html += '</td></tr>';

  html += '<tr><td>tb-2</td><td>cron 写 JSONL 无原子写入（无 rename 保护），并发读可能读到半截行</td><td><span style="color:#5A6B7C">结构性债（静态）</span></td></tr>';

  html += '<tr><td>tb-3</td><td>市场体温过期且页面无"数据可能过期"标注</td><td>';
  if (timingAge != null) {
    html += staleTiming
      ? `<span style="background:#fee2e2;color:#991b1b;padding:1px 7px;border-radius:10px;font-size:10px;font-weight:600">已过期 ${ageText(timingAge)}</span>`
      : `<span style="background:#dcfce7;color:#166534;padding:1px 7px;border-radius:10px;font-size:10px;font-weight:600">正常 ${ageText(timingAge)}</span>`;
  } else {
    html += '<span style="background:#fee2e2;color:#991b1b;padding:1px 7px;border-radius:10px;font-size:10px;font-weight:600">文件缺失</span>';
  }
  html += '</td></tr>';

  html += '<tr><td>tb-4</td><td>pipeline 心跳只查文件 mtime，不查执行是否成功（cron 报错仍显示 ✅）</td><td><span style="color:#5A6B7C">结构性债（静态）</span></td></tr>';

  html += '</tbody></table>';
  html += `<p style="font-size:11px;color:#5A6B7C">tb-1 / tb-3 实时检测，tb-2 / tb-4 为结构性债 · 生成于 ${new Date().toISOString()}</p>`;
  return html;
}

/** 把占位符替换为动态 HTML（优先替换独立段落 <p>，兜底裸占位符） */
function injectLive(html, token, replacement) {
  if (html.includes(`<p>${token}</p>`)) return html.replace(`<p>${token}</p>`, replacement);
  return html.replace(token, replacement);
}

// GET / — 订阅号列表页
router.get('/', (req, res) => {
  const { category, status } = req.query;
  const filter = {};
  if (category) filter.category = category;
  if (status) filter.status = status;

  const accounts = Account.getAll(filter);
  res.render('admin/list', {
    title: 'WeWe RSS 订阅号管理',
    active: 'admin',
    accounts,
    categories: Account.CATEGORIES,
    statuses: Account.STATUSES,
    currentCategory: category || '',
    currentStatus: status || '',
    backend: Account.getBackend(),
  });
});

// GET /architecture — 技术架构图（动态渲染 Markdown 源文件 + 实时系统状态 + 动态数据注入）
router.get('/architecture', (req, res) => {
  try {
    const { marked } = require('marked');
    marked.setOptions({ gfm: true, breaks: true });

    const mdPath = path.join(ROOT, 'docs/architecture.md');
    const raw = fs.readFileSync(mdPath, 'utf-8');

    const sys = require('./system');
    const version = sys.getVersion() || 'V10.1';
    const sysStatus = sys.getStatus();

    let html = marked.parse(raw);
    html = injectLive(html, '{{CRON_TABLE}}', buildCronTable());
    html = injectLive(html, '{{CONTRACTS_TABLE}}', buildContractsTable());
    html = injectLive(html, '{{TECHDEBT_TABLE}}', buildTechDebtTable());

    res.render('admin/architecture', {
      title: `${version} 技术架构 · CycleRadar Trader`,
      active: 'admin',
      body: html,
      version,
      sysStatus,
    });
  } catch (err) {
    console.error('[architecture] failed to render:', err.message);
    res.status(500).send('架构图渲染失败，请检查 docs/architecture.md 是否存在');
  }
});

// GET /accounts/new — 新增页（放在 :id 之前，避免被参数路由捕获）
router.get('/accounts/new', (req, res) => {
  res.render('admin/edit', {
    title: '新增订阅号',
    active: 'admin',
    account: null,
    categories: Account.CATEGORIES,
    isNew: true,
  });
});

// GET /accounts/:id — 单个订阅号详情（编辑页）
router.get('/accounts/:id', (req, res, next) => {
  const account = Account.getById(req.params.id);
  if (!account) return next();
  res.render('admin/edit', {
    title: `编辑：${account.name}`,
    active: 'admin',
    account,
    categories: Account.CATEGORIES,
    isNew: false,
  });
});

// POST /accounts — 新增订阅号
router.post('/accounts', (req, res) => {
  const { name, mp_id, category, tags } = req.body;
  Account.create({ name, mp_id, category, tags });
  res.redirect('/admin');
});

// POST /accounts/:id — 更新订阅号
router.post('/accounts/:id', (req, res, next) => {
  const { name, mp_id, category, tags } = req.body;
  const updated = Account.update(req.params.id, { name, mp_id, category, tags });
  if (!updated) return next();
  res.redirect('/admin');
});

// POST /accounts/:id/delete — 软删除
router.post('/accounts/:id/delete', (req, res, next) => {
  const deleted = Account.softDelete(req.params.id);
  if (!deleted) return next();
  res.redirect('/admin');
});

// POST /accounts/:id/toggle — 暂停/恢复切换
router.post('/accounts/:id/toggle', (req, res, next) => {
  const toggled = Account.toggleStatus(req.params.id);
  if (!toggled) return next();
  const back = req.query.category
    ? `/admin?category=${encodeURIComponent(req.query.category)}`
    : '/admin';
  res.redirect(back);
});

module.exports = router;
