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
// V7.1b: 择时卡 — 大字结论 + 手风琴展开温度详情 + 轮动快照 + header 联动
function buildTimingCard(t, n, snap) {
  if (!t) return '';
  const temp = t.temperature || 0;
  const phase = t.phase || '—';
  const advice = t.advice || '';
  const gc = (n && typeof n.global_conclusion === 'string') ? {} : ((n && n.global_conclusion) || {});
  const action = gc.action || '';
  const keyThesis = gc.key_thesis || '';

  // 动态颜色 + 结论文字
  let accentColor, accentBg, dotClass, verdict;
  if (phase === '进攻' || temp >= 70) {
    accentColor = 'var(--m-positive)'; accentBg = 'rgba(var(--m-positive-rgb),0.08)';
    dotClass = 'bull'; verdict = action || '积极做多';
  } else if (phase === '防守' || temp <= 30) {
    accentColor = 'var(--m-negative)'; accentBg = 'rgba(var(--m-negative-rgb),0.08)';
    dotClass = 'bear'; verdict = action || '防守观望';
  } else {
    accentColor = 'var(--m-warn)'; accentBg = 'rgba(var(--m-warn-rgb),0.08)';
    dotClass = 'neutral'; verdict = action || advice || '轻仓观望';
  }

  // 更新 header 圆点 + 一句话结论
  const dot = document.getElementById('market-dot');
  if (dot) { dot.className = 'market-dot ' + dotClass; }
  const hv = document.getElementById('header-verdict');
  if (hv) { hv.textContent = verdict; hv.style.color = accentColor; }

  // 温度进度条 10格
  const filled = Math.round(temp / 10);
  const bars = Array.from({length:10}, (_,i) =>
    `<div style="flex:1;height:5px;border-radius:3px;background:${i < filled ? accentColor : 'var(--m-surface-2)'};transition:background 0.3s"></div>`
  ).join('');

  // 轮动快照摘要（嵌入手风琴）
  let snapHtml = '';
  if (snap && snap.direction) {
    const conf = snap.confidence || 0;
    const filled8 = Math.round(conf / 12.5);
    const bar = '█'.repeat(filled8) + '░'.repeat(8 - filled8);
    snapHtml = `<div style="margin-top:10px;padding:8px 10px;background:rgba(var(--m-primary-rgb),0.07);border-radius:6px;border-left:2px solid var(--m-primary)">
      <div style="font-size:9px;color:var(--m-text-3);text-transform:uppercase;letter-spacing:.8px;margin-bottom:4px">轮动方向</div>
      <div style="display:flex;align-items:center;justify-content:space-between">
        <span style="font-size:14px;font-weight:700;color:var(--m-primary)">${_h(snap.direction)}</span>
        <span style="font-size:10px;color:var(--m-text-3);font-family:var(--m-mono)">${bar} ${conf}%</span>
      </div>
      ${snap.catalyst ? `<div style="font-size:10px;color:var(--m-text-2);margin-top:4px">${_h(snap.catalyst)}</div>` : ''}
      ${snap.doubt ? `<div style="font-size:10px;color:var(--m-warn);margin-top:2px">存疑: ${_h(snap.doubt)}</div>` : ''}
    </div>`;
  }

  // 折叠详情内容
  const detailHtml = `
    <div style="margin-top:12px">
      <div style="display:flex;gap:4px;margin-bottom:10px">${bars}</div>
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div style="font-size:12px;color:var(--m-text-2)">${phase} · 市场温度 ${temp}/100</div>
        <div style="font-size:22px;font-weight:900;color:${accentColor};opacity:0.9;line-height:1">${temp}</div>
      </div>
      ${keyThesis ? `<div style="font-size:11px;color:var(--m-text-2);margin-top:8px;line-height:1.55;border-left:2px solid ${accentColor};padding-left:8px">${_h(keyThesis)}</div>` : ''}
      ${snapHtml}
    </div>`;

  return `<div class="card accordion-card" style="border-left:3px solid ${accentColor};background:${accentBg}" onclick="this.classList.toggle('open')">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <div>
        <div style="font-size:10px;font-weight:700;color:var(--m-text-3);letter-spacing:1px;text-transform:uppercase;margin-bottom:6px">今日择时</div>
        <div style="font-size:30px;font-weight:800;color:${accentColor};letter-spacing:-0.5px;line-height:1">${_h(verdict)}</div>
      </div>
      <div style="text-align:right;flex-shrink:0;margin-left:12px">
        <span style="font-size:11px;color:var(--m-text-3)">详情<span class="accordion-chevron">▼</span></span>
      </div>
    </div>
    <div class="accordion-body">${detailHtml}</div>
  </div>`;
}

