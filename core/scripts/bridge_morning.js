#!/usr/bin/env node
/**
 * bridge_morning.js — WeWe RSS → morning.json 日报桥接脚本
 *
 * 功能：
 *   1. 从 wewe-rss.db 提取 48h 热点文章 → events
 *   2. 从 upstream_signals.jsonl 提取最新信号 → alpha/ETF/商品
 *   3. 组装 morning.json → /opt/cycleradar-trader/data/morning.json
 *
 * 运行：node /opt/cycleradar-trader/core/scripts/bridge_morning.js
 * Cron：35 6 * * * node /opt/cycleradar-trader/core/scripts/bridge_morning.js
 *
 * 不依赖 better-sqlite3（使用 sqlite3 CLI，同现有 _getHotEvents 模式）
 */

const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

// ── 配置 ──────────────────────────────────────────────────────────────────
const WEWE_DB     = '/opt/wewe-rss-deploy/data/wewe-rss.db';
const SIGNALS_FILE = '/opt/cycleradar-trader/data/upstream_signals.jsonl';
const OUTPUT      = '/opt/cycleradar-trader/data/morning.json';
const MAX_ARTICLES = 30;

// ── 公众号分级（与 mobile.js 的 HOT_FEED_TIER_S/HOT_FEED_TIER_A 对齐）──
const TIER_S = new Set([
  '叙事平权old', '微策神机', '财闻私享', '财经早餐',
]);
const TIER_A = new Map([
  ['数据宝', 3],   // 限流 ≤3
]);

function getTier(source) {
  if (TIER_S.has(source)) return 'S';
  if (TIER_A.has(source)) return 'A';
  return 'default';
}

// ── 1. 提取文章 ──────────────────────────────────────────────────────────

function extractArticles() {
  const since = Math.floor(Date.now() / 1000) - 172800; // 48h
  let stdout;
  try {
    const query = `SELECT a.title, a.publish_time, f.mp_name, a.pic_url ` +
      `FROM articles a JOIN feeds f ON a.mp_id = f.id ` +
      `WHERE a.publish_time > ${since} ORDER BY a.publish_time DESC LIMIT 200;`;
    stdout = execFileSync('sqlite3', ['-csv', '-header', WEWE_DB, query],
      { timeout: 10000, encoding: 'utf8', maxBuffer: 10 * 1024 * 1024 });
  } catch (e) {
    console.error('[bridge] sqlite3 query failed:', e.message);
    return [];
  }

  const lines = stdout.trim().split('\n');
  if (lines.length <= 1) {
    // 只有 header 或无数据
    return [];
  }

  // CSV 解析（兼容引号内逗号）
  function parseCSV(line) {
    const parts = [];
    let buf = '';
    let inQuote = false;
    for (const ch of line) {
      if (ch === '"') { inQuote = !inQuote; continue; }
      if (ch === ',' && !inQuote) { parts.push(buf.trim()); buf = ''; continue; }
      buf += ch;
    }
    parts.push(buf.trim());
    return parts.map(p => p.replace(/\r/g, ''));
  }

  const raw = [];
  for (let i = 1; i < lines.length; i++) {
    const row = parseCSV(lines[i]);
    if (row.length < 4 || !row[0]) continue;
    raw.push({
      title:       row[0],
      publish_time: parseInt(row[1]) || 0,
      source:      row[2] || '',
      pic_url:     row[3] || null,
    });
  }

  // 分级 + 限流
  const tierCount = {};
  const articles = [];
  for (const a of raw) {
    const tier = getTier(a.source);
    const limit = TIER_A.get(a.source);
    if (limit !== undefined) {
      tierCount[a.source] = (tierCount[a.source] || 0) + 1;
      if (tierCount[a.source] > limit) continue;
    }
    articles.push({
      title:        a.title,
      source:       a.source,
      publish_time: new Date(a.publish_time * 1000).toISOString(),
      pic_url:      a.pic_url,
      tier,
      summary:      null,  // LLM 填充位
    });
    if (articles.length >= MAX_ARTICLES) break;
  }

  return articles;
}

// ── 2. 提取信号 ──────────────────────────────────────────────────────────

function extractSignals() {
  const signals = { alpha: [], etf: [], commodity: [] };
  try {
    const raw = fs.readFileSync(SIGNALS_FILE, 'utf8');
    const lines = raw.trim().split('\n').filter(Boolean);

    // 取最新 50 条，按 signal_id 去重（保留最后出现的）
    const recent = lines.slice(-50);
    const seen = new Set();
    const parsed = [];

    for (let i = recent.length - 1; i >= 0; i--) {
      try {
        const s = JSON.parse(recent[i]);
        if (!s.signal_id) continue;
        if (seen.has(s.signal_id)) continue;
        seen.add(s.signal_id);
        // 过滤过期信号
        if (s.expiry && new Date(s.expiry) < new Date()) continue;
        parsed.unshift(s);
      } catch { /* skip malformed lines */ }
    }

    for (const s of parsed) {
      const entry = {
        signal_id:   s.signal_id,
        asset:       s.asset || '',
        direction:   s.direction || 'long',
        confidence:  s.confidence || 0,
        expiry:      s.expiry || null,
        metadata:    s.metadata || null,
      };
           if (s.strategy === 'stock_agent' || s.strategy === 'ma_signals') signals.alpha.push(entry);
      else if (s.strategy === 'rotation_factor') signals.etf.push(entry);
      else if (s.strategy === 'commodity_radar') signals.commodity.push(entry);
      // 其他策略归入 alpha（保守）
      else signals.alpha.push(entry);
    }
  } catch (e) {
    console.error('[bridge] signals read failed:', e.message);
  }
  return signals;
}

// ── 3. 组装 + 写入 ───────────────────────────────────────────────────────

function main() {
  const start = Date.now();
  const articles = extractArticles();
  const signals  = extractSignals();

  const report = {
    generated_at:  new Date().toISOString(),
    source:        'wewe-rss-bridge-v1',
    version:       '1.0.0',
    events:        articles,
    alpha_signals:       signals.alpha,
    sector_outlook:      signals.etf,
    commodity_signals:   signals.commodity,
    global_conclusion:   null,   // V4.2 report_agent 填充
    article_count: articles.length,
    signal_count:  signals.alpha.length + signals.etf.length + signals.commodity.length,
  };

  fs.mkdirSync(path.dirname(OUTPUT), { recursive: true });
  fs.writeFileSync(OUTPUT, JSON.stringify(report, null, 2), 'utf8');

  const elapsed = Date.now() - start;
  console.log(`[bridge] morning.json 生成完成 (${elapsed}ms): ${articles.length} 文章, ${report.signal_count} 信号`);
}

main();
