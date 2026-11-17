# 技术栈适配协议

> **为什么需要这份文件**：`SKILL.md` 第 5–8 节（页面原型、字段配方、状态机、数据规范、自检清单）
> 是**技术栈无关**的，任何栈都能直接套。但"把它落到目标项目里"这一步，规范曾经只有三行映射表——
> 结果是产出能不能融入项目，全靠模型临场发挥，换个人、换个项目，一致性没有保证。
>
> 本文件把这一步拆成**可执行的四步**：先探测、再分层取舍、然后按契约产出、最后按栈验证。

---

## 1. 第一步：探测目标项目约定（生成前强制，不得跳过）

**不探测就生成 = 盲写。** 必须实际读取文件，不要靠问用户（用户描述的约定常常与代码不一致）。

### 1.1 必读清单（按顺序）

| # | 读什么 | 提取什么 |
|---|---|---|
| 1 | `package.json` | 框架与版本、语言（有无 `typescript`）、状态管理、路由、样式依赖、构建脚本 |
| 2 | 项目 `README.md` / `CONTRIBUTING.md` | 团队**明文约定**（命名、目录、样式方案、评审要求） |
| 3 | `tsconfig.json` / `jsconfig.json` / 构建配置 | 路径别名、严格度（`strict` / `noUnusedLocals`） |
| 4 | `src/` 目录树（`find src -type f`） | 目录约定：页面放 `pages/` 还是 `views/`？store 放哪？utils 有没有？ |
| 5 | **2–3 个现有源文件**（一个页面、一个 store、一个组件） | 命名风格、组件写法、状态管理写法、样式方案 —— **这一步信息量最大** |
| 6 | 现有样式 / 令牌文件 | **项目是否已有自己的设计令牌？令牌变量名与值是什么？** |

### 1.2 输出：一张「项目约定画像」

探测完先落成表，再动手。以实测的 Vue 项目为例：

| 维度 | 探测结果 |
|---|---|
| 框架 / 版本 | Vue 3.5（Composition API + `<script setup>`） |
| 语言 | TypeScript 5.6，`strict` + `noUnusedLocals` 均开启 |
| 状态管理 | Pinia 2.3，**setup 写法**，store id 与文件名一致 |
| 路由 | Vue Router 4，页面懒加载，面包屑走 `meta.crumbs` |
| 样式方案 | **CSS Modules**（`<style module>` + `$style`），无 UI 库 |
| 目录 | `views/<页面>/index.vue`、`stores/`、`components/`、`types/` |
| 命名 | 组件文件 **kebab-case**；组件标签 PascalCase；接口不加 `I` 前缀 |
| 路径别名 | `@/` → `src/`（vite.config 与 tsconfig 两处都配了） |
| **已有令牌** | **有**。`src/styles/tokens.css`，主色 `#2f6fed`（≠ Skill 的 `#2B5CFF`） |
| 验证命令 | `npm run build` = `vue-tsc --noEmit && vite build` |

**探测不到时**（空项目 / 全新工程）：按 Skill 默认值走（单文件 HTML 或 kebab-case + CSS 变量），
并在回复里明确写出"我按 X 假设的，与你们约定不符告诉我改"。

---

## 2. 第二步：四层取舍（这是适配的核心）

冲突怎么裁决？**按层裁决，不要整体倒向某一方。**

| 层 | 内容 | 裁决 |
|---|---|---|
| **① 业务规范层** | 页面原型 A1–A6、字段配方、状态机、Mock 数据规范、格式与精度、可交互要求 | **Skill 无条件优先**。这是本 Skill 的价值，不因技术栈改变 |
| **② 工程实现层** | 语言（JS/TS）、状态管理、路由、样式方案、目录结构、命名风格、路径别名、构建工具 | **目标项目无条件优先**。Skill 不得强加自己的偏好 |
| **③ 视觉令牌层** | 色值、字号、间距、圆角 | **项目已有令牌 → 整表服从项目**（见 2.1）；项目没有 → 落 Skill 默认令牌 |
| **④ 防坑规则层** | 数字列 `nowrap`、弹性列 `minmax(0,1fr)`、`tabular-nums`、数字右对齐、表格容器自滚 | **无条件保留**。这些是踩出来的经验，与技术栈无关 |

### 2.1 令牌层：整表替换，禁止混用

项目已有令牌时，**建立映射表并整表替换**，产物里不允许残留任何 Skill 原色值。

实测的映射表（Vue 项目 → Skill 默认）：