// V7.1b: Top5 共振标的 — 每行点击展开入场/止损/逻辑详情
function buildTop5Card(snap, cr) {
  if (!cr) return '';
  const alpha = cr.alpha || [];
  const etf = cr.etf || [];
  const snapDir = snap ? (snap.direction || '') : '';

  // 关键词提取
  const keywords = ['半导体','芯片','科技','AI','算力','医药','军工','有色','消费','地产','新能源','储能','光伏','汽车','银行','券商'];
  const matchKw = keywords.filter(kw => snapDir.includes(kw));

  // 信号评分
  const STRAT_WEIGHT = {'report_agent':1.4,'scanner':1.2,'ma_signals':1.3,'wanjun_models':1.1,'stock_agent':1.0,'rotation_factor':1.2};
  function scoreSignal(s) {
    const base = (s.confidence || 0) * (STRAT_WEIGHT[s.strategy] || 1.0);
    const meta = s.metadata || {};
    const name = meta.stock_name || meta.sector || s.asset || '';
    const kwBonus = matchKw.some(kw => name.includes(kw) || (meta.sector||'').includes(kw)) ? 1.3 : 1.0;
    return base * kwBonus;
  }

  const all = [...alpha.filter(s=>s.direction==='long'), ...etf.filter(s=>s.direction==='long'||s.direction==='neutral')];
  const scored = all.map(s => ({...s, _score: scoreSignal(s)}))
    .sort((a,b) => b._score - a._score)
    .slice(0, 5);

  if (!scored.length) return '';

  const STRAT_LABEL = {'report_agent':'事件','scanner':'形态','ma_signals':'并购','wanjun_models':'量化','stock_agent':'AI','rotation_factor':'轮动','commodity_radar':'商品'};

  const rows = scored.map((s, i) => {
    const meta = s.metadata || {};
    const al = s._alphaLatest || {};
    const name = meta.stock_name || meta.sector || s.asset;
    const conf = Math.round((s.confidence||0)*100);
    const strat = STRAT_LABEL[s.strategy] || s.strategy;
    const confColor = conf >= 80 ? 'var(--m-positive)' : conf >= 60 ? 'var(--m-primary)' : 'var(--m-warn)';
    const isLast = i === scored.length - 1;

    // 信号强度色条（左侧3px竖线颜色）
    const barColor = conf >= 80 ? 'var(--m-positive)' : conf >= 60 ? 'var(--m-primary)' : 'var(--m-warn)';

    // 展开详情内容
    const entry  = al.entry_price  != null ? al.entry_price.toFixed(2)  : (meta.entry  != null ? (+meta.entry).toFixed(2)  : '—');
    const target = al.target_price != null ? al.target_price.toFixed(2) : (meta.target != null ? (+meta.target).toFixed(2) : '—');
    const stop   = al.stop_loss    != null ? al.stop_loss.toFixed(2)    : (meta.stop   != null ? (+meta.stop).toFixed(2)   : '—');
    const thesis = al.thesis || meta.thesis || meta.industry_hint || '';
    const reasons = meta.reasons || meta.active_factors || [];
    const reasonTags = reasons.slice(0,3).map(r =>
      `<span style="font-size:9px;padding:1px 6px;border-radius:3px;background:rgba(var(--m-primary-rgb),0.12);color:var(--m-primary)">${_h(r)}</span>`
    ).join('');

    const detailHtml = `<div class="top5-detail">
      <div style="display:flex;gap:12px;margin-bottom:6px">
        <div><div style="font-size:9px;color:var(--m-text-3);text-transform:uppercase;letter-spacing:.5px">入场</div><div style="font-size:13px;font-weight:700;color:var(--m-text)">${entry}</div></div>
        <div><div style="font-size:9px;color:var(--m-text-3);text-transform:uppercase;letter-spacing:.5px">目标</div><div style="font-size:13px;font-weight:700;color:var(--m-positive)">${target}</div></div>
        <div><div style="font-size:9px;color:var(--m-text-3);text-transform:uppercase;letter-spacing:.5px">止损</div><div style="font-size:13px;font-weight:700;color:var(--m-negative)">${stop}</div></div>
      </div>
      ${thesis ? `<div style="font-size:11px;color:var(--m-text-2);line-height:1.5;margin-bottom:5px">${_h(thesis)}</div>` : ''}
      ${reasonTags ? `<div style="display:flex;flex-wrap:wrap;gap:3px">${reasonTags}</div>` : ''}
    </div>`;

    return `<div class="top5-row" onclick="this.classList.toggle('open')" style="padding:10px 0;border-bottom:${isLast?'none':'1px solid var(--m-border)'}">
      <div style="display:flex;align-items:center;gap:8px">
        <div style="width:3px;height:32px;border-radius:2px;background:${barColor};flex-shrink:0"></div>
        <div style="font-size:12px;font-weight:700;color:var(--m-text-3);width:14px;flex-shrink:0">${i+1}</div>
        <div style="flex:1;min-width:0">
          <div style="font-size:14px;font-weight:700;color:var(--m-text);line-height:1.2">${_h(name)}<span style="font-size:10px;color:var(--m-text-3);font-family:var(--m-mono);margin-left:5px">${s.asset}</span></div>
          <div style="font-size:10px;color:var(--m-text-3);margin-top:2px">${strat}${meta.tier ? ' · '+meta.tier+'级' : ''}</div>
        </div>
        <div style="text-align:right;flex-shrink:0">
          <div style="font-size:15px;font-weight:800;color:${confColor}">${conf}%</div>
          <span class="top5-chevron" style="color:var(--m-text-3)">▼</span>
        </div>
      </div>
      ${detailHtml}
    </div>`;
  }).join('');

  const title = matchKw.length ? `共振 Top5 · ${matchKw.slice(0,2).join('/')}` : '共振 Top5';
  return `<div class="card" style="border-left:3px solid var(--m-primary)">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
      <div class="card-title" style="margin-bottom:0">📡 ${title}</div>
      <div style="font-size:10px;color:var(--m-text-3)">${scored.length} 个信号</div>
    </div>
    ${rows}
  </div>`;
}

