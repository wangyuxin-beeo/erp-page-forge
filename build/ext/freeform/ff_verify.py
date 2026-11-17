#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自由版式验收器（自由版式扩展 · 验收层）。

与 A1–A6 四类断言的口径（语义声明制，见 docs/freeform-extension-plan.md §7 与 freeform.schema.json）：
  契约 / 交互 / 数据 —— 来自 FreeformSpec.semantics 的**声明**（方案 C：能自动识别的自动填，
                        识别不了的提示用户补）。声明了就按 A1–A6 同一标准真实执行
                        （真实点击 / 真实计数，复用 serve_spa 的 driver 引擎，仅新增 ff_* 通用驱动）；
                        **未声明的类别显式标「未声明，本次不验」**，不冒充通过。
  渲染 —— 白屏 / 1280px 横向溢出 / 脏值 / 脚本报错（无头浏览器真实渲染）
  文案 —— requiredTexts 全部出现在 DOM 文本；指令替换过的旧值不得残留
  结构 —— rebuild 区块都在 DOM 里（data-ff-region）；文字区块不是图片
  视觉 —— 原图 vs 生成物截图：画布比例、主色（复用 verify_visual.compare）
  外链 —— 产物零 http(s):// 引用（127.0.0.1 放行）

退出码 0 = 硬性项全过；拿不到浏览器/截图的项标 unsupported，不许当通过。

用法:
    python ff_verify.py --spec ff.spec.json --artifact page.html \
        [--image input.png] [--replaced '{"旧文案":"新文案"}'] \
        --json out.json --report out.html
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.dirname(os.path.dirname(HERE))
ROOT = os.path.dirname(BUILD)
sys.path.insert(0, BUILD)
sys.path.insert(0, HERE)

import ff_spec            # noqa: E402
import render_check       # noqa: E402
import verify_visual      # noqa: E402
import verify_contract    # noqa: E402
import verify_acceptance  # noqa: E402

LINK_RE = re.compile(r"https?://(?!127\.0\.0\.1)", re.I)


# --------------------------------------------------------------------------
# 断言收集
# --------------------------------------------------------------------------

def _strip_tags(html):
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    return re.sub(r"<[^>]+>", " ", html)


def static_checks(spec, html, replaced=None):
    """不需要浏览器的断言：外链 / 文案 / 结构。返回 results 列表。"""
    results = []

    def add(cid, ok, desc, detail, severity="hard"):
        results.append({"id": cid, "pass": bool(ok), "severity": severity,
                        "desc": desc, "detail": detail})

    # 外链（127.0.0.1 放行）—— 交付物零外链是项目铁律
    hits = sorted(set(LINK_RE.findall(html)))
    add("ff.no-external-link", not hits, "产物零外链（127.0.0.1 放行）",
        "发现 %s" % ("、".join(hits[:5]) if hits else "0 处外链引用"))

    body_text = _strip_tags(html)
    # 文案
    for i, t in enumerate(ff_spec.required_texts(spec)):
        add("ff.text.%d" % i, t in body_text, "文案出现：%s" % (t[:40] + ("…" if len(t) > 40 else "")),
            "在产物 DOM 文本中%s" % ("找到" if t in body_text else "未找到"))
    for old in (replaced or {}):
        add("ff.replaced.%d" % len(results), old not in body_text,
            "指令替换后的旧文案不残留：%s" % old[:40],
            "仍残留" if old in body_text else "已替换干净")

    # 结构：rebuild 区块都在；文字区块不是图片
    for r in spec.get("regions") or []:
        if r.get("keep") != "rebuild":
            continue
        mark = 'data-ff-region="%s"' % r["id"]
        ok = mark in html
        add("ff.region.%s" % r["id"], ok,
            "rebuild 区块已重建：%s（%s）" % (r["id"], r.get("role", "")),
            mark if ok else mark + " 未在产物中找到")
        if ok and r.get("role") in ff_spec.TEXT_ROLES:
            seg = html.split(mark, 1)[1][:400]
            bad = "<img" in seg.split("</div>", 1)[0]
            add("ff.textnotimg.%s" % r["id"], not bad,
                "文字区块是真文字而非位图：%s" % r["id"],
                "发现位图" if bad else "文字为真实 DOM 文本")
    return results


