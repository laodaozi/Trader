// ====== Tab Switching ======
function escHtml(s) { return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
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
  else if (name === 'world') loadWorld();
  else if (name === 'graph') loadGraph();
}
loadOverview(); // initial load

// ====== Overview Tab ======
// V7.1: 择时卡 — 合并 timing + global_conclusion，大字结论 + 动态色
function buildTimingCard(t, n) {
  if (!t) return '';
  const temp = t.temperature || 0;
  const phase = t.phase || '—';
  const advice = t.advice || '';
  const gc = (n && typeof n.global_conclusion === 'string') ? {} : ((n && n.global_conclusion) || {});
  // 动态颜色
  let accentColor, accentBg, verdict;
  if (phase === '进攻' || temp >= 70) {
    accentColor = 'var(--m-positive)'; accentBg = 'rgba(var(--m-positive-rgb),0.08)';
    verdict = '积极做多';
  } else if (phase === '防守' || temp <= 30) {
    accentColor = 'var(--m-negative)'; accentBg = 'rgba(var(--m-negative-rgb),0.08)';
    verdict = '防守观望';
  } else {
    accentColor = 'var(--m-warn)'; accentBg = 'rgba(var(--m-warn-rgb),0.08)';
    verdict = advice || '轻仓观望';
  }
  // 温度进度条 10格
  const filled = Math.round(temp / 10);
  const bars = Array.from({length:10}, (_,i) =>
    `<div style="flex:1;height:5px;border-radius:3px;background:${i < filled ? accentColor : 'var(--m-surface-2)'};transition:background 0.3s"></div>`
  ).join('');
  return `<div class="card" style="border-left:3px solid ${accentColor};background:${accentBg}">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px">
      <div>
        <div style="font-size:11px;font-weight:700;color:var(--m-text-3);letter-spacing:1px;text-transform:uppercase;margin-bottom:4px">今日择时</div>
        <div style="font-size:28px;font-weight:800;color:${accentColor};letter-spacing:-0.5px;line-height:1">${verdict}</div>
        <div style="font-size:12px;color:var(--m-text-2);margin-top:5px">${phase} · 市场温度 ${temp}</div>
      </div>
      <div style="text-align:right;flex-shrink:0;margin-left:12px">
        <div style="font-size:36px;font-weight:900;color:${accentColor};opacity:0.9;line-height:1">${temp}</div>
        <div style="font-size:9px;color:var(--m-text-3);letter-spacing:0.5px">/ 100</div>
      </div>
    </div>
    <div style="display:flex;gap:4px">${bars}</div>
  </div>`;
}

// V7.1: Top5 共振标的 — 轮动快照方向 × alpha/etf 信号过滤
function buildTop5Card(snap, cr) {
  if (!cr) return '';
  const alpha = cr.alpha || [];
  const etf = cr.etf || [];
  const snapDir = snap ? (snap.direction || '') : '';

  // 关键词提取（半导体/科技/消费/医药/军工/有色等）
  const keywords = ['半导体','芯片','科技','AI','算力','医药','军工','有色','消费','地产','新能源','储能','光伏','汽车','银行','券商'];
  const matchKw = keywords.filter(kw => snapDir.includes(kw));

  // 信号评分：置信度 × 策略权重 × 关键词匹配加成
  const STRAT_WEIGHT = {'report_agent':1.4,'scanner':1.2,'ma_signals':1.3,'wanjun_models':1.1,'stock_agent':1.0,'rotation_factor':1.2};
  function scoreSignal(s) {
    const base = (s.confidence || 0) * (STRAT_WEIGHT[s.strategy] || 1.0);
    const meta = s.metadata || {};
    const name = meta.stock_name || meta.sector || s.asset || '';
    const kwBonus = matchKw.some(kw => name.includes(kw) || (meta.sector||'').includes(kw)) ? 1.3 : 1.0;
    return base * kwBonus;
  }

  // 合并 alpha + etf，只取 long 方向，按得分排序
  const all = [...alpha.filter(s=>s.direction==='long'), ...etf.filter(s=>s.direction==='long'||s.direction==='neutral')];
  const scored = all.map(s => ({...s, _score: scoreSignal(s)}))
    .sort((a,b) => b._score - a._score)
    .slice(0, 5);

  if (!scored.length) return '';

  const STRAT_LABEL = {'report_agent':'事件','scanner':'形态','ma_signals':'并购','wanjun_models':'量化','stock_agent':'AI','rotation_factor':'轮动','commodity_radar':'商品'};
  const rows = scored.map((s, i) => {
    const meta = s.metadata || {};
    const name = meta.stock_name || meta.sector || s.asset;
    const conf = Math.round((s.confidence||0)*100);
    const strat = STRAT_LABEL[s.strategy] || s.strategy;
    const stars = conf >= 80 ? '★★★' : conf >= 60 ? '★★☆' : '★☆☆';
    const starColor = conf >= 80 ? 'var(--m-positive)' : conf >= 60 ? 'var(--m-primary)' : 'var(--m-warn)';
    const isWatchlist = false; // TODO: 接 watchlist 匹配
    return `<div class="cr-sig-expandable cr-sig-card cr-sig-long" onclick="this.classList.toggle('cr-sig-expanded')" style="cursor:pointer;margin-bottom:6px;border-left-width:3px">
      <div style="font-size:12px;font-weight:700;color:var(--m-text-3);width:16px;flex-shrink:0">${i+1}</div>
      <div style="flex:1;min-width:0">
        <div style="display:flex;align-items:center;gap:6px">
          <span style="font-size:13px;font-weight:700;color:var(--m-text)">${_h(name)}</span>
          <span style="font-size:10px;color:var(--m-text-3);font-family:var(--m-mono)">${s.asset}</span>
          ${isWatchlist ? '<span style="font-size:9px;background:rgba(var(--m-positive-rgb),0.15);color:var(--m-positive);padding:1px 5px;border-radius:3px;font-weight:700">自选</span>' : ''}
        </div>
        <div style="display:flex;gap:6px;margin-top:3px;flex-wrap:wrap">
          <span style="font-size:10px;padding:1px 7px;border-radius:3px;font-weight:600;background:rgba(var(--m-primary-rgb),0.12);color:var(--m-primary)">${strat}</span>
          ${meta.tier ? `<span style="font-size:10px;padding:1px 7px;border-radius:3px;font-weight:600;background:rgba(var(--m-positive-rgb),0.1);color:var(--m-positive)">${meta.tier}级</span>` : ''}
        </div>
      </div>
      <div style="text-align:right;flex-shrink:0">
        <div style="font-size:13px;font-weight:700;color:${starColor}">${stars}</div>
        <div style="font-size:11px;color:var(--m-text-3);margin-top:1px">${conf}%</div>
      </div>
    </div>`;
  }).join('');

  const title = matchKw.length ? `共振 Top5 · ${matchKw.slice(0,2).join('/')}方向` : '共振 Top5';
  return `<div class="card" style="border-left:3px solid var(--m-primary)">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <div class="card-title" style="margin-bottom:0">📡 ${title}</div>
      <div style="font-size:10px;color:var(--m-text-3)">${scored.length} 个信号</div>
    </div>
    ${rows}
    <div style="font-size:10px;color:var(--m-text-3);margin-top:6px;text-align:right">点击展开详情</div>
  </div>`;
}

// V7.1: 自选池异动卡 — 只展示有买卖信号的
function buildWatchlistAlertCard(d) {
  const stocks = (d.strategy && d.strategy.stocks) || [];
  // 只取进攻/买入信号，按 score 排序，最多5条
  const alerts = stocks.filter(s => s.signal_type && (s.signal_type.includes('进攻') || s.signal_type.includes('买入')))
    .sort((a,b) => (b.score||0) - (a.score||0))
    .slice(0, 5);
  if (!alerts.length) return '';

  const rows = alerts.map(s => {
    const score = s.score || 0;
    const scoreColor = score >= 70 ? 'var(--m-positive)' : score >= 50 ? 'var(--m-warn)' : 'var(--m-text-2)';
    const ep = s.entry_low ? s.entry_low.toFixed(2) : '—';
    const sl = s.stop_loss ? s.stop_loss.toFixed(2) : '—';
    return `<div style="display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid var(--m-border)">
      <div style="flex:1;min-width:0">
        <div style="font-size:13px;font-weight:600;color:var(--m-text)">${_h(s.name||s.code)} <span style="font-size:10px;color:var(--m-text-3);font-family:var(--m-mono)">${s.code}</span></div>
        <div style="font-size:10px;color:var(--m-text-3);margin-top:2px">入场 ${ep} · 止损 ${sl}</div>
      </div>
      <div style="text-align:right;flex-shrink:0">
        <div style="font-size:20px;font-weight:800;color:${scoreColor}">${score}</div>
        <div style="font-size:9px;color:var(--m-text-3)">评分</div>
      </div>
    </div>`;
  }).join('');

  return `<div class="card" style="border-left:3px solid var(--m-positive)">
    <div class="card-title" style="margin-bottom:4px">✅ 自选池信号</div>
    <div style="font-size:11px;color:var(--m-text-3);margin-bottom:10px">${alerts.length} 支有买入信号</div>
    ${rows}
  </div>`;
}

