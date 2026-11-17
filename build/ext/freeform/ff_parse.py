#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自由版式指令解析（自由版式扩展 · 指令复用）。

指令形如：
    用「空状态模板」生成 采购订单空状态 标题：暂无采购订单 按钮：新建采购订单

规则：
  - 模板名（取自自由版式模板库）在指令中出现即命中
  - `slot名：值` 覆盖对应插槽；slot 名支持中英文别名（模板 meta 里可配 alias）
  - 未知 slot 名 → 422，并列出全部可用 slot（不静默忽略）
  - 剩余文字（去掉口语与模板名）→ 页面名
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

FILLER = r"帮我|请|麻烦|生成|做|创建|来|写|用|套用|一个|一份|的|页面|页$"
SLOT_LINE = re.compile(r"([^\s:：]+)\s*[:：]\s*([^\n:：]+)")


def parse(cmd, templates):
    """templates: list of {"id","name","slots":{...},"spec":{...}}（自由版式模板）。

    返回 (code, body)。code=200 时 body={"spec","parsed","notes"}；
    spec 已按指令更新 slots 与 name，生成层直接可用。
    """
    cmd = (cmd or "").strip()
    if not cmd:
        return 400, {"error": "请输入指令，例如：用「空状态模板」生成 XX 标题：… 按钮：…"}

    tpl = None
    for t in sorted(templates, key=lambda t: -len(t.get("name") or "")):
        if t.get("name") and t["name"] in cmd:
            tpl = t
            break
    if not tpl:
        return 422, {"error": "没有命中自由版式模板名。可用模板：%s"
                     % "、".join(t.get("name", "?") for t in templates)}

    spec = json.loads(json.dumps(tpl["spec"]))    # 深拷贝
    notes, parsed_slots, unknown = [], {}, []

    body = cmd.replace(tpl["name"], "")
    # slot 覆盖：支持 slot 名与别名（slot 原名恒可匹配自身；别名仅当其配置存在时追加）
    aliases = tpl.get("slotAliases") or {}
    slot_names = {r.get("slot") for r in spec.get("regions") or [] if r.get("slot")}
    alias_rev = {s: s for s in slot_names}          # 原名 → 原名（无别名也能用）
    for k, aa in aliases.items():
        for a in ([k] + aa):
            alias_rev[a] = k
    for m in SLOT_LINE.finditer(body):
        key, val = m.group(1).strip(), m.group(2).strip()
        sid = alias_rev.get(key)
        if sid is None:
            unknown.append(key)
        else:
            parsed_slots[sid] = val

    if unknown:
        avail = {}
        for r in spec.get("regions") or []:
            if r.get("slot"):
                cn = (aliases.get(r["slot"]) or [r["slot"]])[0]
                avail[r["slot"]] = cn
        return 422, {"error": "指令里的 %s 不是本模板的插槽。可用插槽：%s"
                     % ("、".join("「%s」" % u for u in unknown),
                        "、".join("%s(%s)" % (cn, sid) for sid, cn in avail.items()))}

    for sid, val in parsed_slots.items():
        old = spec.setdefault("slots", {}).get(sid)
        spec["slots"][sid] = val
        for r in spec.get("regions") or []:
            if r.get("slot") == sid:
                r["text"] = val
        notes.append("插槽 %s：%s → %s" % (sid, old, val))

    # 剩余文字 → 页面名
    rest = SLOT_LINE.sub("", body)
    rest = re.sub(FILLER, "", rest).strip(" 「」\"' ，,。:：")
    if rest:
        spec["name"] = rest[:40]
        notes.append("页面名改为「%s」" % spec["name"])

    return 200, {"spec": spec, "kind": "freeform",
                 "parsed": {"template": tpl["name"], "templateId": tpl["id"], "templateKind": "freeform",
                            "page": spec.get("name"),
                            "slots": parsed_slots},
                 "notes": notes or ["原样套用自由版式模板「%s」" % tpl["name"]]}
