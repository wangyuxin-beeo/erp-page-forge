#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ERP Page Forge · 确定性生成器（阶段二 · 生成层）

吃一份 PageSpec，吐出**可运行的单文件 HTML**。骨架来自 skill/skeletons/，
数据与列/动作/状态机来自 PageSpec + recipes.json + tokens.json。

三条设计约束：
  1. **确定性**：同一份 PageSpec 两次生成结果逐字节一致（固定种子 PRNG，不掺时间戳）。
     这是「模板骨架 + 语义填充」相对「每次让模型重新写一遍」的核心收益。
  2. **零外链**：产物不引任何 CDN/字体/图表库，断网可开。
  3. **确定性优先，生成式兜底**：A1–A6 六种原型均有骨架；骨架表达不了的诉求
     （PageSpec.custom 或未知原型）明确回退并提示走全量生成 —— 逃生舱比过度模板化重要。

用法:
    # 从 PageSpec 生成
    python build/forge.py --spec skill/spec/examples/license-approval.pagespec.json --out out/license.html
    # 从配方快捷生成（实体名 + 原型）
    python build/forge.py --entity salesOrder --archetype A1 --out out/so.html
    # 只输出模型（调试用）
    python build/forge.py --spec x.json --dump-model

退出码: 0 = 成功, 1 = 失败
"""

import argparse
import hashlib
import json
import os
import random
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SPEC_DIR = os.path.join(ROOT, "skill", "spec")
SKEL_DIR = os.path.join(ROOT, "skill", "skeletons")

SUPPORTED = ("A1", "A2", "A3", "A4", "A5", "A6")


# --------------------------------------------------------------------------
# 载入契约层
# --------------------------------------------------------------------------

def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)



def load_tokens():
    return _load(os.path.join(SPEC_DIR, "tokens.json"))


def load_recipes():
    return _load(os.path.join(SPEC_DIR, "recipes.json"))


def load_version():
    return _load(os.path.join(SPEC_DIR, "version.json"))


def load_skeleton(name):
    with open(os.path.join(SKEL_DIR, name), encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------------------------
# 虚构数据池（脱敏：公司名统一带「（示例）」，人名用具名通用名）
# --------------------------------------------------------------------------

POOLS = {
    "customer": ["杭州科锐精密制造（示例）", "无锡双鹿机械（示例）", "宁波恒信电子（示例）",
                 "苏州乾元装备（示例）", "合肥中启自动化（示例）", "青岛海泰机电（示例）",
                 "常州瑞德传动（示例）", "东莞立信精密（示例）", "天津博远重工（示例）",
                 "成都天启仪表（示例）", "福州宇通电器（示例）", "长沙恒力液压（示例）",
                 "西安泰和材料（示例）", "济南联创科技（示例）"],
    "supplier": ["上海旌旗工贸（示例）", "广州立成五金（示例）", "深圳华芯电子（示例）",
                 "南京金陵轴承（示例）", "温州正泰包装（示例）", "佛山金鹏铝业（示例）",
                 "武汉长江材料（示例）", "郑州中原电控（示例）", "沈阳北方精密（示例）",
                 "厦门鹭岛塑胶（示例）", "常州宏达丝网（示例）", "泉州鸿盛模具（示例）"],
    "material": ["交流伺服电机 ASD-B2 750W", "精密减速机 RV-40E", "瓦楞纸箱 600×400×300",
                 "缠绕膜 50cm", "不锈钢轴承 6205-2RS", "铝合金型材 6063-T5",
                 "PLC 模块 CJ2M-CPU31", "变频器 FR-A840-3.7K", "气缸 SC63×100",
                 "同步带 HTD-5M-1500", "气动三联件 AC30-03", "接近开关 LJ12A3-4-Z/BX",
                 "伺服用编码器线 5m", "控制柜冷风扇 12038"],
    # 16 个通用名：一屏 12 行时按行号顺序取不重复，名字太少会出现整列重名
    "people": ["张明", "李静", "王磊", "陈曦", "刘洋", "赵敏", "周涛", "孙倩",
               "吴磊", "郑浩", "马超", "林芳", "徐磊", "黄婷", "何静", "罗刚"],
    "org": ["销售部", "采购部", "生产制造部", "财务部", "仓储物流部", "质量部", "安全环保部", "信息技术部"],
    "warehouse": ["原料仓", "成品仓", "在途仓", "半成品仓"],
}

# 状态分布：必须含异常态（SKILL 自检清单第 7 条「数据要有故事」）
STATUS_PLAN = {
    "salesOrder": ["已确认", "已确认", "生产中", "部分发货", "已发货", "已发货", "待审核", "待审核",
                   "待审核", "已驳回", "已关闭", "已发货", "已确认", "生产 中"],
    "material": ["启用", "启用", "启用", "启用", "启用", "停用", "启用", "启用", "启用", "启用", "启用", "启用"],
    "customer": ["合作中", "合作中", "合作中", "潜在", "合作中", "暂停合作", "合作中", "合作中",
                 "合作中", "已终止", "合作中", "合作中", "潜在", "合作中"],
    "purchaseRequest": ["待审批", "待审批", "已审批", "已转采购单", "草稿", "已驳回", "待审批", "已审批",
                        "已转采购单", "已关闭", "待审批", "已审批"],
    "inventory": ["充足", "充足", "预警", "低于安全", "充足", "呆滞", "充足", "预警", "充足", "充足", "低于安全", "充足"],
}

# 枚举字段没有 options 时的兜底取值（反向工程 / 截图入场常见）：按字段名关键词匹配，第一项是「状态」
ENUM_FALLBACK = [
    ("状态", ["正常", "正常", "待审核", "正常", "停用", "正常"]),
    ("类型", ["标准", "重点", "普通"]),
    ("类别", ["A 类", "B 类", "C 类"]),
    ("等级", ["一级", "二级", "三级"]),
    ("方式", ["月结30", "月结60", "款到发货"]),
    ("结果", ["合格", "合格", "待复核", "不合格"]),
    ("", ["选项一", "选项二", "选项三"]),
]


def _enum_fallback(name):
    return next(v for k, v in ENUM_FALLBACK if k in name)


def _stable_seed(*parts):
    h = hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(h[:12], 16)


def fmt_money(n):
    return "¥%s" % format(round(float(n), 2), ",.2f")


def fmt_num(n, p=0):
    return format(round(float(n), p), ",.%df" % p)


# --------------------------------------------------------------------------
# PageSpec 补全：把配方与默认值填进去，并标 provenance
# --------------------------------------------------------------------------

def find_recipe(recipes, spec):
    ent = (spec.get("entity") or {}).get("name", "")
    wanted = set([ent] + [str(a) for a in (spec.get("entity") or {}).get("aliases", [])])
    key = (spec.get("entity") or {}).get("recipeKey")
    if key and key in recipes["entities"]:
        return key, recipes["entities"][key]
    for k, r in recipes["entities"].items():
        names = set([r["name"]] + list(r.get("aliases", [])))
        if names & wanted:
            return k, r
    for k, r in recipes["entities"].items():
        if ent and (ent in r["name"] or r["name"] in ent):
            return k, r
    return None, None



def complete_spec(spec, recipes):
    """补全 PageSpec：字段、状态机、动作、查询条件、列。缺项标 provenance。"""
    spec = json.loads(json.dumps(spec))  # deep copy，别改调用方的对象
    key, recipe = find_recipe(recipes, spec)

    fields = spec.get("fields") or []
    have = set(f["name"] for f in fields)

    # 从配方补齐字段（配方字段在前，用户字段保留；同名以用户为准）
    if recipe:
        merged = []
        for f in recipe.get("fields", []):
            u = next((x for x in fields if x["name"] == f["name"]), None)
            merged.append(u or dict(f, provenance="recipe"))
        for f in fields:
            if f["name"] not in set(m["name"] for m in merged):
                merged.append(dict(f, provenance=f.get("provenance", "user")))
        fields = merged
        if not spec.get("entity", {}).get("codeRule"):
            spec.setdefault("entity", {})["codeRule"] = recipe.get("codeRule", "编码-年份-序号")
    for f in fields:
        f.setdefault("provenance", "fallback")
        f.setdefault("key", _camel(f["name"]))
    spec["fields"] = fields

    # 状态机
    if not spec.get("statusMachine") and recipe and recipe.get("statusMachine"):
        sm = json.loads(json.dumps(recipe["statusMachine"]))
        sm["_provenance"] = "recipe"
        spec["statusMachine"] = sm
    sm = spec.get("statusMachine") or {}
    for st in sm.get("states", []):
        st.setdefault("tone", "neutral")
        st.setdefault("terminal", False)

    # 动作：从配方的 defaultActions 或状态机转移推导
    if not spec.get("actions"):
        acts = []
        if recipe and recipe.get("defaultActions"):
            acts = json.loads(json.dumps(recipe["defaultActions"]))
        elif spec.get("archetype") == "A2":
            # A creation form owns draft/submit actions, not later approvers'
            # lifecycle events. Explicit caller actions above are preserved.
            submit = next((t["action"] for t in sm.get("transitions", [])
                           if t.get("from") == sm.get("initial", "草稿")), "提交审批")
            acts = [{"name": "取消", "scope": "form-footer", "provenance": "archetype"},
                    {"name": "暂存草稿", "scope": "form-footer", "provenance": "archetype"},
                    {"name": submit, "scope": "form-footer", "primary": True, "provenance": "archetype"}]
        elif spec.get("archetype") == "A4":
            # Inventory threshold transitions are automatic business events,
            # not buttons on an operational dashboard.
            acts = [{"name": "导出", "scope": "header", "provenance": "archetype"},
                    {"name": "处理", "scope": "row", "provenance": "archetype"}]
        elif sm.get("transitions"):
            n = {}
            for t in sm["transitions"]:
                n.setdefault(t["action"], []).append(t["from"])
            acts = [{"name": a, "scope": "row", "allowedStates": sorted(set(s))} for a, s in n.items()]
        spec["actions"] = acts
    for a in spec.get("actions", []):
        a.setdefault("scope", "row")
        a.setdefault("allowedStates", [])

    # 查询条件：按 ERP 习惯排序 —— 编码/名称类（输入框）→ 状态类（下拉）→ 日期
    # 若全排成下拉会出现"一个输入框都没有"的畸形查询区，交互断言也无从驱动。
    if spec.get("archetype") in ("A1", "A6") and not spec.get("query"):
        rank = {"code": 0, "text": 1, "person": 1, "org": 1, "enum": 2, "date": 3}
        cands = []
        for f in fields:
            if f["type"] not in rank:
                continue
            ctrl = "select" if f["type"] == "enum" else ("date-range" if f["type"] == "date" else "input")
            cands.append((rank[f["type"]], f["name"], ctrl))
        cands.sort(key=lambda x: (x[0], x[1]))
        picked, seen = [], set()
        for _, name, ctrl in cands:
            if name in seen:
                continue
            seen.add(name)
            picked.append({"field": name, "control": ctrl})
            if len(picked) >= 5:
                break
        spec["query"] = {"conditions": picked}

    # 列（列表页 8-10 列）
    if not spec.get("columns") and recipe:
        byname = {f["name"]: f for f in fields}
        cols = []
        for cn in recipe.get("listColumns", []):
            f = byname.get(cn)
            if f:
                cols.append({"field": cn, "sortable": f["type"] not in ("attachment", "textarea")})
        if cols:
            spec["columns"] = cols
    if not spec.get("columns"):
        spec["columns"] = [{"field": f["name"]} for f in fields[:8]]

    # 数据规模
    d = spec.setdefault("data", {})
    d.setdefault("minRows", 6 if spec.get("archetype") == "A5" else 12)
    d.setdefault("currency", "¥")
    d.setdefault("disclaimer", "演示用虚构数据，与任何真实企业无关")

    spec["_recipeKey"] = key
    spec["_recipe"] = recipe
    return spec



def _camel(name):
    """中文业务名 → 代码层驼峰 key。优先用配方里已有的 key。"""
    preset = {
        "订单号": "code", "客户名称": "customer", "订单日期": "orderDate", "约定交期": "dueDate",
        "订单类型": "type", "业务员": "owner", "订单金额": "amount", "订单状态": "status",
        "发货状态": "shipStatus", "开票状态": "invoiceStatus", "收款状态": "payStatus",
        "结算方式": "settlement", "不含税金额": "amountExTax", "税额": "taxAmount", "优惠金额": "discount",
        "申请单号": "code", "申请部门": "dept", "申请人": "applicant", "申请日期": "applyDate",
        "需求到货日期": "needDate", "采购类型": "purchaseType", "申请金额合计": "amount",
        "审批状态": "status", "用途说明": "purpose", "供应商建议": "suggestSupplier",
        "物料编码": "matCode", "物料名称": "matName", "物料类型": "type", "规格型号": "spec",
        "计量单位": "unit", "库存数量": "stock", "安全库存": "safetyStock", "库存金额": "stockAmount",
        "状态": "status", "仓库": "warehouse", "可用数量": "availableQty", "库存状态": "status",
        "库龄": "ageDays", "批次号": "batchNo", "客户编码": "customerCode",
        "许可证类型": "licenseType", "许可证号": "licenseNo", "工厂": "plant",
        "类别": "category", "功能位置": "funcLoc", "修改内容": "changeContent", "修改节点": "changeNode",
    }
    if name in preset:
        return preset[name]
    slug = hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]
    return "f_" + slug


def field_index(spec):
    return {f["name"]: f for f in spec.get("fields", [])}


def build_columns(spec):
    """列定义：供运行时渲染（金额右对齐、状态出胶囊、时间不折行都在运行时统一保证）。"""
    fi = field_index(spec)
    cols = []
    for c in spec.get("columns", []):
        f = fi.get(c["field"])
        if not f:
            continue
        t = f["type"]
        col = {
            "key": f["key"], "name": f["name"],
            "type": {"money": "money", "number": "number", "date": "date", "datetime": "date"}.get(t, "text"),
            "align": "right" if t in ("money", "number") else ("center" if t in ("date", "datetime", "enum") else "left"),
            "precision": f.get("precision", 2 if t == "money" else 0),
        }
        if f.get("unit"):
            col["unit"] = f["unit"]
        if t == "enum" and f["name"] in [s["name"] for s in (spec.get("statusMachine") or {}).get("states", [])]:
            col["type"] = "status"
        elif t == "enum" and "状态" in f["name"]:
            col["type"] = "status"
        if c.get("sortable", True):
            col["sortable"] = True
        cols.append(col)
    return cols


def status_tone_map(spec):
    return {s["name"]: s.get("tone", "neutral") for s in (spec.get("statusMachine") or {}).get("states", [])}



# --------------------------------------------------------------------------
# Mock 数据（确定性）
# --------------------------------------------------------------------------

def gen_rows(spec, n):
    rnd = random.Random(_stable_seed(spec["page"]["name"], spec.get("entity", {}).get("name", "")))
    fields = spec.get("fields", [])
    tones = status_tone_map(spec)
    rkey = spec.get("_recipeKey")
    states = [s["name"] for s in (spec.get("statusMachine") or {}).get("states", [])]
    live = [s for s in states if not next((x for x in (spec.get("statusMachine") or {}).get("states", []) if x["name"] == s), {}).get("terminal")]
    # 状态分布：PageSpec 显式声明了状态机时必须以它为准 ——
    # 截图识别出的状态（如「正常/待审核/停用」）才是那张图上的真实取值；
    # 配方里的预设分布（如客户的「合作中/潜在/暂停合作」）只用于快捷生成。
    # 不这样区分，还原出来的状态列会整列不是图上的值。
    plan = states if states else STATUS_PLAN.get(rkey or "", [])
    today = date(2026, 9, 17)

    def pick_state(i):
        if plan:
            return plan[i % len(plan)].replace(" ", "")
        if live or states:
            return live[i % len(live)] if live else states[0]
        # 无状态机（反向工程 / 截图常见）：返回空串会让状态列整列为空 → 用通用兜底枚举
        return ENUM_FALLBACK[0][1][i % len(ENUM_FALLBACK[0][1])]

    rows = []
    for i in range(n):
        st_name = pick_state(i)
        row = {"__id": "R%03d" % (i + 1), "statusTone": tones.get(st_name, "neutral")}
        for f in fields:
            k, t, name = f["key"], f["type"], f["name"]
            # 状态字段：只要名字带「状态」或类型是 status，就用状态机里的值。
            # 原先的条件要求「options 里必须含该状态值」才赋值 —— 而截图识别来的字段
            # 常常没有 options（像素里看不出枚举全集），导致整列状态被渲染成一堆「—」。
            if name in states or t == "status" or (t == "enum" and "状态" in name):
                row[k] = st_name
                # 行内按钮的状态过滤、以及点击后的状态改写，读的都是 r.status。
                # 只写进字段 key（中文列名转出来的 key 可能是 danJuZhuangTai 这类）
                # 会导致按钮过滤拿不到状态 → 整列只剩「打印」这种无状态限制的动作。
                row["status"] = st_name
                continue
            if t == "code":
                row[k] = _code(spec, i, name)
            elif t == "money":
                row[k] = round(rnd.uniform(8_000, 1_300_000), 2)
            elif t == "number":
                row[k] = rnd.randint(1, 900)
            elif t == "date":
                row[k] = (today - timedelta(days=rnd.randint(0, 40))).isoformat()
            elif t == "datetime":
                d = today - timedelta(days=rnd.randint(0, 20))
                row[k] = "%s %02d:%02d" % (d.isoformat(), rnd.randint(8, 19), rnd.choice([5, 12, 26, 40, 55]))
            elif t == "person":
                row[k] = _pool_pick(POOLS["people"], i, name)
            elif t == "org":
                row[k] = POOLS["org"][rnd.randrange(len(POOLS["org"]))]
            elif t == "bool":
                row[k] = rnd.random() > .3
            elif t == "enum":
                opts = f.get("options") or _enum_fallback(name)
                row[k] = opts[i % len(opts)] if not f.get("options") else opts[rnd.randrange(len(opts))]
            elif t == "textarea":
                row[k] = rnd.choice(["常规补货。", "客户加急，请优先处理。", "项目备料，按计划到货。", "设备维修更换件。"])
            else:
                row[k] = _text(name, rnd, i)
        rows.append(row)

    # 业务自洽：金额列 = 不含税 + 税额（有这些列时）
    fi = field_index(spec)
    if "不含税金额" in fi and "税额" in fi and "订单金额" in fi:
        for r in rows:
            ex = round(r[fi["不含税金额"]["key"]] * 0.87, 2)
            r[fi["不含税金额"]["key"]] = ex
            r[fi["税额"]["key"]] = round(r[fi["订单金额"]["key"]] - ex, 2)
    return rows



def _code(spec, i, field_name):
    ent = spec.get("entity") or {}
    rule = ent.get("codeRule") or "编码-年份-序号"
    # 截图识别出的编码前缀与位宽优先 —— 参考图是 RK-2026-100371 这种，
    # 不接的话会退回通用的「示例-12」，一眼就不像。
    prefix = (ent.get("codePrefix") or "").upper()
    if not prefix:
        # 只取 ASCII 字母：str.isalpha() 对中文也返回 True，
        # 「无独立编码，用 物料编码+仓库+批次」这种说明会被整段当成前缀
        prefix = "".join(ch for ch in rule.split("-")[0] if ch.isascii() and ch.isalpha()).upper()
    # 字段语义优先于实体规则：库存行的「物料编码」「批次号」不该共用一个前缀
    by_field = {"客户编码": "CUS", "物料编码": "MAT", "批次号": "LOT", "供应商编码": "SUP"}
    if not ent.get("codePrefix") and field_name in by_field:
        prefix = by_field[field_name]
    if "库位" in field_name or "货位" in field_name:
        return "%s-%02d-%02d" % ("ABC"[i % 3], 1 + i % 8, 1 + (i * 3) % 12)
    prefix = prefix or "DOC"
    width = int(ent.get("codeWidth") or 4)
    seq = int(ent.get("codeStart") or {4: 12, 5: 100, 6: 10000}.get(width, 12)) + i
    if "日期" in rule or "年月日" in rule:
        return "%s-20260917-%0*d" % (prefix, width, seq)
    if "月" in rule and "年份" in rule:
        return "%s-2026-09-%0*d" % (prefix, width, seq)
    return "%s-2026-%0*d" % (prefix, width, seq)


def _pool_pick(pool, idx, name=""):
    """按行号顺序取主数据池里的值，并用字段名做一次偏移。

    随机取值会让同一页出现三行同名公司（池子只有十几个），
    而截图里每行都是不同主体 —— 顺序取既保证一屏内不重复，
    字段名偏移又让「客户名称」与「往来单位」不会取到同一个名字。
    """
    if not pool:
        return ""
    off = sum(ord(ch) for ch in str(name)) % len(pool)
    return pool[(idx + off) % len(pool)]


def _text(name, rnd, idx=0):
    if "客户" in name or "对方" in name or "购方" in name or "销方" in name \
            or "往来" in name or "单位" in name:
        return _pool_pick(POOLS["customer"], idx, name)
    if "供应商" in name:
        return _pool_pick(POOLS["supplier"], idx, name)
    if "物料" in name or "产品" in name or "规格" in name:
        return _pool_pick(POOLS["material"], idx, name)
    if "仓库" in name:
        return POOLS["warehouse"][rnd.randrange(len(POOLS["warehouse"]))]
    if "部门" in name or "车间" in name:
        return POOLS["org"][rnd.randrange(len(POOLS["org"]))]
    if "地址" in name:
        return "%s 工业园区 12 号" % POOLS["org"][rnd.randrange(len(POOLS["org"]))]
    if "电话" in name or "手机" in name:
        return "138****%04d" % rnd.randint(1000, 9999)
    return "示例-%02d" % rnd.randint(1, 99)


# --------------------------------------------------------------------------
# 骨架渲染
# --------------------------------------------------------------------------

def _hex2rgb(s):
    s = str(s).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def _rgb2hex(t):
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(v)))) for v in t)


def lum_of(hexv):
    """相对亮度。判断侧栏是深色还是浅色 —— 决定侧栏文字该用浅字还是深字。"""
    r, g, b = _hex2rgb(hexv)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def mix(a, b, t):
    """颜色插值：t=0 → a，t=1 → b。用于从截图量到的一两个色推出整套色阶。"""
    ca, cb = _hex2rgb(a), _hex2rgb(b)
    return _rgb2hex([ca[i] + (cb[i] - ca[i]) * t for i in range(3)])



# 派生规则：截图里量不到「浅底/深色/hover/选中态」这些色阶，
# 但只要有主色和侧栏底，就能按业界通行比例推出来 —— 比直接用 Skill 默认色去凑要像得多。
DERIVED = [
    ("color", "brandSoft",     lambda ov: mix(ov["brand"], "#FFFFFF", 0.90), "brand"),
    ("color", "brandDark",     lambda ov: mix(ov["brand"], "#000000", 0.18), "brand"),
    ("color", "sidebarHover",  lambda ov: mix(ov["sidebar"], "#FFFFFF", 0.07), "sidebar"),
    ("color", "sidebarActive", lambda ov: mix(ov["sidebar"], ov["brand"], 0.28), "sidebar"),
    # 侧栏文字随底色翻转：参考图若是浅色侧栏，还沿用浅灰字就成了「浅字浅底」，整条导航看不见。
    # 阈值 150 ≈ 中性灰，深于此用浅字、浅于此用深字。
    ("color", "sidebarText",       lambda ov: "#4A5468" if lum_of(ov["sidebar"]) > 150 else "#C7CEDE", "sidebar"),
    ("color", "sidebarTextStrong", lambda ov: "#1C2230" if lum_of(ov["sidebar"]) > 150 else "#FFFFFF", "sidebar"),
    ("color", "sidebarTextDim",    lambda ov: "#8B93A5" if lum_of(ov["sidebar"]) > 150 else "#6B7691", "sidebar"),
    ("color", "sidebarBorder",     lambda ov: "rgba(0,0,0,.08)" if lum_of(ov["sidebar"]) > 150
                                             else "rgba(255,255,255,.06)", "sidebar"),
]


def merge_tokens(base, override):
    """按 PageSpec 覆盖令牌，返回 (合并后的 tokens, 变更清单)。

    这是「视觉还原」的注入点。截图识别出的配色/尺寸走这里进产物，
    而不是让模型凭印象重写一套 CSS。

    override 结构:
        { "mode": "inherit-visual", "values": { "color": { "brand": {"value": "#0F766E"} } } }
    mode:
        - inherit-skill （或为空）→ 原样用 Skill 默认令牌
        - inherit-visual         → 用截图量到的值覆盖，缺的色阶按 DERIVED 派生
    只覆盖「值」，保留 base 里的 role / 用途说明 —— 说明文档不该随截图变。
    """
    if not override:
        return base, []
    if override.get("mode", "inherit-visual") == "inherit-skill":
        return base, []

    merged = json.loads(json.dumps(base))          # 深拷贝，避免污染调用方的 base
    vals = override.get("values") or {}
    changed = []

    def setval(group, key, new, source):
        slot = merged["values"].get(group, {}).get(key)
        if slot is None:
            return
        old = slot.get("value")
        if old != new:
            changed.append({"token": "%s.%s" % (group, key), "from": old, "to": new, "source": source})
        slot["value"] = new

    # ① 显式覆盖（截图直接量到的）
    for group, items in vals.items():
        for key, v in items.items():
            new = v.get("value") if isinstance(v, dict) else v
            if new in (None, ""):
                continue
            src = v.get("source", "visual") if isinstance(v, dict) else "visual"
            setval(group, key, new, src)

    # ② 派生色阶：只补「没被显式覆盖、但依赖项已覆盖」的那些
    flat = {k: merged["values"]["color"][k]["value"]
            for k in merged["values"].get("color", {})}
    explicit = {k for k, _ in ((c["token"].split(".")[1], c) for c in changed)}
    for group, key, fn, dep in DERIVED:
        if key in explicit:
            continue
        if dep not in explicit:                    # 依赖项没被截图改过 → 不派生，保留 Skill 默认
            continue
        try:
            setval(group, key, fn(flat), "derived:" + dep)
        except (ValueError, KeyError):
            continue

    merged["_override"] = {"mode": override.get("mode", "inherit-visual"), "changed": changed}
    return merged, changed



def render_tokens_css(tokens):
    c, r, sh, fs, sp, ly = (tokens["values"][k] for k in ("color", "radius", "shadow", "font", "space", "layout"))

    def v(d, k, dflt=""):
        return (d.get(k) or {}).get("value", dflt)

    return """