function buildRotationSnapshotCard(snap) {
  if (!snap || !snap.direction) return '';
  const conf = snap.confidence || 0;
  const filled = Math.round(conf / 12.5);
  const bar = '█'.repeat(filled) + '░'.repeat(8 - filled);
  const upd = snap.updated_at ? snap.updated_at.slice(0,16).replace('T',' ') : '';
  const rows = [
    snap.catalyst     && `<div class="snap-row"><span class="snap-label">催化剂</span>${snap.catalyst}</div>`,
    snap.evidence     && `<div class="snap-row"><span class="snap-label">证据</span>${snap.evidence}</div>`,
    snap.lead_signals && `<div class="snap-row"><span class="snap-label">领先</span>${snap.lead_signals}</div>`,
    snap.watchlist    && `<div class="snap-row"><span class="snap-label">标的</span>${snap.watchlist}</div>`,
    snap.doubt        && `<div class="snap-row snap-doubt"><span class="snap-label">存疑</span>${snap.doubt}</div>`,
  ].filter(Boolean).join('');
  return `<div class="card rotation-snap-card">
    <div class="card-title">🧭 当前轮动判断</div>
    <div class="snap-header">
      <span class="snap-direction">${snap.direction}</span>
      <span class="snap-conf" style="font-family:var(--m-mono)">${bar} ${conf}%</span>
    </div>
    ${rows}
    ${upd ? `<div class="snap-updated">更新于 ${upd}</div>` : ''}
  </div>`;
}

