#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""截图独立还原 · 确定性渲染器（与内置 A1–A6 链路完全隔离）

输入是一份**独立截图结构契约** `screenshot.spec.json`（kind:"screenshot"），
不是 PageSpec，也不套 A1–A6：

    {
      "kind": "screenshot",
      "canvas": {"w": 1600, "h": 900},
      "page": {"title": "...", "breadcrumb": [...], "brand": "..."},
      "elements": [
        {"id":"e1","type":"text|input|select|date|textarea|checkbox|radio|button|table|image|divider|panel|title",
         "bbox":[x,y,w,h], "text":"原样照抄的文字", "label":"字段标签",
         "control":"input|select|date|textarea", "required":false, "primary":false,
         "options":["枚举取值(select 时)"], "style":{"fontSize":14,"bold":false,"align":"left","color":"#333","bg":"#fff","radius":6},
         "table":{"columns":[{"text":"列头"}], "rowCount":12, "rowActions":["编辑"]},
         "children":[ ... 同结构，bbox 同样相对整图左上角 ... ]},
        ...
      ],
      "uncertain": ["看不清或靠猜的地方，逐条写明"]
    }

设计要点（与用户产品边界一致）：
  · **不套 A1–A6**：截图只还原"原图结构 + 原图样式"，不强行归类为列表/表单等原型。
  · **不走 forge / recipes**：本脚本零依赖 forge.py 与 recipes.json，确定性渲染。
  · **按 bbox 还原布局**：每个元素绝对定位到原图坐标，整体版式与原图一致。
  · **默认即原图还原，不 premium**：不带任何主题预设。
  · **不整图铺底冒充**：图片元素渲染成占位框，不把整张截图当背景。
  · **不推断交互、不编业务**：表格单元格用「—」占位，未知按钮点击只给演示反馈。
  · **安全**：所有文字 html.escape；不引任何外链；唯一的脚本是固定的演示反馈运行时
    （不来自模型数据，不可被注入）；不写任何密钥。

用法:
    python build/screenshot_render.py --spec screenshot.spec.json --out page.html
    python build/screenshot_render.py --spec screenshot.spec.json --out page.html --report acceptance.json
