#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视觉断言（第 5 类）—— 把「生成得像不像」变成可量化的比对。

背景：用户丢一张截图让 AI 做页面，最常见的问题是"看着像、细节不对"：
侧边栏宽度差 20px、顶栏高了一截、主色变成另一支蓝。这类差异肉眼不易察觉，
但一眼就能看出"不是同一个系统"。所以要把它们**量出来**。

比对什么（都是客观可算的，不依赖主观判断）：
  1. 画布尺寸与宽高比
  2. 左侧导航宽度（从左往右扫描，找深色→浅色的分界）
  3. 顶部工具栏高度（从上往下扫描，找第一条水平分界线）
  4. 主色（统计高饱和像素的众数）
  5. 配色板（出现最多的若干颜色）
  6. 结构指纹：深色/浅色像素占比（判断整体版式：深色侧栏 + 浅色卡片）

实现约束：**零依赖**。PNG 解码用标准库 zlib + 手写反过滤，
与项目「不引任何第三方库」的约定一致。

用法:
    python build/verify_visual.py --expect <原截图.png> --actual <生成物截图.png>
    python build/verify_visual.py --expect input.png --actual out.png --json
    python build/verify_visual.py --features input.png          # 只看特征
"""

import argparse
import collections
import json
import os
import struct
import sys
import zlib

# --------------------------------------------------------------------------
# 最小 PNG 解码（8-bit，非隔行；RGB / RGBA）
# --------------------------------------------------------------------------

def decode_png(path, max_rows=None):
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG 文件: %s" % path)

    pos, idat = 8, []
    w = h = depth = ctype = interlace = None
    while pos < len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, depth, ctype, _comp, _filt, interlace = struct.unpack(">IIBBBBB", chunk)
        elif typ == b"IDAT":
            idat.append(chunk)
        elif typ == b"IEND":
            break
        pos += 12 + ln

    if depth != 8 or interlace != 0 or ctype not in (2, 6):
        raise ValueError("只支持 8-bit 非隔行 RGB/RGBA（当前 depth=%s interlace=%s ctype=%s）"
                         % (depth, interlace, ctype))

    ch = 3 if ctype == 2 else 4
    raw = zlib.decompress(b"".join(idat))
    stride = w * ch

    # 反过滤：逐行做。只解到 max_rows（顶栏/侧栏/主色都在上半部分），省一大截时间。
    rows = h if max_rows is None else min(h, max_rows)
    out = bytearray()
    prev = bytearray(stride)
    i = 0
    for _y in range(rows):
        ft = raw[i]
        i += 1
        line = bytearray(raw[i:i + stride])
        i += stride
        if ft == 1:      # Sub
            for x in range(ch, stride):
                line[x] = (line[x] + line[x - ch]) & 255
        elif ft == 2:    # Up
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 255
        elif ft == 3:    # Average
            for x in range(stride):
                a = line[x - ch] if x >= ch else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 255
        elif ft == 4:    # Paeth
            for x in range(stride):
                a = line[x - ch] if x >= ch else 0
                b = prev[x]
                c = prev[x - ch] if x >= ch else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 255
        out += line
        prev = line
    return {"w": w, "h": h, "ch": ch, "rows": rows, "buf": bytes(out)}


def pixel(img, x, y):
    i = (y * img["w"] + x) * img["ch"]
    b = img["buf"]
    return b[i], b[i + 1], b[i + 2]


def lum(p):
    return 0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2]


def sat(p):
    mx, mn = max(p), min(p)
    return 0.0 if mx == 0 else (mx - mn) / float(mx)


def hexof(p):
    return "#%02X%02X%02X" % p


def dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


# --------------------------------------------------------------------------
# 特征提取
# --------------------------------------------------------------------------

def feature_sidebar_width(img, probe_rows=(40, 80, 150, 260)):
    """左侧导航宽度：从 x=0 往右扫，找第一处"连续变亮"的分界。

    不能简单取"最后一个暗像素"：深色侧边栏上压着白色文字与图标，
    那样量出来会偏大（实测把 216px 量成了 309px）。
    要跳过深色底上的亮色文字 —— 只有当亮色连续 8px 才算真的出了侧边栏。
    """
    votes = []
    for y in probe_rows:
        if y >= img["rows"]:
            continue
        limit = min(img["w"], 600)
        w, x = 0, 0
        while x < limit:
            if lum(pixel(img, x, y)) < 120:
                x += 1
                continue
            run, xx = 0, x
            while xx < limit and run < 8 and lum(pixel(img, xx, y)) > 150:
                run += 1
                xx += 1
            if run >= 8:
                w = x
                break
            x = xx if xx > x else x + 1
        if w:
            votes.append(w)
    if not votes:
        return None
    votes.sort()
    return votes[len(votes) // 2]        # 中位数，避开个别行有分割线的干扰


def feature_sidebar_border(img, th=0, max_x=480):
    """浅色侧栏检测：找一条「贯穿整个探测高度」的垂直分割线。

    侧栏是白底时整片都亮，深色分界法无从下手，只能靠这条边框线。
    要求贯穿率 ≥80%，才能把侧栏里的图标与文字竖笔画排除掉 ——
    那些笔画只覆盖几行，不构成贯穿线。
    """
    y0 = max(12, int(th) + 8)
    y1 = min(img["rows"], y0 + 400)
    if y1 - y0 < 40:
        return None
    for x in range(6, min(img["w"], max_x)):
        hit = tot = 0
        for y in range(y0, y1, 6):
            l, c, r = pixel(img, x - 2, y), pixel(img, x, y), pixel(img, x + 2, y)
            if dist(c, l) > 8 and dist(c, r) > 8:
                hit += 1
            tot += 1
        if tot and hit / float(tot) >= 0.8:
            return x
    return None


def feature_topbar_height(img, probe_cols=(700, 900, 1100)):
    """顶部工具栏高度：从 y=0 往下扫，找第一处"由白变灰"的分界（浅色→页面底色）。"""
    votes = []
    for x in probe_cols:
        if x >= img["w"]:
            continue
        prev = None
        for y in range(0, min(img["rows"], 300)):
            p = pixel(img, x, y)
            if prev is not None and abs(lum(p) - lum(prev)) > 8 and lum(prev) > 230:
                votes.append(y)
                break
            prev = p
    if not votes:
        return None
    votes.sort()
    return votes[len(votes) // 2]


def feature_colors(img, sample_step=3):
    """主色与配色板：只统计高饱和像素找主色（中性灰白黑不算），另统计整体色板。"""
    hues = collections.Counter()
    palette = collections.Counter()
    dark = light = total = 0
    for y in range(0, img["rows"], sample_step):
        for x in range(0, img["w"], sample_step):
            p = pixel(img, x, y)
            total += 1
            palette[(p[0] // 16 * 16, p[1] // 16 * 16, p[2] // 16 * 16)] += 1
            l = lum(p)
            if l < 90:
                dark += 1
            elif l > 215:
                light += 1
            # 主色 = 品牌色：高饱和 + 中等亮度。
            # 必须排除暗色（侧边栏选中态 #22304D 也"高饱和"，但它是深色底，
            # 面积比按钮大得多，不排除就会把主色认成侧边栏色）。
            if sat(p) > 0.35 and 60 < l < 225:
                hues[p] += 1
    primary = hues.most_common(1)[0][0] if hues else None
    top = [{"color": hexof(c), "count": n} for c, n in palette.most_common(6)]
    return {
        "primary": primary,
        "primaryHex": hexof(primary) if primary else None,
        "palette": top,
        "darkRatio": round(dark / float(total or 1), 4),
        "lightRatio": round(light / float(total or 1), 4),
        "coloredPixels": sum(hues.values()),
    }


def features(path, max_rows=420):
    img = decode_png(path, max_rows=max_rows)
    th = feature_topbar_height(img)

    # 侧栏宽度两种策略：先按深色侧栏的亮暗分界量（最准），
    # 量不到（说明侧栏是浅色）再退回"贯穿性垂直边框"检测。
    sw = feature_sidebar_width(img)
    # 下限保护：浅色侧栏 + 选中项色块时，深色分界法会在色块左缘误判出 3px 这种值
    # （实测踩到：生成物的浅色侧栏被量成 3px，视觉评分被诬成 75 分）。
    # 真侧栏不可能窄于 100px，低于此值判为误判，交给边框法。
    if sw is not None and sw < 100:
        sw = None
    how = "dark-edge" if sw else None
    if not sw:
        sw = feature_sidebar_border(img, th=th or 0)
        how = "border-line" if sw else None

    f = {
        "file": os.path.abspath(path),
        "canvas": {"w": img["w"], "h": img["h"], "ratio": round(img["w"] / float(img["h"]), 3)},
        "analyzedRows": img["rows"],
        "sidebarWidth": sw,
        "sidebarDetect": how,
        "topbarHeight": th,
    }
    f.update(feature_colors(img))
    return f


# --------------------------------------------------------------------------
# 视觉令牌提取：从像素里量出「能注入生成」的令牌
# --------------------------------------------------------------------------

def dominant_color(img, x0, y0, x1, y1, step=2, quant=8, keep=None):
    """区域直方图众数，返回 (rgb, 占比)。

    取众数而不是平均：区域里混着文字、图标、分割线，平均会得到一坨脏色。
    分桶后还要在桶内取平均，否则纯白 #FFFFFF 会被量化成 #FCFCFC。
    """
    buckets = collections.defaultdict(lambda: [0, 0, 0, 0])
    for y in range(max(0, y0), min(img["rows"], y1), step):
        for x in range(max(0, x0), min(img["w"], x1), step):
            p = pixel(img, x, y)
            if keep and not keep(p):
                continue
            b = buckets[tuple(c // quant for c in p)]
            b[0] += p[0]
            b[1] += p[1]
            b[2] += p[2]
            b[3] += 1
    if not buckets:
        return None, 0.0
    key, acc = max(buckets.items(), key=lambda kv: kv[1][3])
    total = sum(v[3] for v in buckets.values())
    rgb = tuple(int(round(acc[i] / float(acc[3]))) for i in range(3))
    return rgb, acc[3] / float(total or 1)


def _max_lum(img, x0, y0, x1, y1, step=4):
    """区域内最高亮度。用来定位「最亮那一簇」= 卡片底色。"""
    best = 0
    for y in range(max(0, y0), min(img["rows"], y1), step):
        for x in range(max(0, x0), min(img["w"], x1), step):
            l = lum(pixel(img, x, y))
            if l > best:
                best = l
    return best


# 不能从像素可靠推断的维度 —— 必须显式说出来，不能假装还原了
UNMEASURABLE = [
    ("radius", "圆角半径", "需要看控件边角的抗锯齿，像素级不可靠"),
    ("shadow", "阴影", "与背景色的微弱差，易与压缩噪点混淆"),
    ("font", "字号与字体", "字形度量受截图缩放影响，不可靠"),
    ("density", "行高与组件密度", "同上，属于排版度量"),
    ("columnWidth", "列宽比例", "表格列宽取决于数据与容器，不是固定令牌"),
    ("icon", "图标风格", "位图图标无法还原成矢量"),
]


def visual_tokens(path):
    """从截图像素量出可注入 generating 的令牌 + 明确的还原边界。

    只做「客观可算」的那些：主色、侧栏底、页底、卡片色、侧栏宽、顶栏高。
    其余（圆角/阴影/字号/列宽/图标）列进 cannotReproduce，让用户自己决定要不要人工指定 ——
    报"能还原"却给出一个不像的页面，比直说"这几项我量不出来"伤害更大。
    """
    img = decode_png(path)                      # 需要内容区底部，故不截断
    f = features(path)
    sw = f.get("sidebarWidth")
    th = f.get("topbarHeight") or 56

    measured = {
        "brandHex": f.get("primaryHex"),
        "sidebarWidth": sw,
        "topbarHeight": f.get("topbarHeight"),
    }

    # 侧栏底色：取侧栏中部一块（避开品牌区的 logo 与底部版权）
    if sw:
        c, ratio = dominant_color(img, 2, th + 20, max(6, int(sw * 0.75)),
                                 min(img["rows"], th + 320))
        if c:
            measured["sidebarHex"] = hexof(c)
            measured["sidebarConfidence"] = round(ratio, 3)

    # 内容区：卡片色 = 最亮那一簇；页底色 = 内容区最左竖条（body 左内边距处）
    x0 = int((sw or 0)) + 2
    if img["w"] - x0 > 40:
        # 卡片色不能简单用「亮于 235」去筛：表头底(#F7F8FA)、行分隔线(#F0F2F5) 都会被算进来，
        # 实测两个样本的卡片色都被量成了页底色。改成先找最亮值，再取最亮±6 范围内的众数。
        maxlum = _max_lum(img, x0, th + 2, img["w"] - 2, img["rows"])
        card, cratio = dominant_color(img, x0, th + 2, img["w"] - 2, img["rows"],
                                      keep=lambda p: lum(p) >= maxlum - 6)
        # 页底色优先取内容区最左侧那条竖带：后台的 body 左内边距露出的就是页底，
        # 比"统计整块内容区的浅色"稳得多（后者会把表头底与分隔线拌进来）。
        margin, _mr = dominant_color(img, x0 + 1, th + 6, x0 + 8, img["rows"] - 4)
        if card:
            measured["cardHex"] = hexof(card)
        if margin and (not card or dist(margin, card) > 6):
            measured["pageBgHex"] = hexof(margin)
            measured["pageBgVia"] = "left-margin"
        elif card:
            measured["pageBgHex"] = hexof(card)
            measured["pageBgVia"] = "same-as-card"

    # 组装成能直接喂给 forge 的覆盖结构
    values = {"color": {}, "layout": {}}
    if measured.get("brandHex"):
        values["color"]["brand"] = {"value": measured["brandHex"], "source": "visual"}
    if measured.get("sidebarHex"):
        values["color"]["sidebar"] = {"value": measured["sidebarHex"], "source": "visual"}
    if measured.get("pageBgHex"):
        values["color"]["pageBg"] = {"value": measured["pageBgHex"], "source": "visual"}
    if measured.get("cardHex"):
        values["color"]["card"] = {"value": measured["cardHex"], "source": "visual"}
    if sw:
        values["layout"]["sidebarWidth"] = {"value": "%dpx" % sw, "source": "visual"}
    if measured.get("topbarHeight"):
        values["layout"]["topbarHeight"] = {"value": "%dpx" % measured["topbarHeight"],
                                            "source": "visual"}

    tone = None
    if measured.get("sidebarHex"):
        tone = "light" if lum(_hex2rgb(measured["sidebarHex"])) > 150 else "dark"

    return {
        "file": os.path.abspath(path),
        "canvas": f["canvas"],
        "measured": measured,
        "tokens": {"mode": "inherit-visual", "values": values},
        "coverage": {
            "sidebarTone": tone,
            "reproducible": ["主色", "侧栏底色与宽高", "顶栏高", "内容区底色", "卡片色",
                             "侧栏文字色（按底色自动翻转）"],
            "cannotReproduce": [{"key": k, "name": n, "why": w} for k, n, w in UNMEASURABLE],
            "note": "不可推断的项沿用 Skill 默认或由人工在 PageSpec 里指定；"
                    "像素比对只看版式与配色，文本内容靠契约断言核对。",
        },
    }


def _hex2rgb(s):
    s = str(s).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


# --------------------------------------------------------------------------
# 比对
# --------------------------------------------------------------------------

TOL = {
    "sidebarWidth": 10,     # px
    "topbarHeight": 8,      # px
    "primaryColor": 46,     # RGB 欧氏距离
    "ratio": 0.06,          # 宽高比
    "darkRatio": 0.05,
}


def compare(expect, actual):
    checks = []

    def add(name, ok, ev, severity="hard"):
        checks.append({"id": name, "pass": bool(ok), "evidence": ev, "severity": severity})

    for key, label in (("sidebarWidth", "左侧导航宽度"), ("topbarHeight", "顶部工具栏高度")):
        e, a = expect.get(key), actual.get(key)
        if e is None or a is None:
            add("visual." + key, False, "未能量出（原图 %s / 生成物 %s）" % (e, a), "soft")
            continue
        d = abs(e - a)
        add("visual." + key, d <= TOL[key], "%s：原图 %dpx vs 生成物 %dpx（差 %d，容差 %d）"
            % (label, e, a, d, TOL[key]))

    ep, ap = expect.get("primary"), actual.get("primary")
    if ep and ap:
        d = dist(ep, ap)
        add("visual.primaryColor", d <= TOL["primaryColor"],
            "主色：原图 %s vs 生成物 %s（色距 %.1f，容差 %d）"
            % (expect["primaryHex"], actual["primaryHex"], d, TOL["primaryColor"]))
    else:
        add("visual.primaryColor", False, "有一侧未识别出主色（可能整页无高饱和色）", "soft")

    er, ar = expect["canvas"]["ratio"], actual["canvas"]["ratio"]
    add("visual.canvasRatio", abs(er - ar) <= TOL["ratio"],
        "画布宽高比：原图 %.3f vs 生成物 %.3f（差 %.3f）" % (er, ar, abs(er - ar)))

    ed, ad = expect.get("darkRatio", 0), actual.get("darkRatio", 0)
    add("visual.toneRatio", abs(ed - ad) <= TOL["darkRatio"],
        "深色像素占比（判断「深色侧栏 + 浅色卡片」的整体版式）：原图 %.1f%% vs 生成物 %.1f%%"
        % (ed * 100, ad * 100), "soft")

    hard = [c for c in checks if c["severity"] == "hard"]
    passed = sum(1 for c in hard if c["pass"])
    score = int(round(100.0 * passed / len(hard))) if hard else 0
    return checks, score


def main():
    ap = argparse.ArgumentParser(description="视觉断言：截图与生成物的结构化比对（零依赖）")
    ap.add_argument("--expect", help="原截图（用户给的那张）")
    ap.add_argument("--actual", help="生成物截图")
    ap.add_argument("--features", help="只输出某张图的特征")
    ap.add_argument("--tokens", help="从截图量出可注入生成层的视觉令牌（JSON）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.tokens:
        print(json.dumps(visual_tokens(args.tokens), ensure_ascii=False, indent=2))
        return 0

    if args.features:
        f = features(args.features)
        print(json.dumps(f, ensure_ascii=False, indent=2))
        return 0

    if not (args.expect and args.actual):
        raise SystemExit("[错误] 需要 --expect 与 --actual，或 --features")

    e, a = features(args.expect), features(args.actual)
    checks, score = compare(e, a)

    if args.json:
        print(json.dumps({"expect": e, "actual": a, "checks": checks, "score": score},
                         ensure_ascii=False, indent=2))
    else:
        print("视觉断言（原截图 vs 生成物）")
        print("-" * 96)
        for c in checks:
            mark = "通过" if c["pass"] else ("★ 失败" if c["severity"] == "hard" else "· 建议")
            print("[%s] %-24s %s" % (mark, c["id"], c["evidence"]))
        print("-" * 96)
        print("还原度评分：%d / 100（仅计硬性视觉项）" % score)
        print("注：配色板与文本内容（列头/按钮/状态）需配合 DOM 断言核对，像素比对只看版式与配色。")

    hard = [c for c in checks if c["severity"] == "hard"]
    return 0 if all(c["pass"] for c in hard) else 1


if __name__ == "__main__":
    sys.exit(main())