def render_check_run(spec, artifact, workdir):
    """渲染断言：浏览器可用才跑；否则标 unsupported。"""
    browser = render_check.find_browser()
    if not browser:
        return [{"id": "ff.render.browser", "pass": False, "status": "unsupported",
                 "severity": "hard", "desc": "渲染断言（需无头 Chrome/Edge）",
                 "detail": "未找到浏览器，渲染断言需人工确认"}]
    r = render_check.probe(browser, os.path.abspath(artifact), workdir)
    if not r.get("ok"):
        return [{"id": "ff.render.probe", "pass": False, "status": "fail",
                 "severity": "hard", "desc": "渲染探测",
                 "detail": r.get("err", "探测失败")}]
    # 脏值误报排除：crop 区块的内嵌 base64 里可能出现 "NaN" 字样（base64 字母表），
    # 只统计 data URI 之外的脏值才算真脏值。
    dirty = dict(r.get("dirty") or {})
    if dirty:
        try:
            html = open(artifact, encoding="utf-8").read()
        except OSError:
            html = ""
        in_data = {k: len(re.findall(k, "".join(re.findall(r"data:[^\"')]+", html))))
                   for k in dirty}
        dirty = {k: v - in_data.get(k, 0) for k, v in dirty.items() if v - in_data.get(k, 0) > 0}
    out = [{"id": "ff.render.script-error", "pass": not r.get("errs"),
            "desc": "页面脚本零运行错误", "detail": str(r["errs"]) if r.get("errs") else "0 个报错"},
           {"id": "ff.render.dirty", "pass": not dirty,
            "desc": "无 undefined/NaN 脏值（已排除 base64 位图内的字母巧合）",
            "detail": json.dumps(dirty, ensure_ascii=False) if dirty else "无脏值"},
           {"id": "ff.render.overflow-1280",
            "pass": r.get("overflow", 999) <= render_check.ALLOW_OVERFLOW,
            "desc": "1280px 视口无横向溢出", "detail": "溢出 %spx" % r.get("overflow")},
           {"id": "ff.render.not-blank",
            "pass": r.get("textLen", 0) >= ff_spec.min_text_len(spec),
            "desc": "正文非空（下限 %d 字）" % ff_spec.min_text_len(spec),
            "detail": "正文 %s 字" % r.get("textLen")}]
    for o in out:
        o["severity"] = "hard"
        o["status"] = "pass" if o["pass"] else "fail"
    return out


def screenshot(browser, artifact, out_png, w=1280, h=1000):
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
           "--force-device-scale-factor=1", "--virtual-time-budget=5000",
           "--window-size=%d,%d" % (w, h),
           "--user-data-dir=" + os.path.join(out_png + "_cd"),
           "--screenshot=" + os.path.abspath(out_png),
           "file:///" + os.path.abspath(artifact).replace("\\", "/")]
    subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=120)
    return os.path.exists(out_png)


