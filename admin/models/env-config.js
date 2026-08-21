'use strict';

const fs = require('fs');
const path = require('path');

const CONFIG_FILE = path.join(__dirname, '..', '..', 'data', 'env-config.json');

// 14 模型：key → 中文名 + 所属风格（与 core/scanner.py MODEL_FUNCS 对齐）
const MODELS = [
  { key: 'qkxl',  name: '钱坤寻龙', style: 'short' },
  { key: 'zsji',  name: '主升狙击', style: 'wave' },
  { key: 'htji',  name: '回调狙击', style: 'short' },
  { key: 'xsqk',  name: '向上缺口', style: 'short' },
  { key: 'zxji',  name: '中线狙击', style: 'mid' },
  { key: 'bdxy',  name: '波段雄鹰', style: 'wave' },
  { key: 'rzq',   name: '弱转强',   style: 'reversal' },
  { key: 'sldb',  name: '缩量地板', style: 'reversal' },
  { key: 'ztht',  name: '涨停回踩', style: 'short' },
  { key: 'gwzl',  name: '高位整理', style: 'wave' },
  { key: 'jxgz',  name: '均线共振', style: 'trend' },
  { key: 'hydx',  name: '好运低吸', style: 'trend' },
  { key: 'nsdyy', name: '牛市第一阳', style: 'reversal' },
  { key: 'cqft',  name: '超强反弹', style: 'short' },
];

// 5 档交易风格 → 模型中文名集合
const STYLE_GROUPS = {
  short:    { label: '短线',     models: ['钱坤寻龙', '回调狙击', '向上缺口', '涨停回踩', '超强反弹'] },
  wave:     { label: '波段',     models: ['主升狙击', '波段雄鹰', '高位整理'] },
  mid:      { label: '中线',     models: ['中线狙击'] },
  trend:    { label: '趋势共振', models: ['均线共振', '好运低吸'] },
  reversal: { label: '反转底部', models: ['弱转强', '缩量地板', '牛市第一阳'] },
};

const DEFAULT_CONFIG = {
  version: 1,
  models: Object.fromEntries(MODELS.map(m => [m.key, true])),
  style_preference: '',
};

const NAME_TO_KEY = Object.fromEntries(MODELS.map(m => [m.name, m.key]));

function _read() {
  try {
    const raw = fs.readFileSync(CONFIG_FILE, 'utf8');
    const cfg = JSON.parse(raw);
    return cfg && typeof cfg === 'object' ? cfg : {};
  } catch (_) {
    return {};
  }
}

function _write(cfg) {
  const tmp = CONFIG_FILE + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(cfg, null, 2) + '\n', 'utf8');
  fs.renameSync(tmp, CONFIG_FILE);
}

// 归一化：缺省补齐，未知键剔除
function getConfig() {
  const cfg = _read();
  const models = {};
  for (const m of MODELS) {
    const v = cfg.models && cfg.models[m.key];
    models[m.key] = v === undefined ? true : !!v;
  }
  const pref = (cfg.style_preference && STYLE_GROUPS[cfg.style_preference]) ? cfg.style_preference : '';
  return { version: 1, models, style_preference: pref };
}

function saveConfig(partial) {
  const cur = getConfig();
  const next = {
    version: 1,
    models: cur.models,
    style_preference: cur.style_preference,
  };
  if (partial && partial.models) {
    for (const m of MODELS) {
      if (partial.models[m.key] !== undefined) {
        next.models[m.key] = !!partial.models[m.key];
      }
    }
  }
  if (partial && partial.style_preference !== undefined) {
    next.style_preference = STYLE_GROUPS[partial.style_preference] ? partial.style_preference : '';
  }
  _write(next);
  return next;
}

function getEnabledModelKeys() {
  const cfg = getConfig();
  return MODELS.filter(m => cfg.models[m.key]).map(m => m.key);
}

function getEnabledModelNames() {
  const cfg = getConfig();
  return MODELS.filter(m => cfg.models[m.key]).map(m => m.name);
}

// 中文模型名 → 风格 key；未知返回 null
function styleOfModelName(name) {
  const key = NAME_TO_KEY[name];
  if (!key) return null;
  const model = MODELS.find(m => m.key === key);
  return model ? model.style : null;
}

// 给定信号命中的模型名列表，返回其命中的风格集合（用于 /m 排序）
function stylesOfModelNames(names) {
  if (!Array.isArray(names)) return [];
  const set = new Set();
  for (const n of names) {
    const s = styleOfModelName(n);
    if (s) set.add(s);
  }
  return Array.from(set);
}

module.exports = {
  CONFIG_FILE,
  MODELS,
  STYLE_GROUPS,
  getConfig,
  saveConfig,
  getEnabledModelKeys,
  getEnabledModelNames,
  styleOfModelName,
  stylesOfModelNames,
};
