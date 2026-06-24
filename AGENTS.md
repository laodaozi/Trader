# CycleRadar Trader · 执行规则

> 每次会话自动加载。分两类：**主动**（我自己核对）/ **被动**（触发条件到了自动执行，不问 Scott）。

---

## 冷启动 [主动]

读 `CONTEXT.md` 最后一节 `§9 AFTER_SESSION`——只读这节，3 秒恢复上下文。

---

## 动手前 [主动]

- **任何改动**：先列文件清单，等 Scott 确认，再动手
- **CSS / UI 改动**：对照 `admin/design-tokens.md` 对应区块（Admin 浅色 或 /m Mobile 深色）
- **API 字段**：先 `curl` 实测结构，不假设字段名（踩过：`asset_name`、`global_conclusion` 是字符串非对象）
- **新功能**：并行路由/文件，不替换现有（教训：加 `/m/v6` 不动 `/m`）

---

## 部署 [主动]

- 服务器：`root@139.196.115.64`，内部端口 `3100`，对外走 nginx `80`——不对 Scott 报 3100
- `rsync` 后 `ssh` 验证文件时间戳

---

## 完成后 [被动，自动执行，不问 Scott]

Scott 说「完成 / 好 / 继续 / 部署好了 / 没问题」时触发：

1. `git commit` 带 `[doc-synced]`，代码 + 文档同一个 commit
2. 追加 SESSION_LOG → `~/.claude/SESSION_LOG.md`
3. `curl` 验收三个核心 API 返回非空：`/m/api/summary`、`/m/api/cycleradar`、`/m/api/watchlist`

---

## /m Mobile 设计规范入口 [主动]

所有 `/m` 移动端 CSS 改动必须对照 `admin/design-tokens.md` 的 `## /m Mobile` 区块。
dashboard.ejs 零内联 hex，全部用 `var(--m-*)` 变量。
