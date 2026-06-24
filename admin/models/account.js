'use strict';

/**
 * 数据访问层（Account Model）
 * ----------------------------------------------------------------------------
 * 双后端设计：
 *   ACCOUNT_BACKEND=mock   → MockAccountStore（内存 JSON，开发测试用）
 *   ACCOUNT_BACKEND=sqlite → SqliteAccountStore（直连真实 WEWE RSS DB + 本地 metadata）
 *
 * SqliteAccountStore 混合读取：
 *   1. feeds/accounts 表来自 WEWE RSS 真实 SQLite（read-only 挂载）
 *   2. 分类/标签/软删除 等扩展字段来自本地 metadata SQLite
 *   3. article 计数 / sync 状态实时计算
 *
 * 统一对外暴露：getAll(filter), getById(id), create(data),
 *              update(id, data), softDelete(id), toggleStatus(id)
 */

const path = require('path');

// better-sqlite3 只在 sqlite 后端时才加载（mock 模式不需要）
let Database = null;
function getDatabase() {
  if (!Database) Database = require('better-sqlite3');
  return Database;
}

// ---------------------------------------------------------------------------
// 常量
// ---------------------------------------------------------------------------
const CATEGORIES = ['政策', '行业', '公司', '宏观'];
const STATUSES = ['active', 'paused', 'error'];

// ---------------------------------------------------------------------------
// 工具函数
// ---------------------------------------------------------------------------
function nowISO() {
  return new Date().toISOString();
}

function normalizeTags(tags) {
  if (Array.isArray(tags)) return tags.map((t) => String(t).trim()).filter(Boolean);
  if (typeof tags === 'string') {
    return tags
      .split(',')
      .map((t) => t.trim())
      .filter(Boolean);
  }
  return [];
}

/**
 * 将 feeds 表状态映射到 admin panel 状态
 *   status=1 + has_history=1  → active
 *   status=1 + has_history=0  → error（卡死）
 *   status=0                  → paused
 *   status=其他               → error
 */
function mapFeedStatus(status, hasHistory) {
  if (status === 1 && hasHistory === 1) return 'active';
  if (status === 0) return 'paused';
  return 'error';
}

/**
 * 反向映射：admin panel 状态 → feeds.status（用于写回 DB）
 */
function reverseFeedStatus(adminStatus) {
  if (adminStatus === 'active') return { status: 1, has_history: 1 };
  if (adminStatus === 'paused') return { status: 0, has_history: 1 };
  return { status: 1, has_history: 0 };
}

const DEFAULT_TOKEN_PER_ARTICLE = 250;
const DEFAULT_TOKEN_PER_FEED_OVERHEAD = 500;

// ---------------------------------------------------------------------------
// MockAccountStore：内存实现（开发测试用）
// ---------------------------------------------------------------------------

const MOCK_ACCOUNTS = [
  { id: 1, name: '财闻私享', mp_id: 'MP_WXS_3233243226', category: '宏观',   status: 'active', token_usage_7d: 18420, total_articles: 312, last_sync: '2026-06-07T05:35:00Z', tags: ['财经','私享'],    created_at: '2025-11-02T08:10:00Z', deleted: false },
  { id: 2, name: '台球之门',     mp_id: 'MP_WXS_3191151316', category: '行业',   status: 'paused', token_usage_7d: 4210,  total_articles: 88,  last_sync: '2026-06-05T17:35:00Z', tags: ['体育'],           created_at: '2025-12-15T11:00:00Z', deleted: false },
  { id: 3, name: '低吸波段王',   mp_id: 'MP_WXS_3901470107', category: '公司',   status: 'active', token_usage_7d: 22980, total_articles: 540, last_sync: '2026-06-07T05:35:00Z', tags: ['股票','波段'],   created_at: '2025-10-20T09:30:00Z', deleted: false },
  { id: 4, name: '微策神机',     mp_id: 'MP_WXS_3242358265', category: '公司',   status: 'error',  token_usage_7d: 760,   total_articles: 145, last_sync: '2026-06-04T22:35:00Z', tags: ['量化','策略'],   created_at: '2026-01-08T14:22:00Z', deleted: false },
  { id: 5, name: '小马白话期权', mp_id: 'MP_WXS_3521606446', category: '行业',   status: 'active', token_usage_7d: 15600, total_articles: 268, last_sync: '2026-06-07T05:35:00Z', tags: ['期权','衍生品'], created_at: '2025-09-12T16:45:00Z', deleted: false },
];