// V7.1: 自选池异动卡 — 只展示有买卖信号的
// V7.2: 概览Tab自选买卖提醒 — 从watchlist信号推导，有买入/止损才展示
async function buildWatchlistAlertCard(container) {
  var signals = [];
  try {
    var res = await fetch('/m/api/watchlist');
    var data = await res.json();
    signals = (data && data.signals) ? data.signals : [];
  } catch(e) { return; }

  // 推导结论（复用自选Tab逻辑）
  function deriveConclusion(s) {
    var nx = s.nx_signal || '';
    var lc = s.lifecycle || '';
    if (nx === 'buy' || nx === 'rising')    return 'buy';
    if (nx === 'sell' || lc === '灭·出局') return 'stop';
    if (lc === '坏·注意')                  return 'warn';
    return 'hold';
  }

  var buys  = signals.filter(function(s){ return deriveConclusion(s) === 'buy'; });
  var stops = signals.filter(function(s){ return deriveConclusion(s) === 'stop'; });
  var warns = signals.filter(function(s){ return deriveConclusion(s) === 'warn'; });

  // 没有任何买入/止损/注意 → 不渲染
  if (!buys.length && !stops.length && !warns.length) return;

  function alertRow(s, type) {
    var color = type === 'buy' ? 'var(--m-positive)' : type === 'stop' ? 'var(--m-negative)' : 'var(--m-warn)';
    var tag   = type === 'buy' ? '买入' : type === 'stop' ? '止损' : '注意';
    var close = s.close != null ? (+s.close).toFixed(2) : '—';
    var stop  = s.stop_loss != null ? (+s.stop_loss).toFixed(2) : '—';
    var pnlHtml = '';
    if (s.pnl_pct != null) {
      var pnlClr = s.pnl_pct >= 0 ? 'var(--m-positive)' : 'var(--m-negative)';
      pnlHtml = '<span style="font-size:12px;font-weight:700;font-family:var(--m-mono);color:' + pnlClr + '">' + (s.pnl_pct >= 0 ? '+' : '') + s.pnl_pct.toFixed(1) + '%</span>';
    }
    return '<div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--m-border)">' +
      '<div style="width:3px;align-self:stretch;border-radius:2px;background:' + color + ';flex-shrink:0"></div>' +
      '<div style="flex:1;min-width:0">' +
        '<span style="font-size:13px;font-weight:700;color:var(--m-text)">' + _h(s.name || s.code) + '</span>' +
        '<span style="font-size:10px;color:var(--m-text-3);font-family:var(--m-mono);margin-left:5px">' + (s.code||'') + '</span>' +
        (type === 'stop' && stop !== '—' ? '<div style="font-size:10px;color:var(--m-negative);margin-top:2px">止损价 ' + stop + ' · 现价 ' + close + '</div>' : '') +
        (type === 'buy' ? '<div style="font-size:10px;color:var(--m-text-3);margin-top:2px">现价 ' + close + '</div>' : '') +
      '</div>' +
      '<div style="display:flex;flex-direction:column;align-items:flex-end;gap:3px;flex-shrink:0">' +
        '<span style="font-size:11px;font-weight:700;padding:2px 8px;border-radius:8px;background:rgba(0,0,0,.2);color:' + color + '">' + tag + '</span>' +
        pnlHtml +
      '</div>' +
    '</div>';
  }

  var html = '';
  if (stops.length || warns.length) {
    var stopRows = stops.map(function(s){ return alertRow(s, 'stop'); }).join('') +
                  warns.map(function(s){ return alertRow(s, 'warn'); }).join('');
    html += '<div style="margin-bottom:10px">' +
      '<div style="font-size:10px;font-weight:700;color:var(--m-negative);text-transform:uppercase;letter-spacing:.8px;margin-bottom:4px">⚠ 需要处理</div>' +
      stopRows + '</div>';
  }
  if (buys.length) {
    var buyRows = buys.map(function(s){ return alertRow(s, 'buy'); }).join('');
    html += '<div>' +
      '<div style="font-size:10px;font-weight:700;color:var(--m-positive);text-transform:uppercase;letter-spacing:.8px;margin-bottom:4px">✅ 买入信号</div>' +
      buyRows + '</div>';
  }

  var card = '<div class="card" style="border-left:3px solid ' + (stops.length ? 'var(--m-negative)' : 'var(--m-positive)') + '">' +
    '<div class="card-title" style="margin-bottom:8px">🔔 自选股提醒</div>' +
    html +
    '<div style="font-size:10px;color:var(--m-text-3);margin-top:8px;text-align:right"><a href="#" onclick="loadTab(\'watchlist\');return false" style="color:var(--m-primary)">查看全部自选 →</a></div>' +
    '</div>';

  container.insertAdjacentHTML('beforeend', card);
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
    const [sumRes, crRes] = await Promise.all([
      fetch('/m/api/summary'),
      fetch('/m/api/cycleradar')
    ]);
    const d = await sumRes.json();
    const cr = crRes.ok ? await crRes.json() : null;
    const el = document.getElementById('overview-content');
    // V7.2: 择时 + Top5 先渲染，自选提醒异步追加
    el.innerHTML =
      buildTimingCard(d.timing, d.event_narrative, d.rotation_snapshot) +
      buildTop5Card(d.rotation_snapshot, cr);
    el.style.display = 'block';
    document.getElementById('overview-loading').style.display = 'none';
    // 自选提醒：有止损/买入才渲染，异步追加不阻塞主渲染
    buildWatchlistAlertCard(el).catch(function() {/* 静默失败，不影响概览主体 */});
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
// V7.1: 色条状态编码 + 买入/持有/止损结论徽章
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

    // 结论推导：nx_signal + lifecycle → 买入 / 持有 / 止损 / 观察
    function deriveConclusion(s) {
      var nx = s.nx_signal || '';
      var lc = s.lifecycle || '';
      if (nx === 'buy' || nx === 'rising')   return { label:'买入', color:'var(--m-positive)', bar:'var(--m-positive)', bg:'rgba(var(--m-positive-rgb),0.12)' };
      if (nx === 'sell' || lc === '灭·出局') return { label:'止损', color:'var(--m-negative)', bar:'var(--m-negative)', bg:'rgba(var(--m-negative-rgb),0.12)' };
      if (lc === '坏·注意')                  return { label:'注意', color:'var(--m-warn)',     bar:'var(--m-warn)',     bg:'rgba(var(--m-warn-rgb),0.12)' };
      if (lc === '住·持有')                  return { label:'持有', color:'var(--m-primary)',  bar:'var(--m-primary)',  bg:'rgba(var(--m-primary-rgb),0.10)' };
      return { label:'观察', color:'var(--m-text-3)', bar:'var(--m-border)', bg:'rgba(255,255,255,0.03)' };
    }

    // 排序：买入 > 注意/止损 > 持有 > 观察
    var ORDER = { '买入':0, '止损':1, '注意':2, '持有':3, '观察':4 };
    signals = signals.slice().sort(function(a, b) {
      return (ORDER[deriveConclusion(a).label]||99) - (ORDER[deriveConclusion(b).label]||99);
    });

    // 顶部汇总条
    var buyCnt  = signals.filter(function(s){ return deriveConclusion(s).label === '买入'; }).length;
    var holdCnt = signals.filter(function(s){ return deriveConclusion(s).label === '持有'; }).length;
    var stopCnt = signals.filter(function(s){ return ['止损','注意'].includes(deriveConclusion(s).label); }).length;

    var summaryHtml = '<div style="display:flex;gap:0;margin-bottom:12px;border:1px solid var(--m-border);border-radius:8px;overflow:hidden">' +
      _wlSummaryCell('买入', buyCnt, 'var(--m-positive)', true) +
      _wlSummaryCell('持有', holdCnt, 'var(--m-primary)', false) +
      _wlSummaryCell('止损/注意', stopCnt, 'var(--m-negative)', false) +
      '</div>';

    var rows = signals.map(function(s) {
      var c = deriveConclusion(s);
      var close  = s.close       != null ? (+s.close).toFixed(2)       : '—';
      var entry  = s.entry_price != null ? (+s.entry_price).toFixed(2) : '—';
      var stop   = s.stop_loss   != null ? (+s.stop_loss).toFixed(2)   : '—';

      var pnlHtml = '';
      if (s.pnl_pct != null) {
        var pnlClr  = s.pnl_pct >= 0 ? 'var(--m-positive)' : 'var(--m-negative)';
        var pnlSign = s.pnl_pct >= 0 ? '+' : '';
        pnlHtml = '<span style="font-size:13px;font-weight:700;font-family:var(--m-mono);color:' + pnlClr + '">' + pnlSign + s.pnl_pct.toFixed(1) + '%</span>';
      }

      // 价格区：市价 / 成本 / 止损（有值才显示）
      var priceItems = [];
      if (close !== '—') priceItems.push('<span class="wl-zone-item"><span class="wl-zone-label">市价</span><span class="wl-zone-val">' + close + '</span></span>');
      if (entry !== '—') priceItems.push('<span class="wl-zone-item"><span class="wl-zone-label">成本</span><span class="wl-zone-val">' + entry + '</span></span>');
      if (stop  !== '—') priceItems.push('<span class="wl-zone-item"><span class="wl-zone-label">止损</span><span class="wl-zone-val" style="color:var(--m-negative)">' + stop + '</span></span>');

      return '<div style="display:flex;align-items:stretch;gap:0;padding:10px 0;border-bottom:1px solid var(--m-border);cursor:pointer" onclick="openStockModal(\'' + (s.code||'') + '\')">' +
        // 左侧色条
        '<div style="width:3px;border-radius:2px;background:' + c.bar + ';flex-shrink:0;margin-right:10px"></div>' +
        // 主内容
        '<div style="flex:1;min-width:0">' +
          '<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">' +
            '<span style="font-size:14px;font-weight:700;color:var(--m-text)">' + _h(s.name||s.code) + '</span>' +
            '<span style="font-size:10px;color:var(--m-text-3);font-family:var(--m-mono)">' + (s.code||'') + '</span>' +
          '</div>' +
          (priceItems.length ? '<div class="wl-zone" style="margin-top:2px">' + priceItems.join('') + '</div>' : '') +
        '</div>' +
        // 右侧：结论 + 收益
        '<div style="display:flex;flex-direction:column;align-items:flex-end;gap:4px;flex-shrink:0;min-width:56px">' +
          '<span style="font-size:11px;font-weight:700;padding:2px 10px;border-radius:10px;background:' + c.bg + ';color:' + c.color + '">' + c.label + '</span>' +
          pnlHtml +
        '</div>' +
      '</div>';
    }).join('');

    container.innerHTML = '<div class="card">' +
      '<div class="card-title" style="margin-bottom:10px">📱 自选股 (' + signals.length + ')</div>' +
      summaryHtml + rows + '</div>';
    container.style.display = 'block';
  } catch(e) {
    document.getElementById('wl-loading').innerHTML = '<div class="nodata">加载失败: ' + e.message + '</div>';
  }
}