| Skill 默认 | 语义 | 目标项目令牌 | 值 |
|---|---|---|---|
| `#2B5CFF` | 主色 | `var(--brand-600)` | `#2f6fed` |
| `#EEF3FF` | 主色浅底 | `var(--brand-50)` | `#eef4ff` |
| `#12A150` | 成功 | `var(--ok-600)` | `#16a34a` |
| `#E08B00` | 警告 | `var(--warn-600)` | `#d97706` |
| `#E5484D` | 危险 | `var(--danger-600)` | `#dc2626` |
| `#7C5CFF` | 信息紫 | `var(--info-600)` | `#7c3aed` |
| `#F4F6FA` | 页面底 | `var(--bg-app)` | `#f5f7fb` |
| `#FFFFFF` | 卡片 | `var(--bg-card)` | `#ffffff` |
| `#E6E9F0` | 分割线 | `var(--line-1)` | `#e8ebf2` |
| `#1C2230` | 正文 | `var(--text-1)` | `#1a2033` |
| `#5A6478` | 次级文字 | `var(--text-2)` | `#5b6579` |
| `#8B93A5` | 弱化文字 | `var(--text-3)` | `#8c95a6` |
| 圆角 10/6/999 | — | `var(--radius-card/input/pill)` | 同值 |

**为什么不混用**：新页面要与项目已有页面视觉连续。硬套 Skill 主色会让这个页面"一眼看出是外面搬来的"，
在真实项目里会被评审打回。**令牌是项目的品牌资产，不是 Skill 的。**

### 2.2 候选色缺失时：按同色系推导 + 注释来源

项目令牌不可能覆盖所有场景（例如抽屉遮罩）。此时**允许推导新值，但必须：**

1. 与项目令牌**同色系**（测例中遮罩取与 `--nav-bg`、`--shadow-card` 同源的 `rgba(16,24,40,.45)`）
2. 在代码注释里**写明推导依据**
3. 不引入与项目气质无关的新色相

随手取 `#333`、`rgba(0,0,0,.5)` 这类"通用值"同样违反令牌纪律。

### 2.3 字号：项目未定义则沿用 Skill 数值

实测项目定义了色值令牌但**没有定义字号令牌**。此时字号按 Skill 的视觉规范直接写数值
（正文 14 / 表格 13 / 表头 12.5 / 标题 20 / 副标题 12），不算违规——"禁止自写色值"约束的是色值，不是字号。

---

## 3. 第三步：按栈产出契约

### 3.1 各栈文件形态

| 目标栈 | 文件形态 | 状态管理 | 样式 | 别名 |
|---|---|---|---|---|
| **单文件 HTML**（默认，无项目时） | 1 个 `.html`，内联 CSS/JS | 原生 JS | 内联 CSS + `:root` 令牌 | 无 |
| **React** | `xxx-list/` 目录：`index.jsx` + `filter-bar` + `pro-table` + `status-tag` | **跟项目**（RTK slice / zustand / 组件内 state） | **跟项目**（CSS Modules / styled / Tailwind） | 跟项目 |
| **Vue 3** | `views/xxx/index.vue` + kebab-case 子组件 | **跟项目**（Pinia setup / Vuex） | **跟项目**（`<style module>` / SCSS） | 跟项目 |
| **小程序 / 移动端** | 卡片列表替代表格，筛选收进顶部抽屉，操作收进底部弹层 | 跟项目 | 跟项目 | 跟项目 |

### 3.2 组件拆分与命名：跟项目走

Skill 旧版建议 React 用 `ListPage.jsx + FilterBar.jsx`（PascalCase）。**实测项目用 kebab-case**，
所以产物是：

```
src/views/sales-order/
  index.vue            页面主入口（骨架串联）
  filter-bar.vue       查询区
  pro-table.vue        数据表格（含勾选列、排序、状态胶囊、行内操作）
  detail-drawer.vue    详情抽屉
  types.ts             页面私有类型与列定义
src/components/
  status-tag.vue       跨页面复用 → 放公共组件目录
src/stores/
  sales-order.ts       Pinia store（setup 写法）
```

**规则**：拆分的**粒度**由 Skill 定（查询区/表格/抽屉/标签必须分开，这关系到可维护性）；
拆分后的**命名与放置位置**由项目定。

### 3.3 状态管理：跟项目，但状态机映射不变

Skill 只规定了"当前状态 → 可执行动作"这张表，**不规定状态存在哪**。
- React + Redux Toolkit → 落成 `createSlice`，动作表落成 `Record<Status, string[]>`
- Vue + Pinia → 落成 `defineStore` setup 写法
- 无状态库 → 组件内 `useState` / `ref`

**状态机语义（业务规范层）不变**：按钮可见性严格跟着状态走，已生效单据不出现"编辑/删除"。

### 3.4 各栈特有的落地要点

**CSS Modules（Vue `<style module>` / React `*.module.css`）**
- `:root` 里的 CSS 变量**仍然是全局的** —— CSS Modules 只隔离**类名**，`var(--x)` 是运行时查找，照常可用。
  前提：令牌文件已在入口（`main.ts` / `index.js`）全局引入。
- Vue `<script setup>` 里**用不了 `$style`**（`$style` 只在模板可用）。需要动态类名时用 `useCssModule()`：
  ```ts
  import { useCssModule } from 'vue'
  const s = useCssModule()
  const toneClass = computed(() => ({ ok: s.tagOk, warn: s.tagWarn /* ... */ }[props.tone]))
  ```
  实测：`<style module>` + `$style` 能通过 `vue-tsc` strict 模式类型检查。