class MockAccountStore {
  constructor(seed) {
    this.accounts = JSON.parse(JSON.stringify(seed));
    this.nextId = this.accounts.reduce((m, a) => Math.max(m, a.id), 0) + 1;
  }

  getAll(filter = {}) {
    const { category, status, includeDeleted = false } = filter;
    return this.accounts.filter((a) => {
      if (!includeDeleted && a.deleted) return false;
      if (category && a.category !== category) return false;
      if (status && a.status !== status) return false;
      return true;
    });
  }

  getById(id) {
    const numId = Number(id);
    return this.accounts.find((a) => a.id === numId && !a.deleted) || null;
  }

  create(data) {
    const account = {
      id: this.nextId++,
      name: data.name || '',
      mp_id: data.mp_id || '',
      category: data.category || '行业',
      status: data.status || 'active',
      token_usage_7d: data.token_usage_7d || 0,
      total_articles: data.total_articles || 0,
      last_sync: data.last_sync || null,
      tags: normalizeTags(data.tags),
      created_at: nowISO(),
      deleted: false,
    };
    this.accounts.push(account);
    return account;
  }

  update(id, data) {
    const account = this.getById(id);
    if (!account) return null;
    if (data.name !== undefined) account.name = data.name;
    if (data.mp_id !== undefined) account.mp_id = data.mp_id;
    if (data.category !== undefined) account.category = data.category;
    if (data.status !== undefined) account.status = data.status;
    if (data.tags !== undefined) account.tags = normalizeTags(data.tags);
    return account;
  }

  softDelete(id) {
    const account = this.getById(id);
    if (!account) return null;
    account.deleted = true;
    return account;
  }

  toggleStatus(id) {
    const account = this.getById(id);
    if (!account) return null;
    account.status = account.status === 'active' ? 'paused' : 'active';
    return account;
  }

  close() { /* no-op */ }
}

// ---------------------------------------------------------------------------
// SqliteAccountStore：真实 SQLite 实现
// ---------------------------------------------------------------------------

class SqliteAccountStore {
  /**
   * @param {string} weweDbPath  - WEWE RSS SQLite 路径（本地副本或远程挂载）
   * @param {string} metaDbPath  - 本地 metadata SQLite 路径（扩展字段）
   */
  constructor(weweDbPath, metaDbPath) {
    this.weweDbPath = weweDbPath;
    this.metaDbPath = metaDbPath;

    // 打开 WEWE RSS DB（只读，保护数据）
    this.weweDb = new (getDatabase())(weweDbPath, { readonly: true });

    // 打开/创建本地 metadata DB（读写）
    this.metaDb = new (getDatabase())(metaDbPath);
    this.metaDb.pragma('journal_mode = WAL');
    this._initMetaSchema();

    // 预填充：为 feeds 表中已有但 metadata 中尚未注册的记录补一行默认
    this._seedMissingMetadata();
  }

  _initMetaSchema() {
    this.metaDb.exec(`
      CREATE TABLE IF NOT EXISTS feed_metadata (
        mp_id       TEXT PRIMARY KEY,
        category    TEXT DEFAULT '行业',
        tags        TEXT DEFAULT '[]',      -- JSON array
        deleted     INTEGER DEFAULT 0,
        created_at  TEXT DEFAULT (datetime('now')),
        updated_at  TEXT DEFAULT (datetime('now'))
      );
    `);
  }

  _seedMissingMetadata() {
    const existing = new Set(
      this.metaDb.prepare('SELECT mp_id FROM feed_metadata').all().map((r) => r.mp_id)
    );
    const feeds = this.weweDb.prepare('SELECT id FROM feeds').all();
    const insert = this.metaDb.prepare(
      'INSERT OR IGNORE INTO feed_metadata (mp_id) VALUES (?)'
    );
    const tx = this.metaDb.transaction(() => {
      for (const f of feeds) {
        if (!existing.has(f.id)) insert.run(f.id);
      }
    });
    tx();
  }