function _wlSummaryCell(label, count, color, first) {
  return '<div style="flex:1;text-align:center;padding:10px 6px' + (first ? '' : ';border-left:1px solid var(--m-border)') + '">' +
    '<div style="font-size:20px;font-weight:800;color:' + color + '">' + count + '</div>' +
    '<div style="font-size:10px;color:var(--m-text-3);margin-top:2px">' + label + '</div>' +
  '</div>';
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
    const [crRes, hyRes, trkRes] = await Promise.all([
      fetch('/m/api/cycleradar'),
      fetch('/m/api/haoyunge'),
      fetch('/m/api/tracker/all')
    ]);
    var d = await crRes.json();
    var hy = hyRes.ok ? await hyRes.json() : null;
    var trk = trkRes.ok ? await trkRes.json() : null;
    // V7.2: 事件叙事第一，统计栏合并好运指数+多空综述，移除独立好运哥卡和市场综述卡
    document.getElementById('cr-content').innerHTML =
      buildCrEventNarrative(d.event_narrative, d.hotEvents) +
      buildCrStatsBar(d.summary, d.event_narrative, d.daily_pnl, hy) +
      buildStrategyWinRateCard(trk, d.byStrategy) +
      buildCrCategorySections(d.hotEvents, d.alpha, d.etf, d.commodity, d.alpha_latest);
    attachCrExpandHandlers();
    document.getElementById('cr-content').style.display = 'block';
    document.getElementById('cr-loading').style.display = 'none';
  } catch(e) {
    document.getElementById('cr-loading').innerHTML = '<div class="nodata">加载失败: ' + e.message + '</div>';
  }
}

