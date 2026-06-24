'use strict';

const fs = require('fs/promises');
const path = require('path');

const REPORTS_DIR = path.join(__dirname, '..', '..', 'data', 'backtest_reports');

/**
 * 读取 Python min_backtest.py 输出的 HTML 报告
 * 目录: cycleradar-trader/data/backtest_reports/ (symlink → ~/交易员/strategy/)
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

module.exports = { listReports, readReport };
