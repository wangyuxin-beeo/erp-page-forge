#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""门户交互检测：用无头浏览器真实驱动 index.html，验证核心交互是否真的生效。

检查项：
  1. 6 张卡片、6 个缩略图 iframe 是否都渲染出来
  2. 点击缩略图 → 弹窗打开、标题正确、iframe srcdoc 有内容
  3. 切到「查看源码」→ 源码面板可见且有内容
  4. 关闭弹窗 → 弹窗关闭
  5. 原型分类筛选 → 可见卡片数正确
  6. 搜索框输入 → 可见卡片数正确
  7. 统计条 / Skill 提示词 / 提交材料卡是否渲染
  8. 门户自身控制台是否报错

依赖：本机已安装 Chrome 或 Edge。找不到时退出码 0 跳过。

用法:
    python build/portal_check.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "index.html")

PROBE = r"""<!doctype html>
<meta charset="utf-8">
<title>portal-probe</title>
<iframe id="f" src="file:///__INDEX__" style="width:1440px;height:2400px;border:0"></iframe>
<pre id="out">PENDING</pre>
<script>
const fr = document.getElementById('f');
fr.onload = () => setTimeout(() => {
  const d = fr.contentDocument, w = fr.contentWindow;
  const L = {errs: []};
  try {
    w.addEventListener('error', e => L.errs.push(String(e.message)));
    const vis = () => [...d.querySelectorAll('.card')].filter(c => !c.classList.contains('hide')).length;

    L.cards        = d.querySelectorAll('.card').length;
    L.thumbs       = d.querySelectorAll('.thumb .frame').length;
    L.chips        = d.querySelectorAll('.chip').length;
    L.statsItems   = d.getElementById('stats').children.length;
    L.deliverItems = d.getElementById('deliver').children.length;
    L.skillPrompt  = d.getElementById('skillText').textContent.length;

    /* 点击第一个缩略图 → 应打开弹窗 */
    d.querySelector('.thumb').click();
    L.modalOpen   = d.getElementById('modal').classList.contains('on');
    L.modalTitle  = d.getElementById('mTitle').textContent;
    L.modalBadge  = d.getElementById('mBadge').textContent;
    L.srcdocLen   = (d.getElementById('mFrame').getAttribute('srcdoc') || '').length;
    L.bodyLocked  = d.body.classList.contains('lock');

    /* 切到源码页签 */
    w.switchTab('code');
    L.codeTabOn = d.getElementById('mBody').classList.contains('code');
    L.codeText  = d.getElementById('mCode').textContent.length;

    /* 关闭 */
    w.closeModal();
    L.modalClosed = !d.getElementById('modal').classList.contains('on');

    /* 原型分类筛选：点第 2 个 chip（A1） */
    const chips = [...d.querySelectorAll('.chip')];
    chips[1].click();
    L.afterA1Filter = vis();
    chips[0].click();
    L.afterAllFilter = vis();

    /* 搜索 */
    const q = d.getElementById('q');
    q.value = '对账';
    q.dispatchEvent(new w.Event('input'));
    L.afterSearch = vis();
    q.value = 'zzz-not-exist';
    q.dispatchEvent(new w.Event('input'));
    L.afterNoResult = vis();
    L.emptyStateShown = !!d.getElementById('empty');
    q.value = '';
    q.dispatchEvent(new w.Event('input'));
    L.afterClear = vis();
  } catch (e) {
    L.err = String(e);
  }
  document.getElementById('out').textContent = 'RESULT::' + JSON.stringify(L);
}, 2000);
</script>
"""

# 期望值：(字段, 断言描述, 判定函数)
EXPECT = [
    ("cards", "卡片数为 6", lambda v: v == 6),
    ("thumbs", "缩略图 iframe 渲染 6 个", lambda v: v == 6),
    ("statsItems", "统计条 4 格", lambda v: v == 4),
    ("deliverItems", "提交材料卡 6 张", lambda v: v == 6),
    ("skillPrompt", "Skill 提示词非空", lambda v: v and v > 200),
    ("modalOpen", "点击缩略图后弹窗打开", lambda v: v is True),
    ("modalBadge", "弹窗原型编号为 A1", lambda v: v == "A1"),
    ("srcdocLen", "弹窗 iframe 有内容", lambda v: v and v > 5000),
    ("bodyLocked", "弹窗打开时锁定页面滚动", lambda v: v is True),
    ("codeTabOn", "「查看源码」页签可切换", lambda v: v is True),
    ("codeText", "源码面板有内容", lambda v: v and v > 5000),
    ("modalClosed", "弹窗可关闭", lambda v: v is True),
    ("afterA1Filter", "筛选 A1 后可见 1 张", lambda v: v == 1),
    ("afterAllFilter", "切回全部后可见 6 张", lambda v: v == 6),
    ("afterSearch", "搜索「对账」命中 1 张", lambda v: v == 1),
    ("afterNoResult", "无匹配时可见 0 张", lambda v: v == 0),
    ("emptyStateShown", "无匹配时出现空状态", lambda v: v is True),
    ("afterClear", "清空搜索后恢复 6 张", lambda v: v == 6),
]


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


def main():
    if not os.path.exists(INDEX):
        print("缺少 index.html，请先运行 python build/build_portal.py")
        return 1

    browser = find_browser()
    if not browser:
        print("[跳过] 未找到 Chrome / Edge，无法执行门户交互检测（可用 BROWSER_BIN 指定）")
        return 0

    workdir = tempfile.mkdtemp(prefix="forge_portal_")
    try:
        probe = os.path.join(workdir, "probe.html")
        with open(probe, "w", encoding="utf-8") as f:
            f.write(PROBE.replace("__INDEX__", INDEX.replace("\\", "/")))

        cmd = [
            browser, "--headless=new", "--disable-gpu", "--no-sandbox",
            "--allow-file-access-from-files", "--hide-scrollbars",
            "--disable-extensions", "--no-first-run",
            "--virtual-time-budget=9000",
            "--user-data-dir=" + os.path.join(workdir, "_cd"),
            "--dump-dom", "file:///" + probe.replace("\\", "/"),
        ]
        p = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        m = re.search(r"RESULT::(\{.*?\})</pre>", p.stdout, re.S)
        if not m:
            print("[失败] 未取到交互检测结果（浏览器未启动或超时）")
            return 1
        got = json.loads(m.group(1))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if got.get("err"):
        print("[失败] 探测脚本抛错: " + got["err"])
        return 1

    print("%-40s %-22s %s" % ("检查项", "实际值", "结果"))
    print("-" * 78)
    failed = 0
    for key, desc, pred in EXPECT:
        v = got.get(key)
        try:
            ok = bool(pred(v))
        except Exception:
            ok = False
        if not ok:
            failed += 1
        shown = str(v)
        if len(shown) > 20:
            shown = shown[:18] + ".."
        print("%-40s %-22s %s" % (desc, shown, "通过" if ok else "★ 未通过"))

    errs = got.get("errs") or []
    print("-" * 78)
    print("门户控制台报错: %s" % (errs if errs else "无"))
    print("交互检测: %d / %d 通过" % (len(EXPECT) - failed, len(EXPECT)))
    return 0 if (failed == 0 and not errs) else 1


if __name__ == "__main__":
    sys.exit(main())