// ── V7.2 信号Tab统计栏：合并好运指数 + 多空综述 ──
function buildCrStatsBar(summary, en, dailyPnl, hy) {
  var gc = (en && en.global_conclusion) || {};
  var sigCount = summary ? (summary.active || 0) : 0;
  var l = summary ? (summary.longCount || 0) : 0;
  var s = summary ? (summary.shortCount || 0) : 0;

  // 30日胜率
  var winRate = null;
  if (dailyPnl && dailyPnl.win_rate != null) winRate = dailyPnl.win_rate;
  else if (gc.win_rate != null) winRate = gc.win_rate;

  // 好运指数：来自 haoyunge API（posture.score 或 position_pct）
  var hyScore = null;
  var hyLabel = '';
  var hyColor = 'var(--m-text-2)';
  if (hy) {
    // posture 字段或直接的 position_pct
    var pos = hy.position_pct || hy.posture_pct || null;
    if (pos != null) {
      hyScore = Math.round(pos);
      hyColor = hyScore >= 70 ? 'var(--m-positive)' : hyScore >= 40 ? 'var(--m-warn)' : 'var(--m-negative)';
      hyLabel = hy.posture || hy.regime || '';
    }
  }

  // 多空比文字
  var lsRatio = s > 0 ? (l / s).toFixed(1) + 'x' : (l > 0 ? '全多' : '—');
  var lsColor = l >= s ? 'var(--m-positive)' : 'var(--m-negative)';

  // 市场体制一句话（来自信号分布）
  var regime = '';
  if (l > 0 || s > 0) {
    var ratio = l / Math.max(s, 1);
    if (ratio >= 2.5)     regime = '强势做多';
    else if (ratio >= 2)  regime = '进攻偏多';
    else if (l >= s)      regime = '均衡偏多';
    else if (ratio >= 0.4) regime = '偏空防御';
    else                   regime = '强势避险';
  }

  // 顶行：4格核心数据
  var topRow = '<div style="display:flex;border-bottom:1px solid var(--m-border)">' +
    _statCell(sigCount + '条', '活跃信号', 'var(--m-primary)', true) +
    _statCell(lsRatio, '多空比', lsColor, false) +
    _statCell(winRate !== null ? winRate + '%' : '—', '30日胜率', winRate >= 60 ? 'var(--m-positive)' : winRate !== null && winRate < 45 ? 'var(--m-negative)' : 'var(--m-text-2)', false) +
    (hyScore !== null ? _statCell(hyScore + '%', '好运指数', hyColor, false) : _statCell('—', '好运指数', 'var(--m-text-3)', false)) +
    '</div>';

  // 底行：体制标签 + 好运指数说明
  var bottomParts = [];
  if (regime) bottomParts.push('<span style="font-size:11px;font-weight:700;color:' + lsColor + '">' + regime + '</span>');
  if (hyLabel) bottomParts.push('<span style="font-size:11px;color:var(--m-text-3)">· 好运哥建议 <b style="color:' + hyColor + '">' + hyLabel + '</b></span>');
  if (l > 0 || s > 0) bottomParts.push('<span style="font-size:11px;color:var(--m-text-3)">多 ' + l + ' · 空 ' + s + '</span>');

  var bottomRow = bottomParts.length
    ? '<div style="display:flex;align-items:center;gap:8px;padding:8px 14px;flex-wrap:wrap">' + bottomParts.join('') + '</div>'
    : '';

  return '<div class="cr-stats-bar" style="background:var(--m-surface-2);border-radius:10px;margin-bottom:12px;overflow:hidden">' + topRow + bottomRow + '</div>';
}

