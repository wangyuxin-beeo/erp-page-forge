# ERP Page Forge

ERP 高频页面生成工具与 DSH 插件。包含原创 TypeScript 插件、Python 生成及验收引擎、浏览器端 Studio、页面契约、六类骨架和实体配方。当前版本 `1.8.0`。完整源码已发布于 [wangyuxin-beeo/erp-page-forge](https://github.com/wangyuxin-beeo/erp-page-forge)，构建方式与验证范围见下文。许可维持 `UNLICENSED`。

## 功能范围

- A1 列表、A2 表单、A3 详情、A4 看板、A5 审批、A6 对账；支持 PageSpec JSON、实体配方和自然语言指令。
- 默认与 premium 主题、字段及组件契约、可复用模板、模板回流、工作区、运行历史与下载。
- HTML/前端页面反抽 PageSpec；Axure 导出目录解析；截图结构识别与独立截图还原；自由版式扩展。
- 网页和经授权的本机/内网原型采集、多页关系、项目技术栈与主题分析、项目代码生成。
- 契约、数据、渲染与交互验收。浏览器或相应依赖缺失时，相关检查会降级或要求人工确认；不能据此宣称渲染验收通过。
- DSH 工具注册、Studio 工作台接入、模型桥接与宿主视觉路由。宿主 SDK 的运行实现由 DSH 提供，本仓库仅保留插件自身代码和编译期接口声明。

## 目录

| 目录 | 内容 |
| --- | --- |
| `plugin/src` | 插件、Python 调度、模型桥、Studio 及工作台集成 |
| `plugin/scripts` | 编译资源拷贝、安全打包与可用的插件检查脚本 |
| `build` | Python 核心实现与专项检查；名称是历史约定，并非预编译产物 |
| `build/ext/freeform` | 自由版式实现和 schema |
| `studio` | 原生 HTML/CSS/JavaScript Studio 及模型提示词 |
| `skill/spec` | PageSpec schema、令牌、组件契约、配方和示例 |
| `skill/skeletons`、`templates` | 六类页面骨架和独立模板 |
| `tests`、`docs` | 原创检查代码与经过整理的技术说明 |

## 先运行核心功能

需要 Python 3.10 或更新版本；确定性生成使用标准库，在仓库根目录执行：

```sh
python build/forge.py --entity salesOrder --archetype A1 --out ./demo-sales-order.html
python build/forge.py --entity approvalTask --archetype A5 --out ./demo-approval.html
python build/forge.py --spec skill/spec/examples/license-approval.pagespec.json --out ./demo-spec.html
python build/studio_server.py --port 3060
```

Studio 地址为 `http://127.0.0.1:3060/`。请保持命令运行。默认仅监听本机，独立运行时数据默认位于引擎的数据目录；可在启动前通过 `FORGE_DATA_DIR` 指定独立可写目录。插件模式使用 DSH profile 数据目录。

截图裁剪/像素分析和网页采集分别需要 Pillow 与 Playwright。按需安装，公开候选不捆绑这些依赖或浏览器二进制：

```sh
python -m pip install -r requirements-optional.txt
python -m playwright install chromium
```

部分浏览器验收会查找本机 Chrome/Edge；Axure 深度解析可利用外部 Axhub-Skills，其代码不在本仓库中，相关说明见 `skill/references/axure-entry.md`。静态 Axure 解析和小型合成 fixture 已保留。

历史截图示例和它们的识别 JSON 已排除，Studio 中对应示例入口已隐藏。上传自己的图片、页面采集、模板生成及 Axure 合成示例仍可使用。部分历史专项检查需要被排除的截图 fixture，运行前请阅读扫描报告中的说明。

## 构建 DSH 插件

需要 Node.js 20+ 和 npm，安装 TypeScript 与 Node 类型开发依赖。宿主 peer dependencies 由实际 DSH 安装提供；源码编译不要求把宿主实现复制进仓库。

```sh
cd plugin
npm install --legacy-peer-deps
npm run build
npm pack
```

`--legacy-peer-deps` 用于避免开发环境自动拉取宿主私有 peer 包。`build` 编译到 `plugin/lib`，拷贝浏览器客户端并按白名单组装 `plugin/engine`。`npm pack` 只在已有编译入口时组装资源；输出 `.tgz`。这些生成目录已加入忽略规则，源码交付中不包含它们。

更严格的发行暂存：完成 build 后执行 `node scripts/pack-plugin.mjs --stage-only`，然后审阅新建的 `release/staging-*` 目录；该脚本也支持显式传入 pnpm JavaScript CLI 路径。当前未附锁文件，干净机器安装仍依赖包注册表可用性。

插件声明 DSH `>=0.2.2-rc.1 <1.0.0`，需要工具、模型和附件服务；完整 GUI 工作台还依赖宿主 Web 服务。实际宿主安装、重载和真实模型链路须在对应版本 DSH 上验证。本次没有安装或替换当前宿主插件。

## 模型配置与数据处理

DSH 模式复用宿主模型配置。独立 Studio 可通过进程环境变量设置 OpenAI 兼容接口：`FORGE_AI_BASE`、`FORGE_AI_KEY`、`FORGE_AI_MODEL`，并可配置 `FORGE_AI_TIMEOUT`。密钥不要写入源码。未配置模型时，确定性生成、模板与人工标注仍可使用。

启用模型识别/还原时，所选图片、采集内容和有限项目主题源码会发送给配置的模型服务。生成页面中的数据是演示数据。仅采集和处理已获授权的资料。

## 检查与当前状态

```sh
python -m unittest discover -s tests -p "test_*.py"
python build/check_taxonomy.py
```

具体实测、环境限制、保留的兼容路径和未执行项目见 `PUBLIC-SCAN-REPORT.md`。来源与逐文件 SHA-256 见 `SOURCE-MANIFEST.json`；公开依赖和未捆绑项见 `THIRD-PARTY-NOTICES.md`。

## 许可

沿用原插件 `UNLICENSED`，此次没有改为 MIT 或其他开源协议。公开可见不等于授予复制、修改或再分发许可；如后续需要采用其他许可证，应由权利人单独决定。第三方依赖适用各自许可证，未随本候选分发。
