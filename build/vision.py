#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""截图 → PageSpec（阶段二延伸：把反向工程入口从「已有页面」扩到「一张截图」）

分工是刻意分开的，这是这套方案能"准"的关键：

  ┌─ 多模态模型（看得懂）──→ 产出 extract.json：页面名、列、控件、动作、状态、置信度、存疑项
  └─ 本脚本（算得准）─────→ 校验提取结果 → 用 recipes 补全 → 落成合规 PageSpec（provenance=visual）

为什么不让模型直接画页面：那样每张图的结果都不一样，也没法验收。
把它压成 PageSpec 之后，生成走 forge.py（确定性）、验收走四类断言 + 视觉断言，
于是「识别 → 编译 → 准确」这条链路每一步都可复核。

本脚本还做**输入体检**：从像素里量出侧边栏宽度/顶栏高度/主色/深色占比，
若明显不像 ERP 后台（比如深色占比 <3%、量不到侧边栏），直接提示"这张图可能不是后台截图"，
而不是硬着头皮生成一个错的。

用法:
    # 只看输入体检（不需要 extract）
    python build/vision.py --image shot.png --check

    # 提取结果 → PageSpec
    python build/vision.py --image shot.png --extract extract.json --out spec.json

    # 一步到底：提取 → PageSpec → 确定性生成
    python build/vision.py --image shot.png --extract extract.json --out spec.json --forge out.html

退出码: 0 = 成功/体检通过, 1 = 失败或体检有硬性问题
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import forge                       # noqa: E402  复用 _camel / 配方补全
import verify_visual               # noqa: E402  复用像素特征
from reverse import guess_type     # noqa: E402  复用字段类型推断

SPEC_DIR = os.path.join(ROOT, "skill", "spec")


def _load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------
# 输入体检
# --------------------------------------------------------------------------

HEALTH = [
    # (维度, 判据, 不通过时的说明)
    # 注：这里不检查"深色占比"。侧栏是深色还是浅色都能还原（浅色侧栏的令牌会按亮度
    #     自动翻转文字色），把它当硬性门槛会把合法的浅色后台截图误判成"不是后台"。
    ("sidebar", lambda f: f.get("sidebarWidth") and 120 <= f["sidebarWidth"] <= 340,
     "量不到 120–340px 的左侧导航：这张图可能不是 ERP 后台截图，或是被裁过的局部"),
    ("topbar", lambda f: f.get("topbarHeight") and 28 <= f["topbarHeight"] <= 100,
     "量不到 28–100px 的顶部工具栏：可能是浏览器整页截图（含地址栏）或手机截图"),
    ("primary", lambda f: bool(f.get("primary")),
     "识别不出品牌主色：页面可能整页无强调色，或有明显色偏"),
]


def check_input(image):
    f = verify_visual.features(image)
    issues = []
    for name, ok, msg in HEALTH:
        if not ok(f):
            issues.append({"id": name, "message": msg})
    return f, issues


# --------------------------------------------------------------------------
# 提取结果校验
# --------------------------------------------------------------------------

def parse_code_pattern(pat):
    """从示例单号（如 RK-2026-100371）解析前缀、位宽与起始序号。

    编码格式是最容易被忽略、又最影响"像不像"的一项：
    单号从 RK-2026-100371 变成「示例-12」，业务人员一眼就出戏。
    """
    if not pat:
        return {}
    out = {}
    parts = str(pat).split("-")
    if parts and parts[0] and not parts[0].isdigit():
        out["codePrefix"] = parts[0]
    m = re.search(r"(\d{3,})\s*$", str(pat))
    if m:
        out["codeWidth"] = len(m.group(1))
        out["codeStart"] = int(m.group(1))
    return out


REQUIRED_KEYS = ["archetype"]
VALID_ARCH = ["A1", "A2", "A3", "A4", "A5", "A6", "freeform"]
MAX_COLS = 10          # SKILL 规范：列表页只放 6–10 列，其余进详情
VALID_REGION_ROLES = ["header", "query", "form", "table", "detail", "footer", "panel", "side", "title"]
VALID_CONTROLS = ["input", "select", "date", "date-range", "textarea", "number",
                  "money", "checkbox", "radio", "search", "upload", "person"]