function _statCell(val, label, color, first) {
  return '<div style="flex:1;text-align:center;padding:10px 4px' + (first ? '' : ';border-left:1px solid var(--m-border)') + '">' +
    '<div style="font-size:16px;font-weight:800;color:' + color + ';line-height:1.2">' + val + '</div>' +
    '<div style="font-size:10px;color:var(--m-text-3);margin-top:2px">' + label + '</div>' +
    '</div>';
}

// ── V7.2 策略胜率排行卡（信号有效性检验）──
function buildStrategyWinRateCard(trk, byStrategy) {
  // byStrategy: 当前活跃信号分布（来自 cycleradar API）
  // trk: tracker 历史数据（hits/misses/records）
  var STRAT_LABEL = {
    'report_agent': '事件驱动', 'scanner': '形态扫描',
    'ma_signals': '均线信号', 'wanjun_models': '量化模型',
    'stock_agent': 'AI研判', 'rotation_factor': '轮动因子',
    'commodity_radar': '商品雷达'
  };

  // 从 tracker records 统计每策略胜率
  var stratStats = {};
  if (trk && trk.records) {
    trk.records.forEach(function(r) {
      // signal 字段如 "✅ 买入"，strategy 字段从 byStrategy 映射不到，用 signal 类型代替
      // verdict: HIT / LOSE / EXPIRE / pending/null
      var sig = r.signal || '';
      // 用 horizon 作为维度：5日/10日/20日
      var h = r.horizon || 5;
      var key = h + '日';
      if (!stratStats[key]) stratStats[key] = { hit: 0, lose: 0, expire: 0, total: 0 };
      stratStats[key].total++;
      var v = (r.verdict || '').toUpperCase();
      if (v === 'HIT')    stratStats[key].hit++;
      else if (v === 'LOSE' || v === 'MISS') stratStats[key].lose++;
      else if (v === 'EXPIRE') stratStats[key].expire++;
    });
  }

  // 当前活跃信号分布
  var activeRows = '';
  if (byStrategy && byStrategy.length) {
    activeRows = byStrategy.slice(0,6).map(function(s) {
      var label = STRAT_LABEL[s.strategy] || s.strategy;
      var longPct = s.count > 0 ? Math.round(s.long / s.count * 100) : 0;
      var barColor = longPct >= 70 ? 'var(--m-positive)' : longPct >= 40 ? 'var(--m-primary)' : 'var(--m-negative)';
      return '<div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--m-border)">' +
        '<div style="flex:1;font-size:11px;color:var(--m-text)">' + label + '</div>' +
        '<div style="font-size:11px;color:var(--m-text-3);flex-shrink:0">' + s.count + '条</div>' +
        '<div style="width:60px;height:4px;background:var(--m-border);border-radius:2px;flex-shrink:0">' +
          '<div style="width:' + longPct + '%;height:100%;background:' + barColor + ';border-radius:2px"></div>' +
        '</div>' +
        '<div style="font-size:10px;color:' + barColor + ';font-weight:700;width:28px;text-align:right;flex-shrink:0">' + longPct + '%多</div>' +
      '</div>';
    }).join('');
  }

  // 胜率统计（horizon维度）
  var winRateRows = '';
  var horizons = Object.keys(stratStats).sort();
  if (horizons.length) {
    winRateRows = horizons.map(function(h) {
      var st = stratStats[h];
      var decided = st.hit + st.lose;
      var wr = decided > 0 ? Math.round(st.hit / decided * 100) : null;
      var wrColor = wr === null ? 'var(--m-text-3)' : wr >= 60 ? 'var(--m-positive)' : wr >= 45 ? 'var(--m-warn)' : 'var(--m-negative)';
      var wrText = wr !== null ? wr + '%' : '待验证';
      return '<div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--m-border)">' +
        '<div style="flex:1;font-size:11px;color:var(--m-text)">' + h + '跟踪</div>' +
        '<div style="font-size:10px;color:var(--m-text-3)">' + st.total + '条 · 已判' + decided + '</div>' +
        '<div style="font-size:13px;font-weight:800;color:' + wrColor + ';min-width:44px;text-align:right">' + wrText + '</div>' +
      '</div>';
    }).join('');
  } else {
    winRateRows = '<div style="font-size:11px;color:var(--m-text-3);padding:8px 0">暂无已判定记录，信号验证中</div>';
  }

  return '<div class="cr-section" style="margin-bottom:12px">' +
    '<details>' +
      '<summary style="display:flex;align-items:center;gap:8px;padding:10px 14px;background:var(--m-surface-2);border-radius:8px;cursor:pointer;list-style:none;user-select:none">' +
        '<span style="font-size:12px;font-weight:700;color:var(--m-text)">📊 策略有效性</span>' +
        '<span style="font-size:10px;color:var(--m-text-3);margin-left:auto">' + (trk ? trk.totalDecisions : 0) + '条历史 · 点击展开</span>' +
        '<span style="font-size:10px;color:var(--m-text-3)">▼</span>' +
      '</summary>' +
      '<div style="padding:4px 14px 0">' +
        '<div style="font-size:10px;font-weight:700;color:var(--m-text-3);text-transform:uppercase;letter-spacing:.8px;margin:10px 0 4px">胜率验证（按持有周期）</div>' +
        winRateRows +
        '<div style="font-size:10px;font-weight:700;color:var(--m-text-3);text-transform:uppercase;letter-spacing:.8px;margin:10px 0 4px">当前活跃信号分布</div>' +
        activeRows +
      '</div>' +
    '</details>' +
  '</div>';
}


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

