'use strict';

const path = require('path');
const express = require('express');
const adminRouter = require('./routes/admin');
const dashboardRouter = require('./routes/dashboard');
const traderRouter = require('./routes/trader');
const mobileRouter = require('./routes/mobile');
const articlesRouter = require('./routes/articles');
const templatesRouter = require('./routes/templates');
const recoverRouter = require('./routes/recover');
const healthRouter = require('./routes/health');
const systemRouter = require("./routes/system");
const schedulerRouter = require('./routes/scheduler');
const briefRouter = require('./routes/brief');     // V9.1: 每日简报 API
const discoverRouter = require('./routes/discover'); // V9.1: 发现 feed API

const app = express();
const PORT = process.env.PORT || 3100;

// 视图引擎
app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, 'views'));

// 解析表单提交（application/x-www-form-urlencoded）
app.use(express.urlencoded({ extended: true }));
app.use(express.json());

// 静态资源
app.use(express.static(path.join(__dirname, 'public')));
// V9.2: 移除废弃 /article 静态路由（旧 output/article 已弃用，预览改由 /admin/articles/view/:name 用 marked 渲染 data/articles）

// V9.1: 根路径锚定到 /m（移动端首页），一屏看清全貌
app.get('/', (req, res) => res.redirect('/m'));

// 路由
app.use('/', mobileRouter);
app.use('/admin', adminRouter);
app.use('/admin', dashboardRouter);
app.use('/admin', traderRouter);
app.use('/admin', articlesRouter);
app.use('/admin', templatesRouter);
app.use('/admin/recover', recoverRouter);
app.use('/admin', healthRouter);
app.use('/admin', schedulerRouter);
 app.use("/admin", systemRouter);
app.use('/', briefRouter);                        // V9.1: /api/brief 端点
app.use('/', discoverRouter);                     // V9.1: /api/discover 端点

// 404 处理
app.use((req, res) => {
  res.status(404).render('admin/error', {
    title: '404 未找到',
    status: 404,
    active: 'admin',
    message: `页面不存在：${req.originalUrl}`,
  });
});

// 错误处理中间件
// eslint-disable-next-line no-unused-vars
app.use((err, req, res, next) => {
  console.error('[error]', err.stack || err);
  res.status(500).render('admin/error', {
    title: '500 服务器错误',
    status: 500,
    active: 'admin',
    message: process.env.NODE_ENV === 'production' ? '服务器内部错误' : String(err.message || err),
  });
});

const server = app.listen(PORT, () => {
  console.log(`CycleRadar Dashboard 运行于 http://localhost:${PORT}/admin/dashboard`);
});

module.exports = { app, server };
