// ====== Tab Switching ======
document.querySelectorAll('.m-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.m-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.m-panel').forEach(p => p.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('p-' + tab.dataset.tab).classList.add('active');
    loadTab(tab.dataset.tab);
  });
});

// ====== Tab Router ======
let loaded = {};
function loadTab(name) {
  if (loaded[name]) return;
  loaded[name] = true;
  if (name === 'overview') loadOverview();
  else if (name === 'watchlist') loadWatchlist();
  else if (name === 'cycleradar') loadCycleradar();
}
loadOverview(); // initial load

// ====== Overview Tab ======
async function loadOverview() {
  try {
    const [sumRes, hyRes] = await Promise.all([
      fetch('/m/api/summary'),
      fetch('/m/api/haoyunge')
    ]);
    const d = await sumRes.json();
    const hy = hyRes.ok ? await hyRes.json() : null;
    const el = document.getElementById('overview-content');
    el.innerHTML = buildThermoCard(d.timing) + buildNarrativeCard(d.event_narrative) + buildHaoYunCard(hy) + buildSignalsCard(d.strategy) + buildTopStocksCard(d.tracker) + buildTrackerHitCard(d.tracker);
    el.style.display = 'block';
    document.getElementById('overview-loading').style.display = 'none';
  } catch(e) {
    document.getElementById('overview-loading').innerHTML = '<div class="nodata">加载失败: ' + e.message + '</div>';
  }
}

function buildThermoCard(t) {
  if (!t) return '';
  const pct = t.positionRatio ? (t.positionRatio*100).toFixed(0) : 0;
  const phaseCls = t.phase === '进攻' ? 'bull' : t.phase === '防守' ? 'bear' : 'neutral';
  return '<div class="card">' +
    '<div class="card-title">🌡️ 市场体温</div>' +
    '<div class="thermo-phase ' + phaseCls + '">' + (t.phase||'—') + '</div>' +
    '<div class="thermo-detail">仓位 ' + pct + '% · ' + (t.advice||'') + '</div>' +
    '<div class="thermo-bar"><div class="thermo-fill" style="width:' + pct + '%"></div></div>' +
    '</div>';
}

// V6.4: 今日研判卡片 — event_narrative 合约（对接新 schema: sector_transmission/time_dimension）
function buildNarrativeCard(n) {
  if (!n) return '';
  var gc = n.global_conclusion || {};
  var regime = gc.market_regime || gc.regime || '未知';
  var confidence = gc.confidence != null ? Math.round(gc.confidence * 100) + '%' : '—';
  var action = gc.action || '';
  var thesis = gc.key_thesis || '';
  var risks = gc.risk_warnings || [];
  var sector = gc.sector_outlook || '';

  var regimeIcon = regime.includes('牛') ? '🐂' : regime.includes('熊') ? '🐻' : regime.includes('震荡') ? '📊' : '🌐';
  var regimeColor = regime.includes('牛') ? '#22c55e' : regime.includes('熊') ? '#ef4444' : '#f59e0b';
  var actionBadge = action.includes('加仓') ? '<span style="background:#22c55e;color:#000;padding:1px 6px;border-radius:3px;font-size:10px;font-weight:700">' + _h(action) + '</span>'
    : action.includes('减仓') ? '<span style="background:#ef4444;color:#fff;padding:1px 6px;border-radius:3px;font-size:10px;font-weight:700">' + _h(action) + '</span>'
    : '<span style="color:#94a3b8;font-size:10px">' + _h(action) + '</span>';

  var eventRows = (n.events || []).slice(0, 4).map(function(ev, idx) {
    var rank = ev.rank || (idx + 1);
    var firstSector = (ev.sector_transmission || [])[0] || {};
    var dir = firstSector.direction || '';
    var impact = dir.includes('看多') ? 'positive' : dir.includes('看空') ? 'negative' : 'neutral';
    var timeDim = ev.time_dimension || '';
    var dateStr = timeDim ? '—' + timeDim : '';
    return '<div style="font-size:10px;line-height:1.5;padding:2px 0;border-bottom:1px solid rgba(255,255,255,0.04)"><span style="color:#64748b">' + _h(dateStr) + '</span> <span style="color:' + (impact==='positive'?'#22c55e':impact==='negative'?'#ef4444':'#94a3b8') + '">' + (impact==='positive'?'↑':impact==='negative'?'↓':'→') + '</span> #' + rank + ' ' + _h(ev.title||'') + '</div>';
  }).join('');

  var riskRows = risks.slice(0, 3).map(function(r) {
    return '<span style="display:inline-block;margin:1px 3px 1px 0;padding:1px 5px;border-radius:3px;background:rgba(239,68,68,0.1);color:#fca5a5;font-size:9px">⚠ ' + _h(r) + '</span>';
  }).join('');

  return '<div class="card">' +
    '<div class="card-title">📋 今日研判 <span style="font-size:9px;color:#64748b;font-weight:400">event_narrative</span></div>' +
    '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">' +
      '<span style="font-size:22px">' + regimeIcon + '</span>' +
      '<span style="font-size:14px;font-weight:700;color:' + regimeColor + '">' + _h(regime) + '</span>' +
      '<span style="font-size:10px;color:#64748b">置信度 ' + confidence + '</span>' +
      actionBadge +
    '</div>' +
    (thesis ? '<div style="font-size:11px;line-height:1.5;color:#e2e8f0;margin-bottom:6px">' + _h(thesis) + '</div>' : '') +
    (sector ? '<div style="font-size:10px;color:#94a3b8;margin-bottom:6px">🏭 ' + _h(sector) + '</div>' : '') +
    (eventRows ? '<div style="margin-bottom:4px">' + eventRows + '</div>' : '') +
    (riskRows ? '<div style="margin-top:4px">' + riskRows + '</div>' : '') +
    '</div>';
}

// V6.4: 好运哥策略纪律卡片（精简话术）
function buildHaoYunCard(hy) {
  if (!hy || hy.error) return '';
  var COLOR = {
    '积极进攻': '#22c55e',
    '进攻': '#10b981',
    '均衡偏进攻': '#84cc16',
    '均衡': '#f59e0b',
    '防御': '#f97316',
    '强制空仓': '#ef4444'
  };
  var color = COLOR[hy.posture] || '#6b7280';
  var rulesHtml = '';
  if (hy.rules && hy.rules.length) {
    rulesHtml = '<div class="haoyunge-rules">' +
      hy.rules.map(function(r) { return '<div class="haoyunge-rule">' + r + '</div>'; }).join('') +
      '</div>';
  }
  return '<div class="card haoyunge-card">' +
    '<div class="card-title"><span class="cr-ico">🎯</span> 好运哥交易纪律</div>' +
    '<div class="haoyunge-posture" style="color:' + color + '">' + hy.posture + '</div>' +
    '<div class="haoyunge-meta">' +
      '<span>仓位：' + (hy.maxPosition || '—') + '</span>' +
      '<span class="haoyunge-meta-sep">|</span>' +
      '<span>月目标：' + (hy.monthlyTarget || '—') + '</span>' +
      '<span class="haoyunge-meta-sep">|</span>' +
      '<span>周目标：' + (hy.weeklyTarget || '—') + '</span>' +
    '</div>' +
    rulesHtml +
    '</div>';
}