  // -----------------------------------------------------------------------
  // 读操作：从 WEWE RSS DB + metadata 合并
  // -----------------------------------------------------------------------

  /**
   * 从 feeds 表构建 account 列表，合并 metadata 扩展字段
   */
  _buildAccounts(filter = {}) {
    const { category, status, includeDeleted = false } = filter;

    // 查询 feeds 表（含 article 计数）
    const feeds = this.weweDb.prepare(`
      SELECT
        f.id          AS mp_id,
        f.mp_name     AS name,
        f.status      AS feed_status,
        f.has_history,
        f.sync_time,
        f.mp_cover,
        f.mp_intro,
        f.created_at  AS feed_created_at,
        f.updated_at  AS feed_updated_at,
        COALESCE(ac.cnt, 0) AS total_articles,
        COALESCE(ac7.cnt_7d, 0) AS articles_7d
      FROM feeds f
      LEFT JOIN (
        SELECT mp_id, COUNT(*) AS cnt FROM articles GROUP BY mp_id
      ) ac ON ac.mp_id = f.id
      LEFT JOIN (
        SELECT mp_id, COUNT(*) AS cnt_7d
        FROM articles
        WHERE publish_time >= (CAST(strftime('%s', 'now') AS INTEGER) - 7*24*3600)
        GROUP BY mp_id
      ) ac7 ON ac7.mp_id = f.id
      ORDER BY f.sync_time DESC
    `).all();

    // 查询所有 metadata（一次取出，避免 N+1）
    const metadata = {};
    const metaRows = this.metaDb.prepare('SELECT * FROM feed_metadata').all();
    for (const m of metaRows) {
      let tags = [];
      try { tags = JSON.parse(m.tags); } catch (_) { tags = []; }
      metadata[m.mp_id] = { category: m.category, tags, deleted: !!m.deleted, metaCreatedAt: m.created_at };
    }

    // 合并（rows 模式，直接返回数组）
    const accounts = [];
    for (const f of feeds) {
      const meta = metadata[f.mp_id] || { category: '行业', tags: [], deleted: false, metaCreatedAt: f.feed_created_at };

      // 过滤软删除
      if (!includeDeleted && meta.deleted) continue;

      const adminStatus = mapFeedStatus(f.feed_status, f.has_history);

      // 分类/状态筛选
      if (category && meta.category !== category) continue;
      if (status && adminStatus !== status) continue;

      const token_usage_7d = f.articles_7d * DEFAULT_TOKEN_PER_ARTICLE + DEFAULT_TOKEN_PER_FEED_OVERHEAD;

      accounts.push({
        id: f.mp_id, // 使用 MP_WXS_xxx 作为 ID
        name: f.name,
        mp_id: f.mp_id,
        category: meta.category,
        status: adminStatus,
        token_usage_7d,
        total_articles: f.total_articles,
        articles_7d: f.articles_7d,
        last_sync: f.sync_time
          ? new Date(f.sync_time * 1000).toISOString()
          : null,
        tags: meta.tags,
        created_at: meta.metaCreatedAt || f.feed_created_at,
        deleted: meta.deleted,
        mp_cover: f.mp_cover,
        mp_intro: f.mp_intro,
        has_history: f.has_history,
      });
    }
    return accounts;
  }

  getAll(filter = {}) {
    return this._buildAccounts(filter);
  }

  getById(id) {
    const results = this._buildAccounts({ includeDeleted: true });
    return results.find((a) => a.id === id || a.mp_id === id) || null;
  }

  // -----------------------------------------------------------------------
  // 写操作：更新 metadata + 同步 feeds 表状态
  // -----------------------------------------------------------------------

  create(data) {
    const mp_id = (data.mp_id || '').trim();
    if (!mp_id) throw new Error('mp_id 不能为空');

    // 写入 metadata
    this.metaDb.prepare(`
      INSERT INTO feed_metadata (mp_id, category, tags, created_at, updated_at)
      VALUES (?, ?, ?, datetime('now'), datetime('now'))
      ON CONFLICT(mp_id) DO UPDATE SET
        category = excluded.category,
        tags = excluded.tags,
        updated_at = excluded.updated_at
    `).run(mp_id, data.category || '行业', JSON.stringify(normalizeTags(data.tags || [])));

    // 尝试写入 feeds 表（如果 DB 非只读且有写入权限）
    try {
      this.weweDb.prepare(`
        INSERT INTO feeds (id, mp_name, mp_cover, mp_intro, status, sync_time, update_time, has_history)
        VALUES (?, ?, '', '', 1, 0, CAST(strftime('%s','now') AS INTEGER), 1)
      `).run(mp_id, data.name || mp_id);
    } catch (e) {
      console.warn('[account.js] feeds 写入跳过（DB 可能只读）:', e.message);
    }

    return this.getById(mp_id);
  }

