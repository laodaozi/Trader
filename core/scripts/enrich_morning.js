#!/usr/bin/env node
/**
 * enrich_morning.js — V4.2b LLM 日报增强
 * 
 * 读取 bridge_morning.js 产出的 morning.json，
 * 通过 FinStep Gateway LLM API（DeepSeek/GPT/Claude）生成 market_summary / global_conclusion / 事件摘要，
 * 写回 morning.json。
 * 
 * 依赖：DEEPSEEK_API_KEY 或 LLM_API_KEY 环境变量（否则跳过）
 * 用法：
 *   node enrich_morning.js                     # 正常模式
 *   node enrich_morning.js --force             # 忽略新鲜度，强制重新生成
 *   node enrich_morning.js --dry-run           # 只打印要发的 prompt 不调 API
 * 
 * cron 排程（bridge 之后）：
 *   27 6 * * * /usr/local/bin/node /opt/cycleradar-trader/core/scripts/enrich_morning.js
 * 
 * DeepSeek 计费（v3/v4 Pro）：
 *   Input:  $1/1M tokens    Output: $4/1M tokens
 *   典型消耗：~1,800 input + ~400 output = ~$0.004/次，约 $0.12/月
 */

const fs = require('fs');
const path = require('path');

// ── 配置 ──
const MORNING_JSON = path.resolve(__dirname, '../../data/morning.json');
const LOG_DIR = path.resolve(__dirname, '../../data/logs');
const LOG_FILE = path.join(LOG_DIR, 'enrich_morning.log');
const MAX_AGE_HOURS = 1.5;       // 超时视为过期，不覆盖
const API_TIMEOUT_MS = 60_000;   // API 调用超时（60000ms）

// DeepSeek API endpoint（兼容 OpenAI SDK — 支持 FinStep Gateway 或直连 DeepSeek）
const API_BASE = process.env.LLM_API_BASE || process.env.DEEPSEEK_API_BASE || 'https://new-api.finstep.cn/v1';
const API_KEY = process.env.LLM_API_KEY || process.env.DEEPSEEK_API_KEY || '';
const MODEL = process.env.LLM_MODEL || 'deepseek-v4-pro';  // FinStep gateway model ID

// ── 参数解析 ──
const FORCE = process.argv.includes('--force');
const DRY_RUN = process.argv.includes('--dry-run');

// ── 工具 ──
const now = () => new Date().toISOString();
const log = (...args) => {
  const line = `[${now()}] ${args.join(' ')}`;
  console.log(line);
  fs.mkdirSync(LOG_DIR, { recursive: true });
  fs.appendFileSync(LOG_FILE, line + '\n');
};

// ── Prompt 构建 ──
function buildPrompt(morning) {
  const events = morning.events || [];
  const alpha = morning.alpha_signals || [];
  const sector = morning.sector_outlook || [];
  const commodity = morning.commodity_signals || [];

  // 事件摘要（只取标题，不然 prompt 超长）
  const eventList = events.map((e, i) =>
    `  ${i + 1}. [${e.tier || '?'}] ${e.source}：「${e.title}」`
  ).join('\n');

  // Alpha 信号摘要
  const sTier = alpha.filter(s => s.metadata?.tier === 'S').map(s => s.metadata?.stock_name);
  const bTier = alpha.filter(s => s.metadata?.tier === 'B').map(s => s.metadata?.stock_name);

  // 行业轮动
  const longSectors = sector.filter(s => s.direction === 'long').map(s => s.asset);
  const shortSectors = sector.filter(s => s.direction === 'short').map(s => s.asset);

  // 商品
  const commoditySummary = commodity.map(c => {
    const m = c.metadata || {};
    return `  ${c.asset} ${c.direction === 'long' ? '涨' : '跌'}${Math.abs(m.chg_pct || 0)}% (${m.exchange || ''})`;
  }).join('\n');

  const prompt = `你是一位专业的A股市场分析师，请根据以下今日早间资讯和信号数据，生成一份简明的市场总结。

## 早间资讯（${events.length} 篇）
${eventList}

## Alpha 信号（${alpha.length} 条）
S 级（高置信）：${sTier.join('、') || '无'}
B 级（中置信）：${bTier.join('、') || '无'}
核心主线：${[...new Set(alpha.map(s => s.metadata?.reasons).flat().filter(Boolean))].join('、')}

## 行业轮动（${sector.length} 条）
偏多：${longSectors.join('、') || '无'}
偏空：${shortSectors.join('、') || '无'}

## 商品信号（${commodity.length} 条）
${commoditySummary || '无'}

请用 JSON 格式输出以下内容（只输出 JSON，不要任何解释）：

{
  "market_summary": "一句话概括今日市场核心矛盾（≤80字）",
  "global_conclusion": "3-5句话的详细市场总结，涵盖：1) 核心事件解读 2) 主题/主线判断 3) 风险提示（≤250字）",
  "key_themes": ["主题1", "主题2", "主题3"],
  "events_summary": [
    {"title": "原标题", "one_liner": "一句话要点（≤30字）"}
  ]
}`;

  return prompt;
}