function buildSignalsCard(s) {
  if (!s || !s.signals) return '';
  var icons = {'🔥进攻':'🔥','✅买入':'✅','🕐埋伏':'🕐','—观望':'—'};
  var rows = '';
  for (var label in s.signals) {
    rows += '<div class="sig-cell"><div class="sig-icon">' + (icons[label]||'') + '</div><div class="sig-count">' + s.signals[label] + '</div><div class="sig-label">' + label + '</div></div>';
  }
  return '<div class="card">' +
    '<div class="card-title">📡 信号分布</div>' +
    '<div class="signal-row">' + rows + '</div>' +
    '<div style="font-size:10px;color:#b8a06a;margin-top:8px;text-align:center">更新: ' + (s.date||'') + ' · 共' + (s.total||0) + '只</div>' +
    '</div>';
}

function buildTopStocksCard(t) {
  if (!t || !t.topStocks || t.topStocks.length===0) return '';
  var items = '';
  t.topStocks.forEach(function(s,i) {
    items += '<div class="stock-item">' +
      '<div class="s-rank">' + (i+1) + '</div>' +
      '<div class="s-body"><div class="s-code">' + (s.code||'') + '</div><div class="s-name">' + (s.name||'-') + '</div></div>' +
      '<div class="s-score">' + (s.score||0) + '</div>' +
      '</div>';
  });
  return '<div class="card">' +
    '<div class="card-title">🏆 TOP 信号股</div>' +
    items +
    '</div>';
}

function buildTrackerHitCard(t) {
  if (!t) return '';
  return '<div class="card">' +
    '<div class="card-title">🎯 跟踪命中率</div>' +
    '<div class="hit-grid">' +
    '<div class="hit-cell"><div class="hit-h">总跟踪</div><div class="hit-v">' + (t.totalDecisions||0) + '</div></div>' +
    '<div class="hit-cell"><div class="hit-h">命中</div><div class="hit-v" style="color:#16a34a">' + (t.hits||0) + '</div></div>' +
    '<div class="hit-cell"><div class="hit-h">未命中</div><div class="hit-v" style="color:#dc2626">' + (t.misses||0) + '</div></div>' +
    '</div>' +
    '<div style="font-size:11px;color:#b8a06a;text-align:center">命中率 ' + (t.hitRate||0) + '% · Pending ' + (t.pending||0) + '</div>' +
    '</div>';
}

// ====== Watchlist Tab ======
async function loadWatchlist() {
  try {
    var res = await fetch('/m/api/watchlist');
    var data = await res.json();
    // API returns {signals: [...]} (watchlist_signals contract), not {stocks: [...]}
    var signals = (data && data.signals) ? data.signals : [];
    var container = document.getElementById('wl-content');
    document.getElementById('wl-loading').style.display = 'none';
    if (signals.length === 0) {
      container.innerHTML = '<div class="nodata">暂无自选股 | 前往 <a href="/admin">Admin</a> 添加</div>';
    } else {
      // Summary: NX signal distribution
      var nxBuy    = signals.filter(function(s) { return s.nx_signal === 'buy' || s.nx_signal === 'rising'; }).length;
      var nxSell   = signals.filter(function(s) { return s.nx_signal === 'sell'; }).length;
      var nxNeutral = signals.filter(function(s) { return s.nx_signal === 'neutral'; }).length;
      var nxEmpty  = signals.length - nxBuy - nxSell - nxNeutral;

      function nxBadge(nx) {
        if (nx === 'buy')    return 'var(--m-positive)';
        if (nx === 'rising') return 'var(--m-primary)';
        if (nx === 'sell')   return 'var(--m-negative)';
        return 'var(--m-text-2)';
      }
      function nxLabel(nx) {
        var m = {buy:'买入', rising:'趋升', sell:'卖出'};
        return m[nx] || (nx || '—');
      }

      var rows = signals.map(function(s) {
        var pnlHtml = '—';
        if (s.pnl_pct != null) {
          var clr = s.pnl_pct >= 0 ? 'var(--m-positive)' : 'var(--m-negative)';
          var sign = s.pnl_pct >= 0 ? '+' : '';
          pnlHtml = '<span style="font-size:13px;font-weight:600;font-family:var(--m-mono);color:' + clr + '">' + sign + s.pnl_pct.toFixed(1) + '%</span>';
        }
        var lcMap  = {'生·进入':'var(--m-primary)','住·持有':'var(--m-positive)','坏·注意':'var(--m-warn)','灭·出局':'var(--m-text-3)','未知':'var(--m-text-3)'};
        var lcClr  = lcMap[s.lifecycle] || 'var(--m-text-3)';
        var close  = s.close != null ? s.close.toFixed(2) : '—';
        var entry  = s.entry_price != null ? s.entry_price.toFixed(2) : '—';

        return '<div class="wl-row" data-code="' + (s.code||'') + '" onclick="openStockModal(\'' + (s.code||'') + '\')">' +
          '<div class="wl-left">' +
            '<span class="wl-name">' + (s.name||'-') + ' <span class="wl-code">' + (s.code||'') + '</span></span>' +
            '<div class="wl-zone">' +
              '<span class="wl-zone-item"><span class="wl-zone-label">市价</span><span class="wl-zone-val">' + close + '</span></span>' +
              '<span class="wl-zone-item"><span class="wl-zone-label">成本</span><span class="wl-zone-val">' + entry + '</span></span>' +
            '</div>' +
          '</div>' +
          '<div class="wl-right">' +
            '<span style="font-size:12px;font-weight:600;color:' + nxBadge(s.nx_signal) + '">NX ' + nxLabel(s.nx_signal) + '</span>' +
            pnlHtml +
            '<span style="font-size:10px;color:' + lcClr + '">' + (s.lifecycle||'') + '</span>' +
          '</div>' +
        '</div>';
      }).join('');

      container.innerHTML =
        '<div class="card">' +
          '<div class="card-title">📱 自选股 (' + signals.length + ')</div>' +
          '<div style="display:flex;gap:16px;padding:4px 0 8px;font-size:11px;color:var(--m-text-2)">' +
            '<span>📈 看多 <b style="color:var(--m-positive)">' + nxBuy + '</b></span>' +
            '<span>📉 看空 <b style="color:var(--m-negative)">' + nxSell + '</b></span>' +
            '<span>— 中性 <b style="color:var(--m-text-3)">' + nxNeutral + '</b></span>' +
            (nxEmpty > 0 ? '<span>? 未知 <b style="color:var(--m-text-3)">' + nxEmpty + '</b></span>' : '') +
          '</div>' +
          rows +
        '</div>';
    }
    container.style.display = 'block';
  } catch(e) {
    document.getElementById('wl-loading').innerHTML = '<div class="nodata">加载失败: ' + e.message + '</div>';
  }
}