"""
import argparse
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

CONTRACT_VERSION = "screenshot-1.0"

VALID_TYPES = ("panel", "text", "title", "input", "select", "date", "textarea",
               "checkbox", "radio", "button", "table", "image", "divider")

# 这些按钮文案是"已知动作"，仍只给演示反馈，但文案更克制（不伪造业务行为）
KNOWN_ACTIONS = ("新建", "新增", "导出", "导入", "查询", "搜索", "筛选", "重置", "提交",
                 "保存", "暂存", "取消", "关闭", "编辑", "删除", "审核", "审批", "打印",
                 "下载", "上传", "确定", "确认", "上一页", "下一页", "刷新")


def esc(s):
    """转义所有用户/模型可见文字，杜绝 XSS 与事件注入。"""
    return html.escape(str(s), quote=True)


# --------------------------------------------------------------------------
# 契约校验（截图入口用：旧 A1–A6 extract 不被接受）
# --------------------------------------------------------------------------

def validate(spec):
    errs, warns = [], []
    if not isinstance(spec, dict):
        return ["spec 不是对象"], warns
    if spec.get("kind") != "screenshot":
        errs.append("kind 必须为 'screenshot'（独立截图结构，不套 A1–A6）；"
                    "旧 extract（含 archetype / fields / columns）请改用截图独立识别重新生成")
    cv = spec.get("canvas") or {}
    if not (isinstance(cv.get("w"), int) and isinstance(cv.get("h"), int)
            and cv["w"] > 0 and cv["h"] > 0):
        errs.append("canvas.w/h 必须为正整数")
    els = spec.get("elements") or []
    if not els:
        errs.append("elements 为空：请重新识别截图，禁止生成占位模板")
    ids = set()

    def walk(nodes, path):
        for e in nodes:
            if not isinstance(e, dict):
                errs.append("%s 元素不是对象" % path)
                continue
            eid = e.get("id")
            if not eid:
                errs.append("%s 缺 id" % path)
            elif eid in ids:
                errs.append("重复 element id: %s" % eid)
            else:
                ids.add(eid)
            if not isinstance(e, dict):
                errs.append("%s 不是对象" % (path + (eid or "?")))
                continue
            # confidence / source：S1 新增的可追溯字段。旧契约（无字段）向后兼容，缺省不报错。
            conf = e.get("confidence")
            if conf is not None:
                if not isinstance(conf, (int, float)) or isinstance(conf, bool) \
                        or not (0 <= conf <= 1):
                    errs.append("element %s confidence 必须为 0~1 的数字" % eid)
            src = e.get("source")
            if src is not None and src not in ("pixel", "vlm", "user"):
                errs.append("element %s source 必须为 pixel|vlm|user，收到 %r" % (eid, src))
            bbox = e.get("bbox")
            if not (isinstance(bbox, list) and len(bbox) == 4
                    and all(isinstance(v, (int, float)) for v in bbox)):
                errs.append("element %s bbox 必须为 [x,y,w,h]" % eid)
            else:
                x, y, w, h = [float(v) for v in bbox]
                if w <= 0 or h <= 0:
                    errs.append("element %s 宽高须 > 0" % eid)
                if x < 0 or y < 0:
                    errs.append("element %s 坐标须 >= 0" % eid)
                if cv.get("w") and x + w > cv["w"] + 1:
                    warns.append("element %s 右边界超出画布宽" % eid)
                if cv.get("h") and y + h > cv["h"] + 1:
                    warns.append("element %s 下边界超出画布高" % eid)
            t = e.get("type")
            if t not in VALID_TYPES:
                errs.append("element %s 未知 type: %s" % (eid, t))
            if t == "table":
                tb = e.get("table") or {}
                cols = tb.get("columns") or []
                if not cols:
                    errs.append("element %s 是表格但缺 columns" % eid)
                else:
                    for c in cols:
                        if not (isinstance(c, dict) and isinstance(c.get("text"), str)) and not isinstance(c, str):
                            errs.append("element %s 表格列头需为含 text 的对象或字符串" % eid)
                            break
            walk(e.get("children") or [], path + (eid or "?") + ".")

    walk(els, "")
    return errs, warns


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------

def _safe_color(v):
    """只放行字面颜色：绝不接受 CSS 声明、URL 或来自模型数据的 HTML。"""
    return isinstance(v, str) and bool(re.fullmatch(r"#[0-9a-fA-F]{3,8}|transparent|white|black", v))


def box_style(e):
    x, y, w, h = [int(round(v)) for v in e["bbox"]]
    s = "position:absolute;left:%dpx;top:%dpx;width:%dpx;height:%dpx;" % (x, y, w, h)
    st = e.get("style") or {}
    if st.get("fontSize"):
        s += "font-size:%dpx;" % int(st["fontSize"])
    if st.get("bold"):
        s += "font-weight:700;"
    if st.get("align") in ("center", "right"):
        s += "text-align:%s;" % st["align"]
    # 颜色类键名兼容两套写法：渲染器旧契约用 bg/radius/borderWidth，
    # 视觉模型自然输出 backgroundColor/borderRadius/border（字符串）——此前被静默丢弃，
    # 导致分组蓝条、按钮深蓝底、输入框灰底整页丢失。两套都收，别再让样式对不上键名就消失。
    if _safe_color(st.get("color")):
        s += "color:%s;" % st["color"]
    bg = st.get("bg") if _safe_color(st.get("bg")) else None
    if bg is None and _safe_color(st.get("backgroundColor")):
        bg = st["backgroundColor"]
    if bg:
        s += "background:%s;" % bg
    if _safe_color(st.get("borderColor")):
        s += "border-color:%s;" % st["borderColor"]
    if _safe_color(st.get("accentColor")):
        s += "accent-color:%s;" % st["accentColor"]
    m = re.fullmatch(r"\s*(\d+)px\s+solid\s+(#[0-9a-fA-F]{3,8})\s*", str(st.get("border") or ""))
    if m:  # "1px solid #dfe4ed" 形式：宽与色一起生效
        s += "border:%dpx solid %s;" % (max(0, min(10, int(m.group(1)))), m.group(2))
    elif isinstance(st.get("borderWidth"), (int, float)):
        s += "border:%dpx solid;" % max(0, min(10, int(st["borderWidth"])))
    radius = st.get("radius") if st.get("radius") is not None else st.get("borderRadius")
    if radius:
        s += "border-radius:%dpx;" % int(radius)
    return s


def _cell_html(text, action_styles):
    """普通单元格转义输出；操作列按 rowActionStyles 给动作词上色（只放行字面颜色）。"""
    if not action_styles:
        return esc(text)
    parts = []
    for tok in str(text).split():
        st = action_styles.get(tok)
        if isinstance(st, dict) and _safe_color(st.get("color")):
            parts.append('<span style="color:%s">%s</span>' % (st["color"], esc(tok)))
        else:
            parts.append(esc(tok))
    return " ".join(parts)


def render_table(e):
    tb = e.get("table") or {}
    cols = tb.get("columns") or []
    ts = e.get("style") or {}
    head_bg = ts.get("headerBackground") if _safe_color(ts.get("headerBackground")) else None
    head_h = ts.get("headerHeight") if isinstance(ts.get("headerHeight"), (int, float)) else None
    row_h = ts.get("rowHeight") if isinstance(ts.get("rowHeight"), (int, float)) else None
    row_bgs = ts.get("rowBackgrounds") if isinstance(ts.get("rowBackgrounds"), list) else []
    act_styles = ts.get("rowActionStyles") if isinstance(ts.get("rowActionStyles"), dict) else {}
    _th_st = ("background:%s;" % head_bg if head_bg else "") + \
             ("height:%dpx;" % max(1, min(200, int(head_h))) if head_h else "")
    th_attr = ' style="%s"' % _th_st if _th_st else ""
    heads = "".join("<th%s>%s</th>" % (th_attr, esc(c["text"]) if isinstance(c, dict) else esc(c)) for c in cols)
    colgroup = "<colgroup>" + "".join(
        '<col style="width:%dpx">' % max(1, min(10000, int(c["width"])))
        if isinstance(c, dict) and isinstance(c.get("width"), (int, float)) else '<col>'
        for c in cols) + "</colgroup>"
    rowacts = tb.get("rowActions") or []
    # 模型可能已把「操作」作为真实列输出（columns 里含"操作"），此时不得再追加按钮列——
    # 否则表头多一列、colgroup 少一列，table-layout:fixed 下第 N+1 列塌缩 + 横向溢出被裁，表格显示不全。
    has_action_col = any((c.get("text") if isinstance(c, dict) else c) == "操作" for c in cols)
    append_action = bool(rowacts) and not has_action_col
    if append_action:
        heads += '<th%s class="c">操作</th>' % th_attr
        colgroup += '<col>'
    # 表格标题（原图 table 的 text）作为 caption 还原，确保文字落进 DOM
    caption = ""
    if e.get("text"):
        caption = "<caption>%s</caption>" % esc(e["text"])
    data = tb.get("rows") or []
    n = min(200, len(data) if data else max(0, int(tb.get("rowCount", 0))))
    rows = []
    for i in range(n):
        cells = ""
        row = data[i] if data and isinstance(data[i], list) else []
        bg_i = row_bgs[i] if i < len(row_bgs) else None
        td_style = ""
        if _safe_color(bg_i):
            td_style += "background:%s;" % bg_i
        if row_h:
            td_style += "height:%dpx;" % max(1, min(200, int(row_h)))
        td_attr = ' style="%s"' % td_style if td_style else ""
        for ci, c in enumerate(cols):
            v = row[ci] if ci < len(row) else "—"
            cells += "<td%s>%s</td>" % (td_attr, _cell_html(v, act_styles))
        if append_action:
            btns = "".join('<button class="ctl btn sm" data-el="%s" type="button">%s</button>'
                           % (esc(e["id"]), esc(a)) for a in rowacts)
            cells += "<td%s>%s</td>" % (td_attr, btns)
        rows.append("<tr>%s</tr>" % cells)
    return ("<table class='ss-table'>" + colgroup + "%s<thead><tr>%s</tr></thead><tbody>%s</tbody></table>"
            ) % (caption, heads, "".join(rows))


def render_control(e):
    ph = esc(e.get("text") or e.get("label") or "")
    c = e.get("control") or e["type"]
    if c == "select":
        opts = e.get("options") or []
        # 第一个缺省项放标签/文字，确保字段名落进 DOM 且为可识别的占位选择
        value = str(e.get("value") or "")
        if value and value not in [str(o) for o in opts]:
            opts = [value] + opts
        opt_html = "" if value else "<option disabled selected value=''>%s</option>" % ph
        opt_html += "".join("<option%s>%s</option>" % (" selected" if str(o) == value else "", esc(o)) for o in opts)
        if not opts:
            opt_html = "<option>请选择</option>"
        return "<select class='ctl'>%s</select>" % opt_html
    if c == "date":
        # 无文本子节点，用 title 携带字段名，确保文字落进 DOM（不伪造业务）
        return "<input type='date' class='ctl' title='%s'>" % ph
    if c == "textarea":
        return "<textarea class='ctl' placeholder='%s'></textarea>" % ph
    return "<input class='ctl' value='%s' placeholder='%s'>" % (esc(e.get("value") or ""), ph)


def render_element(e):
    eid = e.get("id", "?")
    t = e.get("type", "text")
    style = box_style(e)
    cls = "el el-%s" % t
    if t == "table":
        inner = render_table(e)
    elif t in ("input", "select", "date", "textarea"):
        inner = render_control(e)
    elif t == "button":
        inner = "<button class='ctl btn' type='button' data-el='%s'>%s</button>" % (
            esc(eid), esc(e.get("text") or e.get("label") or "按钮"))
    elif t == "checkbox":
        inner = "<label class='ctl chk'><input type='checkbox'> %s</label>" % esc(e.get("text") or "")
    elif t == "radio":
        inner = "<label class='ctl rdo'><input type='radio' name='%s'%s> %s</label>" % (esc(e.get("label") or eid), " checked" if e.get("checked") else "", esc(e.get("text") or ""))
    elif t == "image":
        inner = "<div class='ph-img' role='img' aria-label='%s' title='%s'>[图片占位]</div>" % (esc(e.get("text") or "图片"), esc(e.get("text") or "图片"))
    elif t == "divider":
        inner = ""
    else:
        inner = "<span class='txt'>%s</span>" % esc(e.get("text") or "")
    label_html = ""
    if t in ("input", "select", "date", "textarea") and e.get("label"):
        # New contracts provide exact labelBBox; legacy results use an explicit inferred position.
        lb = e.get("labelBBox")
        if not isinstance(lb, list) or len(lb) != 4:
            x, y, w, h = e["bbox"]
            lb = [x, max(0, y - 26), w, 20]
        label = {"bbox": lb, "style": {"fontSize":12, "color":"#333333"}}
        label_html = "<div class='ss-label' data-label-for='%s' style='%s'>%s%s</div>" % (
            esc(eid), box_style(label), "<span style='color:#d33'>* </span>" if e.get("required") else "", esc(e["label"]))
    return label_html + "<div class='%s' id='el-%s' style='%s'>%s</div>" % (cls, esc(eid), style, inner)


def collect(nodes, out):
    for e in nodes:
        out.append(e)
        collect(e.get("children") or [], out)


def render(spec):
    cv = spec.get("canvas", {"w": 1280, "h": 800})
    W, H = int(cv.get("w", 1280)), int(cv.get("h", 800))
    page = spec.get("page") or {}
    title = page.get("title") or "截图还原页面"
    brand = page.get("brand") or ""
    crumb = page.get("breadcrumb") or []
    els = []
    collect(spec.get("elements") or [], els)
    parts = [render_element(e) for e in els]
    body_inner = "\n".join(parts)

    crumb_html = " / ".join(esc(c) for c in crumb) if crumb else esc(title)
    top_html = ""
    if brand or crumb:
        top_html = "<div class='topbar'><span class='brand'>%s</span><span class='crumb'>%s</span></div>" % (
            esc(brand), crumb_html)

    uncertain = spec.get("uncertain") or []
    if uncertain:
        def _fmt_uncertain(u):
            if isinstance(u, dict):
                # S1 结构化条目：elementId / field / why
                return "%s · %s：%s" % (esc(u.get("elementId", "?")),
                                        esc(u.get("field", "?")),
                                        esc(u.get("why", "")))
            return esc(u)
        items = "".join("<li>%s</li>" % _fmt_uncertain(u) for u in uncertain)
        review = ("<div class='review-banner'>⚠ 待人工复核：AI 仅还原原图的结构与样式，"
                  "交互与业务内容需人工确认；本页未推断、未编造业务。"
                  "<ul>%s</ul></div>" % items)
    else:
        review = ("<div class='review-banner ok'>✓ 已通过截图结构还原；"
                  "交互与业务内容仍建议人工复核确认。</div>")

    doc = TEMPLATE % {
        "title": esc(title),
        "top": top_html,
        "review": review,
        "cv_w": W, "cv_h": H,
        "body": body_inner,
        "disclaimer": ("本页由「截图结构」独立还原，演示占位数据，非真实业务；"
                       "按钮交互仅为演示反馈，待人工确认后填充真实逻辑。"),
    }
    return doc


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s · 截图还原</title>
<style>
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Microsoft YaHei",sans-serif;color:#1c2230;background:#f4f6fa;font-size:14px}
.topbar{display:flex;align-items:center;gap:16px;padding:10px 16px;background:#fff;border-bottom:1px solid #e6e9f0}
.topbar .brand{font-weight:700;color:#2b5cff}
.topbar .crumb{color:#5a6478;font-size:13px}
.review-banner{background:#fff7e6;border:1px solid #ffd591;color:#874d00;padding:8px 14px;font-size:12px;line-height:1.6}
.review-banner.ok{background:#f0fbf3;border-color:#b7ebc8;color:#1f7a3d}
.review-banner ul{margin:4px 0 0;padding-left:18px}
.wrap{max-width:100%%;margin:0 auto}
.cv{position:relative;background:#fff;margin:0 auto;overflow:hidden;border:1px solid #eef0f4}
.el{overflow:hidden}
.el-text .txt,.el-title .txt{display:block;width:100%%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.el-panel{}
.ctl{width:100%%;height:100%%;border:1px solid #d5d9e3;border-radius:inherit;padding:2px 6px;font:inherit;background:inherit;color:inherit}
.ss-label{white-space:nowrap;line-height:20px;overflow:hidden}
.el-radio .ctl,.el-checkbox .ctl{border:0;padding:0;background:transparent;white-space:nowrap}
.el-radio input,.el-checkbox input{width:13px;height:13px;margin:0 4px 0 0;vertical-align:middle}
.ctl.btn{background:inherit;color:inherit;border:1px solid #9baed0;border-radius:inherit;cursor:pointer;font:inherit;padding:0 4px;width:100%%}
.ctl.btn.sm{font-size:12px;padding:2px 8px;background:#eef3ff;color:#2b5cff}
.el-checkbox .ctl,.el-radio .ctl{width:auto;height:auto;display:inline-block;vertical-align:middle;margin-right:4px}
.el-radio,.el-checkbox{overflow:visible;line-height:16px}
.el-radio input,.el-checkbox input{accent-color:#1682ea}
.ss-table{width:100%%;height:100%%;table-layout:fixed;border-collapse:collapse;font:inherit;background:inherit}
.ss-table caption{caption-side:top;text-align:left;font-weight:700;padding:2px 2px 6px;color:#1c2230;font-size:14px}
.ss-table th,.ss-table td{border:1px solid #e6e9f0;padding:6px 10px;text-align:left;vertical-align:top}
.ss-table th{background:#f4f6fa;font-weight:600}
.ss-table td{height:30px}
.ss-note{font-size:11px;color:#8b93a5;padding:4px 2px}
.ph-img{width:100%%;height:100%%;border:1px dashed #c9d1de;background:#f7f9fc;display:flex;align-items:center;justify-content:center;color:#8b93a5;font-size:12px;text-align:center;padding:6px}
.foot{padding:10px 16px;color:#8b93a5;font-size:12px;border-top:1px solid #eef0f4}
.toast{position:fixed;left:50%%;bottom:24px;transform:translateX(-50%%);background:#1c2230;color:#fff;padding:8px 14px;border-radius:8px;font-size:13px;opacity:0;transition:opacity .2s;z-index:10000;max-width:80%%}
.toast.show{opacity:1}
</style>
</head>
<body>
<div class="wrap">
%(top)s
%(review)s
<div class="canvas-viewport"><div class="cv" data-width="%(cv_w)d" data-height="%(cv_h)d" style="width:%(cv_w)dpx;height:%(cv_h)dpx;transform-origin:top left">
%(body)s
</div></div>
<div class="foot">%(disclaimer)s</div>
</div>
<script>
/* 唯一的脚本：固定的演示反馈运行时（不来自模型数据，不可被注入）。 */
window.__PAGE_ERRORS__ = [];
window.addEventListener('error', function (e) { window.__PAGE_ERRORS__.push(String(e.message || e)); });
function showToast(msg){
  var d = document.querySelector('.toast');
  if(!d){ d = document.createElement('div'); d.className='toast'; document.body.appendChild(d); }
  d.textContent = msg; d.classList.add('show');
  clearTimeout(d._t); d._t = setTimeout(function(){ d.classList.remove('show'); }, 2800);
}
function fitCanvas(){
  var cv=document.querySelector('.cv'),vp=document.querySelector('.canvas-viewport');
  if(!cv||!vp)return;
  var w=Number(cv.dataset.width),h=Number(cv.dataset.height);
  var scale=Math.min(1,document.documentElement.clientWidth/w);
  cv.style.transform='scale('+scale+')';cv.style.margin='0';
  vp.style.height=(h*scale)+'px';vp.style.width='100%%';vp.style.overflow='hidden';
}
window.addEventListener('resize',fitCanvas);
document.addEventListener('DOMContentLoaded', function(){
  fitCanvas();
  document.querySelectorAll('.ctl.btn').forEach(function(b){
    b.addEventListener('click', function(ev){
      ev.preventDefault();
      var label = (b.textContent || '按钮').trim();
      showToast('演示反馈：截图识别到的按钮「' + label + '」，其行为需人工确认（未推断业务交互）');
    });
  });
});
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description="截图独立还原 · 确定性渲染器")
    ap.add_argument("--spec", required=True, help="screenshot.spec.json 路径")
    ap.add_argument("--out", required=True, help="输出 HTML 路径")
    args = ap.parse_args()

    spec = json.load(open(args.spec, encoding="utf-8"))
    errs, warns = validate(spec)
    if errs:
        sys.stderr.write("截图契约校验失败：\n  " + "\n  ".join(errs) + "\n")
        return 1
    for w in warns:
        sys.stderr.write("  ⚠ " + w + "\n")

    doc = render(spec)
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc)
    sys.stderr.write("已生成截图还原页: %s (%.1f KB)\n" % (out, len(doc.encode("utf-8")) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