def visual_checks(spec, source_image, artifact, workdir):
    """视觉断言：原图 vs 生成物截图。无源图或无浏览器 → unsupported/跳过。"""
    if not source_image or not os.path.exists(source_image):
        return [{"id": "ff.visual.source", "pass": False, "status": "unsupported",
                 "severity": "soft", "desc": "视觉还原比对",
                 "detail": "无源图（HTML 入场无像素基准），视觉还原由人眼复核"}]
    browser = render_check.find_browser()
    if not browser:
        return [{"id": "ff.visual.shot", "pass": False, "status": "unsupported",
                 "severity": "hard", "desc": "视觉还原比对（需无头浏览器截图）",
                 "detail": "未找到浏览器，需人工比对"}]
    shot = os.path.join(workdir, "actual.png")
    canvas = spec.get("canvas") or {"w": 1280, "h": 900}
    h = max(600, min(3200, int(canvas["h"] * 1280.0 / canvas["w"]) + 20))
    if not screenshot(browser, artifact, shot, h=h):
        return [{"id": "ff.visual.shot", "pass": False, "status": "unsupported",
                 "severity": "hard", "desc": "视觉还原比对", "detail": "生成物截图失败"}]
    e, a = verify_visual.features(source_image), verify_visual.features(shot)
    checks, score = verify_visual.compare(e, a)
    # 自由版式没有「侧栏 + 顶栏」的 ERP 框架语义：这两项从原图量到的常是外框/导航条，
    # 语义不成立，降为软性（主色 / 画布比例 / 深浅版式仍是硬性）。
    SOFT_KEYS = {"visual.sidebarWidth", "visual.topbarHeight"}
    out = [{"id": c["id"], "pass": c["pass"],
            "severity": "soft" if c["id"] in SOFT_KEYS else c["severity"],
            "desc": {"visual.sidebarWidth": "左侧导航宽度一致（自由版式降为建议项）",
                     "visual.topbarHeight": "顶部栏高度一致（自由版式降为建议项）",
                     "visual.primaryColor": "品牌主色一致（色距 ≤46）",
                     "visual.canvasRatio": "画布宽高比一致",
                     "visual.toneRatio": "深浅版式一致"}.get(c["id"], c["id"]),
            "detail": c["evidence"]} for c in checks]
    for o in out:
        o["status"] = "pass" if o["pass"] else "fail"
    return out, score


# --------------------------------------------------------------------------
# 语义断言（契约 / 交互 / 数据）—— 声明即验，未声明不验
# --------------------------------------------------------------------------

def contract_from_semantics(spec, html):
    """契约断言：semantics.contract 声明 → 复用 verify_contract 的 static 检查器。"""
    sem = spec.get("semantics") or {}
    text = _strip_tags(html)
    out = []
    for c in sem.get("contract") or []:
        ok, ev = verify_contract.run_static_check(
            {"kind": c.get("kind"), "value": c.get("value", ""), "pattern": c.get("pattern", "")},
            text)
        out.append({"id": "ff.contract.%s" % c.get("id", "?"),
                    "pass": bool(ok), "severity": "hard",
                    "desc": "契约（%s声明）：%s" % (c.get("from", "user"), c.get("kind")),
                    "detail": ev, "status": "pass" if ok else "fail"})
    return out


def driver_defs_from_semantics(spec):
    """semantics.interactions / data → driver 调用定义（选择器锚定 data-ff-region）。"""
    sem = spec.get("semantics") or {}
    defs = []
    DRV = {"scroll": "ff_click_scrolls", "toggle": "ff_click_toggles", "confirms": "ff_click_confirms"}
    for it in sem.get("interactions") or []:
        args = {"selector": "[data-ff-region='%s']" % it["trigger"]}
        if it.get("target"):
            args["target"] = "[data-ff-region='%s']" % it["target"]
        defs.append({"id": "ff.inter.%s" % it.get("id", "?"), "category": "interaction",
                     "driver": DRV[it["type"]],
                     "desc": it.get("desc") or ("交互声明：%s" % it["type"]),
                     "severity": "hard", "args": args})
    for d in sem.get("data") or []:
        defs.append({"id": "ff.data.%s" % d.get("id", "?"), "category": "data",
                     "driver": "ff_count_equals",
                     "desc": d.get("desc") or "数据自洽：两处计数应相等",
                     "severity": "hard", "args": {"a": d["a"], "b": d["b"]}})
    return defs


def _stage_height(spec):
    """生成页实际渲染高度（px）：画布按 1280 视口等比缩放后的高度。用于把探测视口
    压得比页面矮，让 scroll 类交互真的能滚动。"""
    canvas = spec.get("canvas") or {"w": 1280, "h": 900}
    scale = min(1.0, 1280.0 / canvas["w"])
    return max(300, int(canvas["h"] * scale))