// ====== Stock Modal ======
async function openStockModal(code) {
  var modal = document.getElementById('stock-modal');
  var body = document.getElementById('modal-content');
  body.innerHTML = '<div class="loading"><div class="spin"></div></div>';
  modal.classList.add('show');
  try {
    var res = await fetch('/m/api/tracker/stock/' + code);
    var d = await res.json();
    var rows = (d.history||[]).map(function(h) {
      return '<tr>' +
        '<td>' + (h.date||'') + '</td>' +
        '<td>' + (h.signal||'') + '</td>' +
        '<td>' + (h.direction||'') + '</td>' +
        '<td>' + (h.target||'—') + '</td>' +
        '<td>' + (h.actual||'—') + '</td>' +
        '<td>' + (h.deviation||'') + '</td>' +
        '<td><span class="verdict v-' + (h.verdict||'nodata').toLowerCase() + '">' + (h.verdict||'NODATA') + '</span></td>' +
        '</tr>';
    }).join('');
    body.innerHTML = '<div class="card-title">📊 ' + code + ' ' + (d.name||'') + ' · 跟踪历史</div>' +
      '<div class="hit-grid">' +
      '<div class="hit-cell"><div class="hit-h">总决策</div><div class="hit-v">' + (d.totalDecisions||0) + '</div></div>' +
      '<div class="hit-cell"><div class="hit-h">命中率</div><div class="hit-v">' + (d.hitRate||0) + '%</div></div>' +
      '<div class="hit-cell"><div class="hit-h">平均偏差</div><div class="hit-v">' + (d.avgDeviation||'—') + '</div></div>' +
      '</div>' +
      '<table class="trk-table"><thead><tr><th>日期</th><th>信号</th><th>方向</th><th>目标</th><th>实际</th><th>偏差</th><th>判定</th></tr></thead><tbody>' + rows + '</tbody></table>';
  } catch(e) {
    body.innerHTML = '<div class="nodata">加载失败: ' + e.message + '</div>';
  }
}

function closeModal() { document.getElementById('stock-modal').classList.remove('show'); }

// ====== Cycleradar Tab ======
async function loadCycleradar() {
  try {
    var res = await fetch('/m/api/cycleradar');
    var d = await res.json();
    document.getElementById('cr-content').innerHTML =
      buildCrStatsBar(d.summary, d.event_narrative, d.daily_pnl) +
      buildCrEventNarrative(d.event_narrative, d.hotEvents) +
      buildCrMarketSummary(d.summary, d.hotEvents, d.alpha, d.etf, d.commodity) +
      buildCrSummaryCards(d.summary) +
      buildCrCategorySections(d.hotEvents, d.alpha, d.etf, d.commodity, d.alpha_latest);
    // V4.1: attach expand handlers after DOM rendered
    attachCrExpandHandlers();
    document.getElementById('cr-content').style.display = 'block';
    document.getElementById('cr-loading').style.display = 'none';
  } catch(e) {
    document.getElementById('cr-loading').innerHTML = '<div class="nodata">加载失败: ' + e.message + '</div>';
  }
}

// ── V5.1 信号Tab顶部统计栏（参考图: 信源/条数/LLM置信/胜率）──
function buildCrStatsBar(summary, en, dailyPnl) {
  var gc = (en && en.global_conclusion) || {};
  var srcCount = summary ? (summary.strategyCount || 0) : 0;
  var sigCount = summary ? (summary.active || 0) : 0;
  var llmConf = gc.confidence || 0;

  // V6.5: 30日胜率优先用 daily_pnl (calc_30d_winrate.py 回溯计算), 次选 gc.win_rate
  var winRate = null;
  if (dailyPnl && dailyPnl.win_rate != null) {
    winRate = dailyPnl.win_rate;
  } else if (gc.win_rate != null) {
    winRate = gc.win_rate;
  }

  var items = [
    { label: '信源', value: srcCount + '/16' },
    { label: '条数', value: sigCount + '条' },
    { label: 'LLM置信', value: llmConf },
    { label: '30日胜率', value: winRate !== null ? winRate + '%' : '—' }
  ];

  var html = items.map(function(it) {
    return '<span class="cr-stat-item"><span class="cr-stat-val">' + it.value + '</span><span class="cr-stat-label">' + it.label + '</span></span>';
  }).join('<span class="cr-stat-sep">·</span>');

  return '<div class="cr-stats-bar">' + html + '</div>';
}

