# 项目原型采集、主题读取与代码生成（1.6.0）

Studio 顶部「参考网页」使用项目自研 Playwright 采集，不依赖 Axhub 服务或扩展。

## 入口

- Axure 导出目录：沿用「设计台 → Axure」静态解析入口。也可通过 web_capture 工具的 prototype 模式传绝对目录，临时本机服务采集后自动关闭。
- 本机浏览器原型：选择「本机/内网原型」，输入完整 URL，例如 `http://127.0.0.1:32767/start.html`。
- 授权内网：选择「本机/内网原型」，输入真实完整 URL，例如 `http://prototype.example/erp`。电脑须能解析域名并通过所在网络/VPN访问。
- 普通公开网页：保留公开网页模式，仅允许公开 http/https 80/443。

原型模式允许用户输入的准确 origin（协议、主机、端口）以及公开资源；其他内网源仍阻止。只在授权 origin 内跟随明确导航链接和同源 iframe；不同内网认证域、资源域或SSO跳转尚需额外授权配置，当前不会自动放行。

## 使用流程

1. 选择访问模式，输入完整地址，设置最多页面数（1–10，默认3）及视口。
2. 需要登录时勾选「手动登录」，在弹出的专用浏览器完成登录，再点击浏览器右上角「登录完成，开始采集」。最长等待120秒；工具/API可用loginTimeout在30–180秒之间调整。浏览器会话不继承日常浏览器，不导出或持久化Cookie。
3. 需要按项目主题生成时，填写本机前端项目绝对目录。项目根目录需有package.json；此输入表示允许读取有限的主题源码和依赖名称，并在点击AI生成时发送给当前配置模型。
4. 采集后查看截图、页面/交互统计、主题来源和交互清单，下载页面数据、主题JSON、原型JSON或完整包。此阶段无需模型。
5. 可选静态HTML/CSS还原；或选择「按项目主题生成代码包」。后者使用读取到的React/Vue/HTML技术栈、组件依赖、主题变量、原型DOM与交互声明生成新增前端代码包，不直接覆盖原项目。
6. 下载后先审阅并在目标项目构建、运行和测试。Studio项目模式预览仅为交付说明，不是目标项目实际运行效果；模型API连通不代表生成代码编译通过。

## 交互依据

Axure优先解析loadDocument/loadCurrentPage的纯JSON数据参数及可读取的原型元数据。识别页面目录、linkWindow跳转、setPanelState和showWidget等动作声明。不可解析的JavaScript表达式不执行。

普通HTML提取href、aria-controls、data-target等明确关系；没有目标声明的按钮保留needs_review。跟随明确同源导航进行有限页面遍历，不自动点击提交、保存、审批等业务按钮。声明被读取不等于交互已实际执行验证。条件分支、隐藏状态、Canvas、跨源iframe、动态复杂交互可能缺失。

## 项目主题

只读扫描src/styles/theme等有限目录和主题命名文件，跳过node_modules、构建产物、隐藏文件、符号链接及疑似凭据文件；提取CSS/SCSS变量、组件库及路由依赖。默认最多18个源文件，约65KB源码。未发现主题时明确提示，不凭空认定项目主题。主题只用于生成提示词，尚不保证覆盖项目所有主题规则。

项目代码包包含新增文件建议、路由与接口接入说明以及forge目录内的主题/原型依据。没有后端接口契约时使用mock与服务适配层，不连接原型系统接口。代码不会自动安装依赖、执行或写回原项目。

## 安装依赖

使用运行Studio的同一个Python：

```powershell
python build/web_capture.py --install
```

独立开发默认写入build/web-deps。插件设置FORGE_DATA_DIR为其实际数据目录后执行，引擎会优先从该目录的web-deps读取。优先使用本机Chrome/Edge，再尝试Playwright Chromium。状态API检查包存在性，浏览器实际可启动情况由采集验证。

## API与文件

- GET `/api/web/status`
- POST `/api/web/capture`：`{url,width?,height?,workspace?,access:'public'|'prototype',maxPages?,login?,loginTimeout?,projectPath?}`。
- POST `/api/web/restore`：`{run,hint?,mode:'static'|'project'}`。
- 工具forge_studio_action操作web_status/web_capture/web_restore；web_restore复用当前会话模型。

page-data.json含主页面节点、计算样式、主题、素材；prototype.json含pages、edges、unknown及声明证据；后续页面为page-2.json/png等。project-context.json记录有限项目主题与技术栈；project-code.zip为项目模式代码交付物。每页2500可见节点、12000访问节点、截图前12000像素；素材最多40项/单项2MB/总8MB，AI上下文另有限额。

采集与生成各单任务，避免并发浏览器占用。采集超时按页面数和登录时长设置。页面和交互始终保留待验收状态。

## 验证边界

本机fixture验证了非标准端口、多页Axure播放器、声明交互、目录采集、手动登录流程和不导出输入值/Cookie；项目主题与代码包经模拟模型测试。未在实际部署网络/SSO、真实项目编译与真实模型还原质量上验收。内网页面和项目主题在点击AI生成时发送给配置模型，应使用已获授权的模型服务。