:root{
  --brand:%s; --brand-soft:%s; --brand-dark:%s;
  --success:%s; --warning:%s; --danger:%s; --purple:%s;
  --page-bg:%s; --card:%s; --line:%s; --line-strong:%s;
  --text-1:%s; --text-2:%s; --text-3:%s;
  --sidebar:%s; --sidebar-hover:%s; --sidebar-active:%s;
  --sidebar-text:%s; --sidebar-text-strong:%s; --sidebar-text-dim:%s; --sidebar-border:%s;
  --radius-card:%s; --radius-input:%s; --radius-pill:%s;
  --shadow-card:%s;
  --fs-body:%s; --fs-table:%s; --fs-title:%s; --fs-kpi:%s;
  --sidebar-w:%s; --topbar-h:%s; --content-pad:%s;
}""" % (
        v(c, "brand"), v(c, "brandSoft"), v(c, "brandDark"),
        v(c, "success"), v(c, "warning"), v(c, "danger"), v(c, "purple"),
        v(c, "pageBg"), v(c, "card"), v(c, "line"), v(c, "lineStrong"),
        v(c, "text1"), v(c, "text2"), v(c, "text3"),
        v(c, "sidebar"), v(c, "sidebarHover"), v(c, "sidebarActive"),
        v(c, "sidebarText", "#C7CEDE"), v(c, "sidebarTextStrong", "#FFFFFF"),
        v(c, "sidebarTextDim", "#6B7691"), v(c, "sidebarBorder", "rgba(255,255,255,.06)"),
        v(r, "card"), v(r, "input"), v(r, "pill"),
        v(sh, "card"),
        v(fs, "body"), v(fs, "table"), v(fs, "title"), v(fs, "kpi"),
        v(ly, "sidebarWidth"), v(ly, "topbarHeight"), v(sp, "contentPadding"),
    )


def render_nav(spec):
    # 截图识别会带出侧边栏菜单（spec["nav"]）—— 菜单是"像不像同一个系统"的第一眼印象，
    # 能还原就还原；没有就退回通用模块列表。
    nav = spec.get("nav")
    if nav:
        out = []
        for g in nav:
            if g.get("group"):          # 参考图若没有分组标题（如 Ant 风格侧栏），不要留空占位
                out.append('<div class="nav-title">%s</div>' % g["group"])
            for it in g.get("items", []):
                on = (it == spec["page"].get("module")) or (it == spec["page"]["name"])
                out.append('<a class="nav%s" href="#"><span class="ico">▪</span>%s</a>'
                           % (" on" if on else "", it))
        return "\n      ".join(out)

    module = spec["page"].get("module", "业务")
    modules = [
        ("销售管理", ["销售订单", "客户档案"]),
        ("采购管理", ["采购申请", "供应商档案"]),
        ("库存管理", ["物料主数据", "库存看板"]),
        (module, [spec["page"]["name"]]),
    ]
    # 当前页已在通用菜单里（如「库存看板」）就不再追加一个同名分组，否则侧栏会出现两次
    if any(spec["page"]["name"] in items for _t, items in modules[:-1]):
        modules = modules[:-1]
        module = next(t for t, items in modules if spec["page"]["name"] in items)
    seen, out = set(), []
    for title, items in modules:
        if title in seen:
            continue
        seen.add(title)
        out.append('<div class="nav-title">%s</div>' % title)
        for it in items:
            on = (it == spec["page"]["name"])
            out.append('<a class="nav%s" href="#"><span class="ico">▪</span>%s</a>' % (" on" if on else "", it))
    return "\n      ".join(out)



def render_query(spec):
    fi = field_index(spec)
    out = []
    for c in (spec.get("query") or {}).get("conditions", []):
        f = fi.get(c["field"])
        if not f:
            continue
        k, ctrl = f["key"], c.get("control", "input")
        if ctrl == "select":
            opts = "".join('<option>%s</option>' % o for o in (f.get("options") or []))
            out.append('<div class="fld"><label>%s</label><select data-q="%s"><option value="">全部</option>%s</select></div>'
                       % (f["name"], k, opts))
        elif ctrl == "date-range":
            # 参考图上的日期条件通常是「起 ~ 止」一个控件。
            # 起止分别用 data-qfrom / data-qto，过滤时对同一字段做上下界比较。
            out.append('<div class="fld"><label>%s</label>'
                       '<span class="drange"><input type="date" data-qfrom="%s"><i>~</i>'
                       '<input type="date" data-qto="%s"></span></div>' % (f["name"], k, k))
        else:
            out.append('<div class="fld"><label>%s</label><input data-q="%s" placeholder="请输入%s"></div>'
                       % (f["name"], k, f["name"]))
    return "\n    ".join(out)


def render_table_head(cols, with_check=True):
    th = ['<th class="c" style="width:38px"><input type="checkbox" class="chk" id="all"></th>'] if with_check else []
    for c in cols:
        cls = []
        if c.get("sortable", True):
            cls.append("sortable")
        if c["align"] == "right":
            cls.append("r")
        elif c["align"] == "center":
            cls.append("c")
        th.append('<th class="%s" data-key="%s">%s<span class="ar">▲▼</span></th>'
                  % (" ".join(cls), c["key"], c["name"]))
    th.append('<th class="c" style="width:180px">操作</th>')
    return "".join(th)


def render_actions_defs(spec):
    out = []
    for a in spec.get("actions", []):
        d = {"act": a["name"], "label": a["name"], "scope": a.get("scope", "row")}
        if a.get("allowedStates"):
            d["states"] = a["allowedStates"]
        if a.get("primary"):
            d["primary"] = True
        if a.get("confirm") or a["name"] in ("驳回", "作废", "删除", "停用", "终止"):
            d["danger"] = True
        tr = next((t for t in (spec.get("statusMachine") or {}).get("transitions", []) if t["action"] == a["name"]), None)
        if tr:
            d["to"] = tr["to"]
        out.append(d)
    return out


def render_bulk_actions(spec):
    """批量动作：勾选后可用，未勾选时禁用（对应 A1 自检「未勾选给提示而不是静默失败」）。"""
    acts = []
    for a in spec.get("actions", []):
        if a.get("scope") in ("bulk",) or a["name"] in ("导出", "批量审核", "批量导出"):
            tr = next((t for t in (spec.get("statusMachine") or {}).get("transitions", []) if t["action"] == a["name"]), None)
            acts.append((a["name"], tr["to"] if tr else None))
    if not acts:
        acts = [("批量导出", None), ("批量审核", None)]
    out = []
    for name, to in acts[:3]:
        attr = ' data-bulk="1"' + (' data-to="%s"' % to if to else "")
        out.append('<button class="btn btn-sm"%s disabled>%s</button>' % (attr, name))
    return "".join(out)


def render_head_actions(spec):
    """头部动作：每屏主色实心按钮 ≤1 个。"""
    acts = [a for a in spec.get("actions", []) if a.get("scope") == "header"]
    if not acts:
        acts = [{"name": "导出"}, {"name": "新建", "primary": True}]
    out = []
    for a in acts:
        cls = "btn btn-p" if a.get("primary") else "btn"
        out.append('<button class="%s" data-head="%s">%s</button>' % (cls, a["name"], a["name"]))
    return "".join(out)


def fill(skeleton, mapping):
    html = skeleton
    for k, v in mapping.items():
        html = html.replace("{{%s}}" % k, v)
    left = [m for m in __import__("re").findall(r"\{\{([A-Z_]+)\}\}", html)]
    if left:
        raise SystemExit("[错误] 骨架占位符未填充: %s" % "、".join(sorted(set(left))))
    return html



# --------------------------------------------------------------------------
# 各原型构建
# --------------------------------------------------------------------------

def build_a1(spec):
    cols = build_columns(spec)
    rows = gen_rows(spec, max(spec["data"]["minRows"], 12))
    sum_col = next((c for c in reversed(cols) if c["type"] == "money"), None)
    span_after = len(cols) - (cols.index(sum_col) + 1) + 1 if sum_col else 0
    tfoot = ('<td colspan="%d">当前筛选结果合计（<span id="sumCount">0</span> 条）</td>'
             '<td class="r num" id="sumAmt" data-sum="1">¥0.00</td>'
             '<td colspan="%d"></td>' % (cols.index(sum_col) + 1 if sum_col else len(cols), span_after)) if sum_col else ""
    model = {
        "archetype": "A1",
        "rows": rows,
        "columns": cols,
        "detailFields": spec.get("fields", []),
        "codeKey": next((f["key"] for f in spec["fields"] if f["type"] == "code"), "code"),
        "actionDefs": render_actions_defs(spec),
        "toneOf": status_tone_map(spec),
        "defaultSort": (cols[0]["key"] if cols else None),
        "defaultDir": "asc",
        "sumKey": sum_col["key"] if sum_col else None,
        "asOf": "2026-09-17 09:30",
    }
    body = fill(load_skeleton("A1.html"), {
        "PAGE_NAME": spec["page"]["name"],
        "PAGE_SUB": "加载中…",
        "HEAD_ACTIONS": render_head_actions(spec),
        "QUERY_FIELDS": render_query(spec),
        "BULK_ACTIONS": render_bulk_actions(spec),
        "TABLE_HEAD": render_table_head(cols),
        "TABLE_FOOT": tfoot,
    })
    return body, model



def build_a5(spec):
    fi = field_index(spec)
    sm = spec.get("statusMachine") or {}
    nodes = sm.get("nodeFlow") or ["申请人提交", "部门经理审核", "财务复核", "总经理审批", "归档生效"]
    # 8 条起：三个视图都要有内容，否则「枚举覆盖」断言必然落空
    rows = gen_rows(spec, max(spec["data"]["minRows"], 8))

    # 任务卡需要：标题、金额、单号、剩余时限；并制造「本人提交」与「超期」两类特殊数据
    for i, r in enumerate(rows):
        # 单据编号与提交人是工作台的必备锚点（任务卡、详情标题、时间线都要用）。
        # 字段里没有就补：优先取行内已有的编码类字段，否则按编码规则生成。
        fi_code = next((f["key"] for f in spec["fields"] if f["type"] == "code"), None)
        r["code"] = r.get("code") or (r.get(fi_code) if fi_code else None) or _code(spec, i, "单号")
        r["submitter"] = r.get("applicant") or r.get("submitter") or POOLS["people"][i % len(POOLS["people"])]
        r["title"] = r.get("title") or (POOLS["material"][i % len(POOLS["material"])][:12] + "变更申请")
        if not r.get("remainText"):
            h = [4, 6, 30, 52, 8, 16][i % 6]
            r["remainText"] = ("剩 %dh" % h) if h else "已办结"
        r["remainHours"] = 4 if i == 0 else (30 if i == 2 else 12)
        r["submitterDept"] = POOLS["org"][i % len(POOLS["org"])]
        r["nodeIndex"] = r.get("nodeIndex", min(i % len(nodes), len(nodes) - 1))
        r["timelineMeta"] = ["提交人 张明", "部门经理 李静 已同意", "", "", ""]
        r["memos"] = {0: "常规变更，附件已附。"}
        r["isMine"] = False
        r["lines"] = [{
            fi.get("许可证号", {}).get("key", "licenseNo"): r.get(fi.get("许可证号", {}).get("key", "licenseNo"), "—"),
            fi.get("工厂", {}).get("key", "plant"): r.get(fi.get("工厂", {}).get("key", "plant"), "—"),
            fi.get("功能位置", {}).get("key", "funcLoc"): r.get(fi.get("功能位置", {}).get("key", "funcLoc"), "—"),
            fi.get("修改内容", {}).get("key", "changeContent"): r.get(fi.get("修改内容", {}).get("key", "changeContent"), "—"),
            "amount": round(8000 + i * 1350.5, 2),
        } for _ in range(1)]
    # 状态按「桶」分配，保证三个视图都有内容且四个枚举值全覆盖：
    #   待办 = 非我发起 + 待审批；已办 = 非我发起 + 已通过/已驳回；我发起 = isMine + 已通过/已转办
    for i, r in enumerate(rows):
        if i < 4:
            r["isMine"], r["status"] = False, "待审批"
        elif i < 6:
            r["isMine"], r["status"] = False, ("已通过" if i % 2 == 0 else "已驳回")
        else:
            r["isMine"], r["status"] = True, ("已通过" if i % 2 == 0 else "已转办")
        r["statusTone"] = status_tone_map(spec).get(r["status"], "neutral")
        if r["status"] != "待审批":
            r["remainText"] = r["status"]     # 终态不该再显示剩余时限

    line_cols = [
        {"key": fi.get("许可证号", {}).get("key", "licenseNo"), "name": "许可证号", "type": "text", "align": "left"},
        {"key": fi.get("工厂", {}).get("key", "plant"), "name": "工厂", "type": "text", "align": "left"},
        {"key": fi.get("功能位置", {}).get("key", "funcLoc"), "name": "功能位置", "type": "text", "align": "left"},
        {"key": fi.get("修改内容", {}).get("key", "changeContent"), "name": "修改内容", "type": "text", "align": "left"},
        {"key": "amount", "name": "费用金额", "type": "money", "align": "right", "precision": 2},
    ]
    detail_fields = [f for f in spec["fields"] if f["type"] not in ("attachment",)]
    model = {
        "archetype": "A5",
        "rows": rows,
        "nodeFlow": nodes,
        "lineColumns": line_cols,
        "lineSumKey": "amount",
        "sumKey": "amount",
        "detailFields": detail_fields,
        "actionDefs": [
            {"act": "agree", "label": "同意", "primary": True},
            {"act": "reject", "label": "驳回", "danger": True},
            {"act": "transfer", "label": "转办"},
        ],
        "candidates": POOLS["people"],
        "toneOf": status_tone_map(spec),
    }
    body = fill(load_skeleton("A5.html"), {
        "PAGE_NAME": spec["page"]["name"],
        "PAGE_SUB": "加载中…",
        "HEAD_ACTIONS": '<button class="btn" data-head="刷新">刷新</button>',
        "NOTICE": "审批流：%s。剩余时限 &lt;12h 的任务请优先办理；<b>本人提交的申请不可自审</b>。"
                  % " → ".join(nodes),
    })
    return body, model


def render_form_fields(fields):
    """表头字段：必填星号 + 控件形态由字段类型决定（与运行时 F.field 保持一致的规则）。"""
    out = []
    for f in fields:
        req = "req" if f.get("required") else ""
        t = f["type"]
        if t == "code":
            ctrl = '<input data-key="%s" value="%s" readonly aria-readonly="true">' % (f["key"], f.get("default", "自动生成"))
        elif t == "enum":
            opts = "".join('<option>%s</option>' % o for o in (f.get("options") or []))
            ctrl = '<select data-key="%s"><option value="">请选择</option>%s</select>' % (f["key"], opts)
        elif t == "textarea":
            ctrl = '<textarea data-key="%s" rows="2" placeholder="请输入%s"></textarea>' % (f["key"], f["name"])
        elif t == "date":
            ctrl = '<input type="date" data-key="%s">' % f["key"]
        elif t in ("money", "number"):
            ctrl = '<input class="num" data-key="%s" placeholder="请输入%s">' % (f["key"], f["name"])
        else:
            ctrl = '<input data-key="%s" placeholder="请输入%s">' % (f["key"], f["name"])
        out.append('<div class="fld"><label class="%s">%s</label>%s<div class="err" data-err="%s"></div></div>'
                   % (req, f["name"], ctrl, f["key"]))
    return "\n      ".join(out)



def build_a2(spec):
    fi = field_index(spec)
    rec = spec.get("_recipe") or {}
    line_fields = rec.get("detailLines") or []
    if not line_fields:
        line_fields = [
            {"name": "行号", "key": "lineNo", "type": "number", "align": "center", "precision": 0},
            {"name": "物料名称", "key": "matName", "type": "text"},
            {"name": "需求数量", "key": "qty", "type": "number", "align": "right", "precision": 0, "editable": True},
            {"name": "单位", "key": "unit", "type": "text", "align": "center"},
            {"name": "预估单价", "key": "price", "type": "money", "align": "right", "precision": 2, "editable": True},
            {"name": "预估金额", "key": "amount", "type": "money", "align": "right", "precision": 2},
        ]
    qty_key = next((f["key"] for f in line_fields if f["type"] == "number" and "数量" in f["name"]), "qty")
    # 数量与单价必须可编辑：表单页的"改数看合计"是评审必验点，不可编辑等于没实现交互
    for f in line_fields:
        if f["type"] in ("number", "money") and ("数量" in f["name"] or "单价" in f["name"]):
            f["editable"] = True
    price_key = next((f["key"] for f in line_fields if f["type"] == "money" and ("单价" in f["name"] or "价格" in f["name"])), "price")
    amount_key = next((f["key"] for f in line_fields if f["type"] == "money" and "金额" in f["name"]), "amount")
    line_no_key = next((f["key"] for f in line_fields if "行号" in f["name"]), "lineNo")

    # 明细行必须把 lineColumns 里每一列都填上值。
    # 原先只写死「名称/数量/单价/金额」几个键，其余列整列空白 ——
    # 静态契约断言查不出来（那些字段在 spec 里是有的），是 no-empty-column 渲染断言抓出来的。
    SPEC_POOL = ["600×400×300", "φ40×120", "HTD-5M-1500", "6205-2RS", "SC63×100", "6063-T5"]
    UNIT_POOL = ["个", "件", "套", "台", "箱", "米"]
    PURPOSE_POOL = ["常规补货", "项目备料", "客户加急", "设备维修更换", "安全库存储备"]
    today_l = date(2026, 9, 17)

    rnd = random.Random(_stable_seed(spec["page"]["name"], "lines"))
    lines = []
    for i in range(3):
        mat = POOLS["material"][(i + 3) % len(POOLS["material"])]   # 顺序取，避免同一单里物料重名
        qty = rnd.choice([40, 60, 120, 200, 300, 500])
        price = round(rnd.uniform(12, 4200), 2)
        row = {}
        for f in line_fields:
            k, t, name = f["key"], f["type"], f["name"]
            if k == line_no_key:
                row[k] = i + 1
            elif k == qty_key:
                row[k] = qty
            elif k == price_key:
                row[k] = price
            elif k == amount_key:
                row[k] = round(qty * price, 2)          # 金额 = 数量 × 单价，保证自洽
            elif "名称" in name and ("物料" in name or "产品" in name or "品名" in name):
                row[k] = mat
            elif "编码" in name:
                row[k] = "MAT-2026-%04d" % (1012 + i)
            elif "规格" in name or "型号" in name:
                row[k] = SPEC_POOL[i % len(SPEC_POOL)]
            elif "单位" in name:
                row[k] = UNIT_POOL[i % len(UNIT_POOL)]
            elif "用途" in name or "备注" in name or "说明" in name:
                row[k] = PURPOSE_POOL[i % len(PURPOSE_POOL)]
            elif t == "date":
                row[k] = (today_l + timedelta(days=7 + i * 3)).isoformat()
            elif t == "number":
                row[k] = rnd.randint(2, 30)
            elif t == "money":
                row[k] = round(rnd.uniform(10, 900), 2)
            else:
                row[k] = _text(name, rnd, i)
        lines.append(row)

    head_fields = [dict(f) for f in spec["fields"] if f["type"] != "textarea"]
    for f in head_fields:
        if f["type"] == "code":
            f["default"] = _code(spec, 0, f["name"])
    # 底部按钮条：优先用 spec 里的 form-footer 动作（截图识别的真实按钮文案），
    # 按 id 语义回填骨架 JS 依赖的三个 id；没有 form-footer 动作时回退默认三键（兼容旧 spec）。
    foot_specs = [a for a in (spec.get("actions") or []) if a.get("scope") == "form-footer"]

    def _foot_id(name):
        if "暂存" in name:
            return "saveDraft"
        if "提交" in name or name == "保存":
            return "submitBtn"
        if "取消" in name or "关闭" in name:
            return "cancelBtn"
        return ""

    if foot_specs:
        footer_html = "\n    ".join(
            '<button class="btn%s"%s data-foot="%s">%s</button>' % (
                " btn-p" if a.get("primary") else "",
                (' id="%s"' % _foot_id(a["name"])) if _foot_id(a["name"]) else "",
                a["name"], a["name"])
            for a in foot_specs)
    else:
        footer_html = ('<button class="btn" id="cancelBtn">取消</button>\n'
                       '    <button class="btn" id="saveDraft">暂存草稿</button>\n'
                       '    <button class="btn btn-p" id="submitBtn">提交审批</button>')
    model = {
        "archetype": "A2",
        "headFields": [dict(f, required=bool(f.get("required"))) for f in head_fields],
        "lineColumns": line_fields,
        "lines": lines,
        "qtyKey": qty_key, "priceKey": price_key, "amountKey": amount_key, "lineNoKey": line_no_key,
        "lineDefaults": {"unit": "个"},
    }
    line_head = "".join('<th class="%s">%s</th>' % (
        "r" if f.get("align") == "right" else ("c" if f.get("align") == "center" else ""), f["name"])
        for f in line_fields)
    n = len(line_fields)
    qty_at = next((i for i, f in enumerate(line_fields) if f["key"] == qty_key), 0)
    amt_at = next((i for i, f in enumerate(line_fields) if f["key"] == amount_key), n - 1)
    foot = ('<td colspan="%d" class="r">合计</td><td class="r num" id="sumQty" data-sum-qty="1">0</td>'
            '<td colspan="%d"></td><td class="r num" id="sumAmt" data-sum="1">¥0.00</td><td></td>'
            % (qty_at, max(amt_at - qty_at - 1, 0)))
    body = fill(load_skeleton("A2.html"), {
        "PAGE_NAME": spec["page"]["name"],
        "PAGE_SUB": "单据状态：<b>草稿</b> · 新建",
        "HEAD_ACTIONS": '<button class="btn" data-head="导入明细">导入明细</button>',
        "NOTICE": "提交后单据进入审批流，审批期间不可编辑；<b>暂存</b>只校验必填项，不进入审批。",
        "HEAD_FIELDS": render_form_fields(head_fields),
        "LINE_HEAD": line_head,
        "LINE_FOOT": foot,
        "FOOTER_ACTIONS": footer_html,
    })
    return body, model


# ---------------- A3 · 主数据详情页 ----------------
# 中间业务子表：按 Tab 名给出贴业务的列；未登记的 Tab 用通用五列，保证不留空列。
SUBTABLES = {
    "库存分布": [("仓库", "text"), ("库位", "text"), ("账面数量", "number"), ("可用数量", "number"), ("库存金额", "money")],
    "信用与账期": [("期间", "text"), ("信用额度", "money"), ("已用额度", "money"), ("账期天数", "number"), ("逾期金额", "money")],
    "成交记录": [("订单号", "text"), ("订单日期", "date"), ("产品", "text"), ("数量", "number"), ("金额", "money")],
    "供应品类": [("品类", "text"), ("主供物料", "text"), ("年采购量", "number"), ("份额", "number"), ("年采购额", "money")],
    "合作记录": [("采购单号", "text"), ("下单日期", "date"), ("物料", "text"), ("数量", "number"), ("金额", "money")],
}
GENERIC_SUB = [("项目", "text"), ("说明", "text"), ("数量", "number"), ("金额", "money"), ("更新日期", "date")]



def _sub_rows(cols, n, rnd, seed_i=0):
    today = date(2026, 9, 17)
    rows = []
    for i in range(n):
        r = {}
        for ci, (name, t) in enumerate(cols):
            k = "c%d" % ci
            if t == "money":
                r[k] = round(rnd.uniform(3_000, 260_000), 2)
            elif t == "number":
                r[k] = rnd.randint(5, 900)
            elif t == "date":
                r[k] = (today - timedelta(days=7 + i * 11 + rnd.randint(0, 6))).isoformat()
            elif "单号" in name:
                r[k] = "%s-2026-%04d" % ("PO" if "采购" in name else "SO", 81 + i * 7 + seed_i)
            elif name == "期间":
                r[k] = "2026-%02d" % (9 - i)
            elif name == "库位":
                r[k] = "A-%02d-%02d" % (i + 1, rnd.randint(1, 30))
            elif name == "说明":
                r[k] = ["常规维护", "价格调整", "补充资料", "年度复核"][i % 4]
            elif t == "person":
                r[k] = POOLS["people"][(i + seed_i) % len(POOLS["people"])]
            else:
                r[k] = _text(name, rnd, i + seed_i)
        rows.append(r)
    return rows


def _sub_cols(cols):
    out = []
    for ci, (name, t) in enumerate(cols):
        out.append({"key": "c%d" % ci, "name": name,
                    "type": {"money": "money", "number": "number"}.get(t, "text"),
                    "align": "right" if t in ("money", "number") else ("center" if t == "date" else "left"),
                    "precision": 2 if t == "money" else 0})
    return out


def _fmt_val(f, v):
    t = f.get("type")
    if v is None or v == "":
        return "—"
    if t == "money":
        return fmt_money(v)
    if t == "number":
        return fmt_num(v, f.get("precision", 0)) + (" " + f["unit"] if f.get("unit") else "")
    if t == "bool":
        return "是" if v else "否"
    return str(v)


def _is_status_field(f, states):
    return f["name"] in states or f["type"] == "status" or (f["type"] == "enum" and "状态" in f["name"])



def build_a3(spec):
    fi = field_index(spec)
    rec = spec.get("_recipe") or {}
    rnd = random.Random(_stable_seed(spec["page"]["name"], "a3"))
    sm = spec.get("statusMachine") or {}
    states = [s["name"] for s in sm.get("states", [])]
    R = gen_rows(spec, 6)[0]
    tone = status_tone_map(spec)
    if not R.get("status"):
        R["status"] = states[0] if states else "启用"
    tone.setdefault(R["status"], "ok")
    k = lambda n: fi.get(n, {}).get("key")
    # 业务自洽：可用 ≤ 账面、金额 = 数量 × 成本、可用额度 = 额度 − 已用
    if k("库存数量") and k("可用库存"):
        R[k("可用库存")] = int(R[k("库存数量")] * 0.82)
    if k("库存数量") and k("标准成本") and k("库存金额"):
        R[k("标准成本")] = round(rnd.uniform(80, 2600), 2)
        R[k("库存金额")] = round(R[k("库存数量")] * R[k("标准成本")], 2)
    if k("信用额度") and k("已用额度") and k("可用额度"):
        R[k("已用额度")] = round(R[k("信用额度")] * 0.63, 2)
        R[k("可用额度")] = round(R[k("信用额度")] - R[k("已用额度")], 2)
    if k("税率"):
        R[k("税率")] = 13
        fi["税率"]["unit"] = "%"

    code_f = next((f for f in spec["fields"] if f["type"] == "code"), {"key": "code", "name": "编码", "type": "code"})
    name_f = next((f for f in spec["fields"] if f["type"] == "text"), code_f)
    kv_fields = [f for f in spec["fields"] if not _is_status_field(f, states) and f["type"] != "attachment"]
    half = (len(kv_fields) + 1) // 2

    def kv(group):
        return "".join('<div><div class="kv-label">%s</div><div class="kv-value">%s</div></div>'
                       % (f["name"], _fmt_val(f, R.get(f["key"]))) for f in group)
    base_pane = ('<div class="grp"><div class="grp-h">基础信息</div><div class="kv-grid">%s</div></div>'
                 '<div class="grp"><div class="grp-h">业务参数</div><div class="kv-grid">%s</div></div>'
                 % (kv(kv_fields[:half]), kv(kv_fields[half:])))

    tabs = list(rec.get("detailTabs") or ["基本信息", "业务明细", "关联单据", "变更记录"])
    if "关联单据" not in tabs:
        tabs.insert(len(tabs) - 1 if "变更记录" in tabs else len(tabs), "关联单据")
    if "变更记录" not in tabs:
        tabs.append("变更记录")

    sub_tables, bar, panes = [], [], []
    related_cols = [("单据类型", "text"), ("单据编号", "text"), ("单据日期", "date"), ("经办人", "person"), ("金额", "money")]
    for i, t in enumerate(tabs):
        tid = "log" if t == "变更记录" else ("t%d" % i)
        if t == "基本信息":
            pane, n = base_pane, ""
        elif t == "变更记录":
            pane, n = '<div class="detail-pad"><ul class="tl" id="changeLog"></ul></div>', '<span class="n">0</span>'
        else:
            raw = related_cols if t == "关联单据" else SUBTABLES.get(t, GENERIC_SUB)
            cols = _sub_cols(raw)
            rows = _sub_rows(raw, 5, rnd, i)
            if t == "关联单据":
                for j, r in enumerate(rows):
                    r["c0"] = ["销售订单", "采购订单", "入库单", "出库单", "质检单"][j % 5]
                    r["c1"] = ["SO", "PO", "RK", "CK", "QC"][j % 5] + "-2026-%04d" % (120 + j * 13)
            sub_tables.append({"id": tid, "columns": cols, "rows": rows})
            head = "".join('<th class="%s">%s</th>' % ("r" if c["align"] == "right" else ("c" if c["align"] == "center" else ""), c["name"]) for c in cols)
            pane = ('<div class="tbl-wrap"><table><thead><tr>%s</tr></thead><tbody id="tb-%s"></tbody></table></div>'
                    % (head, tid))
            n = '<span class="n">%d</span>' % len(rows)
        on = " on" if i == 0 else ""
        bar.append('<button class="dtab%s" role="tab" data-tab="%s" aria-selected="%s">%s%s</button>'
                   % (on, tid, "true" if i == 0 else "false", t, n))
        panes.append('<div class="pane%s" data-pane="%s">%s</div>' % (on, tid, pane))

    # 变更记录：必须是字段级 旧值 → 新值
    money_f = next((f for f in kv_fields if f["type"] == "money"), None)
    num_f = next((f for f in kv_fields if f["type"] == "number"), None)
    log = []
    if money_f:
        v = R.get(money_f["key"]) or 0
        log.append({"field": money_f["name"], "old": fmt_money(round(v * 0.9, 2)), "new": fmt_money(v), "memo": "年度价格复核"})
    if num_f:
        v = R.get(num_f["key"]) or 0
        log.append({"field": num_f["name"], "old": fmt_num(max(v - 20, 0)), "new": fmt_num(v), "memo": "按近三月用量调整"})
    log.append({"field": name_f["name"], "old": str(R.get(name_f["key"], "—"))[:6] + "（旧称）",
                "new": str(R.get(name_f["key"], "—")), "memo": "规范命名"})
    log.append({"field": "状态", "old": "草稿", "new": R["status"], "memo": "资料审核通过"})
    for i, c in enumerate(log):
        d = date(2026, 9, 17) - timedelta(days=3 + i * 19)
        c["time"] = "%s %02d:%02d" % (d.isoformat(), 9 + i, 12 + i * 7)
        c["who"] = POOLS["people"][(i + 1) % len(POOLS["people"])]

    # 摘要条：编码 / 名称 / 状态 + 最多 3 个数值指标
    summary = [dict(code_f), dict(name_f), {"key": "status", "name": "状态", "type": "status"}]
    summary += [dict(f) for f in kv_fields if f["type"] in ("money", "number")][:3]

    # 动作：来自状态机转移；破坏性动作（停用/冻结/终止）标 danger
    acts = []
    for t in sm.get("transitions", []):
        hit = next((a for a in acts if a["act"] == t["action"]), None)
        if hit:
            hit["states"].append(t["from"])
            continue
        acts.append({"act": t["action"], "label": t["action"], "to": t["to"], "states": [t["from"]],
                     "danger": any(w in t["action"] for w in ("停用", "冻结", "终止", "作废", "暂停"))})
    if not acts:
        acts = [{"act": "停用", "label": "停用", "to": "停用", "states": [R["status"]], "danger": True},
                {"act": "启用", "label": "启用", "to": R["status"], "states": ["停用"]}]
        tone.setdefault("停用", "neutral")
    acts.append({"act": "编辑", "label": "编辑", "primary": True})

    model = {
        "archetype": "A3", "record": R, "summary": summary, "statusKey": "status",
        "codeKey": code_f["key"], "nameKey": name_f["key"], "subTables": sub_tables,
        "changeLog": log, "actionDefs": acts, "toneOf": tone,
        "now": "2026-09-17 10:05", "user": "张明",
    }
    body = fill(load_skeleton("A3.html"), {
        "PAGE_NAME": "%s · %s" % (spec["page"]["name"], R.get(name_f["key"], "")),
        "PAGE_SUB": "加载中…", "HEAD_ACTIONS": "",
        "TAB_BAR": "".join(bar), "TAB_PANES": "".join(panes),
    })
    return body, model



# ---------------- A4 · 运营看板页 ----------------
RANGES = [("本月", "2026-09-01 ~ 2026-09-17", 1.0, "日", 17),
          ("本季", "2026-07-01 ~ 2026-09-17", 3.1, "周", 12),
          ("本年", "2026-01-01 ~ 2026-09-17", 11.6, "月", 12)]
TONE_COLOR = {"ok": "var(--success)", "warn": "var(--warning)", "danger": "var(--danger)",
              "purple": "var(--purple)", "brand": "var(--brand)", "neutral": "var(--text-3)"}


def build_a4(spec):
    rnd = random.Random(_stable_seed(spec["page"]["name"], "a4"))
    ent = spec.get("entity", {}).get("name", "业务")
    tone = status_tone_map(spec)
    states = [s["name"] for s in (spec.get("statusMachine") or {}).get("states", [])] or ["正常", "预警", "异常"]
    for s, t in zip(states, ["ok", "warn", "danger", "purple"]):
        tone.setdefault(s, t)
    money_fs = [f for f in spec["fields"] if f["type"] == "money"]
    num_fs = [f for f in spec["fields"] if f["type"] == "number"]
    # KPI 4 张起步：总额 / 总量 / 预警数 / 异常占比
    base_kpis = [
        {"name": (money_fs[0]["name"] if money_fs else ent + "金额") + "合计", "type": "money", "base": 8_600_000},
        {"name": (num_fs[0]["name"] if num_fs else ent + "数量") + "合计", "type": "number", "base": 46_000, "unit": "件"},
        {"name": "%s%s数" % (ent, states[1] if len(states) > 1 else "异常"), "type": "number", "base": 38, "unit": "项"},
        {"name": "%s占比" % states[-1], "type": "percent", "base": 6.4},
    ]
    colors = ["var(--brand)", "var(--success)", "var(--warning)", "var(--danger)"]

    # A dashboard's warning rows expose the full declared inventory record;
    # otherwise recipe fields outside listColumns silently disappear.
    warning_spec = dict(spec, columns=[{"field": f["name"]} for f in spec["fields"]])
    cols = build_columns(warning_spec)
    all_rows = gen_rows(spec, 18)
    good = states[0]
    ranges = {}
    for ri, (rname, period, scale, unit, npts) in enumerate(RANGES):
        kpis = []
        for ki, k in enumerate(base_kpis):
            v = k["base"] * (scale if k["type"] != "percent" else 1) * rnd.uniform(0.92, 1.08)
            kpis.append({"name": k["name"], "type": k["type"], "unit": k.get("unit", ""),
                         "value": round(v, 2 if k["type"] in ("money", "percent") else 0),
                         "delta": round(rnd.uniform(-12, 15), 1), "color": colors[ki]})
        if unit == "日":
            labels = ["%d日" % (d + 1) for d in range(npts)]
        elif unit == "周":
            labels = ["W%d" % (27 + w) for w in range(npts)]
        else:
            labels = ["%d月" % (m + 1) for m in range(npts)]
        lv = 260_000 * scale / (npts / 12.0)
        trend = [{"x": lb, "v": round(lv * (0.8 + 0.35 * rnd.random() + i * 0.012), 0)} for i, lb in enumerate(labels)]
        share = [{"name": s, "v": round(rnd.uniform(5, 60) if i else rnd.uniform(55, 80), 1),
                  "color": TONE_COLOR.get(tone.get(s, "neutral"))} for i, s in enumerate(states)]
        warn = [r for r in all_rows if r.get("status") and r["status"] != good][: 4 + ri * 2]
        ranges[rname] = {"period": period, "kpis": kpis, "trend": trend, "share": share, "warn": warn}

    model = {"archetype": "A4", "ranges": ranges, "warnColumns": cols, "toneOf": tone,
             "trendUnit": "元", "asOf": "2026-09-17 09:30"}
    warn_head = "".join('<th class="%s">%s</th>' % ("r" if c["align"] == "right" else ("c" if c["align"] == "center" else ""), c["name"])
                        for c in cols) + '<th class="c">操作</th>'
    body = fill(load_skeleton("A4.html"), {
        "PAGE_NAME": spec["page"]["name"], "PAGE_SUB": "加载中…",
        "RANGE_OPTIONS": "".join("<option>%s</option>" % r[0] for r in RANGES),
        "HEAD_ACTIONS": '<button class="btn" data-head="导出">导出</button>',
        "TREND_TITLE": "%s金额趋势" % ent, "SHARE_TITLE": "%s状态构成" % ent,
        "WARN_TITLE": "预警清单（非「%s」状态）" % good,
        "WARN_HEAD": warn_head,
        "CALIBER": "口径说明：金额为含税金额，单位元；环比 = (本期 − 上一同长度周期) ÷ 上一同长度周期；"
                   "涨用红色、跌用绿色（中国习惯）；演示数据为虚构。",
    })
    return body, model



# ---------------- A6 · 台账 / 对账页 ----------------
def build_a6(spec):
    rnd = random.Random(_stable_seed(spec["page"]["name"], "a6"))
    tone = status_tone_map(spec)
    for s, t in (("待确认", "warn"), ("已确认", "ok"), ("有差异", "danger")):
        tone.setdefault(s, t)
    partners = POOLS["customer"][:3]
    periods = ["2026-07", "2026-08", "2026-09"]          # 按时间正序算，期初承接上期期末
    memos = [("销售出库", "CK"), ("销售回款", "SK"), ("销售出库", "CK"), ("开具发票", "FP"),
             ("销售退货", "TH"), ("销售回款", "SK"), ("销售出库", "CK"), ("预收冲销", "YS")]
    sets = {}
    for pi, p in enumerate(partners):
        opening = round(rnd.uniform(120_000, 480_000), 2)
        for qi, per in enumerate(periods):
            lines = []
            for i in range(7 + (pi + qi) % 3):
                memo, pre = memos[i % len(memos)]
                rec = round(rnd.uniform(18_000, 160_000), 2) if pre in ("CK", "FP") else 0.0
                got = round(rnd.uniform(20_000, 150_000), 2) if pre in ("SK", "YS") else 0.0
                if pre == "TH":
                    rec = -round(rnd.uniform(2_000, 12_000), 2)
                lines.append({"date": "%s-%02d" % (per, 2 + i * 3),
                              "code": "%s-%s-%04d" % (pre, per.replace("-", ""), 11 + i + pi * 20),
                              "memo": memo, "receivable": rec, "received": got, "diff": 0})
            closing = round(opening + sum(l["receivable"] - l["received"] for l in lines), 2)
            current = (qi == len(periods) - 1)
            # 当前期间、第 1/3 个往来单位制造一笔差异：对方未记一笔回款（在途）
            if current and pi != 1:
                hit = next(l for l in lines if l["received"])
                hit["diff"] = round(hit["received"] * 0.1, 2)
            partner_bal = round(closing + sum(l["diff"] for l in lines), 2)
            sets["%s|%s" % (p, per)] = {"opening": opening, "lines": lines, "partnerBalance": partner_bal,
                                        "code": _code(spec, pi * len(periods) + qi, "对账单号"),
                                        "status": "待确认" if current else "已确认"}
            opening = closing

    model = {"archetype": "A6", "sets": sets, "toneOf": tone}
    head = ('<th class="c">日期</th><th>单号</th><th>摘要</th>'
            '<th class="r">应收</th><th class="r">已收</th><th class="r">累计余额</th>')
    body = fill(load_skeleton("A6.html"), {
        "PAGE_NAME": spec["page"]["name"], "PAGE_SUB": "加载中…",
        "PARTNER_LABEL": "往来单位",
        "PARTNER_OPTIONS": "".join("<option>%s</option>" % p for p in partners),
        "PERIOD_OPTIONS": "".join("<option>%s</option>" % p for p in reversed(periods)),
        "LEDGER_HEAD": head,
    })
    # Statement identity and confirmation state belong to the current partner /
    # period, and must refresh with the ledger rather than remain hidden in JSON.
    body = body.replace("' · 状态 ' + F.tag(st,", "' · 对账单号 ' + S.code + ' · 确认状态 ' + F.tag(st,")
    body = body.replace('<button class="btn btn-p" id="__confirm">确认对账</button>',
                        '<button class="btn btn-p" id="__confirm">确认对账</button>'
                        '<button class="btn" id="__resolve" style="display:none">差异已处理</button>')
    body = body.replace("    S._diff = diffAmt;", "    F.$('#__resolve').style.display = S.status === '有差异' ? '' : 'none';\n    S._diff = diffAmt;")
    resolution = """
  F.$('#__resolve').onclick = function () {
    var S = M.sets[key()];
    if (S.status !== '有差异') { return; }
    F.modal({ title: '差异已处理',
      note: '请先完成线下核实。此演示会将对方确认余额调整为账面余额，并完成本期对账；原明细金额保持不变。',
      fields: [{ key: 'memo', label: '差异处理说明', type: 'textarea', required: true }],
      confirmText: '确认差异已处理' }).then(function (v) {
        if (!v) { return; }
        S.resolutionMemo = v.memo;
        S.partnerBalance = Math.round((S.opening + S.lines.reduce(function (sum, row) {
          return sum + row.receivable - row.received;
        }, 0)) * 100) / 100;
        S.lines.forEach(function (row) { row.diff = 0; });
        S.status = '已确认'; render(); F.toast('差异已处理，对账已确认（演示）');
      });
  };
