#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Vue 产物渲染期校验：对运行中的 Vite dev server 做真实渲染检测

检查项：
  1. 渲染后的 DOM 是否出现 undefined / NaN / [object Object] 等脏值
  2. 指定视口宽度下是否产生横向溢出
  3. 是否真的渲染出内容（正文长度下限，防白屏）
  4. 关键结构是否到位（侧边栏 / 表格行数 / 状态胶囊数量）

实现要点：把目标路由装进一个同源 iframe 里测量，而不是直接给 Chrome 传窗口尺寸。
这样测量结果不受 Windows 显示缩放（125% 等）干扰，拿到的就是真实 CSS 像素。

用法:
    python build/verify_vue_render.py
    python build/verify_vue_render.py --project C:/path/to/app --route /sales --width 1280
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile

DEFAULT_PROJECT = r"./example-project"
DEFAULT_ORIGIN = "http://127.0.0.1:3020"

ALLOW_OVERFLOW = 2
MIN_TEXT_LEN = 300
DIRTY_WORDS = ["undefined", "NaN", "[object Object]", "Infinity"]

PROBE = """<!doctype html>
<meta charset="utf-8">
<title>probe</title>
<iframe id="f" src="__ROUTE__" style="width:__W__px;height:900px;border:0"></iframe>
<pre id="out">PENDING</pre>
<script>
var DIRTY = __DIRTY__;
document.getElementById('f').onload = function () {
  var fr = document.getElementById('f');
  setTimeout(function () {
    var r = { ok: false };
    try {
      var d = fr.contentDocument, w = fr.contentWindow;
      var root = d.documentElement;
      var bodyText = (d.body ? d.body.innerText : '') || '';
      var html = d.body ? d.body.innerHTML : '';
      var dirty = {};
      DIRTY.forEach(function (k) {
        var n = html.split(k).length - 1;
        if (n) { dirty[k] = n; }
      });
      var app = d.getElementById('app');
      r = {
        ok: true,
        innerWidth: w.innerWidth,
        scrollWidth: root.scrollWidth,
        overflow: root.scrollWidth - w.innerWidth,
        textLen: bodyText.replace(/\\s+/g, '').length,
        dirty: dirty,
        hasSidebar: !!d.querySelector('aside'),
        tableRows: d.querySelectorAll('tbody tr').length,
        tagCount: d.querySelectorAll('span[class*="tag"]').length,
        appChildren: app ? app.children.length : -1
      };
    } catch (e) {
      r = { ok: false, err: String(e) };
    }
    document.getElementById('out').textContent = 'RESULT::' + JSON.stringify(r);
  }, 1200);
};
</script>
"""


def find_browser():
    env = os.environ.get("BROWSER_BIN")
    if env and os.path.exists(env):
        return env
    for name in ("chrome", "google-chrome", "chromium", "msedge"):
        p = shutil.which(name)
        if p:
            return p
    home = os.path.expanduser("~")
    cands = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.join(home, r"AppData\Local\Google\Chrome\Application\chrome.exe"),
    ]
    return next((c for c in cands if os.path.exists(c)), None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=DEFAULT_PROJECT)
    ap.add_argument("--origin", default=DEFAULT_ORIGIN)
    ap.add_argument("--route", default="/sales")
    ap.add_argument("--width", type=int, default=1280)
    args = ap.parse_args()

    browser = find_browser()
    if not browser:
        print("[跳过] 未找到 Chrome / Edge，可用 BROWSER_BIN 指定路径")
        return 0

    project = os.path.abspath(args.project)
    probe_file = os.path.join(project, "__probe.html")
    workdir = tempfile.mkdtemp(prefix="vuelab_probe_")

    html = (
        PROBE.replace("__ROUTE__", args.route)
        .replace("__W__", str(args.width))
        .replace("__DIRTY__", json.dumps(DIRTY_WORDS))
    )

    try:
        with open(probe_file, "w", encoding="utf-8") as f:
            f.write(html)

        url = args.origin.rstrip("/") + "/__probe.html"
        cmd = [
            browser, "--headless=new", "--disable-gpu", "--no-sandbox",
            "--hide-scrollbars", "--disable-extensions", "--no-first-run",
            "--force-device-scale-factor=1",
            "--virtual-time-budget=9000",
            "--user-data-dir=" + os.path.join(workdir, "_cd"),
            "--dump-dom", url,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        m = re.search(r"RESULT::(\{.*?\})</pre>", proc.stdout, re.S)
        if not m:
            print("★ 未取到探测结果（dev server 未启动？或浏览器启动失败）")
            return 1
        r = json.loads(m.group(1))
    finally:
        if os.path.exists(probe_file):
            os.remove(probe_file)
        shutil.rmtree(workdir, ignore_errors=True)

    if not r.get("ok"):
        print("★ 探测失败：%s" % r.get("err"))
        return 1

    problems = []
    if r["dirty"]:
        problems.append("脏值 " + json.dumps(r["dirty"], ensure_ascii=False))
    if r["overflow"] > ALLOW_OVERFLOW:
        problems.append("横向溢出 %dpx" % r["overflow"])
    if r["textLen"] < MIN_TEXT_LEN:
        problems.append("疑似白屏（正文 %d 字）" % r["textLen"])
    if not r["hasSidebar"]:
        problems.append("侧边栏未渲染")
    if r["tableRows"] < 10:
        problems.append("表格行数偏少（%d 行）" % r["tableRows"])
    if r["tagCount"] < 5:
        problems.append("状态胶囊数量异常（%d 个）" % r["tagCount"])
    if r["appChildren"] < 1:
        problems.append("Vue 未挂载（#app 无子节点）")

    print("路由 %s  @ %dpx" % (args.route, args.width))
    print("-" * 68)
    print("  视口宽          %s" % r["innerWidth"])
    print("  内容宽          %s" % r["scrollWidth"])
    print("  横向溢出        %spx" % r["overflow"])
    print("  正文长度        %s 字" % r["textLen"])
    print("  侧边栏渲染      %s" % ("是" if r["hasSidebar"] else "否"))
    print("  表格行数        %s" % r["tableRows"])
    print("  状态胶囊        %s 个" % r["tagCount"])
    print("  #app 子节点     %s" % r["appChildren"])
    print("  脏值            %s" % (json.dumps(r["dirty"], ensure_ascii=False) if r["dirty"] else "无"))
    print("-" * 68)
    print("渲染校验：%s" % ("全部通过" if not problems else "★ " + "；".join(problems)))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
