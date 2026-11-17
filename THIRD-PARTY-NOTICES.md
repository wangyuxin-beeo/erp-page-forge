# 来源与第三方边界

本候选从项目所有者提供的 ERP Page Forge 工作树按白名单整理。逐文件原始 SHA-256、候选 SHA-256 与修改状态见 `SOURCE-MANIFEST.json`。清理过程未修改原工作树。来源归属基于工作树和文件内容审阅，没有替代作者权属确认或法律审查。

## 原创功能源码

包含插件薄层、模型桥与宿主适配、Python 引擎、Studio 前端、模板/契约/配方、自由版式扩展和相应检查代码。包清单中的 `UNLICENSED` 保持不变。没有新增 MIT 许可。

## 外部接口和依赖

| 项目 | 用途 | 本候选是否捆绑实现 |
| --- | --- | --- |
| DSH、Cordis、dsh-tools、dsh-llm、Schemastery | 宿主服务和插件接口 | 否，仅包名引用和少量自行编写的边界声明 |
| TypeScript、@types/node | 插件开发与编译 | 否，安装时获取 |
| React | DSH 客户端外部依赖 | 否，由宿主提供 |
| Python | 核心运行时 | 否 |
| Pillow | 可选图像操作 | 否 |
| Playwright、浏览器 | 可选采集与浏览器验收 | 否；浏览器也需用户自行安装 |
| Axhub-Skills | Axure 深度解析的外部技能 | 否；原说明标注其 MIT，需安装者核验实际版本许可 |
| Ant Design、Arco、Element | 组件契约中的目标映射参考 | 否；映射登记不代表已实现相应渲染器 |

第三方名称及接口引用不构成对其实现的再分发。公开源码版不包含从宿主 SDK 提取的实现、打包的依赖目录、Windows wheels、旧发行包、运行日志或真实客户截图。保留的 `build/axure/sample-prototype` 是代码内声明的合成 Axure 结构 fixture，不包含 Axure 运行时。

历史 `.workbuddy` 技能和运行时搜索路径是兼容性实现，不是当前机器的用户配置。`127.0.0.1` 是本地服务或隔离测试地址；`example.com`、`*.example`、`*.invalid` 是文档/测试占位地址。测试中的私网 IP 用于校验访问策略，不代表真实部署。