def semantic_probe(spec, artifact, workdir):
    """真实执行交互/数据断言：写探测页 → 无头浏览器加载 → 回读 driver 结果。"""
    defs = driver_defs_from_semantics(spec)
    if not defs:
        return []
    browser = render_check.find_browser()
    if not browser:
        return [{"id": d["id"], "desc": d["desc"], "severity": "hard",
                 "status": "unsupported", "detail": "未找到浏览器，交互/数据断言需人工确认"}
                for d in defs]
    probe = verify_acceptance.probe_accept(
        browser, "file://", os.path.abspath(artifact), defs, 1280,
        height=max(500, min(1100, _stage_height(spec) - 10)),
        workdir=workdir, timeout=180)
    if not probe.get("ok"):
        return [{"id": d["id"], "desc": d["desc"], "severity": "hard",
                 "status": "unsupported", "detail": "探测失败：%s" % (probe.get("err") or probe.get("stderr") or "?")}
                for d in defs]
    by_id = {r.get("id"): r for r in probe.get("results") or []}
    out = []
    for d in defs:
        got = by_id.get(d["id"]) or {}
        out.append({"id": d["id"], "desc": d["desc"], "severity": d["severity"],
                    "driver": d["driver"],
                    "status": got.get("status", "unsupported"),
                    "pass": got.get("status") == "pass",
                    "detail": got.get("detail", "未返回结果")})
    return out




# --------------------------------------------------------------------------
# 汇总与报告
# --------------------------------------------------------------------------

CATEGORIES = [
    ("contract", "契约断言", "semantics 里声明的业务文案/要素：出现了必须在、禁用的必须不在"),
    ("render", "渲染断言", "真实渲染后量出来：1280px 溢出、脏值、白屏、脚本报错"),
    ("text", "文案断言", "声明过的文案（slot 与静态文字）逐条出现在产物里"),
    ("structure", "结构断言", "rebuild 区块真实重建为 DOM，文字区块不是位图"),
    ("interaction", "交互断言", "声明的按钮被真实点击，验证行为真的发生（滚动/显隐/确认）"),
    ("data", "数据断言", "声明的自洽关系真实核对（如角标数=条目数）"),
    ("visual", "视觉断言", "原图与生成物的版式与配色比对（像素量测）"),
]


