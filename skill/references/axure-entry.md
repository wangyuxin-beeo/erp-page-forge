# Axure 原型入口（第四输入入口）

> 依赖外部技能包：[Axhub-Skills](https://github.com/lintendo/Axhub-Skills)（MIT 协议），
> 如需深度解析，请自行安装相应外部技能。本仓库**不捆绑**其代码，只声明依赖 ——
> 这样 `erp-page-forge` 自身仍是纯文本可复制包，第三方代码仍由上游维护、可独立升级。

## 1. 为什么值得单独做一个入口

截图入口（`build/vision.py`）和 Axure 入口解决的是同一件事——"用户给个东西，要一个准的页面"——
但**精度差一个档次**。Axure 原型比截图多三样东西：

| Axure 里的东西 | 截图里没有 | 我们拿它干什么 |
|---|---|---|
| **组件标注**（产品经理写的文字） | 只能靠像素猜 | 字段名、类型、枚举取值、按钮可见规则 —— 全是文字定义，不用猜 |
| **站点地图** | 只能看到当前页 | 侧栏菜单的分组与菜单项（原型的信息架构就是导航结构） |
| **交互映射** | 交互规则看不出来 | 动作与状态流转 |

一句话：**截图让你看到"长什么样"，Axure 让你知道"是什么、能做什么"。**

## 2. 怎么用

```bash
# ① 只看提取（不生成），先确认画布对不对
python build/axure.py --src <Axure导出目录> --page <页面名> --extract-only

# ② 提取 → PageSpec（唯一生成输入）
python build/axure.py --src <导出目录> --page inout-list --out spec.json

# ③ 一步到底：提取 → PageSpec → 确定性生成
python build/axure.py --src <导出目录> --page inout-list --out spec.json --forge out.html

# ④ 验收（与其它入口同一套闸门，不改一个字）
python build/verify_acceptance.py --spec spec.json --artifact out.html --url out.html
python build/build_report.py --report build/axure/acc-inout.json --out report.html
```

`--src` 既接受**本地导出目录**（推荐，离线可用），也接受**原型 URL**（AxShare / Axure Cloud）。

本地目录会被自动挂到一个临时 HTTP 服务上——因为 Node 的 `fetch` 不支持 `file://`，
而 Axure 的数据存在静态 JS 文件里，需要通过相对路径取。

## 3. 标注 → PageSpec 的映射口径

Axure 的标注（`notes.json`）里每条带 `ownerId` 的记录，按 `type` 分流：

| 标注 type | 映射到 | 备注 |
|---|---|---|
| `code` / `text` / `enum` / `date` / `number` / `money` / `person` / `status` | `PageSpec.fields` + `columns` | **顺序 = Axure 里的排列顺序**（产品经理的排列就是业务上该有的顺序） |
| `action` | `PageSpec.actions` | 读 `scope` / `allowedStates` / `primary`，按钮可见性直接落地 |
| 字段带 `options` | 枚举取值 | 同时喂给该字段与状态机 |
| 字段带 `control` | `query.conditions` 的控件类型 | `select` / `date-range` / `input` |
| 字段带 `codePattern`（如 `RK-2026-100371`） | 编码前缀 / 位宽 / 起始号 | 生成物单号直接是 `RK-2026-100371` 这种格式 |
| 页面级 `notes`（`description`/`module`/`breadcrumb`） | `page` 与面包屑 | 面包屑字符串按 `/` 拆分 |
| `sitemap.rootNodes` | `spec.nav` | Folder = 分组，Page = 菜单项 |

**两条刻意的设计**：

1. **不覆盖标注**。标注说 `type: money` 就按金额渲染，标注说 `allowedStates: [待审核]`
   就只有待审核能点。这是产品经理的定义，不该被 Skill 的默认值改写。
2. **key 用码点编码**。中文列名转英文 key 时不做拼音（零依赖约束下不引拼音库），
   产出形如 `f_5355_636e_53f7` 的稳定 key；可读性交给 `fieldName` 展示。

## 4. 可用性矩阵（哪些能力需要 Playwright）

**本项目的策略是刻意不装 Playwright**，因为它要下载 150–300 MB 的 Chromium。

| 能力 | 无 Playwright | 说明 |
|---|---|---|
| 站点地图 / 字段标注 / 交互映射 | ✅ 可用 | 纯 Node.js 在 `vm` 沙箱里解析静态 JS，不碰浏览器 |
| 页面截图 | ⚠️ 跳过 | 改由本机 Chrome 通道补（`build/verify_visual.py` 已在用同一套） |
| 设计令牌（`theme.json`） | ⚠️ 跳过 | 同上；Axure 原型多为线框，视觉令牌本来也主要来自 Skill 规范 |
| `extract-page-data` 的 markdown / links / theme | ⚠️ 跳过 | 这四个模块静态 import playwright |
| `clone-page`（高精度克隆） | ❌ 不可用 | 整个流程建立在 Playwright 采集之上，需装 Playwright 才能跑 |

### 怎么做到"不下载也不报错"

`extract-axure-data` 的 `ensureDependencies()` 是**无条件调用**的，它只检查
`~/.cache/axure-extractor/node_modules/playwright` **目录是否存在**，不存在就去 `npm install`。

我们的做法（`build/axure.py` 的 `ensure_no_playwright()`）：
放一个**只有 `package.json`、没有 `index.mjs`** 的占位目录。于是

- `ensureDependencies()` 看到目录在 → 直接返回，**不下载**
- `loadPlaywright()` 找不到 `index.mjs` → 返回 `null` → 脚本自己打印
  `⏭️ 跳过截图和主题（Playwright 不可用）` → **走它自己的降级分支**

这是**利用上游已有的降级设计**，不改它一行代码，上游升级也不冲突。
占位包的 `description` 里写明了为什么存在，不是黑魔法。

想启用完整能力（含 `clone-page`）时，删掉占位目录再跑一次即可，脚本会自己装。

## 5. 安全审计结论（安装前已做）

| 级别 | 项目 | 结论 |
|---|---|---|
| **P0** | —— | **未发现** |
| **P1** | 自动安装 Playwright + Chromium | 已用降级占位规避，**不下载** |
| **P1** | 批注类技能会注册 Chrome Native Messaging Host | **未安装**（与"交付物零外链零依赖"约定冲突） |
| **P2** | `new Function` | 只用于执行**仓库自带**的注入脚本（`readFileSync` 本地路径），非远程代码 |
| **P2** | 916 KB 注入脚本 `axhub-annotation.global.js` | `fetch` / `XMLHttpRequest` / `eval` / `atob` / `document.cookie` **全为 0 次**，域名仅 `axhub.im` 与 React 打包产物的标准引用 → **无网络能力** |
| **P2** | Axure 数据解析 | 在 Node `vm` 沙箱内执行，5 秒超时，只暴露 `$axure` 桩函数 → 隔离正确 |
| **P2** | 凭据访问 | 无 `.ssh` / `.aws` / `.env` 读取；只用标准 `~/.cache` 目录 |
| **P2** | 协议 | 仓库根为 **MIT**，可自由使用/修改/分发（保留版权声明） |

**未安装的 9 个技能及原因**（需要时可单独加，命令在 README）：

| 技能 | 为什么不装 |
|---|---|
| `axhub-commentary` / `axhub-annotation-standalone` | 注册浏览器 Native Host + 往页面注入 916KB 运行时，与零依赖交付冲突 |
| `figma-content-operator` / `react-to-figma-make` | 方向相反（代码 → 设计） |
| `china-customer-research` | 客户调研，与页面生成无关 |
| `git-repo-beginner-guide` | Git 入门，无关 |
| `mcp-installer` | 通用 MCP 安装器，本项目不需要 |
| `search-design-system` / `design-system-search` | 依赖 Axhub 远端设计系统服务 |

```bash
# 需要时补装某个技能（示例）
cp -r <Axhub-Skills>/skills/<技能名> .workbuddy/skills/
```

## 6. 实测

样本：`build/axure/sample-prototype/`（模拟 Axure 导出结构，13 条标注）

| 验证 | 结果 |
|---|---|
| 提取链路 | 无 Playwright 下正常产出 `sitemap.json` / `notes.json`(13 条) / `interactions.json` |
| 标注 → PageSpec | 9 字段（类型全对）、4 动作（`allowedStates` 精确）、5 态状态机、编码 `RK`+6 位+起始 100371、3 组枚举、4 个查询条件 |
| 站点地图 → 侧栏 | `[{group: 仓储中心, items: [出入库单据管理]}]`，与原型一致，当前页高亮 |
| 四类断言 | **38 / 38 通过**（契约 · 渲染 · 交互 · 数据） |
| 生成物 | `build/axure/out-inout.html` 47.9 KB 零依赖单文件 |

**关键点**：这个页面**一个字的配色都没让模型发挥**，字段顺序、类型、按钮可见性全部来自标注文字。
这就是 Axure 入口相对截图入口的价值。

## 7. 边界（当前不做）

- **只读标注，不读视觉稿**。Axure 原型是线框图，不定义最终前端视觉；配色仍走
  `spec/tokens.json`（或参考图覆盖）。若原型是高保真且带 `theme.json`，会存进
  `spec._axureTheme` 供人工参考，尚未自动注入。
- **批量动作不从标注推**。Axure 标注里没有 scope=bulk 的动作时，工具条仍会渲染骨架的
  默认批量按钮 —— 属于"我们加的"，不是原型定义的。
- **动态面板 / 条件显示不展开**。这类状态在静态 JS 里不完整，需要 Playwright 回访原型页补采
  （见上游 SKILL.md 的"查缺补漏"一节）。
- **原型判定是启发式**。`guess_archetype()` 按字段与动作猜 A1/A2，猜不准会退回 A1；
  可在页面级标注里写 `archetype: A5` 显式指定。
