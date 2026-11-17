#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ERP Page Forge · 反向工程入口（阶段二）

从一个**已有页面**抽出它的 PageSpec —— 让用户不必从零描述，只要说
「照这个做，字段换成我的」。这是三条入口里最被低估的一条，
因为它正好接上"示例会被大家维护"这个现实：社区里已有的页面就是最好的模板源。

抽取的是**结构与交互契约**（原型、列、字段、状态、动作），不是像素级还原。
抽出的 PageSpec 可直接喂给 build/forge.py 再生成一份新的。

用法:
    python build/reverse.py --artifact templates/05-approval-inbox.html
    python build/reverse.py --artifact <页面> --out build/reversed.pagespec.json
    python build/reverse.py --artifact <页面> --rename 出入库单据 --out new.json   # 换名称保留结构

退出码: 0 = 成功
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SPEC_DIR = os.path.join(ROOT, "skill", "spec")

TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def text_of(html_fragment):
    return WS.sub(" ", TAG.sub(" ", html_fragment)).strip()


def is_noise(s):
    """过滤模板字符串残留与纯符号 —— 抽出的必须是真实业务文案。"""
    if not s:
        return True
    if "${" in s or "{{" in s or "}}" in s:
        return True
    return not re.search(r"[\u4e00-\u9fa5A-Za-z]", s)


def read_artifact(path):
    if os.path.isdir(path):
        parts = []
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [d for d in dirnames if d not in ("node_modules", "dist", "build", ".git")]
            for fn in sorted(filenames):
                if os.path.splitext(fn)[1].lower() in (".html", ".jsx", ".tsx", ".vue", ".js", ".ts", ".css"):
                    try:
                        with open(os.path.join(dirpath, fn), encoding="utf-8", errors="replace") as f:
                            parts.append(f.read())
                    except OSError:
                        pass
        return "\n".join(parts), path
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read(), path


# --------------------------------------------------------------------------
# 原型推断
# --------------------------------------------------------------------------

ARCH_SIGNS = [
    ("A5", ["审批轨迹", "待办"], ["三栏", "cols"]),
    ("A4", ["环比", "同比", "kpi", "趋势"], ["kpi"]),
    ("A6", ["期初", "期末余额", "对账期间"], ["@media print"]),
    ("A3", ["变更记录", "变更轨迹", "基本信息"], ["tab"]),
    ("A2", ["暂存", "添加行", "明细信息"], ["foot-bar"]),
    ("A1", ["查询", "重置", "分页", "共"], ["pager"]),
]


def guess_archetype(html):
    low = html.lower()
    scores = {}
    for code, strong, weak in ARCH_SIGNS:
        s = sum(2 for kw in strong if kw.lower() in low) + sum(1 for kw in weak if kw.lower() in low)
        scores[code] = s
    best = max(scores.items(), key=lambda kv: kv[1])
    return (best[0] if best[1] > 0 else "A1"), scores


# --------------------------------------------------------------------------
# 结构抽取
# --------------------------------------------------------------------------

FIELD_TYPE_RULES = [
    (r"金额|单价|成本|额度|税额|余额|合计|毛利", "money"),
    (r"数量|库存|天数|进度|得分|率|时限|工龄|司龄|库龄|逾期", "number"),
    (r"日期|时间|交期|到期", "date"),
    (r"状态|类型|等级|类别|性质|范围|方式|程度", "enum"),
    (r"编码|编号|单号|工号|账号|证号|码$", "code"),
    (r"人|员|负责人|申请人|业务员|经理|提交人", "person"),
    (r"部门|组织|车间|仓$|工厂", "org"),
    (r"备注|说明|内容|原因|摘要", "textarea"),
]


def guess_type(name):
    for pat, t in FIELD_TYPE_RULES:
        if re.search(pat, name):
            return t
    return "text"


def slug(name):
    presets = {
        "订单号": "code", "客户名称": "customer", "订单日期": "orderDate", "约定交期": "dueDate",
        "订单类型": "type", "业务员": "owner", "订单金额": "amount", "订单状态": "status",
        "许可证号": "licenseNo", "许可证类型": "licenseType", "工厂": "plant", "类别": "category",
        "功能位置": "funcLoc", "修改内容": "changeContent", "修改节点": "changeNode",
        "物料编码": "matCode", "物料名称": "matName", "状态": "status",
    }
    # 旧写法取 hex 前 6 位 = 只取了第一个汉字的 3 字节 → 「客户编码/客户类型/客户状态」全撞成 f_e5aea2，
    # 多列读同一个 key 会出现整列为空。改为对完整名称做摘要。
    import hashlib
    return presets.get(name) or ("f_" + hashlib.md5(re.sub(r"\W", "", name).encode("utf-8")).hexdigest()[:8])