// ── V5.0 事件叙事解读（信号Tab顶部，event_narrative_latest.json 驱动）──
function buildCrEventNarrative(en, hotEvents) {
  if (!en) return '';
  var gc = en.global_conclusion || {};
  var events = en.events || [];
  var raw = gc.market_regime || gc.regime || '';
  if (!raw && events.length === 0) return '';

  var llmConf = gc.confidence || 0;

  // 顶部研判行 — V5.3 市场风格语言强化
  var regimeMap = {
    '强势做多': 'offense', '进攻': 'offense',
    '均衡偏多': 'balance', '均衡': 'balance', '均衡偏空': 'balance',
    '防御': 'defense', '防守': 'defense', '强势避险': 'defense', '避险': 'defense'
  };
  var regime = regimeMap[raw] || 'balance';
  var emoji = raw.includes('多') ? '🔥' : raw.includes('空') || raw.includes('防') || raw.includes('避险') || raw.includes('御') ? '🛡️' : '⚖️';
  var regimeIcon = emoji + ' ' + raw;
  var actionText = gc.action || '';
  var freshStr = '';
  if (en.generated_at) {
    var diffH = Math.round((Date.now() - new Date(en.generated_at).getTime()) / 3600000 * 10) / 10;
    freshStr = diffH < 1 ? '刚刚生成' : diffH + '小时前生成';
  }

  var headerHtml = '<div class="cr-en-header">' +
    '<span class="cr-summary-regime ' + regime + '">' + regimeIcon + '</span>' +
    (actionText ? '<span class="cr-summary-action ' + regime + '">' + _h(actionText) + '</span>' : '') +
    (llmConf > 0 ? '<span class="cr-summary-conf ' + regime + '">置信度 ' + llmConf + '</span>' : '') +
    (gc.key_thesis ? '<div class="cr-en-thesis">' + _h(gc.key_thesis) + '</div>' : '') +
    (freshStr ? '<div class="cr-en-fresh">' + freshStr + '</div>' : '') +
    '</div>';

  // 风险警告行
  var riskWarnings = gc.risk_warnings || [];
  var riskHtml = '';
  if (riskWarnings.length > 0) {
    riskHtml = '<div class="cr-risk-warnings">' +
      riskWarnings.map(function(rw) {
        return '<div class="cr-risk-item">⚠️ ' + _h(rw) + '</div>';
      }).join('') +
      '</div>';
  }

  // 事件列表 — V5.3 按热度降序 + 限 10 条
  var eventsHtml = '';
  if (events.length > 0) {
    var sortedEvents = events.slice().sort(function(a, b) { return (a.rank || 999) - (b.rank || 999); }).slice(0, 10);
    var eventItems = sortedEvents.map(function(e, idx) {
      return buildCrEventItem(e, idx);
    }).join('');
    eventsHtml = '<div class="cr-en-events">' +
      '<div class="cr-section-title"><span class="cr-ico">📋</span> 今日事件解读</div>' +
      eventItems +
      '</div>';
  }

  // Hot events integration (merged into narrative block, V6.4)
  var hotHtml = '';
  if (hotEvents && hotEvents.length > 0) {
    hotHtml = _buildCrHotEvents(hotEvents, true);
  }

  return '<div class="cr-en-block">' + headerHtml + riskHtml + eventsHtml + hotHtml + '</div>';
}

// ── V6.4 事件解读卡片（新 schema: trigger_event/direct_reaction/sector_transmission/valuation_impact/trading_window/stock_mapping）──
function buildCrEventItem(e, idx) {
  if (!e) return '';

  // rank
  var rank = e.rank || (idx + 1);
  var numHtml = '<span class="ev-rank">#' + rank + '</span>';

  // title + time_dimension
  var title = e.title || '';
  var timeDim = e.time_dimension || '';
  var timeHtml = timeDim ? '<span class="ev-time">' + _h(timeDim) + '</span>' : '';

  // source line — 信源 + 原标题
  var sourceLine = '';
  if (e.source) {
    sourceLine = '<div class="ev-source">' + _h(e.source);
    if (e.source_title) sourceLine += ' · ' + _h(e.source_title);
    sourceLine += '</div>';
  }

  // trigger_event — 事件驱动逻辑
  var triggerHtml = e.trigger_event ? '<div class="ev-trigger"><span class="ev-label">📌 触发</span>' + _h(e.trigger_event) + '</div>' : '';

  // direct_reaction — 市场直接反应
  var reactionHtml = e.direct_reaction ? '<div class="ev-reaction"><span class="ev-label">⚡ 反应</span>' + _h(e.direct_reaction) + '</div>' : '';

  // sector_transmission tags — direction: 看多→bull / 看空→bear / else→neutral
  var sectors = e.sector_transmission || [];
  var sectorTags = '';
  if (sectors.length > 0) {
    sectorTags = sectors.slice(0, 4).map(function(s) {
      var dir = s.direction || '';
      var cls = dir.indexOf('看多') >= 0 ? 'bull' : dir.indexOf('看空') >= 0 ? 'bear' : 'neutral';
      return '<span class="ev-tag ' + cls + '" title="' + _h(s.reason || '') + '">' + _h(s.name) + '</span>';
    }).join('');
  }
  var tagsHtml = sectorTags ? '<div class="ev-tags">' + sectorTags + '</div>' : '';

  // valuation_impact + trading_window — 估值与交易窗口
  var detailHtml = '';
  if (e.valuation_impact || e.trading_window) {
    detailHtml = '<div class="ev-detail">';
    if (e.valuation_impact) detailHtml += '<div class="ev-valuation"><span class="ev-label">💎 估值</span>' + _h(e.valuation_impact) + '</div>';
    if (e.trading_window) detailHtml += '<div class="ev-trading"><span class="ev-label">📊 窗口</span>' + _h(e.trading_window) + '</div>';
    detailHtml += '</div>';
  }

  // stock_mapping chips — type badge: 受益→green / 弹性→orange / else→gray
  var stocks = e.stock_mapping || [];
  var stocksHtml = '';
  if (stocks.length > 0) {
    var chips = stocks.slice(0, 6).map(function(t, ti) {
      var typeCls = t.type === '受益' ? 'st-benefit' : t.type === '弹性' ? 'st-elastic' : 'st-other';
      var reasonId = 'cr-reason-' + idx + '-' + ti;
      return '<span class="ev-stock" onclick="toggleCrReason(\'' + reasonId + '\')">' +
        '<span class="ev-stock-type ' + typeCls + '">' + _h(t.type || '') + '</span>' +
        _h(t.name) + '<small>(' + _h(t.code) + ')</small>' +
        '<div class="ev-stock-reason" id="' + reasonId + '">' + _h(t.logic || '') + '</div>' +
        '</span>';
    });
    var remaining = Math.max(0, stocks.length - 6);
    if (remaining > 0) chips.push('<span class="ev-stock-more">+' + remaining + '</span>');
    stocksHtml = '<div class="ev-stocks">' + chips.join('') + '</div>';
  }

  return '<div class="ev-card">' +
    '<div class="ev-header">' + numHtml + '<span class="ev-title">' + _h(title) + '</span>' + timeHtml + '</div>' +
    sourceLine +
    triggerHtml +
    reactionHtml +
    tagsHtml +
    detailHtml +
    stocksHtml +
    '</div>';
}