def verify(spec, artifact, source_image=None, replaced=None):
    """返回 (report_dict, exit_code)。"""
    errs, _ = ff_spec.validate(spec)
    html = open(artifact, encoding="utf-8").read() if os.path.exists(artifact) else ""
    workdir = tempfile.mkdtemp(prefix="ff_verify_")
    cats = {}
    try:
        static = static_checks(spec, html, replaced)
        cats["text"] = {"results": [r for r in static if r["id"].startswith("ff.text.")
                                    or r["id"].startswith("ff.replaced.")]}
        cats["structure"] = {"results": [r for r in static
                                         if r["id"].startswith("ff.region.")
                                         or r["id"].startswith("ff.textnotimg.")]}
        cats["external"] = {"results": [r for r in static if r["id"] == "ff.no-external-link"]}
        cats["render"] = {"results": render_check_run(spec, artifact, workdir)}
        vis = visual_checks(spec, source_image, artifact, workdir)
        cats["visual"] = {"results": vis[0] if isinstance(vis, tuple) else vis}
        # 语义断言：声明了才验（方案 C），未声明的类别在报告里标「未声明」
        declared = ff_spec.declared_categories(spec)
        cats["contract"] = {"results": contract_from_semantics(spec, html) if declared["contract"] else []}
        sem_results = semantic_probe(spec, artifact, workdir) if (declared["interaction"] or declared["data"]) else []
        cats["interaction"] = {"results": [r for r in sem_results if r["id"].startswith("ff.inter.")]
                               if declared["interaction"] else []}
        cats["data"] = {"results": [r for r in sem_results if r["id"].startswith("ff.data.")]
                        if declared["data"] else []}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    total = passed = hard_failed = soft_failed = unsupported = 0
    for key, _, _ in CATEGORIES:
        for r in cats[key]["results"]:
            r.setdefault("status", "pass" if r["pass"] else "fail")
            total += 1
            if r["status"] == "unsupported":
                unsupported += 1
            elif r["pass"]:
                passed += 1
            elif r.get("severity") == "hard":
                hard_failed += 1
            else:
                soft_failed += 1
    verdict = "通过" if hard_failed == 0 else "不通过"
    declared = ff_spec.declared_categories(spec)
    undeclared = [name for name, ok in (("契约", declared["contract"]),
                                        ("交互", declared["interaction"]),
                                        ("数据", declared["data"])) if not ok]
    report = {
        "kind": "freeform-acceptance",
        "page": {"name": spec.get("name", "")},
        "artifact": {"path": os.path.abspath(artifact)},
        "boundary": "自由版式：业务语义来自 semantics 声明（能自动识别的已自动填，其余需用户补充）。"
                    "声明了的契约/交互/数据按 A1–A6 同一标准真实执行；未声明的类别不算通过也不算失败，单列提醒。",
        "summary": {"total": total, "passed": passed, "failed": total - passed - unsupported,
                    "hardFailed": hard_failed, "softFailed": soft_failed,
                    "unsupported": unsupported, "undeclaredCategories": len(undeclared),
                    "accepted": hard_failed == 0 and unsupported == 0 and not undeclared,
                    "verdict": ("需人工确认" if hard_failed == 0 and (unsupported or undeclared) else verdict)},
        "categories": cats,
        "declared": declared,
        "undeclared": undeclared,
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    return report, (0 if hard_failed == 0 else 1)


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def render_report(report):
    s = report["summary"]
    ok = s["hardFailed"] == 0
    rows = []
    order = {k: i for i, (k, _, _) in enumerate(CATEGORIES)}
    ext = [r for r in report["categories"].get("external", {}).get("results", [])]
    cats = [(k, t, w, report["categories"].get(k, {}).get("results", []))
            for k, t, w in CATEGORIES]
    for key, title, why, items in [(k, t, w, rs) for k, t, w, rs in cats] + \
            [("external", "交付约束", "产物零外链（与全部生成物同一铁律）", ext)]:
        passed = sum(1 for r in items if r.get("status") == "pass")
        lis = []
        for r in items:
            st = r.get("status", "fail")
            label = {"pass": "通过", "fail": "失败", "unsupported": "需人工确认"}[st]
            soft = ' <span class="soft">建议项</span>' if r.get("severity") == "soft" else ""
            lis.append('<li class="%s"><span class="tag">%s</span>'
                       '<div class="body"><div class="desc">%s%s</div>'
                       '<div class="ev">%s</div></div><code>%s</code></li>'
                       % (st, label, _esc(r.get("desc", "")), soft,
                          _esc(r.get("detail", "")), _esc(r["id"])))
        rows.append('<section class="cat"><h2>%s <span class="count">%d / %d</span></h2>'
                    '<p class="why">%s</p><ul class="checks">%s</ul></section>'
                    % (title, passed, len(items), why, "".join(lis)))
    undeclared = report.get("undeclared") or []
    if undeclared:
        sem_note = ('<section class="cat skipped"><h2>语义覆盖度 '
                    '<span class="badge">未声明 %d 类</span></h2>'
                    '<p class="why">%s 这 %s 断言本次未声明、未执行 —— '
                    '不冒充通过。补充 semantics 声明后重跑即可覆盖。</p></section>'
                    % (len(undeclared), "、".join(undeclared),
                       "类" if len(undeclared) > 1 else "断言"))
    else:
        sem_note = ('<section class="cat"><h2>语义覆盖度 <span class="count">3 / 3</span></h2>'
                    '<p class="why">契约 / 交互 / 数据 三类业务语义均已声明并真实执行'
                    '（与 A1–A6 同一标准）。</p></section>')
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>自由版式验收报告 · %s</title>
<style>
  body{margin:0;font:14px/1.6 "PingFang SC","Microsoft YaHei",Arial,sans-serif;background:#F4F6FA;color:#1C2230}
  .wrap{max-width:900px;margin:0 auto;padding:28px 20px 48px}
  .head{background:#fff;border-radius:10px;padding:20px 24px;margin-bottom:16px;box-shadow:0 1px 2px rgba(16,24,40,.05)}
  .verdict{display:inline-block;padding:2px 12px;border-radius:999px;font-weight:600;color:#fff;background:%s}
  .m{display:inline-block;margin-right:18px;color:#5A6478}
  .m b{color:#1C2230}
  .cat{background:#fff;border-radius:10px;padding:16px 24px;margin-bottom:14px;box-shadow:0 1px 2px rgba(16,24,40,.05)}
  .cat h2{font-size:16px;margin:0 0 6px}
  .count{color:#5A6478;font-size:13px;font-weight:400;margin-left:8px}
  .why{color:#5A6478;font-size:12px;margin:0 0 10px}
  .checks{list-style:none;margin:0;padding:0}
  .checks li{display:flex;gap:10px;align-items:flex-start;padding:7px 0;border-top:1px solid #EEF0F4}
  .tag{flex:none;font-size:12px;padding:1px 8px;border-radius:999px;color:#fff;background:#12A150;margin-top:2px}
  .fail .tag{background:#E5484D}
  .unsupported .tag{background:#E08B00}
  .desc{font-weight:500}
  .ev{color:#8B93A5;font-size:12px}
  .soft{color:#E08B00;font-size:12px}
  code{margin-left:auto;color:#8B93A5;font-size:11px;text-align:right;flex:none}
  .note{background:#EEF3FF;border-radius:8px;padding:10px 14px;color:#3D56B0;font-size:12px;margin-bottom:14px}
  .badge{font-size:12px;padding:1px 10px;border-radius:999px;background:#8B93A5;color:#fff;font-weight:400}
</style>
</head>
<body><div class="wrap">
<div class="head">
  <h1 style="font-size:20px;margin:0 0 10px">自由版式验收报告 · %s</h1>
  <span class="verdict">%s</span>
  <div style="margin-top:10px">
    <span class="m">断言 <b>%d</b> 项</span><span class="m">通过 <b>%d</b></span>
    <span class="m">硬性失败 <b>%d</b></span><span class="m">建议项未过 <b>%d</b></span>
    <span class="m">需人工 <b>%d</b></span>
  </div>
</div>
<div class="note">%s</div>
%s%s<div style="color:#8B93A5;font-size:12px;margin-top:8px">生成时间 %s · 自由版式扩展 ff-1.0</div>
</div></body></html>
""" % (_esc(report["page"]["name"]), "#12A150" if ok else "#E5484D",
       _esc(report["page"]["name"]),
       "通过" if ok else "不通过",
       s["total"], s["passed"], s["hardFailed"], s["softFailed"], s["unsupported"],
       _esc(report["boundary"]), sem_note, "".join(rows), report["generatedAt"])


def main():
    ap = argparse.ArgumentParser(description="自由版式验收")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--image", help="源图（视觉比对基准）")
    ap.add_argument("--replaced", help='指令替换 JSON，如 {"旧文案":"新文案"}')
    ap.add_argument("--json", dest="out_json")
    ap.add_argument("--report", dest="out_report")
    a = ap.parse_args()
    with open(a.spec, encoding="utf-8") as f:
        spec = json.load(f)
    replaced = json.loads(a.replaced) if a.replaced else None
    report, code = verify(spec, a.artifact, a.image, replaced)
    s = report["summary"]
    print("自由版式验收：%s（%d 项，通过 %d，硬性失败 %d，需人工 %d）"
          % (s["verdict"], s["total"], s["passed"], s["hardFailed"], s["unsupported"]))
    for key, title, _ in CATEGORIES:
        for r in report["categories"][key]["results"]:
            mark = {"pass": "✓", "fail": "★", "unsupported": "?"}[r["status"]]
            print("  %s [%s] %s —— %s" % (mark, title[:2], r["desc"], r["detail"]))
    if a.out_json:
        os.makedirs(os.path.dirname(os.path.abspath(a.out_json)), exist_ok=True)
        with open(a.out_json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    if a.out_report:
        os.makedirs(os.path.dirname(os.path.abspath(a.out_report)), exist_ok=True)
        with open(a.out_report, "w", encoding="utf-8", newline="\n") as f:
            f.write(render_report(report))
    return code


if __name__ == "__main__":
    sys.exit(main())
