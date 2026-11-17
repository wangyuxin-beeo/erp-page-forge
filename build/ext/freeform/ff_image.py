#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""截图 → FreeformSpec（自由版式扩展 · 图片入场）。

分工与 vision.py 同构，但服务的是「不是表格/表单」的页面：
  像素（算得准）── 画布、底色、区块 bbox、每个区块的底色与文字色、品牌主色令牌
  标注（看得懂）── 文案内容与 slot（截图量不出文字，由人工/多模态模型提供）

流程：
  1. 纯 Python 解码 PNG（复用 verify_visual.decode_png）
  2. 量出画布底色与内容区
  3. 递归 XY 空白切分 → 区块
  4. 区块分类：纯色块 / 插画（crop）/ 文字（rebuild，文案来自标注）
  5. 合并标注（texts/buttons 按阅读顺序回填）→ FreeformSpec（ff-1.0）

诚实边界：量不出圆角/阴影/字号（与 UNMEASURABLE 同一边界）；标注少于文字区块数时
多余区块留空并进 uncertain，不硬编文案。

用法:
    python ff_image.py --image input.png --labels labels.json --out ff.spec.json [--forge page.html]
    labels.json: {"name":"...", "texts":["文案1","文案2",...], "buttons":["按钮1",...]}
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.dirname(os.path.dirname(HERE))
ROOT = os.path.dirname(BUILD)
sys.path.insert(0, BUILD)
sys.path.insert(0, HERE)

import verify_visual  # noqa: E402
from ff_forge import _encode_png  # noqa: E402  (仅复用编码，用于缩略图)

MIN_GAP = 12         # 空白带最小厚度（px；2x 截图下约 6 逻辑像素）
MIN_BLOCK = 18       # 区块最小边长（px）
SOLID_DIST = 14      # 纯色判定色距
SOLID_RATIO = 0.90


def _px(img, x, y):
    b = img["buf"]
    o = (y * img["w"] + x) * img["ch"]
    return (b[o], b[o + 1], b[o + 2])


def _hex(c):
    return "#%02X%02X%02X" % c


def _dist(a, b):
    return sum((a[i] - b[i]) ** 2 for i in range(3)) ** 0.5


