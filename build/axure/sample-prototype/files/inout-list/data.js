/*
 * 模拟 Axure 页面数据文件（含产品经理在 Axure 里写的字段标注）。
 *
 * 这是接入我们体系的**关键输入**：annotations 里的标注是产品经理亲手写的
 * 字段语义、控件类型、枚举取值与按钮可见规则 —— 比从截图像素里猜要准得多。
 *
 * 注意：extract-axure-data 会清洗掉 ownerId / label / fn 三个键，
 * 所以业务信息放在 fieldName / type / description / control / allowedStates 上。
 */
$axure.loadCurrentPage({
  "page": {
    "packageId": "p-inout-list",
    "generationDate": "2026-10-08",
    "url": "inout-list.html",

    "diagram": { "objects": [] },

    "notes": {
      "description": "出入库单据管理 —— 仓储中心的单据台账。支持审核、撤回、出库/上架、打印；单据数量与金额为明细行合计。",
      "author": "产品：赵敏",
      "version": "v1.3",
      "module": "仓储中心",
      "breadcrumb": "仓储中心 / 出入库单据管理"
    },

    "annotations": [
      {
        "ownerId": "w01",
        "fieldName": "单据号",
        "type": "code",
        "description": "出入库单号。入库以 RK-年份-6位序号，出库以 CK-年份-6位序号，如 RK-2026-100371",
        "placeholder": "请输入单据号",
        "codePattern": "RK-2026-100371"
      },
      {
        "ownerId": "w02",
        "fieldName": "单据类型",
        "type": "enum",
        "control": "select",
        "options": ["入库单", "出库单"],
        "description": "枚举：入库单 / 出库单。入库单的作业动作是「上架」，出库单是「出库」"
      },
      {
        "ownerId": "w03",
        "fieldName": "业务日期",
        "type": "date",
        "control": "date-range",
        "description": "单据的业务发生日期，查询区用起止区间"
      },
      {
        "ownerId": "w04",
        "fieldName": "往来单位",
        "type": "text",
        "control": "input",
        "description": "入库单为供应商、出库单为客户，取自主数据"
      },
      {
        "ownerId": "w05",
        "fieldName": "仓库",
        "type": "enum",
        "control": "select",
        "options": ["华东一号仓", "华东二号仓", "华南周转仓"],
        "description": "枚举：三个仓库"
      },
      {
        "ownerId": "w06",
        "fieldName": "单据数量",
        "type": "number",
        "align": "right",
        "description": "本单各明细行数量合计，整数"
      },
      {
        "ownerId": "w07",
        "fieldName": "单据金额",
        "type": "money",
        "align": "right",
        "description": "本单各明细行金额合计，两位小数"
      },
      {
        "ownerId": "w08",
        "fieldName": "单据状态",
        "type": "status",
        "options": ["待审核", "已审核", "已出库", "已完成", "已作废"],
        "description": "枚举：待审核 / 已审核 / 已出库 / 已完成 / 已作废"
      },
      {
        "ownerId": "w09",
        "fieldName": "制单人",
        "type": "person",
        "description": "创建该单据的人"
      },
      {
        "ownerId": "w10",
        "fieldName": "审核",
        "type": "action",
        "scope": "row",
        "allowedStates": ["待审核"],
        "description": "按钮：仅「待审核」状态可点，点击后本节点办结并流转到下一节点"
      },
      {
        "ownerId": "w11",
        "fieldName": "撤回",
        "type": "action",
        "scope": "row",
        "allowedStates": ["待审核"],
        "description": "按钮：仅「待审核」状态可点，撤回到制单人"
      },
      {
        "ownerId": "w12",
        "fieldName": "打印",
        "type": "action",
        "scope": "row",
        "allowedStates": [],
        "description": "按钮：所有状态可用"
      },
      {
        "ownerId": "w13",
        "fieldName": "新建单据",
        "type": "action",
        "scope": "header",
        "primary": true,
        "description": "页头主按钮"
      }
    ],

    "interactionMap": {
      "onClick": { "targetPage": "inout-detail", "action": "navigate" }
    }
  },

  "objectPaths": {
    "w01": { "scriptId": "docNo" },
    "w02": { "scriptId": "docType" },
    "w03": { "scriptId": "bizDate" },
    "w04": { "scriptId": "partner" },
    "w05": { "scriptId": "warehouse" },
    "w06": { "scriptId": "qty" },
    "w07": { "scriptId": "amount" },
    "w08": { "scriptId": "status" },
    "w09": { "scriptId": "creator" },
    "w10": { "scriptId": "btnAudit" },
    "w11": { "scriptId": "btnWithdraw" },
    "w12": { "scriptId": "btnPrint" },
    "w13": { "scriptId": "btnCreate" }
  }
});