- React CSS Modules 同理：`import s from './x.module.css'`。

**TypeScript（strict）**
- 项目开了 `noUnusedLocals` / `noUnusedParameters` 时，**任何未使用的 import 或变量都会让构建失败**。产出前自查一遍。
- 页面私有类型放页面同级（`views/xxx/types.ts`），跨模块共享类型放 `src/types/`。

**表格放不下时**
- 11 列 × 1266px 在 1280px 视口（内容区仅 1024px）必然放不下。做法：
  `overflow-x: auto`（容器自滚）+ `table-layout: fixed` + `min-width`（列宽合计）。
- **D5 的判定放宽为**：页面根元素不出现横向滚动；**数据表格允许在自身容器内横向滚动**。
  这是 ERP 的通行做法，硬塞成"整页不滚"反而会把列挤到不可读。

---

## 4. 第四步：按栈验证（跨栈产物的强制收尾）

**"代码写完了"不等于"能跑"。** 每个栈都有对应的硬验证线，不过不许交付：

| 目标栈 | 验证命令 | 通过标准 |
|---|---|---|
| 单文件 HTML | `python build/render_check.py` | 无头渲染：零脏值、零横向溢出、非白屏 |
| React | 项目自身的 `npm run build` | 编译成功（含 JSX 解析、模块解析） |
| Vue 3 + TS | `npm run typecheck`（`vue-tsc --noEmit`）+ `npm run build` | **类型零错误** + 构建成功 |
| TypeScript 项目 | `tsc --noEmit` | 零类型错误 |
| 小程序 | 开发者工具编译 | 无报错 |

### 4.1 渲染级验证（跨栈通用）

编译通过只证明"代码合法"，不证明"页面正确"。必须再做一次真实渲染：

```bash
python build/verify_vue_render.py --project <项目路径> --route /sales --width 1280
```

它检查：脏值（`undefined` / `NaN` / `[object Object]`）、横向溢出、正文长度（防白屏）、
关键结构（侧边栏 / 表格行数 / 状态胶囊数）。

**实现要点**：把目标路由装进**同源 iframe** 里测量，而不是给浏览器传窗口尺寸——
后者在 Windows 显示缩放（125%）下拿到的 `innerWidth` 会变成 1024，测出假的溢出。

### 4.2 实测踩到的环境坑

| 坑 | 现象 | 解法 |
|---|---|---|
| Windows 显示缩放 | 传 `--window-size=1280` 实际只有 1024 CSS px | 加 `--force-device-scale-factor=1`；或改用 iframe 探测 |
| Vite dev server 截图卡死 | Chrome `--screenshot` 永不返回 | HMR 的 WebSocket 长连接让虚拟时间永不空闲 → 改用 `vite preview` 静态服务截图 |
| Chrome 参数路径 | `--user-data-dir=/c/Users/...` 被忽略，请求转发给已有会话 | 传 Windows 路径：`--user-data-dir=C:/Users/...` |
| Node 调用路径 | 给 `node.exe` 传 `/c/...` 被解析成 `c:\c\...` | 传给 node 的参数一律用 `C:/...` |

---

## 5. 实测记录（两栈对照）

同一套业务需求（A1 销售订单管理列表页）在两个完全不同的栈上各生成一次：

| | 第一次实测 | 第二次实测 |
|---|---|---|
| 框架 | React 19 | Vue 3.5 |
| 语言 | JavaScript | TypeScript 5.6（strict） |
| 状态管理 | Redux Toolkit | Pinia（setup 写法） |
| 构建 | CRA / webpack | Vite 6 |
| 样式 | 全局 CSS + CSS 变量 | CSS Modules |
| 目录 / 命名 | `pages/` + PascalCase | `views/` + kebab-case |
| 产物规模 | 16 个文件 / ~2800 行 | 9 个文件 / ~1500 行 |
| 验证线 | `react-scripts build` 成功 | `vue-tsc --noEmit` 零错误 + `vite build` 成功 |
| 渲染验证 | 1280px 零溢出、零脏值 | 1280px 零溢出、零脏值、10 行数据、10 个状态胶囊 |

**两次产物在业务规范层完全一致**（同一套字段配方、同一套 9 状态机、同样的按钮-状态对应、
同样的数字右对齐与 `nowrap`），**在工程实现层完全按各自项目约定**。这就是本协议要达成的效果。

### 5.1 第一次实测暴露的规范错误（已修正）

`SKILL.md` 旧版 React 分支写"**状态用 `useState`**（不引第三方表格库）"。
实测中，目标项目已用 Redux Toolkit 承载列表页数据 —— 照做会与项目完全脱节。

**修正为**：状态管理一律跟目标项目走；`useState` 仅作"项目无状态库"时的兜底。
同理，Vue 分支也不再规定"用 script setup"就完事，而是明确要求探测项目用的是 Pinia 还是 Vuex。

### 5.2 第二次实测新增的三条自检项

见 `quality-checklist.md` 的 F 组（F1–F3）：探测留存、令牌整表替换、目标栈编译级验证。