// V7.1: 信号Tab收敛 — Alpha三档折叠，ETF/商品合并为轮动热度卡
function buildCrCategorySections(hotEvents, alpha, etf, commodity, alpha_latest) {
  var sortedAlpha = (alpha || []).slice().sort(function(a, b) {
    return (b.confidence || 0) - (a.confidence || 0);
  });

  // Alpha 按置信度分三档
  var strong = sortedAlpha.filter(function(s){ return (s.confidence||0) >= 0.8; });
  var mid    = sortedAlpha.filter(function(s){ var c=s.confidence||0; return c>=0.6 && c<0.8; });
  var weak   = sortedAlpha.filter(function(s){ return (s.confidence||0) < 0.6; });

  return (
    _buildAlphaTiered(strong, mid, weak, alpha_latest) +
    _buildRotationHeatCard(etf || [], commodity || [])
  );
}

// Alpha 三档折叠展示
function _buildAlphaTiered(strong, mid, weak, alpha_latest) {
  // enrich same as before
  function enrich(list) {
    var alSignals = alpha_latest && alpha_latest.signals ? alpha_latest.signals : (Array.isArray(alpha_latest) ? alpha_latest : []);
    var alMap = {};
    alSignals.forEach(function(al) { if (al.code) alMap[al.code] = al; });
    return list.map(function(s) {
      if (alMap[s.asset]) {
        s = Object.assign({}, s, { _alphaLatest: alMap[s.asset] });
        if (alMap[s.asset].direction === s.direction) {
          s.multi_source = true;
          s.confidence = Math.min(1.0, (s.confidence || 0) + 0.15);
        }
      }
      return s;
    });
  }
  strong = enrich(strong);
  mid    = enrich(mid);

  function buildTier(label, color, bgColor, list, defaultOpen) {
    if (!list.length) return '';
    var items = list.slice(0, 8).map(function(s) {
      return _buildCrSignalRowCompact(s);
    }).join('');
    var openAttr = defaultOpen ? 'open' : '';
    return `<details class="cr-tier-details" ${openAttr} style="margin-bottom:8px">
      <summary style="display:flex;align-items:center;gap:8px;padding:10px 14px;background:var(--m-surface-2);border-radius:8px;cursor:pointer;list-style:none;user-select:none">
        <span style="width:8px;height:8px;border-radius:50%;background:${color};flex-shrink:0"></span>
        <span style="font-size:12px;font-weight:700;color:var(--m-text)">${label}</span>
        <span style="font-size:10px;padding:1px 8px;border-radius:10px;background:${bgColor};color:${color};font-weight:600">${list.length} 个</span>
        <span style="margin-left:auto;font-size:10px;color:var(--m-text-3)">▼</span>
      </summary>
      <div style="padding:4px 0">${items}</div>
    </details>`;
  }

  var total = strong.length + mid.length + weak.length;
  var header = `<div class="cr-section" style="padding-bottom:0">
    <div class="cr-section-title" style="margin-bottom:10px">
      <span class="cr-ico">📈</span> Alpha 信号
      <span style="font-size:10px;color:var(--m-text-3);font-weight:400;margin-left:4px">${total} 条</span>
    </div>`;

  return header +
    buildTier('强信号 ≥80%', 'var(--m-positive)', 'rgba(var(--m-positive-rgb),0.12)', strong, true) +
    buildTier('中信号 60-80%', 'var(--m-primary)', 'rgba(var(--m-primary-rgb),0.12)', mid, false) +
    buildTier('弱信号 <60%', 'var(--m-text-3)', 'rgba(255,255,255,0.06)', weak, false) +
    '</div>';
}