// ── V4.2 RSS 时效条 ──
// 根据 dataFreshness.freshnessStatus 渲染颜色编码指示器
// fresh(绿): <6h / degraded(黄): 6-24h / stale(红): >24h / empty/unknown(灰)
function buildCrFreshnessBar(freshness) {
  if (!freshness) return '';
  var status = freshness.freshnessStatus;
  var hours = freshness.freshnessHours;
  var label, barColor, icon;
  if (status === 'fresh') {
    label = hours !== null ? hours + '小时前更新' : '数据新鲜';
    barColor = '#22c55e'; icon = '🟢';
  } else if (status === 'degraded') {
    label = hours !== null ? hours + '小时未更新' : '更新延迟';
    barColor = '#f59e0b'; icon = '🟡';
  } else if (status === 'stale') {
    label = hours !== null ? '已断流 ' + hours + '小时' : '数据过时';
    barColor = '#ef4444'; icon = '🔴';
  } else {
    label = freshness.note || '数据状态未知';
    barColor = '#64748b'; icon = '⚫';
  }
  return '<div class="cr-freshness" style="border-left-color:' + barColor + '">' +
    '<span class="cr-freshness-icon">' + icon + '</span>' +
    '<span class="cr-freshness-label">' + label + '</span>' +
    (freshness.lastArticleTime ? '<span class="cr-freshness-time">' + freshness.lastArticleTime + '</span>' : '') +
    '</div>';
}

// ── V4.1 市场摘要卡片 ──
function buildCrMarketSummary(summary, hotEvents, alpha, etf, commodity) {
  if (!summary) return '';
  var total = (alpha||[]).length + (etf||[]).length + (commodity||[]).length;
  if (total === 0 && (hotEvents||[]).length === 0) return '';

  var l = summary.longCount || 0;
  var s = summary.shortCount || 0;
  var ratio = l / Math.max(s, 1);

  // 温度判断 — V5.3 市场风格语言强化
  // 多头>2.5x空头=强势做多，多头>2x=进攻，多头≥空头=均衡偏多，空头略占优=防御，空头显著占优=强势避险
  var regime, action;
  if (ratio >= 2.5)      { regime = 'offense'; action = '积极加仓，市场风偏极强'; }
  else if (ratio >= 2.0)  { regime = 'offense'; action = '加仓关注，多头显著占优'; }
  else if (l >= s)        { regime = 'balance'; action = '持仓观察，略偏多'; }
  else if (ratio >= 0.4)  { regime = 'defense'; action = '减仓观望，空头略占优'; }
  else                    { regime = 'defense'; action = '空仓避险，空头主导'; }

  var timeStr = '';
  if (summary.newestTime) {
    var diff = (Date.now() - new Date(summary.newestTime).getTime()) / 1000 / 3600;
    timeStr = diff < 1 ? '刚刚更新' : Math.floor(diff) + '小时前更新';
  }

  // 一句话结论 — V5.3 市场语言强化
  var regimeLabel = regime === 'offense' ? (ratio >= 2.5 ? '强势做多' : '进攻') : regime === 'defense' ? (ratio >= 0.4 ? '防御' : '强势避险') : '均衡偏多';
  var parts = [];
  if (total > 0) parts.push(total + '条活跃信号');
  if ((hotEvents||[]).length > 0) parts.push((hotEvents||[]).length + '个热点');
  var thesis = (parts.length > 0 ? '今日' + parts.join('、') + '。' : '') + '多头' + l + '：空头' + s + '，市场偏' + regimeLabel + '。';

  return '<div class="cr-summary-card">' +
    '<div class="cr-summary-top">' +
      '<span class="cr-summary-regime ' + regime + '">' + (regime === 'offense' ? '🔥 进攻' : regime === 'defense' ? '🛡️ 防守' : '⚖️ 均衡') + '</span>' +
      '<span class="cr-summary-action ' + regime + '">' + action + '</span>' +
    '</div>' +
    '<div class="cr-summary-stats">' +
      '<div class="cr-summary-stat"><div class="cr-summary-stat-val" style="color:#3b82f6">' + total + '</div><div class="cr-summary-stat-lbl">活跃信号</div></div>' +
      '<div class="cr-summary-stat"><div class="cr-summary-stat-val" style="color:#22c55e">' + l + '</div><div class="cr-summary-stat-lbl">多头</div></div>' +
      '<div class="cr-summary-stat"><div class="cr-summary-stat-val" style="color:#ef4444">' + s + '</div><div class="cr-summary-stat-lbl">空头</div></div>' +
      '<div class="cr-summary-stat"><div class="cr-summary-stat-val" style="color:#a78bfa">' + (summary.strategyCount||0) + '</div><div class="cr-summary-stat-lbl">策略</div></div>' +
    '</div>' +
    '<div class="cr-summary-thesis">' + thesis + (timeStr ? ' <span style="color:#64748b">' + timeStr + '</span>' : '') + '</div>' +
    '</div>';
}

function buildCrSummaryCards(s) {
  if (!s) return '<div class="nodata">暂无信号数据</div>';
  var conf = s.avgConfidence != null ? Math.round(s.avgConfidence * 100) + '%' : '—';
  return '<div class="cr-cards">' +
    '<div class="cr-card cr-active"><span class="cr-val">' + (s.active||0) + '</span><span class="cr-lbl">活跃信号</span></div>' +
    '<div class="cr-card cr-ratio"><span class="cr-val">' + (s.longCount||0) + '｜' + (s.shortCount||0) + '</span><span class="cr-lbl">多 / 空</span></div>' +
    '<div class="cr-card"><span class="cr-val" style="color:#e2e8f0">' + (s.strategyCount||0) + '</span><span class="cr-lbl">策略数</span></div>' +
    '<div class="cr-card"><span class="cr-val" style="color:#a78bfa">' + conf + '</span><span class="cr-lbl">均信度</span></div>' +
    '</div>';
}

function buildCrCategorySections(hotEvents, alpha, etf, commodity, alpha_latest) {
  // V4.3: alpha 按置信度降序，高置信度优先
  var sortedAlpha = (alpha || []).slice().sort(function(a, b) {
    return (b.confidence || 0) - (a.confidence || 0);
  });
  return (
    _buildCrAlpha(sortedAlpha, alpha_latest) +
    _buildCrEtf(etf || []) +
    _buildCrCommodity(commodity || [])
  );
}

