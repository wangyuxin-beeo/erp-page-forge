/*
 * 模拟 Axure 导出的站点地图文件。
 *
 * 真实 Axure 导出结构：
 *   {base}/data/document.js   ← 本文件（站点地图 + 全局配置）
 *   {base}/files/{page}/data.js
 *
 * extract-axure-data 的解析方式：在 Node 的 vm 沙箱里执行本文件，
 * 只暴露 $axure.loadDocument 桩函数把数据接出来。所以这里是纯 JS 调用，
 * 不需要任何 Axure 运行时。
 */
$axure.loadDocument({
  "sitemap": {
    "rootNodes": [
      {
        "type": "Folder",
        "pageName": "仓储中心",
        "children": [
          {
            "type": "Page",
            "pageName": "出入库单据管理",
            "url": "inout-list.html",
            "children": []
          }
        ]
      }
    ]
  },
  "configuration": {
    "logoImagePath": "",
    "globalVariables": {
      "currentUser": "王立",
      "userRole": "仓管主管"
    }
  }
});