// 单行紧凑信号（用于三档列表）
function _buildCrSignalRowCompact(s) {
  var meta = s.metadata || {};
  var al = s._alphaLatest || {};
  var name = meta.stock_name || meta.sector || s.asset;
  var conf = Math.round((s.confidence||0)*100);
  var isLong = s.direction === 'long';
  var confColor = conf >= 80 ? 'var(--m-positive)' : conf >= 60 ? 'var(--m-primary)' : 'var(--m-text-3)';
  var STRAT_LABEL = {'report_agent':'事件','scanner':'形态','ma_signals':'并购','wanjun_models':'量化','stock_agent':'AI','rotation_factor':'轮动'};
  var strat = STRAT_LABEL[s.strategy] || s.strategy || '';
  var entry  = al.entry_price  != null ? al.entry_price.toFixed(2)  : '—';
  var target = al.target_price != null ? al.target_price.toFixed(2) : '—';
  var stop   = al.stop_loss    != null ? al.stop_loss.toFixed(2)    : '—';
  var recBadge = s.multi_source ? '<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:rgba(168,85,247,0.15);color:#a855f7;margin-left:4px">共振</span>' : '';

  return `<div class="top5-row" onclick="this.classList.toggle('open')" style="padding:9px 14px;border-bottom:1px solid var(--m-border)">
    <div style="display:flex;align-items:center;gap:8px">
      <span style="font-size:10px;font-weight:800;padding:2px 6px;border-radius:4px;background:${isLong?'rgba(var(--m-positive-rgb),0.12)':'rgba(var(--m-negative-rgb),0.12)'};color:${isLong?'var(--m-positive)':'var(--m-negative)'};flex-shrink:0">${isLong?'多':'空'}</span>
      <div style="flex:1;min-width:0">
        <span style="font-size:13px;font-weight:700;color:var(--m-text)">${_h(name)}</span>
        <span style="font-size:10px;color:var(--m-text-3);margin-left:5px;font-family:var(--m-mono)">${s.asset}</span>
        ${recBadge}
        <div style="font-size:10px;color:var(--m-text-3);margin-top:1px">${strat}</div>
      </div>
      <div style="text-align:right;flex-shrink:0">
        <div style="font-size:14px;font-weight:800;color:${confColor}">${conf}%</div>
        <span class="top5-chevron" style="color:var(--m-text-3)">▼</span>
      </div>
    </div>
    <div class="top5-detail">
      <div style="display:flex;gap:14px;margin-top:6px">
        <div><div style="font-size:9px;color:var(--m-text-3);text-transform:uppercase">入场</div><div style="font-size:12px;font-weight:700;color:var(--m-text)">${entry}</div></div>
        <div><div style="font-size:9px;color:var(--m-text-3);text-transform:uppercase">目标</div><div style="font-size:12px;font-weight:700;color:var(--m-positive)">${target}</div></div>
        <div><div style="font-size:9px;color:var(--m-text-3);text-transform:uppercase">止损</div><div style="font-size:12px;font-weight:700;color:var(--m-negative)">${stop}</div></div>
      </div>
      ${al.thesis ? `<div style="font-size:11px;color:var(--m-text-2);margin-top:5px;line-height:1.5">${_h(al.thesis)}</div>` : ''}
    </div>
  </div>`;
}

// ETF + 商品 → 轮动热度卡（合并，分层颜色，Top5 each）
function _buildRotationHeatCard(etf, commodity) {
  if (!etf.length && !commodity.length) return '';

  function tierColor(conf) {
    if (conf >= 0.8) return { text:'var(--m-positive)', bg:'rgba(var(--m-positive-rgb),0.1)' };
    if (conf >= 0.6) return { text:'var(--m-warn)',     bg:'rgba(var(--m-warn-rgb),0.1)' };
    return { text:'var(--m-text-3)', bg:'rgba(255,255,255,0.04)' };
  }

  function buildGroup(icon, label, list) {
    if (!list.length) return '';
    var sorted = list.slice().sort(function(a,b){ return (b.confidence||0)-(a.confidence||0); }).slice(0,5);
    var rows = sorted.map(function(s) {
      var meta = s.metadata || {};
      var name = meta.stock_name || meta.sector || meta.etf_code || s.asset;
      var conf = Math.round((s.confidence||0)*100);
      var c = tierColor(s.confidence||0);
      var isLong = s.direction === 'long';
      var dirBadge = `<span style="font-size:9px;padding:1px 5px;border-radius:3px;font-weight:700;background:${isLong?'rgba(var(--m-positive-rgb),0.12)':'rgba(var(--m-negative-rgb),0.12)'};color:${isLong?'var(--m-positive)':'var(--m-negative)'}">${isLong?'多':'空'}</span>`;
      return `<div style="display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid var(--m-border)">
        ${dirBadge}
        <span style="font-size:12px;font-weight:600;color:var(--m-text);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${_h(name)}</span>
        <span style="font-size:12px;font-weight:800;padding:2px 8px;border-radius:4px;background:${c.bg};color:${c.text};flex-shrink:0">${conf}%</span>
      </div>`;
    }).join('');
    return `<div style="margin-bottom:12px">
      <div style="font-size:10px;font-weight:700;color:var(--m-text-3);text-transform:uppercase;letter-spacing:.8px;margin-bottom:6px">${icon} ${label} · ${sorted.length}/${list.length}</div>
      ${rows}
    </div>`;
  }

  return `<div class="cr-section">
    <div class="cr-section-title"><span class="cr-ico">🔄</span> 轮动热度</div>
    ${buildGroup('📊','ETF / 板块', etf)}
    ${buildGroup('🛢️','商品', commodity)}
  </div>`;
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
