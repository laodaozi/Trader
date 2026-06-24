'use strict';

const express = require('express');
const router = express.Router();
const fs = require('fs');
const path = require('path');

const TEMPLATES_DIR = path.join(__dirname, '..', '..', 'docs', 'prompt-templates');

const TEMPLATE_FILES = {
  '政策分析': '01-政策分析.md',
  '情绪周期': '02-情绪周期.md',
  '趋势跟踪': '03-趋势跟踪.md',
  '反转信号': '04-反转信号.md',
  '波动率套利': '05-波动率套利.md',
  '催化事件': '06-催化事件.md',
  '兼并重组': '07-兼并重组.md',
};

const ROLE_ORDER = ['政策分析', '情绪周期', '趋势跟踪', '反转信号', '波动率套利', '催化事件', '兼并重组'];

function readTemplate(role) {
  const filename = TEMPLATE_FILES[role];
  if (!filename) return '';
  const filePath = path.join(TEMPLATES_DIR, filename);
  try {
    return fs.readFileSync(filePath, 'utf-8');
  } catch (e) {
    return '';
  }
}

function saveTemplate(role, content) {
  const filename = TEMPLATE_FILES[role];
  if (!filename) return false;
  const filePath = path.join(TEMPLATES_DIR, filename);
  try {
    fs.writeFileSync(filePath, content, 'utf-8');
    return true;
  } catch (e) {
    return false;
  }
}

// GET /templates — 模板列表
router.get('/templates', (req, res) => {
  const templates = ROLE_ORDER.map(role => {
    const tmpl = readTemplate(role);
    const lines = tmpl.split('\n');
    const preview = lines[0] ? lines[0].replace(/^#\s*/, '') : '';
    return { role, preview, hasContent: tmpl.length > 0 };
  });

  res.render('admin/templates', {
    title: '写作模板管理',
    active: 'templates',
    templates,
    success: req.query.saved === '1' ? '模板已保存' : null,
  });
});

// GET /templates/:role — 编辑单个模板
router.get('/templates/:role', (req, res, next) => {
  const role = decodeURIComponent(req.params.role);
  if (!TEMPLATE_FILES[role]) return next();

  res.render('admin/templates-edit', {
    title: `编辑模板：${role}`,
    active: 'templates',
    role,
    content: readTemplate(role),
    error: null,
  });
});

// POST /templates/:role — 保存模板
router.post('/templates/:role', (req, res, next) => {
  const role = decodeURIComponent(req.params.role);
  if (!TEMPLATE_FILES[role]) return next();

  const { content } = req.body;
  if (!content || !content.trim()) {
    return res.render('admin/templates-edit', {
      title: `编辑模板：${role}`,
      active: 'templates',
      role,
      content: content || '',
      error: '模板内容不能为空',
    });
  }

  const ok = saveTemplate(role, content);
  if (ok) {
    return res.redirect('/admin/templates?saved=1');
  }
  res.render('admin/templates-edit', {
    title: `编辑模板：${role}`,
    active: 'templates',
    role,
    content,
    error: '保存失败，请检查文件权限',
  });
});

module.exports = router;
