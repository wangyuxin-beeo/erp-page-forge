#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""截图还原对比页 —— 把「用户给的原图」与「生成物」并排摆出来，附客观指标与验收结论。

为什么要单独做这一页：用户真正关心的是"像不像"，而"像不像"最直观的呈现方式
就是两张图并排看。像素指标与断言数字是佐证，不是主体。

输出为**零外链**单文件 HTML（图片以 data URI 内嵌），可直接发给评审或同事。

用法:
    python build/compare_shot.py \
      --pair "标题|原图.png|生成物.png|验收.json|一句话说明" \
      --pair "..." \
      --out build/shot-compare-report.html
"""

import argparse
import base64
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import verify_visual  # noqa: E402


def data_uri(path):
    with open(path, "rb") as f:
        b = f.read()
    ext = os.path.splitext(path)[1].lstrip(".").lower() or "png"
    return "data:image/%s;base64,%s" % ("jpeg" if ext in ("jpg", "jpeg") else ext,
                                        base64.b64encode(b).decode("ascii"))


def load_acc(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            r = json.load(f)
    except (ValueError, OSError):
        return None
    return r.get("summary") or {}


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def build_card(title, desc, expect_png, actual_png, acc_json):
    e, a = verify_visual.features(expect_png), verify_visual.features(actual_png)
    checks, score = verify_visual.compare(e, a)
    acc = load_acc(acc_json)

    def metric(label, ev, ok):
        tone = "ok" if ok else "bad"
        return ('<div class="m"><span class="mk">%s</span>'
                '<span class="mv %s">%s</span></div>' % (esc(label), tone, esc(ev)))

    skip = {"id", "pass", "evidence", "severity"}
    rows = []
    for c in checks:
        rows.append(metric(c["id"].replace("visual.", ""), c["evidence"], c["pass"]))
    rows.append(metric("还原度评分", "%d / 100（硬性视觉项）" % score, score >= 100))
    if acc:
        ok = (acc.get("hardFailed", 0) == 0)
        rows.append(metric("四类断言", "%d 项：通过 %d · 硬性失败 %d · 需人工 %d"
                           % (acc.get("total", 0), acc.get("passed", 0),
                              acc.get("hardFailed", 0), acc.get("unsupported", 0)), ok))

    return """
  <section class="card">
    <div class="chead">
      <h2>%s</h2>
      <p>%s</p>
    </div>
    <div class="shots">
      <figure>
        <img src="%s" alt="原图">
        <figcaption>① 用户提供的截图（别人的系统）</figcaption>
      </figure>
      <figure>
        <img src="%s" alt="生成物">
        <figcaption>② 生成物（编译后的实际渲染）</figcaption>
      </figure>
    </div>
    <div class="metrics">%s</div>
  </section>""" % (esc(title), esc(desc), data_uri(expect_png), data_uri(actual_png),
                   "".join(rows))


PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>截图还原对比 · ERP Page Forge</title>
<style>
  :root{--ink:#1C2230;--ink2:#5A6478;--ink3:#8B93A5;--line:#E6E9F0;--bg:#F4F6FA;
        --card:#FFF;--ok:#12A150;--bad:#E5484D;--brand:#2B5CFF}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--ink);font:14px/1.6 -apple-system,"Microsoft YaHei",sans-serif;
       padding:28px 22px 48px}
  .wrap{max-width:1240px;margin:0 auto}
  header{margin-bottom:20px}
  h1{font-size:22px;font-weight:600;letter-spacing:.2px}
  .lead{color:var(--ink2);margin-top:8px;font-size:13.5px;max-width:900px}
  .lead b{color:var(--ink)}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;
        padding:18px 20px 20px;margin-bottom:18px}
  .chead h2{font-size:16px;font-weight:600}
  .chead p{color:var(--ink2);font-size:12.5px;margin-top:4px}
  .shots{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin:14px 0}
  figure{min-width:0}
  figure img{width:100%;display:block;border:1px solid var(--line);border-radius:6px;background:#fff}
  figcaption{font-size:12px;color:var(--ink3);margin-top:6px}
  .metrics{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:8px 16px;
           border-top:1px dashed var(--line);padding-top:12px}
  .m{display:flex;gap:10px;font-size:12.5px;align-items:baseline}
  .mk{color:var(--ink2);flex:0 0 132px}
  .mv{color:var(--ink)}
  .mv.ok{color:var(--ok)}
  .mv.bad{color:var(--bad);font-weight:600}
  footer{color:var(--ink3);font-size:12px;margin-top:22px;line-height:1.8}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>截图还原对比报告</h1>
    <p class="lead">
      左边是<b>用户直接丢过来的截图</b>（取自两个风格完全不同的外部系统，不是本 Skill 的模板），
      右边是<b>生成代码编译后的真实渲染</b>。两张图都由无头浏览器按同一视口截取，
      指标由脚本从像素里量出来 —— 版式与配色对齐到什么程度，可以自己核。
    </p>
  </header>
{{CARDS}}
  <footer>
    指标口径：侧栏宽度与顶栏高度按像素测量（容差 10px / 8px）；主色取高饱和像素众数（色距容差 46）；
    深色像素占比用于判断整体版式（深色侧栏 + 浅色卡片）。<br>
    像素比对只覆盖版式与配色；列名、按钮、状态等文本内容由契约/渲染/交互/数据四类断言核对，两者互补。<br>
    图中数据均为虚构演示数据，与任何真实企业无关。由 ERP Page Forge 生成。
  </footer>
</div>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description="截图还原对比页（零外链单文件）")
    ap.add_argument("--pair", action="append", required=True,
                    help="标题|原图|生成物|验收JSON|说明（可重复）")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cards = []
    for raw in args.pair:
        parts = raw.split("|")
        while len(parts) < 5:
            parts.append("")
        title, expect, actual, acc, desc = parts[:5]
        for p in (expect, actual):
            if not os.path.exists(p):
                raise SystemExit("[错误] 图片不存在: " + p)
        cards.append(build_card(title, desc, expect, actual, acc))

    # 用占位替换而不是 % 格式化：模板里的 CSS 有 width:100% 这类字符，会被 % 当格式符
    html = PAGE.replace("{{CARDS}}", "\n".join(cards))

    # 零外链是项目硬约定，这里自查（data URI 不算外链）
    body = html.replace("http://www.w3.org", "")
    for bad in ("http://", "https://"):
        if bad in body:
            raise SystemExit("[错误] 输出含外链 " + bad)

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    print("对比报告已生成: %s（%.0f KB，零外链）" % (out, len(html.encode("utf-8")) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