function _buildCrHotEvents(events, noWrapper) {
  var el = events || [];
  var staleHint = '';
  if (el.length > 0 && el[0]._stale) {
    staleHint = '<span style="font-size:10px;color:#f59e0b;margin-left:6px">⚠️ 缓存 · 源暂不可用</span>';
  }
  if (el.length === 0) {
    if (noWrapper) return '';
    return '<div class="cr-section"><div class="cr-section-title"><span class="cr-ico">🔥</span> 热点事件' + staleHint + '</div><div class="nodata">暂无事件</div></div>';
  }

  // 按时间降序 + 限 10 条；提前过滤掉正文缺失且无标的的事件（Q11）
  var _incompleteKws = ['正文缺失', '无法确认', '信息不完整', '但无正文', '但正文缺失'];
  var sorted = el.slice().sort(function(a, b) { return (b.time || '').localeCompare(a.time || ''); });
  var top10 = sorted.filter(function(e) {
    var t = e.thesis || e.title || '';
    var hasIncomplete = _incompleteKws.some(function(kw) { return t.indexOf(kw) >= 0; });
    var isNonMarket = t === '非市场分析内容';
    return !(isNonMarket || (hasIncomplete && (e.tickers || []).length === 0));
  }).slice(0, 10);

  // ── 总体概述段 ──
  // 汇总：有多少事件、涉及哪些板块、整体方向
  var allSectors = [];
  var bullCount = 0, bearCount = 0;
  top10.forEach(function(e) {
    if (e.sectors && e.sectors.length) {
      e.sectors.forEach(function(s) { if (allSectors.indexOf(s) < 0) allSectors.push(s); });
    }
    var d = (e.direction || e.thesis || '').toLowerCase();
    if (d.indexOf('看多') >= 0 || d.indexOf('利好') >= 0 || d.indexOf('做多') >= 0) bullCount++;
    else if (d.indexOf('看空') >= 0 || d.indexOf('利空') >= 0 || d.indexOf('做空') >= 0) bearCount++;
  });
  var overallDir = bullCount > bearCount ? '🔥 整体偏多' : bearCount > bullCount ? '🛡️ 整体偏空' : '⚖️ 多空均衡';
  var sectorStr = allSectors.slice(0, 5).join(' · ') + (allSectors.length > 5 ? ' 等' : '');
  var summaryHtml = '<div class="cr-hot-overview">' +
    '<div class="cr-hot-overview-line">' +
      '<span class="cr-hot-overview-badge">' + overallDir + '</span>' +
      '<span class="cr-hot-overview-count">共 ' + top10.length + ' 条热点</span>' +
    '</div>' +
    (sectorStr ? '<div class="cr-hot-overview-sectors">涉及板块：' + _h(sectorStr) + '</div>' : '') +
    '</div>';

  // ── 热点事件列表 ──
  var items = top10.map(function(e, idx) {
    // 时间：优先 event_time，其次 time，格式化为相对时间
    var rawTime = e.event_time || e.time || '';
    var timeStr = rawTime ? formatRelativeTime(rawTime) : '';
    var absTime = rawTime ? rawTime.replace('T', ' ').slice(0, 16) : '';

    // 溯源：信源名 + 原标题
    var sourceHtml = '';
    if (e.source || e.mp_name) {
      sourceHtml = '<div class="cr-hot-source">' +
        '<span class="cr-hot-src-name">📰 ' + _h(e.source || e.mp_name) + '</span>' +
        (e.source_title ? '<span class="cr-hot-src-title"> · 《' + _h(e.source_title) + '》</span>' : '') +
        (absTime ? '<span class="cr-hot-src-time"> · ' + absTime + (timeStr ? ' (' + timeStr + ')' : '') + '</span>' : '') +
        '</div>';
    } else if (rawTime) {
      sourceHtml = '<div class="cr-hot-source"><span class="cr-hot-src-time">🕐 ' + absTime + (timeStr ? ' (' + timeStr + ')' : '') + '</span></div>';
    }

    // 摘要/thesis
    var thesis = e.thesis || e.title || '';
    var summaryText = thesis.length > 80 ? thesis.slice(0, 77) + '...' : thesis;

    // 标的
    var tickers = e.tickers || [];
    var tickerHtml = '';
    if (tickers.length > 0) {
      tickerHtml = '<div class="cr-hot-tickers">' +
        tickers.map(function(tk) {
          var label = (tk.code || '') + (tk.name ? ' ' + _h(tk.name) : '');
          var reason = tk.reason ? _h(tk.reason) : '';
          return '<span class="cr-ticker-item">📌 <strong>' + _h(label) + '</strong>' +
            (reason ? '<span class="cr-ticker-reason"> — ' + reason + '</span>' : '') +
            '</span>';
        }).join('') +
        '</div>';
    }

    var wechatBadge = idx === 0 ? '<span class="cr-hot-wechat-badge">📱 微信素材</span>' : '';
    return '<div class="cr-hot-card">' +
      '<div class="cr-hot-label">重点事件 ' + (idx + 1) + wechatBadge + '</div>' +
      sourceHtml +
      '<div class="cr-hot-summary">' + _h(summaryText) + '</div>' +
      tickerHtml +
      '</div>';
  }).join('');

  if (noWrapper) return summaryHtml + items;
  return '<div class="cr-section">' +
    '<div class="cr-section-title"><span class="cr-ico">🔥</span> 热点事件' + staleHint + '</div>' +
    summaryHtml + items +
    '</div>';
}

function _buildCrAlpha(signals, alpha_latest) {
  // V4.4: enrich alpha signals with latest contract data (entry/target/stop/thesis)
  var enriched = (signals || []).slice();
  var alSignals = alpha_latest && alpha_latest.signals ? alpha_latest.signals : (Array.isArray(alpha_latest) ? alpha_latest : []);
  if (alSignals.length) {
    var alMap = {};
    alSignals.forEach(function(al) { if (al.code) alMap[al.code] = al; });
    enriched.forEach(function(s) {
      if (alMap[s.asset]) s._alphaLatest = alMap[s.asset];
    });
  }
  // V5.3: 按置信度降序排列 + 限 10 条
  enriched.sort(function(a, b) { return (b.confidence || 0) - (a.confidence || 0); });
  var limited = enriched.slice(0, 10);
  return _buildCrSignalGroup('📈', 'alpha', limited, '#22c55e');
}

function _buildCrEtf(signals) {
  return _buildCrSignalGroup('📊', 'ETF', signals || [], '#3b82f6');
}

function _buildCrCommodity(signals) {
  return _buildCrSignalGroup('🛢️', '商品', signals || [], '#ef4444');
}

