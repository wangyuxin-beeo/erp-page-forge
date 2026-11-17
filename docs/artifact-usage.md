# 产物使用说明 · 把 Forge Studio 的产物用进真实项目

> 结论：`page.html` 是效果确认稿，**不能直接粘进 React/Vue 项目**。真正要带走的是 `pagespec.json`，
> 在 IDE 里让 AI 助手加载 erp-page-forge 技能，按这份 spec + 你项目的约定生成代码，再编译、跑验收。

## 1. 一次生成有哪些产物

在 Studio 结果栏「下载 ZIP」或「产物与日志」里获取，三个交付文件：

| 文件 | 是什么 | 在真实项目里怎么用 |
|---|---|---|
| `page.html` | 单文件静态页，浏览器直接打开 | 给产品 / 业务确认长相，**不进代码库** |
| `pagespec.json` | 页面契约：原型 A1–A6、字段、列、动作、状态机、编码规则、视觉令牌 | **带走它**，React / Vue 代码按它生成 |
| `report.html` | 本次 `page.html` 的验收报告 | 只证明示例页合格；项目里的页面要重新验 |

其余文件（`acceptance.json` 断言明细、`reversed/vision.pagespec.json` 入场中间结果、`meta.json`）供排查用，不需要带走。

## 2. spec 放哪、叫什么

**没有固定目录或文件名要求。** 技能不会自动扫描项目找 spec，你在指令里告诉 AI 用哪份，它就读哪份；
验收脚本也是 `--spec <路径>` 显式指定。所以目录和命名由你定，但建议全项目统一一条规则：

- **后缀保留 `.pagespec.json`**：一眼可辨，可用 `**/*.pagespec.json` 统一检索。
  不要用 `xxx.spec.json`——`.spec.` 在前端项目里通常是测试文件，会被测试框架误识别。
- **文件名与页面目录同名**：`sales-order-list.pagespec.json` ↔ `src/pages/sales-order-list/`，改页面时立刻找得到契约。

spec 多了以后，二选一，全项目统一：

**A. 集中放，按业务模块分子目录**（spec 多、需要统一评审时）

```
my-app/
  specs/
    sales/
      sales-order-list.pagespec.json
      sales-order-form.pagespec.json
    purchase/
      purchase-request-form.pagespec.json
    inventory/
      inventory-dashboard.pagespec.json
  src/pages/...
```

**B. 与页面同目录（推荐）**（页面与契约一起改、一起删）

```
src/pages/
  sales-order-list/
    index.jsx
    filter-bar.jsx
    sales-order-list.pagespec.json
  purchase-request-form/
    index.jsx
    purchase-request-form.pagespec.json
```

页面几十个时推荐 B：删页面时契约一起删，不留孤儿文件。JSON 没被 import 就不会打进构建产物，放 `src` 下无副作用。

想省掉每次写完整路径：在项目根的 AI 规则文件里写一句约定，例如
「页面契约放在页面目录下，命名 `<页面目录名>.pagespec.json`」，之后说「生成 sales-order-list 页面」即可。

## 3. 在 React 项目里使用（VS Code）

1. **放 spec**：按第 2 节规则复制进项目。
2. **准备技能**：用 WorkBuddy 时可自行把技能安装到 `~/.workbuddy/skills/erp-page-forge/`；
   用其它 AI 工具，把 `SKILL.md` 与 `references/`、`spec/`、`skeletons/` 一起复制进项目（如 `.ai/erp-page-forge/`）。
3. **下指令**：
   
   > 按 erp-page-forge 和 src/pages/sales-order-list/sales-order-list.pagespec.json，在本项目生成销售订单列表页
4. **技能先探测再生成**（强制，见 `skill/references/stack-adaptation.md`）：读 `package.json`、`src/` 结构、
   2–3 个现有页面 / store / 组件，形成「项目约定画像」，然后按四层取舍：
   - **工程实现层服从项目**：JS/TS、RTK / zustand / 组件 state、CSS Modules / Tailwind、文件命名。
   - **业务规范层服从 spec**：列、状态、「状态 → 可用动作」、编码规则（RTK 项目会落成 `createSlice` + 动作矩阵）。
   - **视觉令牌层**：项目已有令牌 > spec 里的参考图令牌 > 技能默认。
   - 典型产出：页面 `index.jsx`、筛选栏 / 表格 / 状态标签组件、store slice、路由与菜单注册。