// ── JSON 截断修复 ──
function fixTruncatedJson(str) {
  // 统计括号深度，补全缺失的 ] 和 }
  let depth = 0;
  let inString = false, escape = false;
  const stack = [];
  for (const ch of str) {
    if (escape) { escape = false; continue; }
    if (ch === '\\') { escape = true; continue; }
    if (ch === '"') { inString = !inString; continue; }
    if (inString) continue;
    if (ch === '{' || ch === '[') stack.push(ch);
    else if (ch === '}') { if (stack[stack.length-1] === '{') stack.pop(); else return null; }
    else if (ch === ']') { if (stack[stack.length-1] === '[') stack.pop(); else return null; }
  }
  if (inString) str += '"';  // 补全未闭合字符串
  while (stack.length) {
    const opener = stack.pop();
    str += opener === '{' ? '}' : ']';
  }
  return str;
}

// ── API 调用（兼容 OpenAI SDK 的 chat/completions endpoint） ──
async function callDeepSeek(prompt) {
  if (!API_KEY) {
    log('⚠️  LLM_API_KEY / DEEPSEEK_API_KEY 未设置，跳过 LLM 增强');
    return null;
  }

  const url = `${API_BASE}/chat/completions`;
  const body = {
    model: MODEL,              // FinStep gateway: deepseek-v4-pro / gpt-5.5 / claude-opus-4-8
    messages: [
      { role: 'system', content: '你是一个专业的A股金融分析助手，输出简洁、有洞察力，只用 JSON 格式回复。' },
      { role: 'user', content: prompt }
    ],
    temperature: 0.3,
    max_tokens: 4096,
    response_format: { type: 'json_object' }
  };

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), API_TIMEOUT_MS);

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${API_KEY}`
      },
      body: JSON.stringify(body),
      signal: controller.signal
    });

    clearTimeout(timeout);

    if (!res.ok) {
      const err = await res.text();
      throw new Error(`HTTP ${res.status}: ${err.slice(0, 200)}`);
    }

    const data = await res.json();
    const content = data.choices?.[0]?.message?.content;
    if (!content) throw new Error('API 返回空内容');

    // 尝试解析 JSON
    try {
      return JSON.parse(content);
    } catch (parseErr) {
      // 可能是 markdown 代码块包裹
      const cleaned = content.replace(/^```json\s*/i, '').replace(/```\s*$/, '').trim();
      try {
        return JSON.parse(cleaned);
      } catch (e2) {
        // 可能是截断 — 尝试补全缺失的括号
        const fixed = fixTruncatedJson(cleaned);
        if (fixed) return JSON.parse(fixed);
        throw e2;
      }
    }
  } catch (err) {
    log(`❌ API 调用失败: ${err.message}`);
    return null;
  }
}

// ── 主流程 ──
async function main() {
  log('━━━ enrich_morning V4.2b ━━━');

  // 1. 检查 API Key
  if (!API_KEY) {
    log('❌ API Key 环境变量未设置（LLM_API_KEY 或 DEEPSEEK_API_KEY）');
    log('   请在 /etc/environment 或 ~/.bashrc 中设置后重启 PM2/cron');
    log('   示例：export DEEPSEEK_API_KEY=sk-xxxxxxx');
    process.exit(1);
  }

  // 2. 读取 morning.json
  if (!fs.existsSync(MORNING_JSON)) {
    log('❌ morning.json 不存在，等待 bridge_morning.js 先产出');
    process.exit(1);
  }

  const morning = JSON.parse(fs.readFileSync(MORNING_JSON, 'utf-8'));

  // 3. 检查新鲜度（防止覆盖新数据）
  const genTime = new Date(morning.generated_at).getTime();
  const ageMinutes = (Date.now() - genTime) / 60000;
  if (!FORCE && ageMinutes > MAX_AGE_HOURS * 60) {
    log(`⏭  morning.json 已过期（${ageMinutes.toFixed(0)}min > ${MAX_AGE_HOURS}h 阈值），跳过`);
    process.exit(0);
  }
  log(`   morning.json 生成于 ${ageMinutes.toFixed(0)} 分钟前 ✓`);

  // 4. 检查是否已有结论
  if (!FORCE && morning.global_conclusion && morning.llm_generated_at) {
    const llmAge = (Date.now() - new Date(morning.llm_generated_at).getTime()) / 60000;
    if (llmAge < 120) {
      log(`⏭  LLM 结论已存在（${llmAge.toFixed(0)} 分钟前），跳过（--force 可覆盖）`);
      process.exit(0);
    }
  }

  // 5. 构建 prompt
  const prompt = buildPrompt(morning);
  log(`📝 Prompt 生成完成（约 ${Math.round(prompt.length / 3.5)} tokens）`);

  if (DRY_RUN) {
    log('   --dry-run 模式，prompt 如下：');
    console.log('\n' + prompt + '\n');
    process.exit(0);
  }

  // 6. 调用 API
  log('🚀 调用 DeepSeek API...');
  const startTime = Date.now();
  const result = await callDeepSeek(prompt);
  const elapsed = Date.now() - startTime;

  if (!result) {
    log('❌ LLM 增强失败，morning.json 未修改');
    process.exit(1);
  }

  log(`✅ API 返回成功（${elapsed}ms）`);
  log(`   market_summary: ${(result.market_summary || '').slice(0, 80)}`);
  log(`   key_themes: ${result.key_themes?.length || 0} 个`);

  // 7. 写回 morning.json
  morning.global_conclusion = result.global_conclusion || result.market_summary || '';
  morning.market_summary = result.market_summary || '';
  morning.key_themes = result.key_themes || [];
  morning.llm_generated_at = now();
  morning.llm_model = MODEL;

  // 将 events_summary 一对一映射到 events
  if (result.events_summary && morning.events) {
    const summaries = result.events_summary;
    morning.events = morning.events.map((evt, i) => {
      const match = summaries[i] || summaries.find(s => s.title === evt.title);
      return {
        ...evt,
        summary: match?.one_liner || match?.summary || null,
      };
    });
  }

  // 原子写入：先写 tmp 再 rename
  const tmpPath = MORNING_JSON + '.tmp';
  fs.writeFileSync(tmpPath, JSON.stringify(morning, null, 2), 'utf-8');
  fs.renameSync(tmpPath, MORNING_JSON);
  log(`💾 morning.json 已更新（global_conclusion + ${result.events_summary?.length || 0} 条摘要）`);
  log('━━━ enrich_morning 完成 ━━━');
}

main().catch(err => {
  log(`💥 未捕获异常: ${err.stack}`);
  process.exit(1);
});
