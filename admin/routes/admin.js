'use strict';

const express = require('express');
const router = express.Router();
const Account = require('../models/account');

// GET / — 订阅号列表页
router.get('/', (req, res) => {
  const { category, status } = req.query;
  const filter = {};
  if (category) filter.category = category;
  if (status) filter.status = status;

  const accounts = Account.getAll(filter);
  res.render('admin/list', {
    title: 'WeWe RSS 订阅号管理',
    active: 'admin',
    accounts,
    categories: Account.CATEGORIES,
    statuses: Account.STATUSES,
    currentCategory: category || '',
    currentStatus: status || '',
    backend: Account.getBackend(),
  });
});

// GET /architecture — 技术架构图
router.get('/architecture', (req, res) => {
  res.render('admin/architecture', {
    title: '技术架构',
    active: 'admin',
  });
});

// GET /accounts/new — 新增页（放在 :id 之前，避免被参数路由捕获）
router.get('/accounts/new', (req, res) => {
  res.render('admin/edit', {
    title: '新增订阅号',
    active: 'admin',
    account: null,
    categories: Account.CATEGORIES,
    isNew: true,
  });
});

// GET /accounts/:id — 单个订阅号详情（编辑页）
router.get('/accounts/:id', (req, res, next) => {
  const account = Account.getById(req.params.id);
  if (!account) return next();
  res.render('admin/edit', {
    title: `编辑：${account.name}`,
    active: 'admin',
    account,
    categories: Account.CATEGORIES,
    isNew: false,
  });
});

// POST /accounts — 新增订阅号
router.post('/accounts', (req, res) => {
  const { name, mp_id, category, tags } = req.body;
  Account.create({ name, mp_id, category, tags });
  res.redirect('/admin');
});

// POST /accounts/:id — 更新订阅号
router.post('/accounts/:id', (req, res, next) => {
  const { name, mp_id, category, tags } = req.body;
  const updated = Account.update(req.params.id, { name, mp_id, category, tags });
  if (!updated) return next();
  res.redirect('/admin');
});

// POST /accounts/:id/delete — 软删除
router.post('/accounts/:id/delete', (req, res, next) => {
  const deleted = Account.softDelete(req.params.id);
  if (!deleted) return next();
  res.redirect('/admin');
});

// POST /accounts/:id/toggle — 暂停/恢复切换
router.post('/accounts/:id/toggle', (req, res, next) => {
  const toggled = Account.toggleStatus(req.params.id);
  if (!toggled) return next();
  const back = req.query.category
    ? `/admin?category=${encodeURIComponent(req.query.category)}`
    : '/admin';
  res.redirect(back);
});

module.exports = router;