5. **你需要补的**：示例数据是脱敏虚构的；真实接口、权限、校验规则等业务逻辑要替换或确认。

Vue 项目流程相同，差异见 `docs/stack-lab-integration.md`。

## 4. 验收

**第一步：编译通过**

```
cd C:\Projects\example-app
npm run build
```

**第二步：用同一份 spec 跑四类断言**（在 erp-page-forge 目录执行，路径与路由按实际改）

```
python build/verify_acceptance.py ^
  --spec C:\Projects\example-app\src\pages\sales-order-list\sales-order-list.pagespec.json ^
  --artifact C:\Projects\example-app\src\pages\sales-order-list ^
  --artifact C:\Projects\example-app\src\store\salesOrderSlice.js ^
  --root ./example-project/build --route /sales-order ^
  --out acc.json
python build/build_report.py --report acc.json --out report.html
```

`--artifact` 可重复：页面目录和 store 文件都要传。生成前想先看「这次要过哪些项」，加 `--plan`。

| 类别 | 检查什么 |
|---|---|
| 契约 | 源码里 spec 要求的列、动作、状态是否齐全，有无空列 |
| 渲染 | 真实打开页面：1280px 下横向溢出、`undefined` 等脏值、白屏 |
| 交互 | 浏览器里真点按钮：筛选是否真的筛、「同意」后待办是否真的减少 |
| 数据 | 合计 = 明细之和、待办计数 = 列表条数等自洽性 |

**怎么判**

- 退出码 `0` = 无硬性失败，可以交付；`1` = 有硬性失败，看 `report.html` 定位。
- `unsupported`（需人工）不算失败也不算通过，必须人工确认。常见原因：找不到浏览器、按钮命名不合惯例无法自动驱动。
- 只传源码、不传 `--root` / `--route` 时只能跑契约一类，其余三类无法执行。
- 像素满分 ≠ 内容正确：视觉断言与内容断言都要跑。

该流程曾在 React 19 + RTK + CRA 项目上实测跑通，记录见 `skill/references/stack-adaptation.md`。

## 5. 附：与「直接把截图丢给 AI」的区别

> 区别不在能不能生成，而在生成之后的**保证、复现、追溯**。
> 直接问 AI 是在赌它这次理解对了；这套系统是在证明它这次生成对了。

**两条路径**

```
直接问 AI：截图 + 一句需求 → 模型自由生成（每次不一样）→ 人工肉眼看 → 上线后才发现偏差
Forge：    四个入口 → PageSpec 契约 → 配方库 + 骨架确定性生成 → 四类断言闸门 → 验收报告 + 归档
```

**四个入口收敛到同一份 PageSpec**：① 一句话 / 字段清单 ② 一张截图 ③ 已有页面 ④ Axure 原型导出目录。
入口不同，产物契约相同，所以校验规则、令牌替换、断言闸门只写一套，四个入口共享。

| 维度 | 直接给 AI 截图 + 需求 | 这套系统 |
|---|---|---|
| 输入 | 一句话，模型自己理解 | 先转成 PageSpec（字段 / 类型 / 枚举 / 动作 / 状态机） |
| 字段类型 | 靠猜，「金额」可能给成文本框 | 走配方库映射，金额 / 日期 / 枚举按规则定 |
| 枚举取值 | 容易编出不存在的枚举值 | 来自原型标注或已有页面，**是证据不是猜测** |
| 结果稳定性 | 同一张图问两次结果不同，没法 diff | 确定性生成，同输入三次 md5 一致 |
| 交付前检查 | 没有，只能自己看 | 四类断言自动跑，硬性项全过才算通过 |
| 出问题定位 | 重新描述一遍让它改 | 看验收报告，哪条断言挂了直接指出来 |
| 团队一致性 | 每个人问出来的风格都不同 | 业务规范跟 Skill、工程实现跟项目、视觉令牌整表替换 |
| 历史与交付 | 聊天记录里翻 | 按工作区归档，有下载按钮和生成日志 |
| 数据安全 | 截图要发到外部模型 | 纯本地，产物零外链 |

**实际解决的四个问题**