def validate_extract(ex):
    errs, warns = [], []

    for k in REQUIRED_KEYS:
        if not ex.get(k):
            errs.append("缺少必填项 `%s`" % k)

    arch = ex.get("archetype")
    if arch and arch not in VALID_ARCH:
        errs.append("archetype `%s` 不在 A1–A6 之内" % arch)

    # 列头来源：新版 regions 的从表列头也算数（与 build_spec 的 raw_cols 口径一致）
    region_cols = []
    for rg in (ex.get("regions") or []):
        if rg.get("role") == "table":
            region_cols += [str(c).strip() for c in (rg.get("table") or {}).get("columns") or []]
    cols = ex.get("columns") or region_cols
    # columns 可缺省：识别不到表格/表头也照常生成，由生成器用配方与占位补全，只警告不拦截
    if not cols:
        warns.append("未识别到列头/字段：将按配方与占位生成，建议生成后核对")
    elif not (2 <= len(cols) <= MAX_COLS):
        warns.append("列数 %d 不在 2–%d 之间：列太多会在实际使用时看不全（规范建议 6–10 列）"
                     % (len(cols), MAX_COLS))

    for c in ex.get("queryControls") or []:
        if not c.get("label"):
            errs.append("查询条件缺 label")
        if c.get("control") not in (None, "input", "select", "date-range", "multi-select"):
            errs.append("查询控件 control `%s` 不合法" % c.get("control"))

    for a in ex.get("actions") or []:
        if a.get("scope") not in (None, "header", "row", "bulk", "form-footer", "panel-footer"):
            errs.append("动作 scope `%s` 不合法" % a.get("scope"))

    # 新版结构：regions（区块 + bbox）。校验 role/字段/表结构，但不强制 —— 旧 extract 仍可用。
    regions = ex.get("regions") or []
    if regions:
        for rg in regions:
            if rg.get("role") not in VALID_REGION_ROLES:
                errs.append("region `%s` 的 role `%s` 不合法" % (rg.get("id", "?"), rg.get("role")))
            for fld in rg.get("fields") or []:
                if not fld.get("label"):
                    errs.append("region `%s` 的字段缺 label" % rg.get("id", "?"))
                if fld.get("control") not in (None, *VALID_CONTROLS):
                    errs.append("region `%s` 字段 `%s` 的 control `%s` 不合法"
                                % (rg.get("id", "?"), fld.get("label"), fld.get("control")))
            tbl = rg.get("table") or {}
            if rg.get("role") == "table" and not tbl.get("columns"):
                errs.append("region `%s` 是 table 但缺 columns" % rg.get("id", "?"))

    # 置信度自检：低于阈值的项强制进 uncertain，逼着人确认而不是悄悄用
    conf = ex.get("confidence") or {}
    low = [k for k, v in conf.items() if isinstance(v, (int, float)) and v < 0.8]
    if low:
        warns.append("以下项置信度 <0.8，已标为待确认：%s" % "、".join(low))

    return errs, warns


# --------------------------------------------------------------------------
# 合成 PageSpec
# --------------------------------------------------------------------------