function _buildCrSignalGroup(icon, label, signals, color) {
  if (signals.length === 0) {
    return '<div class="cr-section"><div class="cr-section-title"><span class="cr-ico">' + icon + '</span> ' + label + '</div><div class="nodata">暂无信号</div></div>';
  }
  var items = signals.map(function(s) {
    var isLong = s.direction === 'long';
    var conf = s.confidence != null ? Math.round(s.confidence * 100) : 0;
    var confColor = conf >= 80 ? '#22c55e' : conf >= 60 ? '#f59e0b' : '#ef4444';
    var meta = s.metadata || {};
    var displayName = meta.stock_name || s.asset || '—';
    var codeHtml = meta.stock_name ? '<span class="cr-sig-code">' + _h(s.asset) + '</span>' : '';
    var tags = '';
    if (meta.tier) tags += '<span class="cr-tag cr-tag-tier">' + _h(meta.tier) + '</span>';
    var reasons = meta.reasons || meta.active_factors || [];
    if (meta.notice_type) reasons = [meta.notice_type].concat(reasons);
    reasons.slice(0,3).forEach(function(r) { tags += '<span class="cr-tag cr-tag-reason">' + _h(r) + '</span>'; });
    var hint = '';
    // V4.3: ETF 显示 etf_code + 行业轮动因子
    if (meta.etf_code) hint = '📊 ' + _h(meta.etf_code);
    if (meta.industry_hint && meta.industry_count) hint += (hint ? ' · ' : '') + '行业: ' + _h(meta.industry_hint) + ' · ' + meta.industry_count + '条同行业';
    else if (meta.industry_hint) hint += (hint ? ' · ' : '') + '行业: ' + _h(meta.industry_hint);
    // V4.3: 商品显示价格变化
    if (meta.chg_pct != null) hint += (hint ? ' · ' : '') + (meta.chg_pct > 0 ? '+' : '') + meta.chg_pct + '%' + (meta.price ? ' @' + meta.price : '');
    // 原有逻辑
    if (meta.score_auto) hint += (hint ? ' · ' : '') + '得分' + meta.score_auto + ' · ' + (meta.stage||'') + (meta.rank ? ' · 排名#' + meta.rank : '');
    if (!meta.etf_code && !meta.industry_hint && !meta.chg_pct && !meta.score_auto && meta.price_5d_pct != null) hint = '近5日 ' + (meta.price_5d_pct > 0 ? '+' : '') + meta.price_5d_pct + '%';

    // V4.1: build detail section for expanded state
    var detail = _buildCrSignalDetail(s, icon);

    // V4.3: Alpha 高置信度推荐标识
    var recBadge = (label === 'alpha' && conf >= 80) ? '<span class="cr-rec-badge">推荐</span>' : '';

    return '<div class="cr-sig-card ' + (isLong ? 'cr-sig-long' : 'cr-sig-short') + ' cr-sig-expandable" onclick="toggleCrCard(this)">' +
      '<span class="cr-dir ' + (isLong ? 'cr-dir-long' : 'cr-dir-short') + '">' + (isLong ? '多' : '空') + '</span>' +
      '<div class="cr-sig-asset">' + _h(displayName) + codeHtml + '</div>' +
      '<div class="cr-sig-meta">' +
        '<span class="cr-sig-actionable">' + _buildActionableHint(s) + '</span>' +
        '<span class="cr-sig-strat">' + _h(s.strategy||'') + '</span> · ' + _h(s.assetType||'') +
        (tags || hint ? '<div class="cr-tags">' + tags + (hint ? '<span style="font-size:10px;color:#94a3b8;margin-left:4px">' + _h(hint) + '</span>' : '') + '</div>' : '') +
      '</div>' +
      '<div class="cr-conf">' +
        '<span class="cr-conf-val" style="color:' + confColor + '">' + conf + '%</span>' +
        '<div class="cr-conf-bar"><div class="cr-conf-fill" style="background:' + confColor + ';width:' + conf + '%"></div></div>' +
      '</div>' +
      '<div class="cr-sig-detail">' + detail + '</div>' +
    '</div>';
  }).join('');
  return '<div class="cr-section"><div class="cr-section-title"><span class="cr-ico">' + icon + '</span> ' + label + '</div>' + items + '</div>';
}

