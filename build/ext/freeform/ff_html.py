#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTML 入场（自由版式扩展）：上传页 → 净化 → FreeformSpec（插槽替换器）。

与图片入场的差别：版式 100% 保留原页 HTML/CSS，只做两件事——
  1. 净化（生成物铁律：零外链、零脚本副作用）
     - 去掉 <script>、外链 <link>、<iframe>、on* 内联事件
     - 外链图片 → 灰底占位（标注原 URL 于注释，不发请求）
     - http(s) 链接 → "#"
  2. 候选 slot：短文案的叶子元素标 data-ff-slot="sN"，供指令替换

产出：skeleton.html（净化后的模板本体）+ FreeformSpec（kind=freeform, source.type=html）。
生成时只替换 data-ff-slot 元素的文字，其余逐字节保留 → 确定性。

用法:
    python ff_html.py --artifact upload/ --out ff.spec.json [--rename 页面名] [--forge page.html]
"""

import argparse
from html import escape
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

MAX_TEXT = 60          # 候选 slot 的文字长度上限
IMG_PLACEHOLDER = ("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg'"
                   " width='400' height='300'%3E%3Crect width='100%25' height='100%25'"
                   " fill='%23E6E9F0'/%3E%3Ctext x='50%25' y='50%25' text-anchor='middle'"
                   " fill='%238B93A5' font-size='14'%3E%E5%9B%BE%E7%89%87%E5%8D%A0%E4%BD%8D"
                   "%3C/text%3E%3C/svg%3E")


def sanitize(html):
    """净化：零外链 + 零脚本副作用。返回 (cleaned, notes)。"""
    notes = []
    n0 = len(re.findall(r"<script\b", html, re.I))
    html = re.sub(r"<script\b[^>]*>.*?</script\s*>", "", html, flags=re.S | re.I)
    html = re.sub(r"<script\b[^>]*/?>", "", html, flags=re.I)
    if n0:
        notes.append("移除 %d 个 <script>（脚本副作用）" % n0)
    n0 = len(re.findall(r"<link\b[^>]*http", html, re.I))
    html = re.sub(r"<link\b[^>]*>", "", html, flags=re.I) if n0 else html
    if n0:
        notes.append("移除 %d 个外链 <link>" % n0)
    n0 = len(re.findall(r"<iframe\b", html, re.I))
    html = re.sub(r"<iframe\b[^>]*>.*?</iframe\s*>", "", html, flags=re.S | re.I)
    if n0:
        notes.append("移除 %d 个 <iframe>" % n0)
    n0 = len(re.findall(r"\son\w+\s*=", html, re.I))
    html = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", html, flags=re.I)
    if n0:
        notes.append("移除 %d 个内联事件" % n0)

    def img_sub(m):
        return _img_sub(m, notes)
    html = re.sub(r"<img\b[^>]*src\s*=\s*(\"([^\"]+)\"|'([^']+)')", img_sub, html)

    def href_sub(m):
        href = m.group(2) or m.group(3)
        if href.startswith(("#", "data:")) or "127.0.0.1" in href:
            return m.group(0)
        return m.group(0).replace(href, "#")
    html = re.sub(r"<a\b[^>]*href\s*=\s*(\"([^\"]+)\"|'([^']+)')", href_sub, html, flags=re.I)
    return html, notes


def _img_sub(m, notes):
    tag = m.group(0)
    src = m.group(2) or m.group(3)
    if src.startswith(("data:",)) or "127.0.0.1" in src:
        return tag
    notes.append("外链图片 → 占位图（%s…）" % src[:40])
    return tag.replace(src, IMG_PLACEHOLDER)


MARK = "data-ff-slot"


def _collect_leaf_texts(html):
    """用栈式扫描找「叶子短文案」：>text</tag> 且 tag 不是 a/script/style，返回 [(start,end,text)]。"""
    out = []
    for m in re.finditer(r"<([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>([^<>]{1,%d})</\1\s*>"
                         % MAX_TEXT, html):
        tag, text = m.group(1).lower(), m.group(2)
        if tag in ("a", "script", "style", "title", "textarea", "option", "label"):
            continue
        if not text.strip():
            continue
        out.append((m.start(), m.end(), m.group(1), text))
    # 嵌套去重：保留最内层（start 最大且 end 最小的）
    out.sort(key=lambda t: (t[0], -(t[1])))
    keep, last_end = [], -1
    for s, e, tag, text in sorted(out, key=lambda t: t[0]):
        inner = [k for k in keep if k[0] <= s and e <= k[1]]
        if inner:
            continue
        keep.append((s, e, tag, text))
    return keep


def build_spec(artifact_dir, name=None):
    """读 upload 目录（单文件或多文件），净化 + 抽 slot，落 FreeformSpec。"""
    files = []
    for base, _, fns in os.walk(artifact_dir):
        for fn in fns:
            if os.path.splitext(fn)[1].lower() in (".html", ".htm"):
                files.append(os.path.join(base, fn))
    if not files:
        raise SystemExit("upload 目录里没有 .html 文件")
    # 多文件取「内容最大」的当主页面（与 reverse.py 的取舍一致）
    main = max(files, key=lambda p: os.path.getsize(p))
    with open(main, encoding="utf-8", errors="replace") as f:
        raw = f.read()
    cleaned, notes = sanitize(raw)

    leaves = _collect_leaf_texts(cleaned)
    slots, regions = {}, []
    # 从后往前替换，避免位移
    for i, (s, e, tag, text) in enumerate(reversed(leaves)):
        sid = "s%d" % (len(leaves) - i)
        slots[sid] = text.strip()
        seg = cleaned[s:e]
        rid = "r-" + sid
        seg_new = re.sub(r"<%s\b" % tag, '<%s %s="%s" data-ff-region="%s"' % (tag, MARK, sid, rid), seg, count=1)
        cleaned = cleaned[:s] + seg_new + cleaned[e:]
        regions.append({"id": rid, "role": "text", "keep": "rebuild", "slot": sid,
                        "text": text.strip(), "bbox": [0, 0, 1280, page_h(len(leaves))],
                        "note": "HTML DOM slot; bbox denotes the containing canvas only, placement is preserved by skeleton.html"})

    # The spec declares r1 as its preserved HTML container. Annotate the real
    # body so structure validation can locate it without changing the layout.
    if re.search(r"<body\b", cleaned, re.I):
        cleaned = re.sub(r"<body\b", '<body data-ff-region="r1"', cleaned, count=1, flags=re.I)
    else:
        cleaned = '<div data-ff-region="r1">' + cleaned + '</div>'

    skel = os.path.join(artifact_dir, "skeleton.html")
    with open(skel, "w", encoding="utf-8", newline="\n") as f:
        f.write(cleaned)

    spec = {
        "kind": "freeform", "specVersion": "ff-1.0",
        "name": name or "HTML 自由版式",
        "source": {"type": "html", "file": "skeleton.html"},
        "canvas": {"w": 1280, "h": page_h(len(leaves))},
        "tokens": {"mode": "skill-default", "values": {}},
        "regions": [{"id": "r1", "role": "container", "keep": "rebuild",
                     "bbox": [0, 0, 1280, page_h(len(leaves))],
                     "note": "HTML 入场：版式由 skeleton.html 承载，本区块仅声明容器"}] + list(reversed(regions)),
        "slots": slots,
        "acceptance": {"requiredTexts": [v for v in slots.values()][:10], "minTextLen": 80},
        "provenance": [{"what": "净化规则", "from": "；".join(notes) or "原页已干净"}]
        if notes else [{"what": "净化", "from": "原页无外链无脚本"}],
    }
    return spec, cleaned, notes


def page_h(n):
    return max(600, 200 + n * 40)


def apply_slots(skeleton_html, slots):
    """确定性插槽替换：只改 data-ff-slot 元素内的文字。返回 (html, replaced_map)。"""
    html, replaced = skeleton_html, {}
    for sid, val in (slots or {}).items():
        pat = re.compile(r"(<[^>]*%s=\"%s\"[^>]*>)(.*?)(</)" % (MARK, re.escape(sid)), re.S)
        m = pat.search(html)
        if not m:
            continue
        old = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        html = pat.sub(lambda mm: mm.group(1) + escape(str(val)) + mm.group(3), html, count=1)
        replaced[sid] = {"old": old, "new": str(val)}
    return html, replaced


def main():
    ap = argparse.ArgumentParser(description="HTML 入场 → FreeformSpec")
    ap.add_argument("--artifact", required=True, help="上传目录或单文件")
    ap.add_argument("--out", required=True)
    ap.add_argument("--rename")
    a = ap.parse_args()
    src = a.artifact
    if os.path.isfile(src):
        tmp = src + ".updir"
        os.makedirs(tmp, exist_ok=True)
        import shutil
        dst = os.path.join(tmp, os.path.basename(src))
        shutil.copyfile(src, dst)
        src = tmp
    spec, cleaned, notes = build_spec(src, a.rename)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)
    print("FreeformSpec 已生成: %s（slot %d 个，净化 %d 处）"
          % (os.path.abspath(a.out), len(spec["slots"]), len(notes)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