def build_spec(image, ex):
    recipes = _load(os.path.join(SPEC_DIR, "recipes.json"))
    feats, health = check_input(image)

    name = ex.get("pageName") or "未命名页面"
    module = ex.get("module") or name.replace("管理", "")
    crumb = ex.get("breadcrumb") or [module, name]
    ent_name = ex.get("entity") or name.replace("管理", "")

    # ① 先定配方。字段的 options / 精度 / 单位这些**截图里看不出来**，
    #    只能靠配方补 —— 不补的话枚举列会渲染成一堆「—」，页面看着像但内容是空的。
    rkey = ex.get("recipeKey")
    if not (rkey and rkey in recipes["entities"]):
        k, _r = forge.find_recipe(recipes, {"entity": {"name": ent_name, "aliases": [name]}})
        rkey = k
    recipe = recipes["entities"][rkey] if rkey else None
    rmap = {f["name"]: f for f in (recipe.get("fields") if recipe else [])}

    # ①' 编码格式：从示例单号（RK-2026-100371）解析前缀/位宽/起始号。
    #     不解析的话会退回通用的「示例-12」，一眼就不像。
    entity_info = {
        "name": ent_name,
        "recipeKey": rkey or "",
        "recipeRef": (recipe["name"] + "（按截图识别的实体命中配方）") if recipe else "待确认",
        "codeRule": (recipe.get("codeRule") or "") if recipe else "",
    }
    entity_info.update(parse_code_pattern(ex.get("codePattern")))
    if ex.get("codeRule"):
        entity_info["codeRule"] = ex["codeRule"]

    # ② 列顺序来自截图；字段定义优先用配方（带 options/精度/单位），没有才按列名推断
    enums = ex.get("enums") or {}
    ftypes = ex.get("fieldTypes") or {}
    status_names = [(s["name"] if isinstance(s, dict) else s) for s in (ex.get("statuses") or [])]

    # ②′ 新版结构化提取：从 regions 里把「表单区字段 / 从表列头 / 底部按钮」落位到
    #     A2 主从单据（表单 + 明细表 + 底部按钮条）的 fields/columns/actions。
    #     这是修复「出库单被当成列表页、字段全丢」的关键一步。
    regions = ex.get("regions") or []
    form_fields, table_cols, footer_actions = [], [], []
    for rg in regions:
        role = rg.get("role")
        if role == "form":
            for fld in rg.get("fields") or []:
                form_fields.append({
                    "label": fld.get("label", ""),
                    "control": fld.get("control") or "input",
                    "required": bool(fld.get("required")),
                    "options": fld.get("options") or [],
                })
        elif role == "table":
            table_cols = [str(c).strip() for c in (rg.get("table") or {}).get("columns") or []]
        elif role == "footer":
            for a in rg.get("actions") or []:
                footer_actions.append({"name": a.get("name"), "primary": bool(a.get("primary"))})

    columns, fields, seen = [], [], set()
    # 列头来源：新版 regions 里的从表列头优先；旧版平铺 columns 仍兼容。
    raw_cols = table_cols or ex.get("columns") or []
    for cn in raw_cols:
        cn = str(cn).strip()
        if not cn or cn in seen:
            continue
        seen.add(cn)
        base = dict(rmap.get(cn) or {"name": cn, "key": forge._camel(cn), "type": guess_type(cn)})
        # 类型以截图判断为准：「单据类型」光看列名会被当文本，
        # 但图里它的取值明显是有限集合（入库单/出库单），应当按枚举渲染。
        if ftypes.get(cn):
            base["type"] = ftypes[cn]
        # 枚举取值必须补全：没有 options 的枚举列会被渲染成一片「—」，
        # 这是"看着像、内容是空的"最常见成因（实测踩到过）。
        opts = enums.get(cn)
        if not opts and ("状态" in cn or ftypes.get(cn) == "status") and status_names:
            opts = status_names
        if opts:
            base["options"] = list(opts)
            if base.get("type") in (None, "text") and ftypes.get(cn) != "status":
                base["type"] = "enum"
        fields.append(dict(base, provenance="visual"))
        columns.append({"field": cn, "sortable": base.get("type") not in ("textarea",)})
    # 配方里还有、但截图没显示的字段：一并带上（详情抽屉/编辑表单要用），标 recipe
    for f in (recipe.get("fields") if recipe else []):
        if f["name"] not in seen:
            fields.append(dict(f, provenance="recipe"))

    # ②″ 表单区字段：把 regions 里 role=form 的「标签 + 控件」编译成 A2 的 headFields。
    #     control → 字段 type 映射，让 render_form_fields 渲染出对应控件（下拉/日期/金额/必填）。
    CONTROL_TYPE = {
        "select": "enum", "date": "date", "date-range": "date", "textarea": "textarea",
        "number": "number", "money": "money", "checkbox": "enum", "radio": "enum",
        "search": "text", "upload": "text", "person": "text", "input": "text",
    }
    for ff in form_fields:
        label = ff["label"].strip()
        if not label or label in seen:
            continue
        seen.add(label)
        # 优先命中配方（带 options/精度/单位），没有才用截图识别的控件类型
        base = dict(rmap.get(label) or {"name": label, "key": forge._camel(label)})
        ctrl_type = CONTROL_TYPE.get(ff["control"], "text")
        base["type"] = ctrl_type
        if ff.get("required"):
            base["required"] = True
        if ff.get("options"):
            base["options"] = list(ff["options"])
            base["type"] = "enum"
        fields.append(dict(base, provenance="visual"))
    # 表单字段不进 columns（列表列），只有从表/列表的列头才进 columns

    # ③ 查询条件：截图上的标签常与列名略有出入（如「订单号/客户名称」vs「订单号/客户」），
    #    必须归一到实际字段名，否则渲染时匹配不上会被整条丢掉。
    names = [f["name"] for f in fields]

    def norm(label):
        if not label:
            return label
        if label in names:
            return label
        for n in names:
            if label in n or n in label:
                return n
        best, score = label, 0
        for n in names:
            s = len(set(label) & set(n))
            if s > score:
                best, score = n, s
        return best if score >= 3 else label

    conds = [{"field": norm(c["label"]), "control": c.get("control") or "input"}
             for c in (ex.get("queryControls") or []) if c.get("label")]

    # ④ 状态机（截图里的状态胶囊）
    states = []
    for s in ex.get("statuses") or []:
        if isinstance(s, str):
            states.append({"name": s, "tone": "neutral"})
        else:
            states.append({"name": s["name"], "tone": s.get("tone", "neutral")})

    # ④′ 动作合并：底部按钮条（footer）用真实按钮文案 + primary 标记；
    #     旧版平铺 actions 仍兼容。若两者都空才回退「编辑」占位。
    _acts = [
        {"name": a["name"], "scope": a.get("scope", "row"),
         "allowedStates": a.get("allowedStates") or [],
         "primary": bool(a.get("primary"))}
        for a in (ex.get("actions") or [])
    ]
    for fa in footer_actions:
        _acts.append({"name": fa["name"], "scope": "form-footer",
                      "allowedStates": [], "primary": bool(fa.get("primary"))})

    spec = {
        "specVersion": "1.0",
        "_source": {
            "kind": "screenshot",
            "image": os.path.abspath(image),
            "extractedBy": "multimodal-model + build/vision.py",
            "note": "字段/列的 provenance 为 visual（从截图识别，非用户明确指定），确认后可直接用于生成",
        },
        "page": {"name": name, "module": module, "menu": ex.get("menu") or name,
                 "breadcrumb": crumb, "brand": ex.get("brand") or ""},
        "archetype": ex.get("archetype"),
        "entity": entity_info,
        "fields": fields,
        "columns": columns,
        "actions": _acts or [{"name": "编辑", "scope": "row", "allowedStates": []}],
        "data": {
            "minRows": ex.get("rowCount") or (12 if ex.get("archetype") == "A1" else 6),
            "currency": "¥", "dateFormat": "YYYY-MM-DD",
            "mustHaveException": True,
            "disclaimer": "演示用虚构数据，与任何真实企业无关",
            # 参考图右上角的登录人也要还原，否则顶栏一眼就"不是那个系统"
            "user": ex.get("user") or "", "userRole": ex.get("userRole") or "",
            "totalCount": ex.get("totalCount") or 0,
        },
        "stack": {"target": "standalone"},
        "custom": [],
    }
    if ex.get("nav"):
        spec["nav"] = ex["nav"]          # 侧边栏菜单（截图识别），无则用通用模块列表
    if conds:
        spec["query"] = {"conditions": conds}
    if states:
        spec["statusMachine"] = {"states": states, "initial": states[0]["name"], "transitions": []}

    # ④″ regions 结构化证据：把 VLM 识别的区块（含 bbox）原样存进诊断层，
    #     供像素对齐 / 人工复核用。若含 from 区 + table 区，说明是「主从单据」，
    #     已在 fields/columns 上落位（见 ②′ ②″），此处仅留证据。
    if regions:
        spec["_regionsEvidence"] = [
            {"id": rg.get("id"), "role": rg.get("role"), "title": rg.get("title", ""),
             "bbox": rg.get("bbox"), "fieldCount": len(rg.get("fields") or []),
             "tableCols": (rg.get("table") or {}).get("columns")}
            for rg in regions
        ]

    # 配方命中信息已在 ① 处写入 spec["entity"]，不再重复推断
    # ⑥ 视觉令牌：把从像素量到的配色与尺寸注入生成层。
    #    不做这一步，产物永远套 Skill 默认蓝 —— 内容识别得再准也"不像"。
    #    这是「还原」与「识别」的分界：识别解决"有什么"，令牌解决"长什么样"。
    vt = verify_visual.visual_tokens(image)
    spec["tokens"] = vt["tokens"]

    diag = {
        "imageFeatures": feats,
        "inputHealth": {"ok": not health, "issues": health},
        "visualTokens": {"measured": vt["measured"], "coverage": vt["coverage"]},
        "confidence": ex.get("confidence") or {},
        "uncertain": list(ex.get("uncertain") or []) + [
            k for k, v in (ex.get("confidence") or {}).items() if isinstance(v, (int, float)) and v < 0.8
        ],
        "hint": "字段与列来自截图识别（provenance=visual）。不确定项请人工确认后再生成；"
                "确认后本文件即为该页面的唯一生成输入。",
    }
    spec["_diagnostics"] = diag
    return spec, diag


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="截图 → PageSpec（多模态提取 + 脚本校验）")
    ap.add_argument("--image", required=True, help="截图路径")
    ap.add_argument("--extract", help="模型产出的视觉提取结果 JSON")
    ap.add_argument("--out", help="输出 PageSpec JSON")
    ap.add_argument("--forge", dest="forge_out", help="顺便确定性生成到该 HTML")
    ap.add_argument("--check", action="store_true", help="只做输入体检")
    args = ap.parse_args()

    if not os.path.exists(args.image):
        raise SystemExit("[错误] 截图不存在: %s" % args.image)

    if args.check:
        feats, issues = check_input(args.image)
        vt = verify_visual.visual_tokens(args.image)
        measured = vt["measured"]
        tone = vt["coverage"]["sidebarTone"]
        print("输入体检：%s" % ("通过" if not issues else "有问题"))
        print("  画布 %dx%d · 侧栏 %s 宽 %spx · 顶栏 %spx · 主色 %s · 深色占比 %.1f%%" % (
            feats["canvas"]["w"], feats["canvas"]["h"], measured.get("sidebarHex"),
            feats["sidebarWidth"], feats["topbarHeight"], feats["primaryHex"],
            feats["darkRatio"] * 100))
        print("  可注入令牌：页底 %s · 卡片 %s · 侧栏色调 %s（%s）" % (
            measured.get("pageBgHex"), measured.get("cardHex"), tone,
            "浅色侧栏 → 侧栏文字自动翻转为深字" if tone == "light" else "深色侧栏 → 侧栏文字用浅字"))
        for i in issues:
            print("  ★ %s：%s" % (i["id"], i["message"]))
        if issues:
            print("  → 这张图可能超出可还原范围；仍要生成请显式确认，不要硬猜。")
        return 0 if not issues else 1

    if not args.extract:
        raise SystemExit("[错误] 需要 --extract（模型产出的视觉提取结果），或只加 --check 做体检")

    ex = _load(args.extract)
    errs, warns = validate_extract(ex)
    if errs:
        print("提取结果校验失败：", file=sys.stderr)
        for e in errs:
            print("  ★ " + e, file=sys.stderr)
        return 1

    spec, diag = build_spec(args.image, ex)
    out = json.dumps(spec, ensure_ascii=False, indent=2)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out + "\n")
        print("PageSpec 已生成: %s" % os.path.abspath(args.out))
    else:
        print(out)

    f = diag["imageFeatures"]
    print("\n识别摘要：", file=sys.stderr)
    print("  页面   : %s（原型 %s）" % (spec["page"]["name"], spec["archetype"]), file=sys.stderr)
    print("  列 %d 个 · 字段 %d 个 · 动作 %d 个 · 状态 %d 个" % (
        len(spec["columns"]), len(spec["fields"]), len(spec["actions"]),
        len((spec.get("statusMachine") or {}).get("states", []))), file=sys.stderr)
    print("  配方   : %s" % spec["entity"].get("recipeRef"), file=sys.stderr)
    print("  像素锚点: 侧栏 %spx · 顶栏 %spx · 主色 %s" % (
        f["sidebarWidth"], f["topbarHeight"], f["primaryHex"]), file=sys.stderr)

    vt = diag.get("visualTokens") or {}
    measured = vt.get("measured") or {}
    cov = vt.get("coverage") or {}
    ntok = sum(len(v) for v in (spec.get("tokens") or {}).get("values", {}).values())
    if ntok:
        print("  视觉令牌: 注入 %d 项（主色 %s · 侧栏 %s/%spx · 顶栏 %spx · 页底 %s · 卡片 %s）" % (
            ntok, measured.get("brandHex"), measured.get("sidebarHex"), measured.get("sidebarWidth"),
            measured.get("topbarHeight"), measured.get("pageBgHex"), measured.get("cardHex")),
            file=sys.stderr)
        if cov.get("sidebarTone") == "light":
            print("          侧栏是浅色 → 侧栏文字与边框已按亮度自动翻转", file=sys.stderr)
    for item in cov.get("cannotReproduce") or []:
        print("          不可从像素推断: %s（%s）" % (item["name"], item["why"]), file=sys.stderr)
    if diag["uncertain"]:
        print("  ⚠ 待人工确认: %s" % "、".join(sorted(set(diag["uncertain"]))), file=sys.stderr)
    for w in warns:
        print("  ⚠ " + w, file=sys.stderr)

    if args.forge_out:
        html, model, _ = forge.build_from_spec(spec)
        dst = os.path.abspath(args.forge_out)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(html)
        print("已生成页面: %s（%.1f KB）" % (dst, len(html.encode("utf-8")) / 1024.0))

    return 0


if __name__ == "__main__":
    sys.exit(main())