// ── V4.1 信号卡片展开详情 ──
function _buildCrSignalDetail(s, icon) {
  var meta = s.metadata || {};
  var html = '<div class="cr-detail-grid">';

  // 1. 有效期
  if (s.expiry) {
    var expiryMs = new Date(s.expiry).getTime();
    var diff = expiryMs - Date.now();
    var days = Math.max(0, Math.ceil(diff / (1000 * 60 * 60 * 24)));
    var cls, txt;
    if (diff <= 0) { cls = 'cr-detail-expiry-expired'; txt = '已过期'; }
    else if (days <= 3) { cls = 'cr-detail-expiry-warn'; txt = days + '天后过期'; }
    else { cls = 'cr-detail-expiry-ok'; txt = days + '天后过期'; }
    html += '<div class="cr-detail-cell"><span class="cr-detail-lbl">有效期</span><span class="cr-detail-val ' + cls + '">' + txt + '</span></div>';
  }

  // 2. 置信度
  var conf = s.confidence != null ? Math.round(s.confidence * 100) : 0;
  var confLvl = conf >= 80 ? '高' : conf >= 60 ? '中' : '低';
  html += '<div class="cr-detail-cell"><span class="cr-detail-lbl">置信度</span><span class="cr-detail-val">' + conf + '% (' + confLvl + ')</span></div>';

  // 3. R:R (from metadata if available)
  if (meta.rr != null) {
    html += '<div class="cr-detail-cell"><span class="cr-detail-lbl">盈亏比 R:R</span><span class="cr-detail-val" style="color:' + (meta.rr >= 1.5 ? '#22c55e' : '#f59e0b') + '">' + meta.rr + ':1</span></div>';
  } else if (meta.score_auto != null) {
    html += '<div class="cr-detail-cell"><span class="cr-detail-lbl">综合得分</span><span class="cr-detail-val">' + meta.score_auto + '</span></div>';
  }

  // 4. 信号ID (for debugging)
  if (s.signal_id) {
    html += '<div class="cr-detail-cell"><span class="cr-detail-lbl">信号ID</span><span class="cr-detail-val" style="font-size:9px;font-family:monospace;color:#64748b">' + _h(s.signal_id.split('-')[0]) + '</span></div>';
  }

  // 5. 阶段/排名
  if (meta.stage || meta.rank) {
    html += '<div class="cr-detail-cell"><span class="cr-detail-lbl">阶段/排名</span><span class="cr-detail-val">' + (meta.stage ? _h(meta.stage) + ' ' : '') + (meta.rank ? '#' + meta.rank : '') + '</span></div>';
  }

  // 6. 行业
  if (meta.industry_hint) {
    html += '<div class="cr-detail-cell"><span class="cr-detail-lbl">关联行业</span><span class="cr-detail-val" style="font-size:11px">' + _h(meta.industry_hint) + (meta.industry_count ? ' (' + meta.industry_count + '条)' : '') + '</span></div>';
  }

  html += '</div>';

  // V4.4: alpha_latest 合约详情 — entry/target/stop/thesis
  if (s._alphaLatest) {
    var al = s._alphaLatest;
    html += '<div class="cr-detail-grid" style="margin-top:8px;padding:8px;background:rgba(34,197,94,0.04);border-radius:8px;border:1px solid rgba(34,197,94,0.12)">';
    html += '<div class="cr-detail-cell"><span class="cr-detail-lbl" style="font-weight:600;color:#22c55e">合约快照</span><span class="cr-detail-val" style="font-size:10px;color:#64748b">' + (al.time_window || '') + ' · ' + _h(al.event_source || '') + '</span></div>';
    html += '<div class="cr-detail-cell"><span class="cr-detail-lbl">入场价</span><span class="cr-detail-val" style="font-weight:600">' + (al.entry_price != null ? al.entry_price.toFixed(2) : '—') + '</span></div>';
    html += '<div class="cr-detail-cell"><span class="cr-detail-lbl">目标价</span><span class="cr-detail-val" style="color:#22c55e;font-weight:600">' + (al.target_price != null ? al.target_price.toFixed(2) : '—') + '</span></div>';
    html += '<div class="cr-detail-cell"><span class="cr-detail-lbl">止损价</span><span class="cr-detail-val" style="color:#ef4444;font-weight:600">' + (al.stop_loss != null ? al.stop_loss.toFixed(2) : '—') + '</span></div>';
    if (al.thesis) html += '<div class="cr-detail-cell" style="grid-column:1/-1"><span class="cr-detail-lbl">核心论点</span><span class="cr-detail-val" style="font-size:11px;line-height:1.5;color:#e2e8f0">' + _h(al.thesis) + '</span></div>';
    if (al.sector_context) html += '<div class="cr-detail-cell" style="grid-column:1/-1"><span class="cr-detail-lbl">行业背景</span><span class="cr-detail-val" style="font-size:11px;line-height:1.5;color:#94a3b8">' + _h(al.sector_context) + '</span></div>';
    html += '</div>';
  }

  // All tags expanded
  var reasons = meta.reasons || meta.active_factors || [];
  if (meta.notice_type) reasons = [meta.notice_type].concat(reasons);
  if (meta.tier) reasons = [meta.tier].concat(reasons);
  if (reasons.length > 0) {
    html += '<div class="cr-all-tags">';
    reasons.forEach(function(r) {
      html += '<span class="cr-tag cr-tag-reason">' + _h(r) + '</span>';
    });
    html += '</div>';
  }

  // V4.3: 多空选择标准 — 展开详情中显式化
  var dirLabel = s.direction === 'long' ? '看多理由' : '看空理由';
  var dirReasons = meta.reasons || meta.active_factors || meta.notice_type ? [meta.notice_type].filter(Boolean).concat(meta.reasons || meta.active_factors || []) : [];
  if (dirReasons.length > 0) {
    var borderColor = s.direction === 'long' ? 'rgba(34,197,94,0.3)' : 'rgba(239,68,68,0.3)';
    html += '<div class="cr-dir-reason" style="border-left-color:' + borderColor + '">';
    html += '<div class="cr-dir-reason-label">📋 ' + _h(dirLabel) + '</div>';
    dirReasons.forEach(function(r) {
      html += '<div class="cr-dir-reason-item">▸ ' + _h(r) + '</div>';
    });
    html += '</div>';
  }

  return html;
}

// ── V4.1.1 信号卡片 actionable 描述 ──
function _buildActionableHint(s) {
  var conf = s.confidence != null ? Math.round(s.confidence * 100) : 0;
  var meta = s.metadata || {};
  var dir = s.direction === 'long' ? '看多' : '看空';
  var parts = [];

  // 核心置信度分级建议（来自 CONTEXT.md 方法论）
  if (conf >= 80) {
    parts.push('高置信度' + dir);
    if (meta.rr && meta.rr >= 1.5) parts.push('R:R ' + meta.rr + ':1 达标');
    else if (meta.rr) parts.push('R:R ' + meta.rr + ':1');
  } else if (conf >= 60) {
    parts.push('中等置信度' + dir);
    parts.push('建议二次确认');
  } else {
    parts.push('低置信度' + dir);
    parts.push('仅作参考');
  }

  // 过期时间告警
  if (s.expiry) {
    var expiryMs = new Date(s.expiry).getTime();
    var diff = expiryMs - Date.now();
    var days = Math.ceil(diff / (1000*60*60*24));
    if (diff <= 0) parts.unshift('已过期');
    else if (days <= 3) parts.push(days + '天后到期');
  }

  // 附加上下文
  if (meta.stage && conf < 80) parts.push(meta.stage + '阶段');
  if (meta.price_5d_pct != null) {
    parts.push('近5日' + (meta.price_5d_pct > 0 ? '+' : '') + meta.price_5d_pct + '%');
  }

  return parts.join(' · ');
}

// V4.1: Toggle signal card expansion
function toggleCrCard(el) {
  el.classList.toggle('cr-sig-expanded');
}

// V4.1: Attach click-outside-to-collapse (no-op, cards self-toggle)
function attachCrExpandHandlers() {
  // Future: add delegation or swipe-to-expand
}
// V5.2: 移动端个股理由 onclick 展开收起（移动端 title hover 不可用，方案 b）
function toggleCrReason(id) {
  var el = document.getElementById(id);
  if (!el) return;
  if (el.style.display === 'block') {
    el.style.display = 'none';
    el.parentElement.classList.remove('expanded');
  } else {
    el.style.display = 'block';
    el.parentElement.classList.add('expanded');
  }
}
function _h(s) {
  if (s == null) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── 相对时间格式化 ──
function formatRelativeTime(iso) {
  if (!iso) return '';
  var diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return '刚刚';
  if (diff < 3600) return Math.floor(diff / 60) + '分钟前';
  if (diff < 86400) return Math.floor(diff / 3600) + '小时前';
  return Math.floor(diff / 86400) + '天前';
}

// ====== Helpers ======
function fmtNum(n) { if (n==null) return '—'; return Number(n).toFixed(2); }
function refreshAll() {
  loaded = {};
  document.getElementById('overview-content').style.display = 'none';
  document.getElementById('overview-loading').style.display = 'block';
  document.getElementById('wl-content').style.display = 'none';
  document.getElementById('wl-loading').innerHTML = '<div class="spin"></div>';
  document.getElementById('wl-loading').style.display = 'block';
  document.getElementById('cr-content').style.display = 'none';
  document.getElementById('cr-loading').innerHTML = '<div class="spin"></div>';
  document.getElementById('cr-loading').style.display = 'block';
  var active = document.querySelector('.m-tab.active');
  if (active) loadTab(active.dataset.tab);
}