"""
    body = body.replace("  pSel.onchange = render;", resolution + "\n  pSel.onchange = render;")
    return body, model


BUILDERS = {"A1": build_a1, "A2": build_a2, "A3": build_a3, "A4": build_a4, "A5": build_a5, "A6": build_a6}


def _data_count(model):
    a = model.get("archetype")
    if a == "A3":
        return 1 + sum(len(t["rows"]) for t in model.get("subTables", []))
    if a == "A4":
        return sum(len(r["warn"]) + len(r["trend"]) for r in model.get("ranges", {}).values())
    if a == "A6":
        return sum(len(x["lines"]) for x in model.get("sets", {}).values())
    return len(model.get("rows", model.get("lines", [])))



ARCH_NAMES = {"A1": "管理列表页", "A2": "单据表单页", "A3": "主数据详情页",
              "A4": "运营看板页", "A5": "审批工作台", "A6": "台账对账页"}

PAGE_TEMPLATE = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s · 示例 ERP</title>
<script>
/* 错误兜底：把初始化阶段的脚本异常记进全局数组。
   验收探测页是在 iframe onload 之后才挂监听的，抓不到初始化期的异常 ——
   没有这段，页面渲染失败只会表现成"正文偏少 / 数据行 0"，看不清根因。 */
window.__PAGE_ERRORS__ = [];
window.addEventListener('error', function (e) { window.__PAGE_ERRORS__.push(String(e.message || e)); });
</script>
<!--
  原型：%(arch)s %(archName)s · ERP Page Forge 生成 · 零依赖 · 虚构演示数据
  依赖规范版本：skill %(skillVer)s（pageSpec %(specVer)s / archetypes %(archVer)s / recipes %(recVer)s / tokens %(tokVer)s）
  生成器：build/forge.py（确定性渲染，同一 PageSpec 两次生成结果一致）
%(tokenNote)s-->
<style>
%(tokensCss)s
%(baseCss)s
%(themeCss)s
</style>
</head>
<body>
<script>window.__MODEL__ = %(model)s;</script>
<script>
%(runtimeJs)s
</script>
<div class="layout">
  <aside class="side">
    <div class="brand"><span class="dot"></span>%(brand)s</div>
    %(nav)s
  </aside>

  <div class="main">
    <div class="top">
      <div class="crumb">%(crumb)s</div>
      <div class="who"><span class="avatar">%(avatar)s</span>%(userName)s · %(userRole)s</div>
    </div>
    <div class="body">
%(body)s
    </div>
    <div class="foot">%(disclaimer)s · 由 ERP Page Forge 生成（原型 %(arch)s）· 依赖规范版本 %(skillVer)s</div>
  </div>
</div>
</body>
</html>
'''


def shell(spec, body, model, tokens, version):
    tokens_css = render_tokens_css(tokens)
    base_css = load_skeleton("base.css")
    runtime = load_skeleton("runtime.js")
    nav = render_nav(spec)
    crumb = " / ".join(spec["page"].get("breadcrumb") or [spec["page"].get("module", ""), spec["page"]["name"]])
    brand = spec["page"].get("brand") or "示例 ERP"
    user_name = (spec.get("data") or {}).get("user") or "张明"
    user_role = (spec.get("data") or {}).get("userRole") or "业务员"
    # 视觉令牌被截图覆盖时，在产物头注释里留痕：评审能一眼看出「哪些值来自参考图」
    changes = spec.get("_tokenChanges") or []
    if changes:
        names = "、".join(c["token"] for c in changes[:8]) + ("…" if len(changes) > 8 else "")
        token_note = "  视觉令牌：按参考截图还原，覆盖 %d 项（%s）\n" % (len(changes), names)
    else:
        token_note = "  视觉令牌：Skill 默认令牌（本次无截图覆盖）\n"
    return PAGE_TEMPLATE % {
        "title": spec["page"]["name"],
        "arch": spec["archetype"],
        "archName": ARCH_NAMES.get(spec["archetype"], ""),
        "skillVer": version["skillVersion"],
        "specVer": version["compat"]["pageSpec"],
        "archVer": version["compat"]["archetypes"],
        "recVer": version["compat"]["recipes"],
        "tokVer": version["compat"]["tokens"],
        "tokensCss": tokens_css,
        "baseCss": base_css,
        "themeCss": spec.get("_themeCss") or "",
        "tokenNote": token_note + ("  主题：%s\n" % spec.get("theme", "default")),
        "nav": nav,
        "crumb": crumb,
        "brand": brand,
        "avatar": (user_name or "张")[0],
        "userName": user_name,
        "userRole": user_role,
        "crumbBlock": "",
        "body": body,
        "disclaimer": spec["data"]["disclaimer"],
        "model": json.dumps(model, ensure_ascii=False, separators=(",", ":")),
        "runtimeJs": runtime,
    }


def load_themes():
    p = os.path.join(SPEC_DIR, "themes.json")
    return _load(p) if os.path.exists(p) else {"themes": {"default": {}}}


def apply_theme(tokens, spec, theme_name):
    """主题预设：先铺主题令牌，再叠 spec.tokens。

    premium 下只保留 spec.tokens 里的主色（截图量到的品牌色），其余按主题走 ——
    否则截图的深色侧栏/硬阴影会把"高级感"冲掉。返回 (tokens, changes, theme_css)。
    """
    themes = load_themes()["themes"]
    if theme_name not in themes:
        raise SystemExit("[错误] 未知主题 %s；可选：%s" % (theme_name, "、".join(themes)))
    th = themes[theme_name] or {}
    changes = []
    if th.get("tokens"):
        tokens, ch = merge_tokens(tokens, th["tokens"])
        changes += [dict(c, source="theme:" + theme_name) for c in ch]
    override = spec.get("tokens")
    if override and th.get("tokens"):
        brand = ((override.get("values") or {}).get("color") or {}).get("brand")
        override = {"mode": "inherit-visual", "values": {"color": {"brand": brand}}} if brand else None
    tokens, ch = merge_tokens(tokens, override)
    changes += ch
    css = load_skeleton(th["css"]) if th.get("css") else ""
    return tokens, changes, css


def build_from_spec(spec, theme=None):
    recipes, tokens, version = load_recipes(), load_tokens(), load_version()
    spec = complete_spec(spec, recipes)
    theme = theme or spec.get("theme") or "default"
    spec["theme"] = theme
    # 视觉令牌：主题预设 → PageSpec.tokens（截图还原的注入点）
    tokens, token_changes, theme_css = apply_theme(tokens, spec, theme)
    spec["_tokenChanges"] = token_changes
    spec["_themeCss"] = theme_css
    arch = spec["archetype"]
    if arch not in BUILDERS:
        raise SystemExit(
            "[提示] 原型 %s 暂无确定性骨架（当前覆盖 %s）。\n"
            "       这不是失败，而是刻意的逃生舱：请让生成层回退到全量生成（见 SKILL.md 第 3 节），"
            "或先在 skill/skeletons/ 下补该原型的骨架。" % (arch, "/".join(SUPPORTED)))
    body, model = BUILDERS[arch](spec)
    return shell(spec, body, model, tokens, version), model, spec


def main():
    ap = argparse.ArgumentParser(description="ERP Page Forge · 确定性生成器")
    ap.add_argument("--spec", help="PageSpec JSON 路径")
    ap.add_argument("--entity", help="从配方快捷生成：实体 key（如 salesOrder）")
    ap.add_argument("--archetype", help="从配方快捷生成：原型编号（如 A1）")
    ap.add_argument("--out", help="输出 HTML 路径")
    ap.add_argument("--dump-model", action="store_true", help="只输出模型 JSON（调试）")
    ap.add_argument("--theme", help="主题预设：default / premium（见 skill/spec/themes.json）")
    args = ap.parse_args()

    if args.spec:
        spec = _load(args.spec)
    elif args.entity and args.archetype:
        recipes = load_recipes()
        if args.entity not in recipes["entities"]:
            raise SystemExit("[错误] 未知实体 %s；可选：%s" % (args.entity, "、".join(recipes["entities"])))
        r = recipes["entities"][args.entity]
        # 页面名随原型走：详情页叫「XX详情」、看板叫「XX看板」，不能一律「XX管理」
        pname = r["name"] + {"A3": "详情", "A4": "看板", "A6": ""}.get(args.archetype, "管理")
        spec = {
            "specVersion": "1.0",
            "page": {"name": pname, "module": r["name"], "breadcrumb": [r["name"], pname]},
            "archetype": args.archetype,
            "entity": {"name": r["name"], "recipeKey": args.entity,
                       "codeRule": r.get("codeRule", ""), "aliases": r.get("aliases", [])},
            "fields": [],
            "stack": {"target": "standalone"},
        }
    else:
        raise SystemExit("[错误] 需要 --spec，或 --entity + --archetype")

    html, model, spec = build_from_spec(spec, theme=args.theme)

    if args.dump_model:
        print(json.dumps(model, ensure_ascii=False, indent=2))
        return 0

    if not args.out:
        raise SystemExit("[错误] 需要 --out")
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)

    print("生成完成: %s" % out)
    print("  原型   : %s" % spec["archetype"])
    print("  页面   : %s" % spec["page"]["name"])
    print("  实体   : %s（配方命中: %s）" % (spec.get("entity", {}).get("name", "-"), spec.get("_recipeKey") or "通用骨架"))
    print("  字段   : %d 个 · 列 %d 个 · 数据 %d 行" % (
        len(spec.get("fields", [])), len(spec.get("columns", [])), _data_count(model)))
    print("  大小   : %.1f KB（零依赖单文件）" % (len(html.encode("utf-8")) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())

