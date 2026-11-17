#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FreeformSpec 校验器（自由版式扩展 · 契约层）。

手写的子集校验（不引 jsonschema 依赖，保持零依赖约定）：
只卡「会让生成器产出错页或假验收」的硬伤，其余交给生成与验收。
"""

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(HERE, "schema", "freeform.schema.json")

ROLES = ("nav", "header", "card", "text", "button", "input",
         "image", "illustration", "divider", "container")
KEEPS = ("rebuild", "crop")
CROP_ONLY = ("image", "illustration")          # 只允许位图裁切的角色
TEXT_ROLES = ("text", "button", "nav", "header", "input")  # 必须重建为真文字的角色
SLOT_RE = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _load_schema():
    with open(SCHEMA, encoding="utf-8") as f:
        return json.load(f)


def validate(spec):
    """校验 FreeformSpec。返回 (errs, warns)；errs 非空即不可生成。"""
    errs, warns = [], []

    def err(msg):
        errs.append(msg)

    def warn(msg):
        warns.append(msg)

    if not isinstance(spec, dict):
        return ["FreeformSpec 需为对象"], warns
    if spec.get("kind") != "freeform":
        err("kind 必须为 freeform（A1–A6 的 PageSpec 走 forge.py，不进本链路）")
    if spec.get("specVersion") != "ff-1.0":
        err("specVersion 必须为 ff-1.0")
    name = spec.get("name")
    if not (isinstance(name, str) and name.strip()):
        err("缺少 name（模板/页面名）")
    elif len(name) > 40:
        err("name 超过 40 字")

    canvas = spec.get("canvas") or {}
    for k in ("w", "h"):
        v = canvas.get(k)
        if not isinstance(v, int) or not 200 <= v <= 8000:
            err("canvas.%s 需为 200–8000 的整数，当前 %r" % (k, v))
    if canvas.get("bg") and not HEX_RE.match(canvas["bg"]):
        err("canvas.bg 需为 #RRGGBB")

    regions = spec.get("regions")
    if not isinstance(regions, list) or not regions:
        err("regions 需为非空数组")
        regions = []
    elif len(regions) > 120:
        err("regions 超过 120 个：自由版式按块表达，不是逐像素还原")

    ids, slots = set(), {}
    cw, ch = canvas.get("w") or 0, canvas.get("h") or 0
    for r in regions:
        rid = r.get("id") or "?"
        tag = "region[%s]" % rid
        if not ID_RE.match(str(r.get("id") or "")):
            err("%s id 需为 1–24 位字母/数字/_/-" % tag)
        elif rid in ids:
            err("%s id 重复" % tag)
        ids.add(rid)

        if r.get("role") not in ROLES:
            err("%s role `%s` 不在允许的 10 个取值里" % (tag, r.get("role")))
        if r.get("keep") not in KEEPS:
            err("%s keep 需为 rebuild / crop" % tag)
            continue
        if r["keep"] == "crop" and r.get("role") not in CROP_ONLY:
            err("%s（%s）不允许 crop：只有插画/logo/复杂装饰才能裁成位图，"
                "文字区块禁止 crop（否则文案断言失效）" % (tag, r.get("role")))
        if r["keep"] == "rebuild" and r.get("role") in TEXT_ROLES and not str(r.get("text") or "").strip():
            warn("%s 是文字区块但 text 为空，生成时会渲染为空白块" % tag)

        bbox = r.get("bbox")
        if (not isinstance(bbox, list) or len(bbox) != 4
                or not all(isinstance(v, int) for v in bbox)):
            err("%s bbox 需为 4 个整数的 [x0,y0,x1,y1]" % tag)
        else:
            x0, y0, x1, y1 = bbox
            if not (0 <= x0 < x1 and 0 <= y0 < y1):
                err("%s bbox 不满足 0≤x0<x1、0≤y0<y1" % tag)
            elif cw and ch and (x1 > cw or y1 > ch):
                err("%s bbox 超出画布 %dx%d" % (tag, cw, ch))
            elif (x1 - x0) * (y1 - y0) < 40:
                warn("%s 面积不足 40px²，可能是个噪点块" % tag)

        st = r.get("style") or {}
        for k in ("bg", "color"):
            if st.get(k) and not HEX_RE.match(st[k]):
                err("%s style.%s 需为 #RRGGBB" % (tag, k))
        fs = st.get("fontSize")
        if fs is not None and not (isinstance(fs, int) and 8 <= fs <= 200):
            err("%s style.fontSize 需为 8–200 整数" % tag)

        slot = r.get("slot")
        if slot is not None:
            if not SLOT_RE.match(slot):
                err("%s slot 名需为 1–24 位字母/数字/_/-" % tag)
            elif slot in slots:
                err("%s slot `%s` 重复" % (tag, slot))
            slots[slot] = rid

    sp_slots = spec.get("slots") or {}
    if not isinstance(sp_slots, dict):
        err("slots 需为对象")
        sp_slots = {}
    for k in sp_slots:
        if not SLOT_RE.match(str(k)):
            err("slots 键 `%s` 不合法" % k)
    for k in slots:
        if k not in sp_slots:
            warn("region 的 slot `%s` 在 slots 表中没有取值，将回用 region.text" % k)
    for k in sp_slots:
        if k not in slots:
            err("slots 表里的 `%s` 没有任何 region 引用它" % k)

    acc = spec.get("acceptance") or {}
    for t in (acc.get("requiredTexts") or []):
        if not isinstance(t, str) or not t.strip():
            err("acceptance.requiredTexts 含空项")

    # semantics（业务语义声明，方案 C）：声明了就照 A1–A6 同一标准验，先卡声明本身的硬伤
    sem = spec.get("semantics") or {}
    if not isinstance(sem, dict):
        sem = {}
        err("semantics 需为对象")
    itypes = ("scroll", "toggle", "confirms")
    for it in sem.get("interactions") or []:
        tag = "semantics.interactions[%s]" % it.get("id", "?")
        if it.get("type") not in itypes:
            err("%s type 需为 %s" % (tag, "/".join(itypes)))
        if it.get("trigger") not in ids:
            err("%s trigger `%s` 不存在（需是 regions 里的区块 id）" % (tag, it.get("trigger")))
        if it.get("type") in ("scroll", "toggle") and it.get("target") not in ids:
            err("%s target `%s` 不存在（需是 regions 里的区块 id）" % (tag, it.get("target")))
        if it.get("type") == "toggle" and it.get("target") == it.get("trigger"):
            err("%s toggle 的目标不能是触发器自己" % tag)
    for d in sem.get("data") or []:
        tag = "semantics.data[%s]" % d.get("id", "?")
        if d.get("type") != "count-equals":
            err("%s type 目前只支持 count-equals" % tag)
        for k in ("a", "b"):
            sel = d.get(k)
            if not (isinstance(sel, str) and sel.strip()):
                err("%s %s 需为非空 CSS 选择器" % (tag, k))
    for c in sem.get("contract") or []:
        tag = "semantics.contract[%s]" % c.get("id", "?")
        if c.get("kind") not in ("text", "absent", "regex", "regex_count"):
            err("%s kind 不合法" % tag)
        if c.get("kind") in ("regex", "regex_count") and not c.get("pattern"):
            err("%s regex 类断言缺 pattern" % tag)
        elif c.get("kind") in ("text", "absent") and not str(c.get("value") or "").strip():
            err("%s 缺 value" % tag)

    return errs, warns


# --------------------------------------------------------------------------
# 方案 C：语义自动识别（能认出的自动填并标 from=auto，认不出的列成待补清单）
# --------------------------------------------------------------------------

DESTRUCTIVE_RE = re.compile(r"删除|作废|停用|注销|终止|清空|撤回|作廢")


def auto_semantics(spec):
    """扫描 regions，返回 (proposals, todos)。

    proposals —— 可以直接写进 spec["semantics"] 的断言（from=auto，用户可在前端确认/删掉）；
    todos     —— 识别不了、需要用户补充的提示（如普通按钮点了会怎样）。
    只在「有把握」时自动填：危险操作按钮 → 必须弹确认；其余一律进 todos，不猜。
    """
    proposals, todos = {"interactions": [], "data": [], "contract": []}, []
    n_i = n_d = 0
    for r in spec.get("regions") or []:
        if r.get("role") != "button" or r.get("keep") != "rebuild":
            continue
        text = str((spec.get("slots") or {}).get(r.get("slot")) or r.get("text") or "")
        if DESTRUCTIVE_RE.search(text):
            n_i += 1
            proposals["interactions"].append({
                "id": "auto-i%d" % n_i, "trigger": r["id"], "type": "confirms",
                "desc": "「%s」是危险操作，点击必须出现确认" % text.strip()[:20],
                "from": "auto",
            })
        else:
            todos.append("按钮「%s」（区块 %s）点击后的行为需要你声明：滚动 / 切换显隐 / 弹确认？"
                         % (text.strip()[:20] or r["id"], r["id"]))
    return proposals, todos


def declared_categories(spec):
    """返回 {category: bool} —— semantics 里声明了哪些断言类别（供验收决定 验/未声明）。"""
    sem = spec.get("semantics") or {}
    return {
        "contract": bool(sem.get("contract")),
        "interaction": bool(sem.get("interactions")),
        "data": bool(sem.get("data")),
    }


def required_texts(spec):
    """文案断言清单：显式声明优先，缺省 = 全部 slot 值 + 非 slot 的静态文字。"""
    acc = (spec.get("acceptance") or {}).get("requiredTexts")
    if acc:
        return list(acc)
    out = []
    for r in spec.get("regions") or []:
        if r.get("keep") != "rebuild":
            continue
        val = (spec.get("slots") or {}).get(r.get("slot")) or r.get("text")
        if val and str(val).strip():
            out.append(str(val).strip())
    return out


def min_text_len(spec):
    return int((spec.get("acceptance") or {}).get("minTextLen") or 120)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python ff_spec.py <freeform.spec.json> [--check]")
        raise SystemExit(2)
    with open(sys.argv[1], encoding="utf-8") as f:
        sp = json.load(f)
    e, w = validate(sp)
    for x in e:
        print("★ %s" % x, file=sys.stderr)
    for x in w:
        print("⚠ %s" % x, file=sys.stderr)
    n = len(required_texts(sp))
    print("校验：%s（断言文案 %d 条，警告 %d）" % ("通过" if not e else "失败", n, len(w)))
    raise SystemExit(0 if not e else 1)
