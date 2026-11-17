#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""截图独立还原 · 验收（与内置四类断言完全独立）

只验收「截图结构还原」这一链路的硬指标，不依赖 A1–A6 契约：
  · 内容：每个元素的文字（原样照抄）都出现在产物 DOM 里
  · 坐标：每个元素的 bbox 原样写成 left/top/width/height，无越界
  · 样式：声明了 fontSize 的元素确实带了 font-size
  · 安全：零外链（http(s)/// 协议）、脚本仅 1 个固定运行时、模型文字里的
         < > 都被转义（XSS 不被注入）、不写密钥

输出 acceptance.json（summary 形状兼容 build_report 风格）+ report.html。

用法:
    python build/verify_screenshot.py --spec screenshot.spec.json --artifact page.html --out acceptance.json
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import screenshot_render  # noqa: E402

SEV = {"content": "契约", "coord": "渲染", "style": "视觉", "security": "安全"}


def _collect(nodes, out):
    for e in nodes:
        out.append(e)
        _collect(e.get("children") or [], out)


def verify(spec, html):
    checks = []
    els = []
    _collect(spec.get("elements") or [], els)

    def add(group, name, ok, detail=""):
        checks.append({"group": group, "name": name, "ok": bool(ok), "detail": detail})

    # --- 内容 ---
    for e in els:
        txt = e.get("text")
        if txt:
            # 渲染里用 html.escape，比对转义后的形态
            add("content", "文字存在: %s" % e.get("id"), esc_present(html, txt), txt)

    # --- 坐标 ---
    cv = spec.get("canvas", {})
    for e in els:
        x, y, w, h = [int(round(v)) for v in e["bbox"]]
        token = "left:%dpx;top:%dpx;width:%dpx;height:%dpx" % (x, y, w, h)
        ok = token in html
        add("coord", "坐标: %s" % e.get("id"), ok, token)
        if cv.get("w") and x + w > cv["w"] + 1:
            add("coord", "不越界(宽): %s" % e.get("id"), False, "x+w=%d > %d" % (x + w, cv["w"]))
        if cv.get("h") and y + h > cv["h"] + 1:
            add("coord", "不越界(高): %s" % e.get("id"), False, "y+h=%d > %d" % (y + h, cv["h"]))

    # --- 样式 ---
    for e in els:
        st = e.get("style") or {}
        if st.get("fontSize"):
            add("style", "字号: %s" % e.get("id"), ("font-size:%dpx" % int(st["fontSize"])) in html,
                "font-size:%dpx" % int(st["fontSize"]))

    # --- 安全 ---
    ext = re.search(r'(?:src|href)\s*=\s*["\']\s*https?:', html) or ("javascript:" in html)
    add("security", "无外链(http/https/javascript:)", not ext)
    # 整图铺底冒充：产物不应把原始截图当背景图内联
    add("security", "未整图铺底冒充", "background-image:url(" not in html and "data:image" not in html)
    nscript = html.count("<script")
    add("security", "脚本仅固定运行时(1个)", nscript == 1, "script 标签数=%d" % nscript)
    # 密钥：产物绝不能出现密钥字段
    add("security", "不含密钥/API 字段", not re.search(r'(?:api[_-]?key|secret|token|password)\s*[:=]', html, re.I))
    # XSS：模型文字里的 < > 必须被转义，不得出现活的标签
    for e in els:
        t = e.get("text") or ""
        if "<" in t or ">" in t:
            live = bool(re.search(r"<%s" % re.escape(t.strip("<").split()[0] if t.strip("<") else "x"), html))
            # 简单判据：转义后应是 &lt; 开头；若 HTML 里直接出现未转义的 <script / <img onerror 则失败
            injected = ("<script" in html and esc_present(html, t)) or ("onerror=" in html and "onerror=" in t)
            add("security", "XSS 转义: %s" % e.get("id"), ("&lt;" in html) and not live, t[:40])

    passed = sum(1 for c in checks if c["ok"])
    total = len(checks)
    hard = sum(1 for c in checks if not c["ok"])
    verdict = "PASS" if hard == 0 else "FAIL"
    summary = {"passed": passed, "total": total, "hardFailed": hard, "verdict": verdict,
               "groups": {SEV.get(c["group"], c["group"]): sum(1 for x in checks if x["group"] == c["group"] and x["ok"])
                          for c in checks}}
    return {"summary": summary, "checks": checks}


def esc_present(html, txt):
    """模型文字经过 html.escape 后仍应作为子串出现。"""
    import html as _h
    return _h.escape(txt) in html


def build_report(res, spec, out_html):
    checks = res["checks"]
    rows = "".join(
        "<tr class='%s'><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            "ok" if c["ok"] else "bad", SEV.get(c["group"], c["group"]),
            _h_esc(c["name"]), _h_esc(c.get("detail", "")))
        for c in checks)
    s = res["summary"]
    doc = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>截图还原验收报告</title><style>
body{font-family:-apple-system,"Microsoft YaHei",sans-serif;margin:24px;color:#1c2230}
h1{font-size:20px}.v{font-weight:700;padding:2px 10px;border-radius:6px}
.v.PASS{background:#ecfdf3;color:#1f7a3d}.v.FAIL{background:#fff1f0;color:#cf1322}
table{border-collapse:collapse;width:100%%;margin-top:12px;font-size:13px}
td,th{border:1px solid #e6e9f0;padding:6px 10px;text-align:left}
tr.ok td:first-child{color:#1f7a3d}tr.bad td:first-child{color:#cf1322}
.bad{background:#fff7f6}.muted{color:#8b93a5;font-size:12px}
</style></head><body>
<h1>截图结构还原 · 验收报告</h1>
<p>结论：<span class="v %s">%s</span> · 通过 %d / %d · 硬失败 %d</p>
<p class="muted">本验收仅针对「截图结构还原」链路（内容/坐标/样式/安全），与 A1–A6 内置四类断言相互独立。PASS 只代表结构源码检查通过；真实浏览器渲染、原图像素还原度及业务交互未自动验证，均需人工复核。</p>
<table><thead><tr><th>类别</th><th>检查项</th><th>明细</th></tr></thead><tbody>%s</tbody></table>
</body></html>""" % (s["verdict"], s["verdict"], s["passed"], s["total"], s["hardFailed"], rows)
    with open(out_html, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc)


import html as _h
def _h_esc(s):
    return _h.escape(str(s))


def main():
    ap = argparse.ArgumentParser(description="截图独立还原 · 验收")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--out", required=True, help="acceptance.json")
    ap.add_argument("--report", help="report.html（可选，默认与 out 同目录）")
    args = ap.parse_args()

    spec = json.load(open(args.spec, encoding="utf-8"))
    html = open(args.artifact, encoding="utf-8").read()
    res = verify(spec, html)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    rep = args.report or (os.path.splitext(args.out)[0] + "-report.html")
    try:
        build_report(res, spec, rep)
    except Exception as e:
        sys.stderr.write("报告生成跳过: %s\n" % e)
    s = res["summary"]
    print("截图验收 %s: %d/%d 通过，硬失败 %d" % (s["verdict"], s["passed"], s["total"], s["hardFailed"]))
    return 0 if s["hardFailed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