def _lum(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def _mode_color(img, x0, y0, x1, y1, step=2, quant=24):
    """区块众数量化色与占比。"""
    from collections import Counter
    cnt = Counter()
    for y in range(y0, y1, step):
        for x in range(x0, x1, step):
            c = _px(img, x, y)
            cnt[(c[0] // quant, c[1] // quant, c[2] // quant)] += 1
    if not cnt:
        return None, 0.0
    k, n = cnt.most_common(1)[0]
    sel, tot = [], 0
    for y in range(y0, y1, step):
        for x in range(x0, x1, step):
            c = _px(img, x, y)
            if (c[0] // quant, c[1] // quant, c[2] // quant) == k:
                sel.append(c)
                tot += 1
    if not sel:
        return None, 0.0
    m = tuple(sum(p[i] for p in sel) // len(sel) for i in range(3))
    return m, n / max(1, sum(cnt.values()))


def _bg_color(img):
    """画布底色 = 四边最外侧 4px 的众色。"""
    cands = []
    w, h = img["w"], img["h"]
    for x in range(0, w, 4):
        cands.append(_px(img, x, 1))
        cands.append(_px(img, x, h - 2))
    for y in range(0, h, 4):
        cands.append(_px(img, 1, y))
        cands.append(_px(img, w - 2, y))
    return _mode_of(cands)


def _mode_of(cands):
    from collections import Counter
    cnt = Counter((c[0] // 16, c[1] // 16, c[2] // 16) for c in cands)
    k, _ = cnt.most_common(1)[0]
    sel = [c for c in cands if (c[0] // 16, c[1] // 16, c[2] // 16) == k]
    return tuple(sum(p[i] for p in sel) // len(sel) for i in range(3))


def _content_bbox(img, bg):
    """非底色内容区的包围盒。容差放宽到 40：外框常带投影渐变，窄容差会把阴影当内容。"""
    w, h = img["w"], img["h"]
    def row_bg_ratio(y, x0=0, x1=None):
        x1 = x1 or w
        n = tot = 0
        for x in range(x0, x1, 6):
            tot += 1
            if _dist(_px(img, x, y), bg) < 40:
                n += 1
        return n / max(1, tot)
    top, bot = 0, h
    for y in range(0, h, 4):
        if row_bg_ratio(y) < 0.92:
            top = y
            break
    for y in range(h - 1, 0, -4):
        if row_bg_ratio(y) < 0.92:
            bot = y + 1
            break
    def col_bg_ratio(x, y0, y1):
        n = tot = 0
        for y in range(y0, y1, 6):
            tot += 1
            if _dist(_px(img, x, y), bg) < 40:
                n += 1
        return n / max(1, tot)
    left, right = 0, w
    for x in range(0, w, 4):
        if col_bg_ratio(x, top, bot) < 0.92:
            left = x
            break
    for x in range(w - 1, 0, -4):
        if col_bg_ratio(x, top, bot) < 0.92:
            right = x + 1
            break
    return left, top, right, bot


def _gaps(img, bg, x0, y0, x1, y1, axis):
    """找出几乎全是底色的行/列带（≥98%），返回 [(start,end)]。"""
    gaps, cur = [], None
    if axis == "h":
        for y in range(y0, y1, 2):
            n = tot = 0
            for x in range(x0, x1, 8):
                tot += 1
                if _dist(_px(img, x, y), bg) < 18:
                    n += 1
            if n / max(1, tot) >= 0.98:
                cur = [y, y] if cur is None else [cur[0], y]
            else:
                if cur and cur[1] - cur[0] >= MIN_GAP:
                    gaps.append(cur)
                cur = None
    else:
        for x in range(x0, x1, 2):
            n = tot = 0
            for y in range(y0, y1, 8):
                tot += 1
                if _dist(_px(img, x, y), bg) < 18:
                    n += 1
            if n / max(1, tot) >= 0.98:
                cur = [x, x] if cur is None else [cur[0], x]
            else:
                if cur and cur[1] - cur[0] >= MIN_GAP:
                    gaps.append(cur)
                cur = None
    if cur and cur[1] - cur[0] >= MIN_GAP:
        gaps.append(cur)
    return gaps


def _segment(img, bg, box, depth=0, axis="h"):
    x0, y0, x1, y1 = box
    if depth > 4 or (x1 - x0) < MIN_BLOCK * 2 or (y1 - y0) < MIN_BLOCK * 2:
        return [box]
    gaps = _gaps(img, bg, x0, y0, x1, y1, axis)
    if not gaps:
        return _segment(img, bg, box, depth + 1, "v" if axis == "h" else "h") \
            if depth < 4 else [box]
    cuts, out = y0 if axis == "h" else x0, []
    for g0, g1 in gaps:
        mid = (g0 + g1) // 2
        if axis == "h":
            sub = (x0, cuts, x1, mid)
        else:
            sub = (cuts, y0, mid, y1)
        out += _segment(img, bg, sub, depth + 1, "v" if axis == "h" else "h")
        cuts = (g1 + 1) if axis == "h" else (g1 + 1)
    if axis == "h":
        out += _segment(img, bg, (x0, cuts, x1, y1), depth + 1, "v")
    else:
        out += _segment(img, bg, (cuts, y0, x1, y1), depth + 1, "h")
    return [b for b in out
            if (b[2] - b[0]) >= MIN_BLOCK and (b[3] - b[1]) >= MIN_BLOCK] or [box]


def _near(a, b, gap_x, gap_y):
    """同一行（横向间隙小且纵向重叠大）或同一段（纵向间隙小且横向重叠大）。"""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    v_ov = min(ay1, by1) - max(ay0, by0)
    h_ov = min(ax1, bx1) - max(ax0, bx0)
    if v_ov >= 0.5 * min(ay1 - ay0, by1 - by0) and (max(ax0, bx0) - min(ax1, bx1)) <= gap_x:
        return True
    if h_ov >= 0.5 * min(ax1 - ax0, bx1 - bx0) and (max(ay0, by0) - min(ay1, by1)) <= gap_y:
        return True
    return False


def _merge(blocks, gap_x=26, gap_y=14):
    """把字母级碎块并回词/行/段：XY 切分对字距敏感，直接切会把一行字切成一列字母。"""
    changed = True
    while changed:
        changed = False
        out = []
        while blocks:
            b = blocks.pop()
            hit = False
            for i, o in enumerate(out):
                if _near(b, o, gap_x, gap_y):
                    out[i] = (min(b[0], o[0]), min(b[1], o[1]),
                              max(b[2], o[2]), max(b[3], o[3]))
                    hit = changed = True
                    break
            if not hit:
                out.append(b)
        blocks = out
    return blocks


def _classify(img, box, content_area):
    """→ (role, keep, bg_color, text_color, extremes_ratio)"""
    x0, y0, x1, y1 = box
    bgc, ratio = _mode_color(img, x0, y0, x1, y1)
    area = (x1 - x0) * (y1 - y0)
    # 极端亮度像素占比：文字块黑白分明，插画过渡色多
    ext = tot = 0
    for y in range(y0, y1, 3):
        for x in range(x0, x1, 3):
            tot += 1
            l = _lum(_px(img, x, y))
            if l < 70 or l > 225:
                ext += 1
    ext_ratio = ext / max(1, tot)

    if ratio >= SOLID_RATIO:
        if (y1 - y0) <= 8 or (x1 - x0) <= 8:
            return "divider", "rebuild", bgc, None, ext_ratio
        if (y1 - y0) <= 90 and (x1 - x0) <= 420 and area < content_area * 0.06:
            return "button", "rebuild", bgc, None, ext_ratio
        return "card", "rebuild", bgc, None, ext_ratio
    if ext_ratio < 0.42 and area >= content_area * 0.012:
        return "illustration", "crop", bgc, None, ext_ratio
    # 文字块：底色取众色，文字色取与底色对比最大的极端色均值
    darks, lights = [], []
    for y in range(y0, y1, 2):
        for x in range(x0, x1, 2):
            c = _px(img, x, y)
            (darks if _lum(c) < 110 else lights).append(c)
    bg_is_light = _lum(bgc) > 150
    pool = darks if bg_is_light else lights
    tc = _mode_of(pool) if len(pool) > 8 else (bgc if bg_is_light else (255, 255, 255))
    return "text", "rebuild", bgc, tc, ext_ratio


def _surface_bbox(img):
    """全图众色 = 页面承载面（白卡片 / 页面底）。返回 (surface_color, 内容区 bbox)。

    截图常带外框（桌面灰底、投影渐变），按「和边缘不像」定边界会被阴影带歪；
    页面本体永远是全图面积最大的一块，拿它的颜色反推内容区最稳。
    """
    from collections import Counter
    w, h = img["w"], img["h"]
    cnt = Counter()
    for y in range(0, h, 6):
        for x in range(0, w, 6):
            c = _px(img, x, y)
            cnt[(c[0] // 16, c[1] // 16, c[2] // 16)] += 1
    k, _n = cnt.most_common(1)[0]
    sel = []
    for y in range(0, h, 6):
        for x in range(0, w, 6):
            c = _px(img, x, y)
            if (c[0] // 16, c[1] // 16, c[2] // 16) == k:
                sel.append(c)
    surface = tuple(sum(p[i] for p in sel) // len(sel) for i in range(3))

    def near(c):
        return _dist(c, surface) < 30

    top = bot = left = right = None
    for y in range(0, h, 3):
        n = tot = 0
        for x in range(0, w, 8):
            tot += 1
            n += near(_px(img, x, y))
        if n / max(1, tot) >= 0.35:
            top = y
            break
    for y in range(h - 1, 0, -3):
        n = tot = 0
        for x in range(0, w, 8):
            tot += 1
            n += near(_px(img, x, y))
        if n / max(1, tot) >= 0.35:
            bot = y + 1
            break
    if top is None or bot is None or bot - top < 200:
        top, bot = 0, h
    for x in range(0, w, 3):
        n = tot = 0
        for y in range(top, bot, 8):
            tot += 1
            n += near(_px(img, x, y))
        if n / max(1, tot) >= 0.35:
            left = x
            break
    for x in range(w - 1, 0, -3):
        n = tot = 0
        for y in range(top, bot, 8):
            tot += 1
            n += near(_px(img, x, y))
        if n / max(1, tot) >= 0.35:
            right = x + 1
            break
    if left is None or right is None or right - left < 200:
        left, right = 0, w
    return surface, (left, top, right, bot)


def build_spec(image_path, labels=None, name=None):
    labels = labels or {}
    img = verify_visual.decode_png(image_path)
    bg_outer = _bg_color(img)
    surface, (bx0, by0, bx1, by1) = _surface_bbox(img)
    if bx1 - bx0 < 200 or by1 - by0 < 200:
        raise SystemExit("内容区过小（%dx%d）：这张图可能不适合做自由版式还原" % (bx1 - bx0, by1 - by0))
    content_area = (bx1 - bx0) * (by1 - by0)
    bg = surface          # 区块之间的空白 = 承载面本色

    blocks = _segment(img, bg, (bx0, by0, bx1, by1))
    blocks = [b for b in _merge(blocks)
              if (b[2] - b[0]) >= MIN_BLOCK and (b[3] - b[1]) >= MIN_BLOCK]
    regions, provenance, uncertain = [], [], []
    texts = [t for t in (labels.get("texts") or []) if str(t).strip()]
    buttons = [t for t in (labels.get("buttons") or []) if str(t).strip()]
    n_text = n_btn = 0

    for i, box in enumerate(sorted(blocks, key=lambda b: (b[1] // 24, b[0]))):
        # 画布以内容区为原点，区块坐标随之平移
        box = (box[0] - bx0, box[1] - by0, box[2] - bx0, box[3] - by0)
        role, keep, bgc, tc, _ = _classify(img, (box[0] + bx0, box[1] + by0,
                                                 box[2] + bx0, box[3] + by0), content_area)
        x0, y0, x1, y1 = box
        r = {"id": "r%d" % (i + 1), "role": role, "keep": keep, "bbox": list(box),
             "style": {"bg": _hex(bgc)}}
        if role == "text":
            if texts:
                t = texts.pop(0)
                n_text += 1
                r["slot"] = "t%d" % n_text
                r["text"] = t
                if tc:
                    r["style"]["color"] = _hex(tc)
                provenance.append({"what": "region %s 文案「%s」" % (r["id"], t[:20]),
                                   "from": "label（按阅读顺序回填）"})
            else:
                r["text"] = ""
                uncertain.append("区块 %s 疑似文字但没有标注文案，已留空（请补 labels.texts）" % r["id"])
            r["style"]["fontSize"] = max(11, min(72, int((y1 - y0) * 0.6)))
        elif role == "button":
            if buttons:
                t = buttons.pop(0)
                n_btn += 1
                r["slot"] = "b%d" % n_btn
                r["text"] = t
                provenance.append({"what": "按钮 %s 文案「%s」" % (r["id"], t[:20]),
                                   "from": "label（按阅读顺序回填）"})
            else:
                r["text"] = ""
                uncertain.append("区块 %s 疑似按钮但没有标注文案，已留空" % r["id"])
            r["style"]["fontSize"] = max(11, min(28, int((y1 - y0) * 0.42)))
            if _lum(bgc) < 150:
                r["style"]["color"] = "#FFFFFF"
        elif role == "illustration":
            r["note"] = "插画/照片：从源图裁切嵌入"
        regions.append(r)

    slots = {r["slot"]: r.get("text", "") for r in regions if r.get("slot")}

    vt = verify_visual.visual_tokens(image_path)
    spec = {
        "kind": "freeform", "specVersion": "ff-1.0",
        "name": name or labels.get("name") or "自由版式页面",
        "source": {"type": "image", "file": os.path.basename(image_path)},
        "canvas": {"w": bx1 - bx0, "h": by1 - by0, "bg": _hex(surface)},
        "tokens": vt["tokens"],
        "regions": regions,
        "slots": slots,
        "acceptance": {"requiredTexts": [v for v in slots.values() if v.strip()] or None,
                       "minTextLen": 60},
        "provenance": provenance or [{"what": "全部区块 bbox 与配色", "from": "pixel（纯 Python 量测）"}],
        "_diagnostics": {
            "pageBg": _hex(bg_outer), "innerBg": _hex(surface), "content": [bx0, by0, bx1, by1],
            "blocks": len(blocks), "measured": vt["measured"],
            "coverage": vt["coverage"],
            "uncertain": uncertain + [u for u in
                                      ["品牌主色 %s" % vt["measured"].get("brandHex")]],
        },
    }
    if not spec["acceptance"]["requiredTexts"]:
        spec["acceptance"].pop("requiredTexts")
    return spec


def main():
    ap = argparse.ArgumentParser(description="截图 → FreeformSpec（自由版式图片入场）")
    ap.add_argument("--image", required=True)
    ap.add_argument("--labels", help="标注 JSON：name / texts[] / buttons[]")
    ap.add_argument("--name", help="模板名（覆盖 labels.name）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--forge", dest="forge_out")
    a = ap.parse_args()
    labels = {}
    if a.labels and os.path.exists(a.labels):
        with open(a.labels, encoding="utf-8") as f:
            labels = json.load(f)
    spec = build_spec(a.image, labels, a.name)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)
    d = spec["_diagnostics"]
    print("FreeformSpec 已生成: %s" % os.path.abspath(a.out))
    print("  画布 %dx%d · 区块 %d 个 · slot %d 个 · 文案标注 %s" % (
        spec["canvas"]["w"], spec["canvas"]["h"], len(spec["regions"]), len(spec["slots"]),
        "已回填" if spec["slots"] else "未提供"))
    print("  主色 %s · 页底 %s" % (d["measured"].get("brandHex"), d.get("pageBg")))
    for u in d["uncertain"]:
        print("  ⚠ %s" % u, file=sys.stderr)
    if a.forge_out:
        import ff_forge
        html = ff_forge.build_html(spec, a.image)
        with open(a.forge_out, "w", encoding="utf-8", newline="\n") as f:
            f.write(html)
        print("已生成页面: %s（%.1f KB）" % (os.path.abspath(a.forge_out),
                                           len(html.encode("utf-8")) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
