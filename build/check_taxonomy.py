#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""术语层 + 组件契约层的自洽性检查。

检查对象：skill/spec/ui-taxonomy.json（第①层）、skill/spec/components.json（第②层）。
核心问题：这两张表是「约定」，不是「代码」—— 约定最大的风险是与既有契约（archetypes.json 的 regions、
PageSpec 的字段）对不上，或者自己内部出现两个名字指同一件事。

用法:
    python build/check_taxonomy.py

退出码: 0 = 全部通过, 1 = 存在失败
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(ROOT, "skill", "spec")

FAILED = []


def chk(name, ok, detail=""):
    print("[%s] %-46s %s" % ("通过" if ok else "★失败", name, detail))
    if not ok:
        FAILED.append(name)


def load(fn):
    with open(os.path.join(SPEC, fn), encoding="utf-8") as f:
        return json.load(f)


def main():
    # ---------- 1. 文件可解析 ----------
    try:
        tax = load("ui-taxonomy.json")
    except Exception as e:
        chk("ui-taxonomy.json 可解析", False, str(e))
        return 1
    tax = load("ui-taxonomy.json")
    comp = load("components.json")
    arch = load("archetypes.json")
    scheme = load("page-spec.schema.json")
    chk("两份契约文件可解析", True,
        "taxonomy %d 字节 / components %d 字节" % (
            os.path.getsize(os.path.join(SPEC, "ui-taxonomy.json")),
            os.path.getsize(os.path.join(SPEC, "components.json"))))

    # ---------- 2. components 内部自洽 ----------
    comps = comp["components"]
    caps = set(comp["capabilities"]) - {"note"}
    bad_caps, bad_defaults = [], []
    for cid, c in comps.items():
        for cap in (c.get("capabilities") or []):
            if cap not in caps:
                bad_caps.append("%s.%s" % (cid, cap))
        for cap in (c.get("defaults") or []):
            if cap not in (c.get("capabilities") or []):
                bad_defaults.append("%s.%s" % (cid, cap))
    chk("capabilities 全部在枚举内定义", not bad_caps, "、".join(bad_caps) or "%d 项能力，%d 个组件" % (len(caps), len(comps)))
    chk("defaults 均为该组件已声明能力", not bad_defaults, "、".join(bad_defaults) or "ok")

    slots = set(comp["slots"]) - {"note"}
    bad_slots = [cid for cid, c in comps.items() if c.get("slot") not in slots]
    chk("slot 取值合法", not bad_slots, "、".join(bad_slots) or "ok")

    modes = set(comp["renderModes"])
    bad_t = [cid for cid, c in comps.items() if set(c.get("targets") or {}) != modes]
    chk("每个组件都登记全部 render_mode", not bad_t, "、".join(bad_t) or "ok")

    # 平台形态（kind）决定第 ③ 层是「库分叉」还是「平台分叉」—— 这一项是决策，不是实现细节，必须显式登记
    modes_meta = comp["renderModes"]
    bad_kind = [m for m, v in modes_meta.items() if v.get("kind") not in ("native", "web")]
    kind_cnt = {}
    for v in modes_meta.values():
        kind_cnt[v.get("kind")] = kind_cnt.get(v.get("kind"), 0) + 1
    chk("render_mode 均声明平台形态 kind", not bad_kind,
        "、".join(bad_kind) or " / ".join("%s %d" % kv for kv in sorted(kind_cnt.items())))

    bad_st = [m for m, v in modes_meta.items() if v.get("status") not in ("active", "planned")]
    bad_eco = [m for m, v in modes_meta.items() if not v.get("ecosystem")]
    chk("render_mode 状态与生态齐全", not bad_st and not bad_eco,
        "、".join(bad_st + bad_eco) or "%d 个目标（active %d）" % (
            len(modes_meta), sum(1 for v in modes_meta.values() if v["status"] == "active")))


    # ---------- 3. components 覆盖 archetypes 的全部 regions ----------
    alias2cid, dup = {}, []
    for cid, c in comps.items():
        for a in (c.get("aliases") or []) + [c["zh"]]:
            if a in alias2cid and alias2cid[a] != cid:
                dup.append("%s→%s/%s" % (a, alias2cid[a], cid))
            alias2cid[a] = cid
    chk("别名不冲突（一区域一名）", not dup, "、".join(dup) or "%d 个可识别名称" % len(alias2cid))

    missing = []
    ref_regions, uniq_regions = 0, set()
    for arch_id, a in arch["archetypes"].items():
        for r in a.get("regions", []):
            ref_regions += 1
            uniq_regions.add(r)
            if r not in alias2cid:
                missing.append("%s/%s" % (arch_id, r))
    chk("archetypes 全部 region 可映射到组件", not missing,
        "、".join(missing) or "%d 个去重区域名（%d 个引用条目）全覆盖" % (len(uniq_regions), ref_regions))

    # ---------- 4. taxonomy 与 components 对齐 ----------
    terms, tids, fams = [], [], tax["families"]
    for fam, body in fams.items():
        for key in ("terms", "modifiers"):
            for t in body.get(key) or []:
                tids.append(t["id"])
                terms.append((fam, t))
    dup_t = sorted({i for i in tids if tids.count(i) > 1})
    chk("术语 id 全局唯一", not dup_t, "、".join(dup_t) or "%d 个术语 / %d 个族" % (len(tids), len(fams)))

    bad_ref = []
    for fam, t in terms:
        c = t.get("component")
        if c and c not in comps:
            bad_ref.append("%s.%s→%s" % (fam, t["id"], c))
    chk("术语引用的组件都存在", not bad_ref, "、".join(bad_ref) or "ok")

    no_comp = [(f, t["id"]) for f, t in terms if not t.get("component")]
    # kind 决定映射要求：component（缺省）必须挂组件；modifier 必须挂能力项；
    # control / atom / state 是原子概念（输入控件、分割线、交互态），不要求挂组件。
    bad_map = []
    counts = {}
    for f, t in terms:
        k = t.get("kind", "component")
        counts[k] = counts.get(k, 0) + 1
        if k == "component" and not t.get("component"):
            bad_map.append("%s.%s 缺 component" % (f, t["id"]))
        if k == "modifier":
            if not t.get("capability"):
                bad_map.append("%s.%s 缺 capability" % (f, t["id"]))
            elif t["capability"] not in caps:
                bad_map.append("%s.%s capability「%s」未在 components.json 定义" % (f, t["id"], t["capability"]))
    chk("术语按 kind 正确映射", not bad_map,
        "、".join(bad_map) or " / ".join("%s %d" % kv for kv in sorted(counts.items())))

    bad_src = [(f, t["id"], t.get("source")) for f, t in terms
               if t.get("source") not in tax["meta"]["sources"]]
    chk("source 均在 meta.sources 登记", not bad_src, "、".join(str(x) for x in bad_src) or "ok")

    # evidence 规则：只有能从截图看出来的才写；导航/布局/数据展示族必须写
    need_ev = [(f, t["id"]) for f, t in terms if f in ("navigation", "layout") and not t.get("evidence")]
    chk("导航/布局族术语均有识别依据", not need_ev, "、".join("%s.%s" % x for x in need_ev) or "ok")

    # ---------- 5. PageSpec 契约落点 ----------
    props = scheme["properties"]
    chk("PageSpec 有 regions 块（组件契约落点）", "regions" in props, "properties 含 regions" if "regions" in props else "缺失")
    chk("PageSpec 有 nav 块（导航形态）", "nav" in props, "properties 含 nav" if "nav" in props else "缺失")
    if "regions" in props:
        allowed = set(props["regions"]["items"]["properties"]["component"]["enum"]) if "enum" in props["regions"]["items"]["properties"]["component"] else None
        chk("schema.regions.component 未硬编码枚举", allowed is None,
            "指向 components.json（避免两处维护同一份清单）" if allowed is None else "硬编码了 %d 项" % len(allowed))

    # ---------- 6. 发布约束 ----------
    import io
    raw = open(os.path.join(SPEC, "ui-taxonomy.json"), encoding="utf-8").read()
    raw += open(os.path.join(SPEC, "components.json"), encoding="utf-8").read()
    fence = raw.count("```")
    chk("无代码围栏（不破坏合并版）", fence == 0, "``` 出现 %d 次" % fence)
    links = [u for u in re.findall(r"https?://[^\s\"<>)]+", raw) if "127.0.0.1" not in u]
    chk("零外链", not links, "、".join(links) or "ok")

    print("-" * 66)
    if FAILED:
        print("失败 %d 项：%s" % (len(FAILED), "、".join(FAILED)))
        return 1
    print("术语层 / 组件契约层自洽性：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