  update(id, data) {
    const account = this.getById(id);
    if (!account) return null;

    // 更新 metadata
    const updates = [];
    const params = [];
    if (data.category !== undefined) { updates.push('category = ?'); params.push(data.category); }
    if (data.tags !== undefined) { updates.push('tags = ?'); params.push(JSON.stringify(normalizeTags(data.tags))); }
    if (data.name !== undefined) {
      // name 在 feeds 表中，尝试更新
      try {
        this.weweDb.prepare('UPDATE feeds SET mp_name = ? WHERE id = ?').run(data.name, account.mp_id);
      } catch (e) {
        console.warn('[account.js] feeds 名称更新跳过（DB 可能只读）:', e.message);
      }
    }
    if (updates.length > 0) {
      updates.push("updated_at = datetime('now')");
      params.push(account.mp_id);
      this.metaDb.prepare(`UPDATE feed_metadata SET ${updates.join(', ')} WHERE mp_id = ?`).run(...params);
    }

    // 更新 feeds 状态
    if (data.status !== undefined) {
      const { status: fs, has_history: hh } = reverseFeedStatus(data.status);
      try {
        this.weweDb.prepare('UPDATE feeds SET status = ?, has_history = ? WHERE id = ?')
          .run(fs, hh, account.mp_id);
      } catch (e) {
        console.warn('[account.js] feeds 状态更新跳过（DB 可能只读）:', e.message);
      }
    }

    return this.getById(id);
  }

  softDelete(id) {
    const account = this.getById(id);
    if (!account) return null;
    this.metaDb.prepare("UPDATE feed_metadata SET deleted = 1, updated_at = datetime('now') WHERE mp_id = ?")
      .run(account.mp_id);
    return { ...account, deleted: true };
  }

  toggleStatus(id) {
    const account = this.getById(id);
    if (!account) return null;
    const newStatus = account.status === 'active' ? 'paused' : 'active';
    return this.update(id, { status: newStatus });
  }

  close() {
    if (this.weweDb) this.weweDb.close();
    if (this.metaDb) this.metaDb.close();
  }
}

// ---------------------------------------------------------------------------
// 后端选择与工厂
// ---------------------------------------------------------------------------

const WEWE_DB_PATH = process.env.WEWE_DB_PATH || path.join(__dirname, '..', 'data', 'wewe-rss.db');
const META_DB_PATH = process.env.META_DB_PATH || path.join(__dirname, '..', 'data', 'metadata.db');
const BACKEND = process.env.ACCOUNT_BACKEND || 'mock';

let store;

if (BACKEND === 'sqlite') {
  try {
    store = new SqliteAccountStore(WEWE_DB_PATH, META_DB_PATH);
    console.log(`[account.js] SQLite 后端已就绪，数据源: ${WEWE_DB_PATH}`);
  } catch (e) {
    console.warn('[account.js] SQLite 后端初始化失败，回退到 Mock：', e.message);
    store = new MockAccountStore(MOCK_ACCOUNTS);
  }
} else {
  store = new MockAccountStore(MOCK_ACCOUNTS);
  console.log('[account.js] Mock 后端已就绪（ACCOUNT_BACKEND=mock）');
}

module.exports = {
  CATEGORIES,
  STATUSES,
  getById: (id) => store.getById(id),
  getAll: (filter) => store.getAll(filter),
  create: (data) => store.create(data),
  update: (id, data) => store.update(id, data),
  softDelete: (id) => store.softDelete(id),
  toggleStatus: (id) => store.toggleStatus(id),
  // 健康检查/调试用
  getStore: () => store,
  getBackend: () => BACKEND,
  MockAccountStore,
  SqliteAccountStore,
};
