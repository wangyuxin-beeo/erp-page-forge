#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自由版式确定性生成器（自由版式扩展 · 生成层）。

输入 FreeformSpec（+ 可选源图），输出零外链单文件 HTML：
  - rebuild 区块 → 真实 DOM（文字是真文字，可被文案断言核对）
  - crop 区块   → 从源图裁切位图、纯 Python 重编码 PNG 后 base64 内嵌（产物仍零外链）
  - 版式        → 区块按 bbox 换算成画布百分比绝对定位，整画布用 transform 等比缩放，
                  1280px 视口不出横向滚动

确定性：同一 spec + 同一源图 → 输出逐字节一致（不写时间戳、PNG 压缩参数固定）。

用法:
    python ff_forge.py --spec ff.spec.json [--image input.png] --out page.html
"""

import argparse
import base64
import binascii
import json
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, BUILD)
sys.path.insert(0, HERE)

import ff_spec  # noqa: E402

DEFAULTS = {
    "pageBg": "#F4F5F7", "card": "#FFFFFF", "ink": "#1C2230",
    "ink2": "#5A6478", "brand": "#2B5CFF", "line": "#E6E9F0",
}


# --------------------------------------------------------------------------
# PNG 裁切与重编码（零依赖；解码复用 verify_visual.decode_png）
# --------------------------------------------------------------------------

def crop_png(image_path, bbox):
    """从 PNG 源图裁出 bbox 区域，返回 PNG 字节（RGB，filter=0，zlib level 6）。"""
    import verify_visual
    img = verify_visual.decode_png(image_path)     # 不截断：裁切需要任意区域
    w, h, ch, buf = img["w"], img["h"], img["ch"], img["buf"]
    x0, y0, x1, y1 = [max(0, min(v, w if i % 2 == 0 else h)) for i, v in enumerate(bbox)]
    if x1 - x0 < 2 or y1 - y0 < 2:
        raise ValueError("裁切区域过小: %r" % (bbox,))
    cw, chh = x1 - x0, y1 - y0
    raw = bytearray()
    for y in range(y0, y1):
        row = buf[(y * w + x0) * ch:(y * w + x1) * ch]
        raw.append(0)                              # filter type 0
        if ch == 4:                                # 丢 alpha（自由版式不接受透明位图）
            row = bytearray(row)
            del row[3::4]
        raw += row
    return _encode_png(cw, chh, bytes(raw))


def _encode_png(w, h, rgb):
    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", binascii.crc32(typ + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rgb, 6))
            + chunk(b"IEND", b""))


# --------------------------------------------------------------------------
# 生成
# --------------------------------------------------------------------------

def _tok(tokens, key, fallback):
    try:
        return tokens["values"]["color"][key]["value"]
    except (KeyError, TypeError):
        return fallback


def _pct(v, total):
    return round(100.0 * v / total, 3)


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def build_html(spec, image_path=None):
    errs, _ = ff_spec.validate(spec)
    if errs:
        raise ValueError("FreeformSpec 校验失败：%s" % "；".join(errs))

    canvas = spec["canvas"]
    cw, ch = canvas["w"], canvas["h"]
    tok = spec.get("tokens") or {}
    # canvas.bg 是从像素量到的页面承载面本色，优先于令牌（令牌里的 pageBg 可能量到外框）
    c_bg = canvas.get("bg") or _tok(tok, "pageBg", DEFAULTS["pageBg"])
    c_card = _tok(tok, "card", DEFAULTS["card"])
    c_brand = _tok(tok, "brand", DEFAULTS["brand"])
    c_ink = _tok(tok, "ink", DEFAULTS["ink"])
    slots = spec.get("slots") or {}
    sem = spec.get("semantics") or {}

    # 语义落地：声明的 scroll/toggle 由生成器直接实现为真实行为（确定性），
    # confirms 只验不造（确认弹层是业务实现，不由模板伪造）。
    acts = {}
    for it in sem.get("interactions") or []:
        trig = it.get("trigger")
        if it.get("type") == "scroll" and it.get("target"):
            acts[trig] = ("scroll", it["target"])
        elif it.get("type") == "toggle" and it.get("target"):
            acts[trig] = ("toggle", it["target"])
    toggle_hidden = {t for op, t in acts.values() if op == "toggle"}

    parts = []
    for r in spec["regions"]:
        rid, role, keep = r["id"], r["role"], r["keep"]
        x0, y0, x1, y1 = r["bbox"]
        st = r.get("style") or {}
        css = ["position:absolute", "left:%s%%" % _pct(x0, cw), "top:%s%%" % _pct(y0, ch),
               "width:%s%%" % _pct(x1 - x0, cw), "height:%s%%" % _pct(y1 - y0, ch)]
        if st.get("radius") is not None:
            css.append("border-radius:%dpx" % st["radius"])
        if role in ("card", "container", "image", "illustration", "divider") and st.get("bg"):
            css.append("background:%s" % st["bg"])

        onclick = ""
        if rid in acts:
            op, tgt = acts[rid]
            if op == "scroll":
                onclick = ' onclick="document.getElementById(\'%s\').scrollIntoView()"' % tgt
            else:
                onclick = (' onclick="var t=document.getElementById(\'%s\');'
                           't.hasAttribute(\'hidden\')?t.removeAttribute(\'hidden\'):t.setAttribute(\'hidden\',\'\')"'
                           % tgt)
        hidden_attr = " hidden" if rid in toggle_hidden else ""

        if keep == "crop":
            if not image_path:
                raise ValueError("region[%s] 是 crop 区块但未提供源图" % rid)
            png = crop_png(image_path, r["bbox"])
            uri = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
            inner = ('<img alt="" src="%s" style="display:block;width:100%%;height:100%%;'
                     'object-fit:cover">' % uri)
            # id 与 data-ff-region 同名输出：语义 onclick 用 getElementById 锚定目标区块
            parts.append('<div id="%s" data-ff-region="%s" class="ff-crop" '
                         'style="%s">%s</div>' % (rid, rid, ";".join(css), inner))
            continue

        # rebuild：文字必须是真文字
        text = slots.get(r.get("slot")) or r.get("text") or ""
        align = st.get("align") or ("center" if role == "button" else "left")
        bg = st.get("bg") or {"card": c_card, "nav": "", "header": ""}.get(role, "")
        if role == "button" and not st.get("bg"):
            bg = c_brand
        color = st.get("color") or (c_ink if role != "button" else "#FFFFFF")
        fs = st.get("fontSize") or max(10, min(96, int((y1 - y0) * (0.62 if role in ("text", "header") else 0.45))))
        weight = 700 if (st.get("bold") or role in ("header", "button")) else 400
        cursor = "cursor:pointer;" if rid in acts else ""
        parts.append(
            '<div id="%s" data-ff-region="%s" class="ff-%s"%s%s style="%s;display:flex;%s'
            'align-items:center;justify-content:%s;background:%s;color:%s;'
            'font-size:%dpx;font-weight:%d;line-height:1.35;overflow:hidden;'
            'white-space:nowrap">%s</div>'
            % (rid, rid, role, hidden_attr, onclick, ";".join(css), cursor,
               {"left": "flex-start", "center": "center", "right": "flex-end"}[align],
               bg, color, fs, weight, _esc(text)))

    regions_html = "\n    ".join(parts)
    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s</title>
<style>
  html,body{margin:0;padding:0;background:%s}
  .ff-stage{width:100%%;overflow:hidden}
  .ff-canvas{position:relative;transform-origin:0 0;font-family:"PingFang SC","Microsoft YaHei",Arial,sans-serif}
  .ff-crop img{-webkit-user-drag:none}
</style>
</head>
<body>
<div class="ff-stage" id="ffStage">
  <div class="ff-canvas" id="ffCanvas" style="width:%dpx;height:%dpx;background:%s">
    %s
  </div>
</div>
<script>
(function(){var W=%d,H=%d,C=document.getElementById("ffCanvas"),S=document.getElementById("ffStage");
function fit(){var s=Math.min(1,document.documentElement.clientWidth/W);C.style.transform="scale("+s+")";S.style.height=(H*s)+"px";}
window.addEventListener("resize",fit);fit();})();
</script>
</body>
</html>
""" % (_esc(spec.get("name") or "自由版式页面"), c_bg, cw, ch, c_bg, regions_html, cw, ch)
    return html


def main():
    ap = argparse.ArgumentParser(description="FreeformSpec → 单文件 HTML（确定性）")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--image", help="源图（crop 区块需要）")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    with open(a.spec, encoding="utf-8") as f:
        spec = json.load(f)
    html = build_html(spec, a.image)
    dst = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    print("已生成自由版式页面: %s（%.1f KB）" % (dst, len(html.encode("utf-8")) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
