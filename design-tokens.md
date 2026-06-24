# Design Tokens · CycleRadar Admin

## Design Read
> 🎨 解读为「量化交易投研 Admin 面板」面向「个人交易员」，参考 Bloomberg Terminal × Linear 融合风格 — #111827 顶栏 + #F1F5F9 内容区，数据密集但视觉克制，旋钮 B1/M1/D7

## 三旋钮
- LAYOUT_BOLDNESS: 1          （极简克制，交易工具不应花哨）
- TRANSITION_RICHNESS: 1       （最小动效，hover 0.15s 过渡即可）
- CONTENT_DENSITY: 7           （高密度，卡片 + 表格 + 热力图）

## 色板
- primary: #3B82F6             （分析蓝，信任感，按钮 / 链接 / 激活态）
- accent: #F59E0B              （交易金，高亮 / CTA / 关键数字）
- bg: #F1F5F9                  （浅灰蓝底，比纯白温和）
- surface: #FFFFFF             （卡片白，最高对比度）
- surface-alt: #F8FAFC         （交替行背景 / 次级面板）
- surface-dark: #1E293B        （暗色面板，health 页面用）
- text: #0F172A                （深蓝黑正文，高可读性）
- text-secondary: #64748B      （灰标签 / 辅助文字）
- text-inverse: #E2E8F0        （暗色背景上的浅文字）
- border: #E2E8F0              （分隔线 / 卡片边框）
- border-light: #F1F5F9        （表格内细线）
- positive: #16A34A            （多头 / 盈利绿）
- negative: #DC2626            （空头 / 亏损红）
- warn: #D97706                （警告橙）
- info: #6366F1                （信息靛）

## 字体策略
- heading: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif
- body: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif
- mono: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace

## 间距系统
- unit: 4px
- page-padding: 20px
- card-padding: 20px 24px
- card-gap: 16px
- section-gap: 24px

## 动效参数
- transition-type: fade
- duration-ms: 150

## 反 Slop 禁令
- ❌ 装饰性渐变（卡片/按钮/背景均禁止）
- ❌ emoji 用于数据呈现（仅导航 icon 保留 📱）
- ❌ 超过 4 种字号在同一页面
- ❌ 纯黑 #000 文字（用 #0F172A 替代）
- ❌ box-shadow 超过 0 4px 12px
- ❌ 圆角超过 12px（卡片 8px，按钮 6px）
- ❌ 无意义动画（禁用 pulse/spin/bounce）

---

## /m Mobile 设计规范

> 科技深色系。参考：Bloomberg Terminal × Vercel Dashboard。
> **铁律：dashboard.ejs 零内联 hex，全部用 `var(--m-*)` CSS 变量。**

### 色板（:root 变量名）

```css
--m-bg:        #0a0e1a;   /* 页面底色，午夜蓝 */
--m-surface:   #111827;   /* 卡片背景 */
--m-surface-2: #1e293b;   /* 次级面板 / tab bar */
--m-border:    #1e293b;   /* 分隔线 / 卡片边框 */
--m-primary:   #3b82f6;   /* 主强调，分析蓝（激活态 / 链接） */
--m-positive:  #10b981;   /* 多头 / 盈利，Emerald */
--m-negative:  #ef4444;   /* 空头 / 亏损 */
--m-warn:      #f59e0b;   /* 警告 */
--m-text:      #e2e8f0;   /* 主文字 */
--m-text-2:    #94a3b8;   /* 辅助文字 */
--m-text-3:    #64748b;   /* 占位 / disabled */
--m-etf:       #a78bfa;   /* ETF / 板块信号，紫 */
```

### 字体

- 界面文字：`-apple-system, "PingFang SC", "Microsoft YaHei", sans-serif`
- **数字 / 代码：`"JetBrains Mono", "SF Mono", Menlo, monospace`**（科技感核心，所有价格/代码/置信度用等宽字体）

### 间距

- 页面 padding：`14px`
- 卡片 padding：`16px`
- 卡片间距：`10px`
- 圆角：卡片 `10px`，按钮 `6px`，badge `4px`

### 反 Slop 禁令

- ❌ 装饰性渐变
- ❌ 超过 3 种字号层级
- ❌ 内联 hex（全部用 `var(--m-*)`）
- ❌ 圆角超过 12px
- ❌ 无意义动画