1. **「看着对」和「确实对」是两件事。** 页面生成的坑不在布局，在细节：编码是 `RK-2026-100371` 还是 `RK-100371`、
   状态是 5 个还是 4 个、操作列在哪些状态下按钮要灰掉。这些眼睛看不出来，必须靠断言查。
   所以「像素满分 ≠ 内容正确」是硬规矩，视觉断言与内容断言都要跑。
2. **产物没法 review。** 模型直接给的代码不是从可读规格里长出来的，评审只能逐行读。
   中间隔一层 PageSpec 后，评审看契约就够了。
3. **不可复现。** 模型输出天生漂移，今天和下周给的代码不一样，回归测试没法做。
   确定性生成换来的是「改一处配方能精确知道哪些页面受影响」。
4. **效率的真实来源。** 不是「AI 帮我写」，而是**产品经理的 Axure 标注直接变成字段定义**——
   13 条标注推出 9 字段 / 9 列 / 4 动作 / 5 状态，断言 38/38 全过，无需人工拆层或二次确认。
   相比对着截图猜，这是数量级的差别。

**不擅长什么（说清楚边界）**

- **样板页型才划算**：A1 / A2 / A5 有骨架模板；A3 / A4 / A6 明确回退全量生成，速度优势变小。
- **语义识别泛化不足**：目前仅用两张示例截图（仓储出入库单、客户档案）验证过；深色卡片、无边框按钮等
  风格差异大的截图仍可能拆不准，需在设计台里人工微调图层。
- **自由创作不如直接问 AI**：要一个没做过的新奇交互，模型更强。它的价值在**高频重复页面**，不是没做过的东西。




7.竞争优势

## 一、文字 / 截图 → 设计稿

生成的是可编辑视觉稿，不是代码。代表：**v0（Vercel）**、**Google Stitch**、**Visily**、**Uizard**、国内的**即时设计 AI**、**MasterGo AI 快搭**（支持文/图生高保真原型，产出直接是可编辑矢量图层）。

## 二、设计稿 / 截图 → 代码（最卷的一类）

- **Figma Make** — 背靠 Figma 设计上下文，可对已连接代码库开 PR
- **Anima** — Figma 插件最顺手，React/Vue 导出，但偏绝对定位、组件拆分少
- **Locofy** — 组件结构最干净，按 token 计费
- **Builder.io Visual Copilot** — 独有**组件映射**：引用项目自己的 Button/Card，而不是生成一堆 div
- **Codia AI / TeleportHQ / CoderUI / Fronty** — 多框架截图转码
- **screenshot-to-code（abi 开源）** — 自托管首选，已接 Playwright 自截图回看
- **Kombai** — 唯一"IDE 内 + 读你代码库 + 浏览器自测"的一档，和上面所有"导出后粘贴"的工具不是一回事

## 三、一句话 → 可运行应用

**Lovable**、**Bolt.new**、**Replit Agent**。会主动补路由、状态、数据库和部署——但这恰恰是"复刻一个页面"时不需要的东西。

## 四、已有结构 → 喂给编程 Agent（不直接出码）

**Figma MCP server**、**MasterGo Magic MCP**、**Builder.io 组件映射**、**Kombai**。这条路线和你做的 Axure 入口是同一逻辑：把结构化标注交给 Agent，而不是让模型从像素猜。

------

对咱们项目有三点判断：

**1. "识别页面结构"这件事，市面上只有两条路。** 要么读结构（Figma / Axure 的节点与标注、MCP 协议），要么猜像素（截图）。你的 Axure 入口走的是第一条，截图入口走第二条但加了确定性拆层 + 语义识别，标注不足时**报错而不是硬编**——这个取舍在同类里是少见的，可以当差异点讲。

**2. Builder.io 的"组件映射"和 Figma MCP 的"以设计系统为准"，正是 `components.json` 契约层要解决的问题。** 它们做的是"映射到项目已有组件"，你做到的是"先把组件契约固化，再决定用哪套渲染实现"。方向一致，但你多了一层可替换性。

**3. 所有工具都止步于"生成完交给你肉眼验收"。** 没有一个提供四类断言闸门、md5 可复现、运行归档与验收报告。这就是回答"与直接给 AI 一张截图+需求有什么区别"时，可以拿整个市场做背书的落脚点——它们的宣传语是"还原度 85%–95%"，而那种数字社区里普遍不认，因为**没有验收标准就没有还原度**。你有。