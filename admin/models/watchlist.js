'use strict';

const fs = require('fs/promises');
const path = require('path');

const WATCHLIST_FILE = path.join(__dirname, '..', '..', 'data', 'watchlist.json');

async function _readAll() {
  try {
    const raw = await fs.readFile(WATCHLIST_FILE, 'utf8');
    return JSON.parse(raw);
  } catch (error) {
    if (error && error.code === 'ENOENT') return { stocks: [], updated_at: null };
    throw error;
  }
}

async function _writeAll(data) {
  await fs.writeFile(WATCHLIST_FILE, JSON.stringify(data, null, 2) + '\n', 'utf8');
}

function _now() {
  return new Date().toISOString();
}

async function getAll() {
  const data = await _readAll();
  return data.stocks || [];
}

async function getByCode(code) {
  const stocks = await getAll();
  return stocks.find((s) => s.code === code) || null;
}

async function add(stock) {
  const data = await _readAll();
  const stocks = data.stocks || [];
  if (stocks.some((s) => s.code === stock.code)) {
    return { added: false, reason: '代码已存在' };
  }
  stocks.push({ code: stock.code, name: stock.name || '', notes: stock.notes || '', added_at: _now() });
  data.stocks = stocks;
  data.updated_at = _now();
  await _writeAll(data);
  return { added: true };
}

async function update(code, fields) {
  const data = await _readAll();
  const stocks = data.stocks || [];
  const idx = stocks.findIndex((s) => s.code === code);
  if (idx === -1) return { updated: false, reason: '股票不存在' };
  if (fields.name !== undefined) stocks[idx].name = fields.name;
  if (fields.notes !== undefined) stocks[idx].notes = fields.notes;
  data.updated_at = _now();
  await _writeAll(data);
  return { updated: true };
}

async function remove(code) {
  const data = await _readAll();
  const stocks = data.stocks || [];
  const len = stocks.length;
  data.stocks = stocks.filter((s) => s.code !== code);
  if (data.stocks.length === len) return { removed: false, reason: '股票不存在' };
  data.updated_at = _now();
  await _writeAll(data);
  return { removed: true };
}

module.exports = { getAll, getByCode, add, update, remove };