def extract(page_path, html):
    # 反抽静态标记，不能把脚本模板字符串或样式内容当成实际字段/按钮。
    html = re.sub(r"<(script|style)\b[^>]*>[\s\S]*?</\1\s*>", "", html, flags=re.I)
    spec = {"specVersion": "1.0", "_source": {"artifact": os.path.abspath(page_path), "_note": "由 build/reverse.py 反向工程抽取；字段类型与原型为推断结果，需人工确认"}}

    title = None
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if m:
        title = text_of(m.group(1)).split("·")[0].strip()
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
    h1 = text_of(m.group(1)) if m else ""
    name = h1 or title or "未命名页面"

    crumb = []
    m = re.search(r'class="[^"]*\bcrumb\b[^"]*"[^>]*>(.*?)</div>', html, re.S | re.I)
    if m:
        crumb = [c.strip() for c in re.split(r"[/>]", text_of(m.group(1))) if c.strip()]
    module = crumb[0] if crumb else name.replace("管理", "").replace("审批", "")

    arch, arch_scores = guess_archetype(html)

    # 列：**只取第一个表格**的表头。页面里常还有抽屉/弹层里的明细表，
    # 把它们一起当列会让列表页凭空多出十几个列。
    tbl = re.search(r"<table[^>]*>(.*?)</table>", html, re.S | re.I)
    scope = tbl.group(1) if tbl else html
    heads = [text_of(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", scope, re.S | re.I)]
    heads = [re.sub(r"[▲▼*]", "", h).strip() for h in heads]
    # "订单号 / 客户" 这类合并表头拆成两列
    expanded = []
    for h in heads:
        for part in re.split(r"\s*/\s*", h):
            part = part.strip()
            if part and part not in ("操作", "序号", "全选"):
                expanded.append(part)
    seen, columns, fields = set(), [], []
    for h in expanded:
        if h in seen or is_noise(h) or len(h) > 16:
            continue
        seen.add(h)
        t = guess_type(h)
        columns.append({"field": h, "sortable": True})
        fields.append({"name": h, "key": slug(h), "type": t, "provenance": "inferred"})

    # 表单标签补充字段
    labels = [text_of(l) for l in re.findall(r"<label[^>]*>(.*?)</label>", html, re.S | re.I)]
    for l in labels:
        l = re.sub(r"[*：:]", "", l).strip()
        if not l or l in seen or len(l) > 18 or is_noise(l):
            continue
        seen.add(l)
        fields.append({"name": l, "key": slug(l), "type": guess_type(l), "provenance": "inferred"})

    # 动作：按钮（按语义归落点，查询/重置这类过滤条件不算业务动作）
    btns = [text_of(b) for b in re.findall(r"<button[^>]*>(.*?)</button>", html, re.S | re.I)]
    acts, seen_a = [], set()
    for b in btns:
        b = b.strip()
        if not b or b in seen_a or is_noise(b) or len(b) > 12:
            continue
        if re.search(r"取消|关闭|刷新|上一页|下一页|清空|跳至", b) or re.match(r"^\d+$", b):
            continue
        if re.search(r"^查询$|^重置$|^搜索$", b):
            continue
        seen_a.add(b)
        if re.search(r"批量", b):
            scope_ = "bulk"
        elif re.search(r"导出|导入|新建|打印|快照|导入明细", b):
            scope_ = "header"
        else:
            scope_ = "row"
        acts.append({"name": b, "scope": scope_, "allowedStates": []})

    # 状态：胶囊标签文案
    tags = [text_of(t) for t in re.findall(r'<span class="[^"]*\btag\b[^"]*"[^>]*>(.*?)</span>', html, re.S | re.I)]
    tags = [re.sub(r"[^\u4e00-\u9fa5A-Za-z]", "", t) for t in tags]
    states, seen_s = [], set()
    for t in tags:
        # 状态名必须是中文业务词；模板字符串残渣（如 rstatus）在这里被挡掉
        if not t or len(t) > 8 or t in seen_s or not re.search(r"[\u4e00-\u9fa5]", t):
            continue
        seen_s.add(t)
        states.append({"name": t, "tone": "neutral"})

    # 节点流：从"审批流：A → B"或时间线文案推
    nodes = []
    m = re.search(r"审批流[：:]\s*([^\n<]{6,160})", html)
    if m:
        nodes = [x.strip() for x in re.split(r"[→>]", text_of(m.group(1))) if x.strip()]
    if not nodes:
        tl = re.findall(r'<div class="t1">(.*?)</div>', html, re.S)
        nodes = [text_of(x) for x in tl if text_of(x)][:8]

    # 实体名
    ent = re.sub(r"(管理|列表|台账|工作台|审批|中心|看板|详情|页面|表)$", "", name) or name
    spec["page"] = {"name": name, "module": module, "menu": name, "breadcrumb": crumb or [module, name]}
    spec["archetype"] = arch
    spec["entity"] = {"name": ent, "recipeRef": "待确认（反向工程推断）", "codeRule": ""}
    spec["fields"] = fields
    spec["columns"] = columns
    spec["actions"] = acts or [{"name": "编辑", "scope": "row", "allowedStates": []}]
    if states:
        spec["statusMachine"] = {
            "states": states, "initial": states[0]["name"],
            "transitions": [{"from": states[i]["name"], "to": states[i + 1]["name"], "action": acts[0]["name"] if acts else "流转"}
                            for i in range(len(states) - 1)],
        }
        if nodes:
            spec["statusMachine"]["nodeFlow"] = nodes
    elif nodes:
        spec["statusMachine"] = {"states": [{"name": "待审批", "tone": "warn"}, {"name": "已通过", "tone": "ok", "terminal": True}],
                                 "initial": "待审批", "nodeFlow": nodes}
    spec["data"] = {"minRows": 12 if arch == "A1" else 6, "currency": "¥", "dateFormat": "YYYY-MM-DD",
                    "mustHaveException": True, "disclaimer": "演示用虚构数据，与任何真实企业无关"}
    spec["stack"] = {"target": "standalone"}
    spec["custom"] = []
    spec["_diagnostics"] = {
        "archetypeScores": arch_scores,
        "detectedColumns": len(columns),
        "detectedStates": [s["name"] for s in states],
        "detectedNodes": nodes,
        "hint": "列/字段/状态/动作均为推断；确认无误后可作为「照这个改字段」的起点喂给 build/forge.py",
    }
    return spec


def try_match_recipe(spec):
    """若抽出的列与某条配方高度重合，就把它标成命中配方 —— 省得用户再手填。"""
    try:
        recipes = _load(os.path.join(SPEC_DIR, "recipes.json"))
    except OSError:
        return
    cols = set(c["field"] for c in spec.get("columns", []))
    if not cols:
        return
    best, best_ratio = None, 0.0
    for key, r in recipes["entities"].items():
        rl = set(r.get("listColumns", []))
        if not rl:
            continue
        ratio = len(cols & rl) / float(len(cols))
        if ratio > best_ratio:
            best, best_ratio = key, ratio
    if best and best_ratio >= 0.5:
        spec["entity"]["recipeKey"] = best
        spec["entity"]["recipeRef"] = "%s（列重合度 %.0f%%）" % (recipes["entities"][best]["name"], best_ratio * 100)
        if not spec["entity"].get("codeRule"):
            spec["entity"]["codeRule"] = recipes["entities"][best].get("codeRule", "")


def main():
    ap = argparse.ArgumentParser(description="反向工程：从已有页面抽 PageSpec")
    ap.add_argument("--artifact", required=True, help="页面文件或目录")
    ap.add_argument("--out", help="输出 PageSpec JSON 路径（不传则打印）")
    ap.add_argument("--rename", help="换页面/实体名称，保留结构（『照这个做，字段换成我的』）")
    args = ap.parse_args()

    html, path = read_artifact(args.artifact)
    if not html.strip():
        raise SystemExit("[错误] 产物内容为空: %s" % args.artifact)

    spec = extract(path, html)
    try_match_recipe(spec)

    if args.rename:
        old = spec["page"]["name"]
        spec["page"]["name"] = args.rename
        spec["entity"]["name"] = args.rename
        if spec["page"].get("breadcrumb"):
            spec["page"]["breadcrumb"][-1] = args.rename
        spec["_source"]["renamedFrom"] = old

    out = json.dumps(spec, ensure_ascii=False, indent=2)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out + "\n")
        print("反向工程完成: %s" % os.path.abspath(args.out))
    else:
        print(out)

    d = spec["_diagnostics"]
    print("\n摘要：", file=sys.stderr)
    print("  页面   : %s（模块 %s）" % (spec["page"]["name"], spec["page"]["module"]), file=sys.stderr)
    print("  原型   : %s（推断分值 %s）" % (spec["archetype"], d["archetypeScores"]), file=sys.stderr)
    print("  列 %d 个 · 字段 %d 个 · 状态 %s" % (
        len(spec["columns"]), len(spec["fields"]),
        "、".join(d["detectedStates"]) or "未识别"), file=sys.stderr)
    print("  配方命中: %s" % spec["entity"].get("recipeRef", "-"), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
