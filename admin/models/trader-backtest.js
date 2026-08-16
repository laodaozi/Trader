'use strict';

const fs = require('fs/promises');
const path = require('path');

const REPORTS_DIR = path.join(__dirname, '..', '..', 'data', 'backtest_reports');
const WINRATE_FILE = path.join(__dirname, '..', '..', 'data', 'backtest_winrate.json');

/**
 * 读取 Python min_backtest.py 输出的 HTML 报告
 * 目录: cycleradar-trader/data/backtest_reports/
 *   （V7.6 融合前曾 symlink → ~/交易员/strategy/，交易员已冻结，
 *    融合后 min_backtest.py 直接输出到本项目 data/ 下，无需 symlink）
 *
 * 文件命名规则:
 *   latest.html             → 最近一次回测
 *   strategy_YYYY-MM-DD.html → 按日归档
 */

async function listReports() {
  try {
    const files = await fs.readdir(REPORTS_DIR);
    const reports = files
      .filter((f) => f.endsWith('.html'))
      .map((f) => {
        const dateMatch = f.match(/strategy_(\d{4}-\d{2}-\d{2})\.html/);
        return {
          filename: f,
          date: dateMatch ? dateMatch[1] : f === 'latest.html' ? '最新' : null,
          isLatest: f === 'latest.html',
          path: path.join(REPORTS_DIR, f),
        };
      })
      .sort((a, b) => {
        if (a.isLatest) return -1;
        if (b.isLatest) return 1;
        return (b.date || '').localeCompare(a.date || '');
      });
    return reports;
  } catch (error) {
    if (error && error.code === 'ENOENT') return [];
    throw error;
  }
}

async function readReport(filename) {
  const filePath = path.join(REPORTS_DIR, filename);
  // 安全检查：防止目录穿越
  if (path.dirname(path.resolve(filePath)) !== path.resolve(REPORTS_DIR)) {
    throw new Error('Invalid filename');
  }
  try {
    return await fs.readFile(filePath, 'utf8');
  } catch (error) {
    if (error && error.code === 'ENOENT') return null;
    throw error;
  }
}

/**
 * 读取 min_backtest.py 输出的模型胜率表 (data/backtest_winrate.json)
 * 供 /admin/trader/strategy 胜率排行使用 (admin-map V7.5 缺口)
 *
 * 返回: { generated_at, models: [{ key, name, win_rate, sample_size, avg_return }] }
 *       generated_at 兼容 Python 端写的 updated_at 字段
 *       按 win_rate 降序，仅含 sample_size >= 5 的模型
 */
async function getWinrateRanking() {
  try {
    const raw = await fs.readFile(WINRATE_FILE, 'utf8');
    const data = JSON.parse(raw);
    const models = Object.entries(data.models || {})
      .map(([key, m]) => ({
        key,
        name: m.name || key,
        win_rate: m.win_rate != null ? m.win_rate : null,
        sample_size: m.sample_size || 0,
        avg_return: m.avg_return != null ? m.avg_return : null,
      }))
      .filter((m) => m.sample_size >= 5)
      .sort((a, b) => (b.win_rate || 0) - (a.win_rate || 0));
    return { generated_at: data.updated_at || data.generated_at || null, models };
  } catch (error) {
    if (error && error.code === 'ENOENT') return { generated_at: null, models: [] };
    throw error;
  }
}

module.exports = { listReports, readReport, getWinrateRanking };