async function loadOverview() {
  try {
    const [sumRes, hyRes, crRes, reflRes, stratRes] = await Promise.all([
      fetch('/m/api/summary'),
      fetch('/m/api/haoyunge'),
      fetch('/m/api/cycleradar'),
      fetch('/m/api/reflection/summary'),
      fetch('/m/api/strategy-report'),
    ]);
    const d = await sumRes.json();
    const hy = hyRes.ok ? await hyRes.json() : null;
    const cr = crRes.ok ? await crRes.json() : null;
    const refl = reflRes.ok ? await reflRes.json() : null;
    const strat = stratRes.ok ? await stratRes.json() : null;
    const el = document.getElementById('overview-content');
    const crEn = cr && cr.event_narrative ? cr.event_narrative : (d.event_narrative || null);
    el.innerHTML =
      buildTodayActionCard(crEn, d.timing) +
      buildTimingCard(d.timing, d.event_narrative) +
      buildTop5Card(d.rotation_snapshot, cr) +
      buildWatchlistAlertCard(d) +
      buildLoopLearningCard(refl) +
      buildNarrativeCard(d.event_narrative) +
      buildHaoYunCard(hy) +
      buildStrategyReportCard(strat) +
      buildTrackerHitCard(d.tracker) +
      buildInsightPanel();
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

// ── V7.9: 今日行动清单 — event_narrative 事件→可执行操作（Tab1 顶部）
// 定位：3条精华操作指令（做多/规避/观察），不展示完整事件解读（Tab3 信号Tab有完整版）
function buildTodayActionCard(en, timing) {
  var events = (en && en.events) ? en.events.slice().sort(function(a,b){ return (a.rank||99)-(b.rank||99); }).slice(0,3) : [];
  if (events.length === 0) return '';

  var gc = (en && en.global_conclusion) ? en.global_conclusion : {};
  var gcText = typeof gc === 'string' ? gc : (gc.summary || gc.key_thesis || '');
  var phase = timing ? timing.phase : '';
  var phaseColor = phase === '进攻' ? '#4ade80' : phase === '防守' ? '#f87171' : '#f59e0b';

  var rows = events.map(function(ev) {
    var stocks = ev.stock_mapping || ev.tickers || [];
    var top2 = stocks.slice(0, 2).map(function(s) {
      var typeCls = (s.type === '受益' || s.type === 'long') ? 'tac-long' : (s.type === '规避' || s.type === 'short') ? 'tac-short' : 'tac-neutral';
      return '<span class="tac-ticker ' + typeCls + '">' + _h(s.name || s.code || '') + '</span>';
    }).join('');
    var more = stocks.length > 2 ? '<span class="tac-more">+' + (stocks.length-2) + '</span>' : '';

    var hasShort = stocks.some(function(s){ return s.type==='规避'||s.type==='short'; });
    var hasLong  = stocks.some(function(s){ return s.type==='受益'||s.type==='long'; });
    var dirTag = hasLong ? '<span class="tac-dir long">做多</span>' : hasShort ? '<span class="tac-dir short">规避</span>' : '<span class="tac-dir neutral">观察</span>';

    // 只取事件核心（≤20字），不展示完整解读
    var title = (ev.title || '').slice(0, 20) + ((ev.title||'').length > 20 ? '…' : '');

    return '<div class="tac-row">' +
      dirTag +
      '<div class="tac-body">' +
        '<div class="tac-title">' + _h(title) + '</div>' +
        (top2 || more ? '<div class="tac-stocks">' + top2 + more + '</div>' : '') +
      '</div>' +
    '</div>';
  }).join('');

  var freshStr = '';
  if (en && en.generated_at) {
    var diffH = Math.round((Date.now() - new Date(en.generated_at).getTime()) / 3600000);
    freshStr = '<span class="tac-fresh">' + (diffH < 1 ? '刚刚' : diffH + 'h前') + '</span>';
  }

  return '<div class="card tac-card">' +
    '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">' +
      '<div class="card-title" style="margin-bottom:0">⚡ 今日行动' + freshStr + '</div>' +
      '<span style="font-size:10px;color:var(--m-text-3)">完整解读 → 信号Tab</span>' +
    '</div>' +
    (gcText ? '<div class="tac-gc">' + _h(gcText.slice(0, 50)) + (gcText.length > 50 ? '…' : '') + '</div>' : '') +
    rows +
  '</div>';
}

// V6.5: 今日操作建议卡片 — 融合市场体温 + 研判 + 胜率 + 风险
function buildActionCard(d) {
  var t = d.timing;
  var n = d.event_narrative;
  var gc = (n && n.global_conclusion) ? n.global_conclusion : {};
  var regime = gc.market_regime || gc.regime || (t ? t.phase : '') || '未知';
  var posPct = t ? t.positionRatio : 0;

  // 仓位建议
  var posAdvice = '';
  if (posPct > 0.6) posAdvice = '仓位偏重，注意止盈';
  else if (posPct > 0.3) posAdvice = '仓位适中，可正常操作';
  else posAdvice = '仓位偏轻，可择机加仓';

  // 操作基调（融合 regime 判断 + timing 阶段）
  var tone = '';
  if (regime.includes('牛') || regime.includes('上涨') || regime.includes('进攻')) tone = '🐂 积极做多，抓主线';
  else if (regime.includes('熊') || regime.includes('下跌') || regime.includes('防守')) tone = '🐻 防守为主，减仓观望';
  else if (regime.includes('震荡')) tone = '📊 高抛低吸，快进快出';
  else tone = '🌐 信号不明，轻仓试探';

  // 跟踪胜率警告
  var wl = d.tracker;
  var wrWarn = '';
  if (wl && wl.hitRate != null && wl.hitRate < 50 && wl.totalDecisions > 0) {
    wrWarn = '<div style="font-size:10px;color:#f59e0b;margin-top:4px">⚠ 跟踪胜率 ' + wl.hitRate + '%，严格止损</div>';
  }

  // 研判给出的操作方向
  var actionText = gc.action || '';

  // 风险列表
  var risks = gc.risk_warnings || [];
  var riskHtml = risks.slice(0, 3).map(function(r) {
    return '<span style="font-size:9px">▪ ' + _h(r) + '</span>';
  }).join('<br>');

  return '<div class="card">' +
    '<div class="card-title">📊 今日操作建议</div>' +
    '<div style="font-size:11px;line-height:1.6;color:#cbd5e1;margin:6px 0">' + tone + '</div>' +
    '<div style="font-size:11px;color:#94a3b8">' + posAdvice + '</div>' +
    (actionText ? '<div style="font-size:10px;color:#f59e0b;margin-top:4px">📌 ' + _h(actionText) + '</div>' : '') +
    wrWarn +
    (riskHtml ? '<div style="margin-top:6px;font-size:9px;color:#64748b">' + riskHtml + '</div>' : '') +
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

// ── V7.8+ Loop Learning 卡片 — 策略反思摘要 + 改进建议 + 下次关注 ──
function buildLoopLearningCard(refl) {
  if (!refl || !refl.summary) return '';

  var summary = refl.summary || '';
  var ll = refl.loop_learning || {};
  var lesson = ll.key_lesson || '';
  var nextFocus = ll.next_focus || '';
  var stratWeights = ll.strategy_weights || {};
  var actionItems = refl.action_items || [];
  var improve = refl.improvement_advice || '';
  var freshStr = '';
  if (refl.generated_at) {
    var diffH = Math.round((Date.now() - new Date(refl.generated_at).getTime()) / 3600000);
    freshStr = '<span style="font-size:9px;color:var(--m-text-3);font-weight:400;margin-left:6px">' +
      (diffH < 1 ? '刚刚' : diffH + 'h前') + '</span>';
  }

  // 策略置信度小徽章
  var weightHtml = '';
  var STRAT_NAMES = {'report_agent':'事件', 'scanner':'形态', 'wanjun_models':'量化'};
  var weightEntries = Object.entries(stratWeights);
  if (weightEntries.length) {
    weightHtml = '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">';
    weightEntries.forEach(function(kv) {
      var name = STRAT_NAMES[kv[0]] || kv[0];
      var score = Math.round(kv[1]);
      var scoreColor = score >= 70 ? 'var(--m-positive)' : score >= 50 ? 'var(--m-warn)' : 'var(--m-negative)';
      weightHtml += '<div style="background:var(--m-surface-2);border:1px solid var(--m-border);border-radius:6px;padding:4px 8px;text-align:center;min-width:56px">' +
        '<div style="font-size:14px;font-weight:700;color:' + scoreColor + '">' + score + '</div>' +
        '<div style="font-size:9px;color:var(--m-text-3);margin-top:1px">' + _h(name) + '</div>' +
      '</div>';
    });
    weightHtml += '</div>';
  }

  // 可执行项（最多3条）
  var actHtml = '';
  var highItems = actionItems.filter(function(a){ return a.priority === 'high'; });
  var showItems = (highItems.length ? highItems : actionItems).slice(0, 3);
  if (showItems.length) {
    actHtml = '<div style="margin-top:8px;display:flex;flex-direction:column;gap:4px">';
    showItems.forEach(function(a) {
      var pColor = a.priority === 'high' ? 'var(--m-negative)' : a.priority === 'medium' ? 'var(--m-warn)' : 'var(--m-text-3)';
      actHtml += '<div style="display:flex;align-items:flex-start;gap:6px;padding:5px 8px;background:var(--m-surface-2);border-radius:5px">' +
        '<span style="font-size:9px;font-weight:700;color:' + pColor + ';flex-shrink:0;margin-top:1px">' + (a.priority === 'high' ? '🔴' : a.priority === 'medium' ? '🟡' : '🟢') + '</span>' +
        '<span style="font-size:11px;color:var(--m-text);line-height:1.4">' + _h(a.action || '') +
          (a.trigger ? '<div style="font-size:9px;color:var(--m-text-3);margin-top:2px">触发：' + _h(a.trigger) + '</div>' : '') +
        '</span>' +
      '</div>';
    });
    actHtml += '</div>';
  }

  return '<div class="card" style="border-left:3px solid #8b5cf6">' +
    '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">' +
      '<div class="card-title" style="margin-bottom:0;color:#a78bfa">🔄 Loop Learning' + freshStr + '</div>' +
    '</div>' +
    '<div style="font-size:12px;color:var(--m-text);line-height:1.5;font-weight:600">' + _h(summary) + '</div>' +
    weightHtml +
    (lesson ? '<div style="margin-top:8px;font-size:11px;color:var(--m-text-2);padding:5px 8px;background:rgba(139,92,246,0.06);border-radius:5px;border-left:2px solid #8b5cf6">💡 教训：' + _h(lesson) + '</div>' : '') +
    (nextFocus ? '<div style="margin-top:5px;font-size:11px;color:var(--m-warn)">🎯 下期聚焦：' + _h(nextFocus) + '</div>' : '') +
    actHtml +
    (improve ? '<div style="margin-top:8px;font-size:10px;color:var(--m-text-3);line-height:1.5;padding:5px 8px;background:var(--m-surface-2);border-radius:5px">' + _h(improve.slice(0, 150)) + (improve.length > 150 ? '…' : '') + '</div>' : '') +
  '</div>';
}

// ── V9.0 策略绩效报告 ──
function buildStrategyReportCard(sr) {
  if (!sr || !sr.strategies || sr.strategies.length === 0) return '';
  var ov = sr.overall || {};
  var barsOuter = '⬤⬤⬤⬤⬤';
  var stars = function(n) { return barsOuter.slice(0, n); };

  var header = '<div class="card-title">🎖️ 策略绩效（' + (ov.total_trades || 0) + ' 笔回溯）</div>' +
    '<div style="display:flex;gap:8px;margin-bottom:10px;font-size:11px;color:var(--m-text-2)">' +
      '<span>综合胜率 <b>' + (ov.win_rate_pct || 0) + '%</b></span>' +
      '<span>均收益 <b>' + ((ov.avg_return_pct || 0) >= 0 ? '+' : '') + (ov.avg_return_pct || 0).toFixed(2) + '%</b></span>' +
    '</div>';

  // 策略卡片网格
  var cardsHtml = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">';
  (sr.strategies || []).forEach(function(s) {
    var winColor = s.win_rate_pct >= 50 ? 'var(--m-positive)' : s.win_rate_pct >= 30 ? 'var(--m-warn)' : 'var(--m-negative)';
    var retColor = (s.avg_return_pct || 0) >= 0 ? 'var(--m-positive)' : 'var(--m-negative)';
    var retSign = (s.avg_return_pct || 0) >= 0 ? '+' : '';
    var starBar = stars(s.stars || 1);
    cardsHtml += '<div style="background:var(--m-surface-2);border-radius:8px;padding:10px">' +
      '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px">' +
        '<span style="font-size:12px;font-weight:700;color:var(--m-text)">' + escHtml(s.label || s.strategy) + '</span>' +
        '<span style="font-size:9px;color:var(--m-text-3)">' + starBar + '</span>' +
      '</div>' +
      '<div style="display:flex;gap:6px;margin-bottom:4px">' +
        '<span style="font-weight:700;color:' + winColor + '">' + s.win_rate_pct + '%</span>' +
        '<span style="color:var(--m-text-3);font-size:10px">胜率</span>' +
      '</div>' +
      '<div style="display:flex;gap:6px;margin-bottom:4px">' +
        '<span style="font-weight:700;color:' + retColor + '">' + retSign + (s.avg_return_pct || 0).toFixed(2) + '%</span>' +
        '<span style="color:var(--m-text-3);font-size:10px">均收益</span>' +
      '</div>' +
      '<div style="font-size:9px;color:var(--m-text-3);display:flex;justify-content:space-between">' +
        '<span>' + s.total_trades + '笔/' + s.unique_stocks + '股</span>' +
        '<span style="color:var(--m-negative)">DD ' + (s.avg_max_dd_pct || 0).toFixed(2) + '%</span>' +
      '</div>' +
    '</div>';
  });
  cardsHtml += '</div>';

  return '<div class="card">' + header + cardsHtml + '</div>';
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

// ── V9.0 快捷问答面板（概览 Tab 底部）──
var INSIGHT_QUESTIONS = [
  { id: 'strategy_perf',  icon: '📊', label: '策略排名' },
  { id: 'resonance',     icon: '🔔', label: '共振方向' },
  { id: 'etf_direction', icon: '📈', label: 'ETF方向' },
  { id: 'market_style',  icon: '🎨', label: '市场风格' },
  { id: 'top_signals',   icon: '⚡', label: '最强信号' },
];

function buildInsightPanel() {
  var buttons = INSIGHT_QUESTIONS.map(function(q) {
    return '<button class="ins-btn" onclick="askInsight(\'' + q.id + '\', this)" data-qid="' + q.id + '">'
      + q.icon + ' ' + q.label + '</button>';
  }).join('');

  return '<div class="card ins-card">'
    + '<div class="card-title">💡 快捷问答</div>'
    + '<div class="ins-btn-row">' + buttons + '</div>'
    + '<div class="ins-result" id="ins-result" style="display:none"></div>'
    + '</div>';
}

async function askInsight(qid, btnEl) {
  var resultEl = document.getElementById('ins-result');
  // 标记 active 按钮
  var allBtns = document.querySelectorAll('.ins-btn');
  allBtns.forEach(function(b) { b.classList.remove('ins-btn-active'); });
  if (btnEl) btnEl.classList.add('ins-btn-active');

  resultEl.style.display = 'block';
  resultEl.innerHTML = '<div class="ins-loading">查询中...</div>';

  try {
    var res = await fetch('/m/api/insights?q=' + encodeURIComponent(qid));
    var data = await res.json();
    if (data.error) {
      resultEl.innerHTML = '<div class="ins-answer" style="color:var(--m-negative)">⚠️ ' + escHtml(data.error) + '</div>';
      return;
    }
    var answer = data.answer || '暂无数据';
    resultEl.innerHTML = '<div class="ins-answer">' + escHtml(answer) + '</div>';
  } catch(e) {
    resultEl.innerHTML = '<div class="ins-answer" style="color:var(--m-negative)">⚠️ 请求失败</div>';
  }
}
async function loadWatchlist() {
  try {
    var res = await fetch('/m/api/watchlist');
    var data = await res.json();
    var signals = (data && data.signals) ? data.signals : [];
    var container = document.getElementById('wl-content');
    document.getElementById('wl-loading').style.display = 'none';
    if (signals.length === 0) {
      container.innerHTML = '<div class="nodata">暂无自选股 | 前往 <a href="/admin">Admin</a> 添加</div>';
      container.style.display = 'block';
      return;
    }

    // V7.8+: 三分类逻辑
    // 🟢 进攻：nx=buy/rising 且 pnl 未深亏（>-15%）→ 可加仓/持有
    // 🔴 减仓/止损：nx=sell 或 pnl<-15% 或 (nx=sell && pnl>0 → 止盈减仓)
    // 🟡 观望：其余（中性信号、pnl 中间地带）
    function classify(s) {
      var nx = s.nx_signal; var pnl = s.pnl_pct;
      if (nx === 'sell' && pnl != null && pnl < -15) return 'stop';    // 止损
      if (nx === 'sell' && pnl != null && pnl >= 5)  return 'reduce';  // 止盈减仓
      if (nx === 'sell')                              return 'reduce';  // 减仓
      if (pnl != null && pnl < -15)                  return 'stop';    // 深亏止损
      if ((nx === 'buy' || nx === 'rising') && pnl != null && pnl > 20) return 'reduce'; // 大赚减仓
      if (nx === 'buy' || nx === 'rising')            return 'attack';  // 进攻
      return 'hold';                                                    // 观望
    }
    function adviceLabel(cls) {
      if (cls === 'attack') return {label:'加仓', color:'var(--m-positive)'};
      if (cls === 'reduce') return {label:'减仓', color:'var(--m-warn)'};
      if (cls === 'stop')   return {label:'止损', color:'var(--m-negative)'};
      return {label:'持有', color:'var(--m-text-2)'};
    }
    function nxBadgeColor(nx) {
      if (nx === 'buy')    return 'var(--m-positive)';
      if (nx === 'rising') return 'var(--m-primary)';
      if (nx === 'sell')   return 'var(--m-negative)';
      return 'var(--m-text-2)';
    }
    function nxLabel(nx) { return {buy:'买入', rising:'趋升', sell:'卖出'}[nx] || (nx || '—'); }

    var groups = { attack: [], hold: [], reduce: [], stop: [] };
    signals.forEach(function(s) { groups[classify(s)].push(s); });

    // 每组按 score 降序（attack），或 pnl 升序（reduce/stop 先看最亏的）
    groups.attack.sort(function(a,b){ return (b.score||0)-(a.score||0); });
    groups.hold.sort(function(a,b){ return (b.score||0)-(a.score||0); });
    var losers = groups.reduce.concat(groups.stop).sort(function(a,b){ return (a.pnl_pct||0)-(b.pnl_pct||0); });

    function renderRow(s) {
      var cls = classify(s);
      var adv = adviceLabel(cls);
      var pnlHtml = '—';
      if (s.pnl_pct != null) {
        var clr = s.pnl_pct >= 0 ? 'var(--m-positive)' : 'var(--m-negative)';
        var sign = s.pnl_pct >= 0 ? '+' : '';
        pnlHtml = '<span style="font-size:13px;font-weight:700;font-family:var(--m-mono);color:' + clr + '">' + sign + s.pnl_pct.toFixed(1) + '%</span>';
      }
      var close  = s.close != null ? s.close.toFixed(2) : '—';
      var entry  = s.entry_price != null ? s.entry_price.toFixed(2) : '—';
      // V8.3: Tier 徽章（多模型综合评分）
      var tierHtml = '';
      if (s.tier && s.score) {
        var tierColors = {
          'T1·强推': 'var(--m-positive)', 'T2·关注': 'var(--m-primary)',
          'T3·观察': 'var(--m-warn)', 'T4·冷门': 'var(--m-text-2)'
        };
        var tc = tierColors[s.tier] || 'var(--m-text-2)';
        tierHtml = '<span style="font-size:9px;font-weight:700;background:rgba(0,0,0,0.2);padding:1px 5px;border-radius:3px;color:' + tc + ';margin-left:4px">' + s.tier + '</span>';
      }
      return '<div class="wl-row" onclick="openStockModal(\'' + (s.code||'') + '\')">' +
        '<div class="wl-left">' +
          '<div style="display:flex;align-items:center;gap:6px">' +
            '<span class="wl-name">' + _h(s.name||'-') + '</span>' +
            '<span class="wl-code" style="font-size:10px;color:var(--m-text-3);font-family:var(--m-mono)">' + (s.code||'') + '</span>' +
            '<span class="wl-advice" style="font-size:10px;font-weight:700;background:rgba(0,0,0,0.2);padding:1px 6px;border-radius:4px;color:' + adv.color + '">' + adv.label + '</span>' +
            tierHtml +
          '</div>' +
          '<div style="font-size:10px;color:var(--m-text-3);margin-top:2px">' +
            '市价 ' + close + ' · 成本 ' + entry +
            ' · <span style="color:' + nxBadgeColor(s.nx_signal) + '">NX ' + nxLabel(s.nx_signal) + '</span>' +
            (s.resonance_count ? ' · ' + s.resonance_count + '源共振' : '') +
          '</div>' +
        '</div>' +
        '<div style="text-align:right;flex-shrink:0">' + pnlHtml + '</div>' +
      '</div>';
    }

    function renderGroup(icon, title, bgColor, items, advice) {
      if (!items.length) return '';
      var rows = items.map(renderRow).join('');
      return '<div class="card" style="border-left:3px solid ' + bgColor + ';margin-bottom:10px">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">' +
          '<div class="card-title" style="margin-bottom:0">' + icon + ' ' + title + ' <span style="font-size:11px;font-weight:400;color:var(--m-text-2)">(' + items.length + ')</span></div>' +
        '</div>' +
        '<div style="font-size:11px;color:var(--m-text-3);margin-bottom:8px;line-height:1.4">💡 ' + advice + '</div>' +
        rows +
      '</div>';
    }

    var attackCount = groups.attack.length;
    var holdCount = groups.hold.length;
    var losersCount = losers.length;

    var summaryHtml = '<div class="card" style="margin-bottom:10px">' +
      '<div style="display:flex;gap:0;border-radius:8px;overflow:hidden">' +
        '<div style="flex:1;text-align:center;padding:10px 6px;background:rgba(38,166,154,0.1)">' +
          '<div style="font-size:20px;font-weight:800;color:var(--m-positive)">' + attackCount + '</div>' +
          '<div style="font-size:10px;color:var(--m-text-3);margin-top:2px">🟢 进攻</div>' +
        '</div>' +
        '<div style="flex:1;text-align:center;padding:10px 6px;background:rgba(245,158,11,0.08)">' +
          '<div style="font-size:20px;font-weight:800;color:var(--m-warn)">' + holdCount + '</div>' +
          '<div style="font-size:10px;color:var(--m-text-3);margin-top:2px">🟡 观望</div>' +
        '</div>' +
        '<div style="flex:1;text-align:center;padding:10px 6px;background:rgba(239,83,80,0.08)">' +
          '<div style="font-size:20px;font-weight:800;color:var(--m-negative)">' + losersCount + '</div>' +
          '<div style="font-size:10px;color:var(--m-text-3);margin-top:2px">🔴 减仓/止损</div>' +
        '</div>' +
      '</div>' +
    '</div>';

    container.innerHTML = summaryHtml +
      renderGroup('🟢', '进攻 — 可加仓', 'var(--m-positive)', groups.attack,
        attackCount > 0 ? 'NX 看多，择机分批加仓。关注量能配合，不追高。' : '') +
      renderGroup('🟡', '观望 — 持仓不动', 'var(--m-warn)', groups.hold,
        holdCount > 0 ? '信号中性，持仓不动。等待 NX 明确后再行动。' : '') +
      renderGroup('🔴', '减仓/止损', 'var(--m-negative)', losers,
        losersCount > 0 ? 'NX 看空或深亏。严格止损，不补仓抄底。' : '');
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
    var [crRes, hitsRes, txRes, tierRes, resRes] = await Promise.all([
      fetch('/m/api/cycleradar'),
      fetch('/m/api/event-hits'),
      fetch('/m/api/transmission-summary?n=3').catch(function() { return { ok: false }; }),
      fetch('/m/api/watchlist-tiers').catch(function() { return { ok: false }; }),
      fetch('/m/api/resonance').catch(function() { return { ok: false }; }),
    ]);
    var d = await crRes.json();
    var hitsData = hitsRes.ok ? await hitsRes.json() : null;
    var txData = txRes.ok ? await txRes.json().catch(function() { return null; }) : null;
    var tierData = tierRes.ok ? await tierRes.json().catch(function() { return null; }) : null;
    var resData = resRes.ok ? await resRes.json().catch(function() { return null; }) : null;
    var hitIndex = (hitsData && hitsData.hit_index) ? hitsData.hit_index : {};
    document.getElementById('cr-content').innerHTML =
      buildCrStatsBar(d.summary, d.event_narrative, d.daily_pnl) +
      buildCrResonance(resData) +
      buildCrMarketSummary(d.summary, d.hotEvents, d.alpha, d.etf, d.commodity) +
      buildCrSummaryCards(d.summary) +
      buildCrEventAnalysis(d.event_narrative, d.hotEvents, hitIndex) +
      buildCrCategorySections(d.alpha, d.etf, d.commodity, d.alpha_latest) +
      buildCrTransmissionSummary(txData) +
      buildCrWatchlistTier(tierData);
    // V4.1: attach expand handlers after DOM rendered
    attachCrExpandHandlers();
    document.getElementById('cr-content').style.display = 'block';
    document.getElementById('cr-loading').style.display = 'none';
  } catch(e) {
    document.getElementById('cr-loading').innerHTML = '<div class="nodata">加载失败: ' + e.message + '</div>';
  }
}

// ── V8.0 World Monitor Tab ── 全球市场监测（A股大盘 + 行业轮动 + 商品期货）──
async function loadWorld() {
  try {
    var res = await fetch('/m/api/world');
    var d = await res.json();
    document.getElementById('world-content').innerHTML = buildWorldContent(d);
    document.getElementById('world-content').style.display = 'block';
    document.getElementById('world-loading').style.display = 'none';
  } catch(e) {
    document.getElementById('world-loading').innerHTML = '<div class="nodata">加载失败: ' + e.message + '</div>';
  }
}

function buildWorldContent(d) {
  if (!d || !d.sectors) return '<div class="nodata">暂无数据</div>';

  var s = d.sectors;
  var html = '';

  // ── 数据版本提示 ──
  if (d.version === 'v8.5') {
    html += '<div style="font-size:9px;color:var(--m-text-3);padding:4px 0 8px;text-align:right">实时管线数据 · 无 AI 合成判词</div>';
  } else if (d.global_summary) {
    // 旧版兼容（global_summary 不为 null 时才显示）
    html += '<div class="card" style="border-left:3px solid var(--m-primary)">' +
      '<div class="card-title">🌐 全球综述</div>' +
      '<div style="font-size:13px;color:var(--m-text);line-height:1.6">' + escHtml(d.global_summary) + '</div>' +
      '</div>';
  }

  // ── 行业轮动 ──
  if (s.sector_rotation) html += buildWorldSector('🏭 行业轮动', s.sector_rotation, false, true);
  // ── ETF 行业 ── V9.0
  if (s.etf) html += buildWorldEtf(s.etf);
  // ── 商品期货 ──
  if (s.commodity) html += buildWorldSector('⚡ 商品期货', s.commodity, false);
  // ── A股大盘（旧版 v8.0 兼容）──
  if (s.a_share_market) html += buildWorldSector('📊 A股大盘', s.a_share_market, true);

  // ── 板块传导摘要 ──
  var tx = s.transmission_summary;
  if (tx) {
    var txFresh = tx.data_freshness ? new Date(tx.data_freshness).toLocaleString('zh-CN', {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '—';
    html += '<div class="card">' +
      '<div class="card-title">🔗 板块传导（' + txFresh + ' 更新）</div>' +
      '<div style="display:flex;gap:12px;margin-bottom:8px">' +
        '<span style="font-size:11px;color:var(--m-positive)">高置信 <b>' + (tx.high_confidence_signals || 0) + '</b></span>' +
        '<span style="font-size:11px;color:var(--m-warn)">中置信 <b>' + (tx.medium_confidence_signals || 0) + '</b></span>' +
      '</div>';
    if (tx.top_events && tx.top_events.length) {
      html += '<div style="font-size:10px;color:var(--m-text-3);margin-bottom:6px">触发事件：' + tx.top_events.map(escHtml).join(' · ') + '</div>';
    }
    if (tx.active_sectors && tx.active_sectors.length) {
      html += '<div style="display:flex;gap:4px;flex-wrap:wrap">';
      tx.active_sectors.forEach(function(sec) {
        html += '<span style="font-size:10px;background:rgba(168,85,247,0.12);padding:2px 7px;border-radius:3px;color:var(--m-text-2)">' + escHtml(sec) + '</span>';
      });
      html += '</div>';
    }
    html += '</div>';
  }

  return html;

  function buildWorldSector(label, sec, isMarket, isSector) {
    if (!sec) return '';
    var v = sec.verdict || {};
    var dims = sec.dimensions || {};
    // v8.5: llm_verdict is null → show real verdict.label instead
    var verdictText = (sec.llm_verdict && sec.llm_verdict !== 'null') ? sec.llm_verdict : (v.label || null);

    var scoreColor = 'var(--m-text-2)';
    var score = v.score || 0;
    if (score >= 70) scoreColor = 'var(--m-positive)';
    else if (score <= 30) scoreColor = 'var(--m-negative)';
    else scoreColor = 'var(--m-warn)';

    // 数据新鲜度
    var freshStr = '';
    if (sec.data_freshness) {
      var dt = new Date(sec.data_freshness);
      freshStr = ' <span style="font-size:8px;color:var(--m-text-3)">' +
        dt.toLocaleString('zh-CN', {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}) + '</span>';
    }

    var titleBar = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">' +
      '<span style="font-size:12px;font-weight:700;color:var(--m-primary);letter-spacing:1px">' + label + freshStr + '</span>' +
      '<span style="font-size:18px;font-weight:900;color:' + scoreColor + '">' + score + '<span style="font-size:10px;color:var(--m-text-3)">/100</span></span>' +
      '</div>';

    // 子项（行情数据）
    var subItems = '';
    if (isMarket && sec.indices) {
      subItems = '<div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:6px">';
      Object.values(sec.indices).forEach(function(idx) {
        var pct = idx.change_pct || 0;
        var c = pct >= 0 ? 'var(--m-positive)' : 'var(--m-negative)';
        subItems += '<span style="font-size:10px;background:var(--m-surface-2);padding:2px 6px;border-radius:4px;color:var(--m-text-2)">' +
          escHtml(idx.name) + ' <b style="color:' + c + '">' + (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%</b></span>';
      });
      subItems += '</div>';
    } else if (isSector && sec.top_momentum && sec.top_momentum.length) {
      subItems = '<div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:6px">';
      (sec.top_momentum || []).slice(0, 5).forEach(function(t) {
        var cConf = (t.confidence || 0) >= 0.6 ? 'var(--m-positive)' : 'var(--m-warn)';
        subItems += '<span style="font-size:10px;background:var(--m-surface-2);padding:2px 6px;border-radius:4px;color:var(--m-text-2)">' +
          escHtml(t.name) + ' <b style="color:' + cConf + '">' + Math.round((t.confidence || 0) * 100) + '%</b></span>';
      });
      subItems += '</div>';
    } else if (sec.commodities) {
      subItems = '<div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:6px">';
      Object.entries(sec.commodities).forEach(function(entry) {
        var name = entry[0]; var comm = entry[1];
        var pct = comm.change_pct || 0;
        var c = pct >= 0 ? 'var(--m-positive)' : 'var(--m-negative)';
        subItems += '<span style="font-size:10px;background:var(--m-surface-2);padding:2px 6px;border-radius:4px;color:var(--m-text-2)">' +
          escHtml(name) + ' <b style="color:' + c + '">' + (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%</b></span>';
      });
      subItems += '</div>';
    }

    // 维度 mini bars（只展示有数据的维度）
    var dimLabels = { trend:'趋势', volatility:'波动', capital_flow:'资金', sentiment:'情绪', key_levels:'关键位', event_risk:'事件' };
    var activeDims = Object.keys(dims).filter(function(k) { return dims[k] && typeof dims[k].score === 'number'; });
    var dimBars = '';
    if (activeDims.length > 0) {
      dimBars = '<div style="display:flex;gap:4px;margin-bottom:8px">';
      activeDims.forEach(function(k) {
        var dimScore = dims[k].score || 0;
        var dimColor = dimScore >= 70 ? 'var(--m-positive)' : dimScore <= 30 ? 'var(--m-negative)' : 'var(--m-warn)';
        dimBars += '<div style="flex:1;text-align:center">' +
          '<div style="font-size:9px;color:var(--m-text-3);margin-bottom:2px">' + (dimLabels[k] || k) + '</div>' +
          '<div style="height:3px;background:var(--m-surface-2);border-radius:2px;overflow:hidden">' +
            '<div style="width:' + dimScore + '%;height:100%;background:' + dimColor + ';border-radius:2px"></div>' +
          '</div>' +
          '<div style="font-size:8px;color:var(--m-text-3);margin-top:1px">' + dimScore + '</div>' +
          '</div>';
      });
      dimBars += '</div>';
    }

    // 额外字段（catalyst / evidence）
    var extra = '';
    if (sec.catalyst && sec.catalyst !== '混沌') {
      extra += '<div style="font-size:10px;color:var(--m-text-3);margin-bottom:4px">催化：' + escHtml(sec.catalyst) + '</div>';
    }
    if (sec.evidence) {
      extra += '<div style="font-size:10px;color:var(--m-text-3);margin-bottom:4px">' + escHtml(sec.evidence) + '</div>';
    }

    var verdictHtml = verdictText
      ? '<div style="font-size:11px;color:var(--m-text-2);line-height:1.5;margin-bottom:8px">' + escHtml(verdictText) + '</div>'
      : '';

    return '<div class="card">' +
      titleBar +
      subItems +
      verdictHtml +
      extra +
      dimBars +
      '</div>';
  }
}

// ── V9.0 ETF 行业卡片 ──
function buildWorldEtf(etf) {
  if (!etf || !etf.top_etfs) return '';
  var v = etf.verdict || {};
  var score = v.score || 0;
  var scoreColor = score >= 70 ? 'var(--m-positive)' : score <= 30 ? 'var(--m-negative)' : 'var(--m-warn)';
  var list = etf.top_etfs || [];

  var html = '<div class="card">' +
    '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">' +
      '<span style="font-size:12px;font-weight:700;color:var(--m-primary);letter-spacing:1px">📈 ETF 行业</span>' +
      '<span style="font-size:18px;font-weight:900;color:' + scoreColor + '">' + score + '<span style="font-size:10px;color:var(--m-text-3)">/100</span></span>' +
    '</div>' +
    '<div style="font-size:10px;color:var(--m-text-3);margin-bottom:6px">' +
      escHtml(v.label || '') + ' · 信号覆盖 ' + (etf.etf_count || list.length) + ' 只 ETF · 多头占比 ' + (etf.long_ratio || 0) + '%' +
    '</div>';

  // ETF 列表（横向卡片）
  html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">';
  list.forEach(function(e) {
    var dirColor = e.direction === 'long' ? 'var(--m-positive)' : e.direction === 'short' ? 'var(--m-negative)' : 'var(--m-warn)';
    var dirLabel = e.direction === 'long' ? '↑多' : e.direction === 'short' ? '↓空' : '○';
    var confWidth = Math.min(100, Math.max(0, e.confidence || 0));
    html += '<div style="background:var(--m-surface-2);border-radius:6px;padding:8px">' +
      '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:3px">' +
        '<span style="font-size:11px;font-weight:600;color:var(--m-text)">' + escHtml(e.name) + '</span>' +
        '<span style="font-size:9px;color:' + dirColor + '">' + dirLabel + '</span>' +
      '</div>' +
      '<div style="height:2px;background:var(--m-border);border-radius:1px;overflow:hidden;margin-bottom:3px">' +
        '<div style="width:' + confWidth + '%;height:100%;background:' + dirColor + ';border-radius:1px"></div>' +
      '</div>' +
      '<div style="font-size:9px;color:var(--m-text-3);display:flex;justify-content:space-between">' +
        '<span>' + (e.signal_count || 1) + ' 策略</span>' +
        '<span style="color:' + dirColor + '">' + (e.confidence || 0) + '%</span>' +
      '</div>' +
    '</div>';
  });
  html += '</div></div>';
  return html;
}

// ── V8.2 传导图谱可视化（事件→板块→个股级联）──
async function loadGraph() {
  try {
    var res = await fetch('/m/api/graph');
    var d = await res.json();
    if (d.error) { document.getElementById('graph-loading').innerHTML = '<div class="nodata">' + escHtml(d.error) + '</div>'; return; }
    document.getElementById('graph-content').innerHTML = buildGraphContent(d);
    document.getElementById('graph-content').style.display = 'block';
    document.getElementById('graph-loading').style.display = 'none';
  } catch(e) {
    document.getElementById('graph-loading').innerHTML = '<div class="nodata">加载失败: ' + e.message + '</div>';
  }
}

function buildGraphContent(d) {
  if (!d || !d.event) return '<div class="nodata">暂无数据</div>';

  var html = '';

  // ── 事件选择器 ──
  var evs = d.available_events || [];
  html += '<div class="card" style="padding:10px 12px">' +
    '<select onchange="switchGraphEvent(this.value)" style="width:100%;padding:8px;border-radius:6px;border:1px solid var(--m-border);background:var(--m-surface-2);color:var(--m-text);font-size:14px">';
  evs.forEach(function(ev) {
    var sel = ev.id === d.event.id ? ' selected' : '';
    html += '<option value="' + escHtml(ev.id) + '"' + sel + '>' + (ev.tier===1 ? '⭐ ':'') + escHtml(ev.name) + '</option>';
  });
  html += '</select></div>';

  // ── 事件详情卡 ──
  var e = d.event;
  var tierBadge = e.tier === 1
    ? '<span style="background:rgba(var(--m-warn-rgb),0.15);color:var(--m-warn);padding:1px 6px;border-radius:4px;font-size:10px">Tier-1</span>'
    : '<span style="background:rgba(var(--m-text-2-rgb),0.1);color:var(--m-text-2);padding:1px 6px;border-radius:4px;font-size:10px">Tier-' + e.tier + '</span>';

  html += '<div class="card" style="border-left:3px solid var(--m-primary)">' +
    '<div style="display:flex;justify-content:space-between;align-items:flex-start">' +
    '<div style="font-weight:700;font-size:15px;color:var(--m-text)">' + escHtml(e.name) + '</div>' +
    tierBadge +
    '</div>' +
    '<div style="font-size:11px;color:var(--m-text-2);margin-top:4px">' +
    (e.category ? escHtml(e.category) + ' · ' : '') +
    (e.direction === 'long' ? '看多' : '看空') +
    '</div></div>';

  // ── 信号级联 (高→中→低) ──
  var sigs = d.signals || [];
  var sc = d.signal_counts || {};
  html += '<div style="padding:0 4px;margin-bottom:6px;display:flex;gap:6px;align-items:center">' +
    '<span style="font-size:11px;font-weight:700;color:var(--m-text)">信号级联</span>' +
    '<span style="font-size:10px;color:var(--m-warn)">🔥' + (sc.high||0) + '</span>' +
    '<span style="font-size:10px;color:var(--m-text-2)">🟡' + (sc.medium||0) + '</span>' +
    '<span style="font-size:10px;color:var(--m-text-3)">◯' + (sc.low||0) + '</span>' +
    '</div>';

  if (sigs.length === 0) {
    html += '<div class="nodata">无传导信号</div>';
  } else {
    sigs.forEach(function(sig) {
      var conf = sig.confidence || 0;
      var icon = conf >= 0.8 ? '🔥' : (conf >= 0.4 ? '🟡' : '◯');
      var color = conf >= 0.8 ? 'var(--m-warn)' : (conf >= 0.4 ? 'var(--m-text-2)' : 'var(--m-text-3)');
      var t = sig.target || {};
      var tx = sig.transmission || {};
      var depthTag = tx.depth === 1
        ? '<span style="font-size:10px;background:rgba(var(--m-positive-rgb),0.1);color:var(--m-positive);padding:1px 4px;border-radius:3px">直连</span>'
        : '<span style="font-size:10px;background:rgba(var(--m-text-2-rgb),0.08);color:var(--m-text-2);padding:1px 4px;border-radius:3px">' + tx.depth + '跳</span>';

      html += '<div class="card" style="padding:8px 12px;margin-bottom:4px">' +
        '<div style="display:flex;justify-content:space-between;align-items:center">' +
        '<div>' +
        '<span style="font-weight:700;font-size:14px;color:' + color + '">' + icon + ' ' + escHtml(t.name) + '</span>' +
        ' <span style="font-size:10px;color:var(--m-text-3)">' + (t.id||'').replace('sector:','').replace('stock:','') + '</span>' +
        '</div>' +
        '<div style="display:flex;gap:4px;align-items:center">' +
        depthTag +
        '<span style="font-size:12px;font-weight:900;color:' + color + '">' + Math.round(conf*100) + '%</span>' +
        '</div></div>' +
        (sig.narrative ? '<div style="font-size:11px;color:var(--m-text-2);margin-top:4px;line-height:1.5">' + escHtml(sig.narrative) + '</div>' : '') +
        '</div>';
    });
  }

  // ── 全景解说 ──
  if (d.narrative) {
    html += '<div class="card" style="border-left:3px solid var(--m-primary);margin-top:8px">' +
      '<div class="card-title">🧠 AI 全景解读</div>' +
      '<div style="font-size:13px;color:var(--m-text);line-height:1.7">' + escHtml(d.narrative) + '</div>' +
      '</div>';
  }

  // ── 图谱概况 ──
  if (d.graph_stats) {
    html += '<div style="text-align:center;font-size:10px;color:var(--m-text-3);margin-top:8px">' +
      escHtml(d.graph_stats) +
      '</div>';
  }

  return html;
}

// 事件切换
async function switchGraphEvent(eventId) {
  document.getElementById('graph-loading').style.display = 'block';
  document.getElementById('graph-content').style.display = 'none';
  try {
    var res = await fetch('/m/api/graph?event_id=' + encodeURIComponent(eventId));
    var d = await res.json();
    document.getElementById('graph-content').innerHTML = buildGraphContent(d);
    document.getElementById('graph-content').style.display = 'block';
    document.getElementById('graph-loading').style.display = 'none';
  } catch(e) {
    document.getElementById('graph-loading').innerHTML = '<div class="nodata">加载失败: ' + e.message + '</div>';
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

// ── V5.0 事件叙事解读（信号Tab顶部，event_narrative_latest.json 驱动）
// V7.8+: hotEvents 参数保留兼容性但不再嵌入展示（热点独立 section 避免重复）
function buildCrEventAnalysis(en, hotEvents, hitIndex) {
  if (!en) {
    // 无 LLM 研判时，如果还有热点，单独展示热点摘要
    var he = hotEvents || [];
    if (he.length === 0) return '';
    return _buildCrHotCompact(he, hitIndex);
  }
  var gc = en.global_conclusion || {};
  var events = en.events || [];
  var raw = gc.market_regime || gc.regime || '';

  var llmConf = gc.confidence || 0;

  // 顶部研判行
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

  // 事件列表 — LLM 结构化解读
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

  // ── V8.3: 热点事件嵌入同一张卡（紧凑模式）──
  var hotHtml = _buildCrHotCompact(hotEvents || [], hitIndex);

  return '<div class="cr-en-block">' + headerHtml + riskHtml + eventsHtml + hotHtml + '</div>';
}

// ── V8.3 热点事件紧凑模式（嵌入事件解析卡，非独立 section）──
function _buildCrHotCompact(events, hitIndex) {
  var el = events || [];
  if (el.length === 0) return '';

  var staleHint = '';
  if (el.length > 0 && el[0]._stale) {
    staleHint = '<span style="font-size:10px;color:#f59e0b;margin-left:6px">⚠️ 缓存 · 源暂不可用</span>';
  }

  // 按时间降序 + 限 8 条（紧凑模式比独立 section 少）
  var sorted = el.slice().sort(function(a, b) { return (b.time || '').localeCompare(a.time || ''); });
  var top = sorted.slice(0, 8);

  // 总体统计行
  var allSectors = [];
  var bullCount = 0, bearCount = 0;
  top.forEach(function(e) {
    if (e.sectors && e.sectors.length) {
      e.sectors.forEach(function(s) { if (allSectors.indexOf(s) < 0) allSectors.push(s); });
    }
    var d = (e.direction || e.thesis || '').toLowerCase();
    if (d.indexOf('看多') >= 0 || d.indexOf('利好') >= 0 || d.indexOf('做多') >= 0) bullCount++;
    else if (d.indexOf('看空') >= 0 || d.indexOf('利空') >= 0 || d.indexOf('做空') >= 0) bearCount++;
  });
  var overallDir = bullCount > bearCount ? '🔥 偏多' : bearCount > bullCount ? '🛡️ 偏空' : '⚖️ 均衡';

  // 紧凑列表项
  var items = top.map(function(e, idx) {
    var rawTime = e.event_time || e.time || '';
    var timeStr = rawTime ? formatRelativeTime(rawTime) : '';
    var thesis = e.thesis || e.title || '';
    var summaryText = thesis.length > 60 ? thesis.slice(0, 57) + '...' : thesis;
    var source = e.source || e.mp_name || '';

    // 命中率 mini badge
    var hitBadge = '';
    var evHash = e.event_hash || e.hash || '';
    if (evHash && hitIndex && hitIndex[evHash]) {
      var hi = hitIndex[evHash];
      var hr = hi.hit_rate_5d;
      if (hr !== null && hr !== undefined) {
        var hrPct = Math.round(hr * 100);
        var hrColor = hrPct >= 60 ? 'var(--m-positive)' : hrPct >= 40 ? 'var(--m-warn)' : 'var(--m-negative)';
        hitBadge = '<span style="font-size:9px;color:' + hrColor + ';margin-left:4px">' + hrPct + '%命中</span>';
      }
    }

    return '<div class="cr-hot-compact-item">' +
      '<span class="cr-hot-compact-time">' + (timeStr || '—') + '</span>' +
      '<span class="cr-hot-compact-text">' + _h(summaryText) + '</span>' +
      (source ? ' <span class="cr-hot-compact-src">' + _h(source) + '</span>' : '') +
      hitBadge +
      '</div>';
  }).join('');

  var countLine = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">' +
    '<span style="font-size:11px;font-weight:700;color:var(--m-text)">' + overallDir + '</span>' +
    '<span style="font-size:10px;color:var(--m-text-3)">共 ' + sorted.length + ' 条' + (top.length < sorted.length ? '，显示前 ' + top.length + ' 条' : '') + '</span>' +
    '</div>';

  return '<div class="cr-hot-compact">' +
    '<div class="cr-section-title"><span class="cr-ico">📰</span> 信源速览' + staleHint + '</div>' +
    countLine +
    items +
    '</div>';
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

// ── V4.2 数据源时效条（source_articles.db，V7.7 起替代 WeWe RSS）──
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

function buildCrCategorySections(alpha, etf, commodity, alpha_latest) {
  // V4.3: alpha 按置信度降序，高置信度优先
  var sortedAlpha = (alpha || []).slice().sort(function(a, b) {
    return (b.confidence || 0) - (a.confidence || 0);
  });
  // V8.3: hotEvents 已合并到 buildCrEventAnalysis 卡内，不再独立 section
  return (
    _buildCrAlpha(sortedAlpha, alpha_latest) +
    _buildCrEtf(etf || []) +
    _buildCrCommodity(commodity || [])
  );
}

function _buildCrHotEvents(events, noWrapper, hitIndex) {
  var el = events || [];
  var staleHint = '';
  if (el.length > 0 && el[0]._stale) {
    staleHint = '<span style="font-size:10px;color:#f59e0b;margin-left:6px">⚠️ 缓存 · 源暂不可用</span>';
  }
  if (el.length === 0) {
    if (noWrapper) return '';
    return '<div class="cr-section"><div class="cr-section-title"><span class="cr-ico">🔥</span> 热点事件' + staleHint + '</div><div class="nodata">暂无事件</div></div>';
  }

  // 按时间降序 + 限 10 条
  var sorted = el.slice().sort(function(a, b) { return (b.time || '').localeCompare(a.time || ''); });
  var top10 = sorted.slice(0, 10);

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

    // V7.10: 事件命中率徽章（verify_event_signals.py 回查数据）
    var hitBadgeHtml = '';
    var evHash = e.event_hash || e.hash || '';
    if (evHash && hitIndex && hitIndex[evHash]) {
      var hi = hitIndex[evHash];
      var hr = hi.hit_rate_5d;
      if (hr !== null && hr !== undefined) {
        var hrPct = Math.round(hr * 100);
        var hrColor = hrPct >= 60 ? 'var(--m-positive)' : hrPct >= 40 ? 'var(--m-warn)' : 'var(--m-negative)';
        var hitNames = (hi.hit_tickers || []).slice(0, 2).join('/') || '—';
        hitBadgeHtml = '<div style="margin-top:5px;display:flex;align-items:center;gap:6px;flex-wrap:wrap">' +
          '<span style="font-size:10px;font-weight:700;color:' + hrColor + ';background:rgba(0,0,0,0.15);border-radius:4px;padding:2px 6px">5日命中 ' + hrPct + '%</span>' +
          (hi.hit_tickers && hi.hit_tickers.length ? '<span style="font-size:10px;color:var(--m-positive)">✓ ' + _h(hitNames) + '</span>' : '') +
          '</div>';
      }
    }

    return '<div class="cr-hot-card">' +
      '<div class="cr-hot-label">重点事件 ' + (idx + 1) + wechatBadge + '</div>' +
      sourceHtml +
      '<div class="cr-hot-summary">' + _h(summaryText) + '</div>' +
      tickerHtml +
      hitBadgeHtml +
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
      if (alMap[s.asset]) {
        s._alphaLatest = alMap[s.asset];
        // V6.5: 共振检测 — scanner code ∩ LLM alpha code + direction 一致
        if (alMap[s.asset].direction === s.direction) {
          s.multi_source = true;
          s.confidence = Math.min(1.0, (s.confidence || 0) + 0.15);
        }
      }
    });
  }

  // V7.8+: 跨策略去重 — 同 asset+direction 只保留最高置信度那条，合并策略来源标签
  var dedupMap = {};
  enriched.forEach(function(s) {
    var key = (s.asset || '') + '|' + (s.direction || '');
    if (!dedupMap[key]) {
      dedupMap[key] = Object.assign({}, s, { _strategies: [s.strategy] });
    } else {
      var existing = dedupMap[key];
      // 追加策略来源
      if (existing._strategies.indexOf(s.strategy) < 0) existing._strategies.push(s.strategy);
      // 保留较高置信度
      if ((s.confidence || 0) > (existing.confidence || 0)) {
        var strats = existing._strategies;
        dedupMap[key] = Object.assign({}, s, { _strategies: strats });
      }
      // 共振：多策略命中 → 置信度 +0.1，且打共振标
      if (existing._strategies.length > 1) {
        dedupMap[key].multi_source = true;
        dedupMap[key].confidence = Math.min(1.0, (dedupMap[key].confidence || 0) + 0.1);
      }
    }
  });
  var deduped = Object.values(dedupMap);

  // V5.3: 按置信度降序排列 + 限 10 条
  deduped.sort(function(a, b) { return (b.confidence || 0) - (a.confidence || 0); });
  var limited = deduped.slice(0, 10);
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
    // V7.8+: 多策略来源标签（去重后合并的策略列表）
    var stratSources = s._strategies || [s.strategy];
    var STRAT_LABEL_MAP = {'report_agent':'事件','scanner':'形态','ma_signals':'并购','wanjun_models':'量化','stock_agent':'AI','rotation_factor':'轮动','commodity_radar':'商品'};
    if (stratSources.length > 1) {
      stratSources.forEach(function(st) {
        tags += '<span class="cr-tag cr-tag-tier" style="background:rgba(168,85,247,0.15);color:#c084fc">' + _h(STRAT_LABEL_MAP[st] || st) + '</span>';
      });
    }
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
    // V6.5: 共振标识 — scanner + LLM alpha 双重确认
    var resBadge = s.multi_source ? '<span class="cr-rec-badge" style="background:rgba(168,85,247,0.2);color:#a855f7">共振</span>' : '';

    return '<div class="cr-sig-card ' + (isLong ? 'cr-sig-long' : 'cr-sig-short') + ' cr-sig-expandable" onclick="toggleCrCard(this)">' +
      '<span class="cr-dir ' + (isLong ? 'cr-dir-long' : 'cr-dir-short') + '">' + (isLong ? '多' : '空') + '</span>' +
      '<div class="cr-sig-asset">' + recBadge + resBadge + _h(displayName) + codeHtml + '</div>' +
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

// ── V8.3 传导路径摘要（信号 Tab 底部）──
function buildCrTransmissionSummary(data) {
  if (!data || !data.top_paths || data.top_paths.length === 0) return '';

  var paths = data.top_paths;

  var items = paths.map(function(p, idx) {
    // 路径链式展示：事件 → ... → 目标
    var chain = p.path_names || ['—'];
    var chainHtml = chain.map(function(name) {
      return '<span class="cr-tx-node">' + _h(name) + '</span>';
    }).join('<span class="cr-tx-arrow">→</span>');

    var conf = Math.round(p.confidence * 100);
    var confColor = conf >= 80 ? 'var(--m-positive)' : conf >= 60 ? 'var(--m-warn)' : '#ef4444';
    var dirLabel = p.direction === 'long' ? '📈 看多' : '📉 看空';
    var dirColor = p.direction === 'long' ? 'var(--m-positive)' : 'var(--m-negative)';

    return '<div class="cr-tx-item">' +
      '<div class="cr-tx-meta">' +
        '<span class="cr-tx-rank">#' + (idx + 1) + '</span>' +
        '<span class="cr-tx-conf" style="color:' + confColor + '">' + conf + '%</span>' +
        '<span class="cr-tx-dir" style="color:' + dirColor + '">' + dirLabel + '</span>' +
        (p.target_code ? '<span class="cr-tx-code">' + _h(p.target_code) + '</span>' : '') +
      '</div>' +
      '<div class="cr-tx-chain">' + chainHtml + '</div>' +
    '</div>';
  }).join('');

  return '<div class="cr-section">' +
    '<div class="cr-section-title"><span class="cr-ico">🧬</span> 传导路径摘要' +
      '<span style="font-size:10px;color:var(--m-text-3);margin-left:8px">共 ' + (data.total_paths || '—') + ' 条路径，Top ' + paths.length + '</span>' +
    '</div>' +
    '<div class="cr-tx-block">' + items + '</div>' +
    '</div>';
}

// ── V8.3 C: 自选池 tier 分级摘要卡 ──
function buildCrWatchlistTier(data) {
  if (!data || !data.distribution || !data.distribution.length) return '';

  var tiers = data.tiers || {};
  var order = ['T1·强推', 'T2·关注', 'T3·观察', 'T4·冷门'];
  var colors = {
    'T1·强推': '#26a69a',
    'T2·关注': '#42a5f5',
    'T3·观察': '#ffa726',
    'T4·冷门': '#78909c',
  };

  var bars = '';
  for (var i = 0; i < order.length; i++) {
    var t = order[i];
    var info = tiers[t] || { count: 0, stocks: [] };
    var count = info.count || 0;
    var color = colors[t] || '#666';
    var label = t.replace('·', '<span style="font-size:9px;opacity:0.7">·</span>');
    var maxW = Math.min(count * 20, 100);
    bars += '<div class="cr-tier-row" style="margin-bottom:4px">' +
      '<span class="cr-tier-label" style="color:' + color + ';font-weight:600;width:70px;display:inline-block;font-size:12px">' + label + '</span>' +
      '<span class="cr-tier-bar" style="display:inline-block;height:14px;background:' + color + ';width:' + maxW + '%;border-radius:3px;opacity:0.7;vertical-align:middle"></span>' +
      '<span class="cr-tier-count" style="font-size:12px;margin-left:6px;color:var(--m-text-2)">' + count + '票</span>' +
      '</div>';
  }

  // T1/T2 上榜详情（最多 3 票）
  var details = '';
  var hasDetail = false;
  for (var j = 0; j < order.length; j++) {
    var tk = order[j];
    var ti = tiers[tk] || { count: 0, stocks: [] };
    var stocks = ti.stocks || [];
    if (stocks.length > 0 && (tk === 'T1·强推' || tk === 'T2·关注')) {
      hasDetail = true;
      var color2 = colors[tk] || '#666';
      details += '<div class="cr-tier-detail-header" style="color:' + color2 + ';font-size:11px;font-weight:600;margin-top:6px">' + tk + '</div>';
      for (var k = 0; k < stocks.length; k++) {
        var s = stocks[k];
        var srcStr = (s.signal_sources || []).slice(0, 2).join('·');
        details += '<div class="cr-tier-stock" style="font-size:11px;line-height:1.6">' +
          '<span style="color:var(--m-text)">' + s.name + '</span> ' +
          '<span style="color:' + color2 + ';font-weight:600">' + s.score + '分</span>' +
          (srcStr ? ' <span style="color:var(--m-text-3);font-size:10px">' + srcStr + '</span>' : '') +
          '</div>';
      }
    }
  }

  var total = 0;
  for (var di = 0; di < data.distribution.length; di++) {
    total += data.distribution[di].count;
  }

  return '<div class="cr-section">' +
    '<div class="cr-section-title"><span class="cr-ico">🎯</span> 自选池 Tier' +
      '<span style="font-size:10px;color:var(--m-text-3);margin-left:8px">共 ' + total + ' 票</span>' +
    '</div>' +
    '<div class="cr-tier-wrap">' + bars + (hasDetail ? details : '') + '</div>' +
    '</div>';
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
// ── V9.0 共振信号卡（底部：多策略交叉验证）──
function buildCrResonance(data) {
  if (!data || !data.resonance_list || !data.resonance_list.length) {
    return '<div class="cr-section" style="padding:12px;margin:0 12px;text-align:center;color:#8e8e93;font-size:13px;">暂无多策略共振标的</div>';
  }
  var top5 = data.resonance_list.slice(0, 5);
  var rows = top5.map(function(r) {
    var badges = r.strategies.map(function(s) {
      var dirIcon = s.direction === 'short' ? '🔻' : '🟢';
      return '<span class="res-badge" style="background:rgba(60,60,67,0.5);padding:2px 6px;border-radius:4px;font-size:11px;margin-right:4px;white-space:nowrap;color:#fff;">' + dirIcon + ' ' + (s.label || s.key) + ' ' + s.confidence + '%</span>';
    }).join('');
    var consensusClass = r.direction_consensus ? 'res-consensus' : 'res-mixed';
    var dirEmoji = r.consensus_direction === 'short' ? '⚠️' : '📈';
    return '<div class="res-row" style="display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid rgba(120,120,128,0.12);">'
      + '<div style="flex:1;min-width:0;">'
        + '<div style="font-size:14px;font-weight:600;color:#fff;">' + dirEmoji + ' ' + r.name + '</div>'
        + '<div style="margin-top:3px;">' + badges + '</div>'
      + '</div>'
      + '<div class="' + consensusClass + '" style="font-size:18px;font-weight:700;padding-left:8px;min-width:50px;text-align:right;">'
        + (r.resonance_score >= 70 ? (r.direction_consensus ? '🔥' : '⚡') : '')
        + r.resonance_score + '<span style="font-size:10px;">分</span>'
      + '</div>'
    + '</div>';
  }).join('');

  return '<div class="cr-section" style="margin:12px;padding:10px 14px;background:linear-gradient(135deg,rgba(100,210,255,0.08),rgba(175,82,222,0.08));border:1px solid rgba(100,210,255,0.2);border-radius:12px;">'
    + '<div class="cr-section-title" style="font-size:15px;font-weight:700;color:#64d2ff;margin-bottom:8px;">🔮 共振信号 · Resonance</div>'
    + '<div style="font-size:11px;color:#8e8e93;margin-bottom:6px;">'
      + data.total_resonances + '只标的获≥2策略交叉验证'
      + (data.sector_keywords && data.sector_keywords.length ? ' · 板块热词：' + data.sector_keywords.slice(0,3).join('、') : '')
    + '</div>'
    + rows
    + '<div style="font-size:10px;color:#8e8e93;margin-top:6px;text-align:right;">'
      + '策略权重：AI=1.0 形态/轮动=1.2 量化=1.1 事件=1.4 并购=1.3'
    + '</div>'
  + '</div>';
};

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
  document.getElementById('world-content').style.display = 'none';
  document.getElementById('world-loading').innerHTML = '<div class="spin"></div>';
  document.getElementById('world-loading').style.display = 'block';
  document.getElementById('graph-content').style.display = 'none';
  document.getElementById('graph-loading').innerHTML = '<div class="spin"></div>';
  document.getElementById('graph-loading').style.display = 'block';
  var active = document.querySelector('.m-tab.active');
  if (active) loadTab(active.dataset.tab);
}
