# -*- coding: utf-8 -*-
"""
React 产物渲染校验 —— 对真实构建产物做「量出来」的验收，而不是眼看。

它会做两件事：
  1) 同源 iframe 探测：在指定 CSS 宽度下量横向溢出、脏值（undefined/NaN）、
     结构完整性（侧边栏 / 表格行数 / 文本量），并列出真正溢出的元素。
  2) 截图：按目标宽度出图，供人工复核视觉结果。

前置：先用 build/serve_spa.py 把构建产物起成服务。

用法：
  python build/serve_spa.py --root "C:/Users/xxx/my-app/build" --port 3030
  python build/verify_react_render.py --base http://127.0.0.1:3030 \
      --path /license-approval --path /sales --width 1280 --shot-width 1600

退出码：0 = 全部通过；1 = 有失败项。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Users\%s\AppData\Local\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

MIN_TEXT_LEN = 400          # 低于此值视为白屏
MAX_OVERFLOW = 1            # px，允许 1px 亚像素误差


def find_browser():
    env = os.environ.get("BROWSER_BIN")
    if env and os.path.exists(env):
        return env
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    for c in CHROME_CANDIDATES:
        p = c % user if "%s" in c else c
        if os.path.exists(p):
            return p
    raise SystemExit("找不到 Chrome/Edge，可设环境变量 BROWSER_BIN 指定")


def run_browser(browser, url, size, extra=None, dump=False, timeout=150):
    """起一个隔离的无头实例；user-data-dir 必须传 Windows 路径。

    dump=True 时才加 --dump-dom。少了这个参数 Chrome 会正常退出但 stdout 为空，
    看起来像"探测页没产出结果"，很容易误判成页面问题。
    """
    tmp = os.path.abspath(tempfile.mkdtemp(prefix="renderchk-"))
    args = [
        browser, "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--hide-scrollbars",
        # Windows 显示缩放会把 CSS 视口缩小，必须钉死为 1
        "--force-device-scale-factor=1",
        "--user-data-dir=" + tmp,
        "--window-size=%d,%d" % size,
        "--virtual-time-budget=9000",
    ]
    if dump:
        args.append("--dump-dom")
    if extra:
        args += extra
    args.append(url)
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout)
        return p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def probe(browser, base, path, width, height=1100):
    url = "%s/__probe.html?path=%s&w=%d&h=%d" % (base.rstrip("/"), path, width, height)
    dom, err = run_browser(browser, url, (width + 120, height + 240), dump=True)
    m = re.search(r"PROBE_RESULT (\{.*?\})</pre>", dom, re.S)
    if not m:
        hint = "stdout 为空（浏览器未产出）" if not dom.strip() else "页面里没有 PROBE_RESULT"
        return {"path": path, "width": width, "ok": False,
                "error": "%s；stderr: %s" % (hint, (err or "").strip()[:160])}
    try:
        return json.loads(m.group(1))
    except Exception as e:
        return {"path": path, "width": width, "ok": False, "error": "解析失败: %s" % e}


def screenshot(browser, base, path, width, out_dir):
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    name = "%s-%d.png" % (re.sub(r"[^a-z0-9]+", "-", path.strip("/").lower()) or "root", width)
    dst = os.path.join(out_dir, name)
    if os.path.exists(dst):
        os.remove(dst)
    run_browser(browser, base.rstrip("/") + path, (width, 1150),
                extra=["--screenshot=" + dst])
    return dst if os.path.exists(dst) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:3030")
    ap.add_argument("--path", action="append", default=[], help="要校验的路由，可重复")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--shot-width", type=int, default=0, help=">0 时额外截图")
    ap.add_argument("--out", default="build/shots")
    ap.add_argument("--min-text", type=int, default=MIN_TEXT_LEN)
    args = ap.parse_args()

    paths = args.path or ["/"]
    browser = find_browser()
    print("浏览器 : %s" % browser)
    print("基线   : %s   宽度 %dpx" % (args.base, args.width))
    print("")

    fails = []
    print("%-22s %-8s %-9s %-10s %-8s %-6s" % ("路由", "溢出px", "脏值", "文本量", "表格行", "结论"))
    print("-" * 72)
    for path in paths:
        r = probe(browser, args.base, path, args.width)
        if not r.get("ok"):
            print("%-22s 探测失败: %s" % (path, r.get("error")))
            fails.append(path + " 探测失败")
            continue
        overflow = r.get("overflowX", 0)
        dirty = r.get("dirty") or []
        text_len = r.get("textLen", 0)
        rows = r.get("rows", 0)

        bad = []
        if overflow > MAX_OVERFLOW:
            bad.append("横向溢出 %dpx" % overflow)
        if dirty:
            bad.append("脏值 " + ",".join(dirty))
        if text_len < args.min_text:
            bad.append("疑似白屏(文本 %d)" % text_len)

        print("%-22s %-8d %-9s %-10d %-8d %-6s" % (
            path, overflow, ",".join(dirty) if dirty else "无", text_len, rows,
            "通过" if not bad else "失败"))
        if bad:
            fails.append(path + "：" + "；".join(bad))
            for el in (r.get("overflowEls") or [])[:6]:
                print("      ↳ 溢出元素: %s" % el)

    if args.shot_width:
        print("")
        print("截图（%dpx）:" % args.shot_width)
        for path in paths:
            f = screenshot(browser, args.base, path, args.shot_width, args.out)
            print("  %-22s %s" % (path, f if f else "失败"))

    print("")
    if fails:
        print("结论：%d/%d 未通过" % (len(fails), len(paths)))
        for f in fails:
            print("  - " + f)
        return 1
    print("结论：%d/%d 全部通过" % (len(paths), len(paths)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
