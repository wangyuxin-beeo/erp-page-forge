#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""渲染期检测：用无头浏览器真实渲染每个页面，检查

  1. 渲染后的 DOM 是否出现 undefined / NaN / null / [object Object] 等脏值
  2. 在 1280px 视口下是否产生横向溢出（SKILL.md 自检清单第 16 条）
  3. 页面是否真的渲染出内容（body 文本长度下限，防止白屏）
  4. 控制台是否报错

依赖：本机已安装 Chrome 或 Edge。找不到浏览器时以退出码 0 跳过（不阻断 CI）。

用法:
    python build/render_check.py            # 控制台表格
    python build/render_check.py --json     # 输出 JSON
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(ROOT, "templates")

VIEWPORT = (1280, 900)          # SKILL.md 承诺「1280px 宽不横向滚动」
ALLOW_OVERFLOW = 2              # 容忍 2px 的亚像素误差
MIN_TEXT_LEN = 300              # 正文少于该长度视为白屏（表格页正文一般在 500 字以上）
DIRTY_WORDS = ["undefined", "NaN", "[object Object]", "Infinity"]

# 探测容器：把目标页面装进固定 1280px 宽的 iframe，读它的布局与 DOM
PROBE = """<!doctype html>
<meta charset="utf-8">
<title>probe</title>
<iframe id="f" src="__SRC__" style="width:%dpx;height:%dpx;border:0"></iframe>
<pre id="out">PENDING</pre>
<script>
window.__errs = [];
window.addEventListener('error', e => window.__errs.push(String(e.message)));
document.getElementById('f').onload = () => {
  const fr = document.getElementById('f');
  setTimeout(() => {
    let r = {ok:false};
    try {
      const d = fr.contentDocument, w = fr.contentWindow;
      const root = d.documentElement;
      const bodyText = (d.body ? d.body.innerText : '') || '';
      const html = d.body ? d.body.innerHTML : '';
      const dirty = {};
      %s.forEach(k => { const n = (html.split(k).length - 1); if (n) dirty[k] = n; });
      r = {
        ok: true,
        innerWidth: w.innerWidth,
        scrollWidth: root.scrollWidth,
        overflow: root.scrollWidth - w.innerWidth,
        textLen: bodyText.replace(/\\s+/g,'').length,
        dirty: dirty,
        errs: window.__errs
      };
    } catch (e) {
      r = {ok:false, err: String(e)};
    }
    document.getElementById('out').textContent = 'RESULT::' + JSON.stringify(r);
  }, 900);
};
</script>
""" % (VIEWPORT[0], VIEWPORT[1], json.dumps(DIRTY_WORDS))


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
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
    ]
    return next((c for c in cands if os.path.exists(c)), None)


def probe(browser, page_path, workdir):
    html = PROBE.replace("__SRC__", "file:///" + page_path.replace("\\", "/"))
    probe_file = os.path.join(workdir, "probe.html")
    with open(probe_file, "w", encoding="utf-8") as f:
        f.write(html)

    cmd = [
        browser, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--allow-file-access-from-files", "--hide-scrollbars",
        "--disable-extensions", "--no-first-run", "--virtual-time-budget=6000",
        "--user-data-dir=" + os.path.join(workdir, "_cd"),
        "--dump-dom", "file:///" + probe_file.replace("\\", "/"),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    m = re.search(r"RESULT::(\{.*?\})</pre>", p.stdout, re.S)
    if not m:
        return {"ok": False, "err": "未取到探测结果（浏览器启动失败或超时）"}
    try:
        return json.loads(m.group(1))
    except ValueError:
        return {"ok": False, "err": "探测结果不是合法 JSON"}


def main():
    as_json = "--json" in sys.argv
    browser = find_browser()
    if not browser:
        msg = "未找到 Chrome / Edge，跳过渲染期检测（可用 BROWSER_BIN 环境变量指定路径）"
        print(json.dumps({"skipped": True, "reason": msg}, ensure_ascii=False) if as_json else "[跳过] " + msg)
        return 0

    workdir = tempfile.mkdtemp(prefix="forge_render_")
    rows, all_ok = [], True

    try:
        for name in sorted(os.listdir(TPL)):
            if not name.endswith(".html"):
                continue
            path = os.path.join(TPL, name)
            r = probe(browser, path, workdir)

            problems = []
            if not r.get("ok"):
                problems.append(r.get("err", "探测失败"))
            else:
                if r["dirty"]:
                    problems.append("脏值 " + json.dumps(r["dirty"], ensure_ascii=False))
                if r["overflow"] > ALLOW_OVERFLOW:
                    problems.append("横向溢出 %dpx" % r["overflow"])
                if r["textLen"] < MIN_TEXT_LEN:
                    problems.append("疑似白屏（正文 %d 字）" % r["textLen"])
                if r["errs"]:
                    problems.append("控制台报错 %s" % r["errs"])

            ok = not problems
            all_ok = all_ok and ok
            rows.append({
                "file": name,
                "innerWidth": r.get("innerWidth"),
                "scrollWidth": r.get("scrollWidth"),
                "overflow": r.get("overflow"),
                "textLen": r.get("textLen"),
                "pass": ok,
                "problems": problems,
            })

        if as_json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            print("%-34s %8s %9s %8s  %s" % ("页面", "视口宽", "内容宽", "正文长", "结果"))
            print("-" * 92)
            for r in rows:
                vb = r["innerWidth"] if r["innerWidth"] is not None else "-"
                sc = r["scrollWidth"] if r["scrollWidth"] is not None else "-"
                tl = r["textLen"] if r["textLen"] is not None else "-"
                print("%-34s %8s %9s %8s  %s" % (
                    r["file"], vb, sc, tl, "通过" if r["pass"] else "★ " + "；".join(r["problems"])))
            print("-" * 92)
            print("渲染期检测全部通过: %s" % all_ok)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
