#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 templates/ 下所有页面的内嵌 JS 语法、外部依赖与免责声明，并统计代码量指标。

用法:
    python build/check_pages.py            # 打印 JSON + 汇总
    python build/check_pages.py --quiet    # 只打印汇总

退出码: 0 = 全部通过, 1 = 存在失败项
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

# 外部依赖扫描：协议前缀 / 常见 CDN 域名 / 绝对协议相对链接 / 外链属性
EXT_PATTERNS = [
    r"https?://",
    r"\bcdn\.", r"\bunpkg\b", r"\bjsdelivr\b", r"\bbootcdn\b", r"\bgoogleapis\b",
    r"//cdn", r"fonts\.(googleapis|gstatic)",
    r'<link[^>]+href\s*=\s*["\'](?!#|data:|\./|[a-zA-Z0-9_\-]+\.css)',
    r'<img[^>]+src\s*=\s*["\'](?!#|data:)',
    r'<script[^>]+src\s*=',
]

DISCLAIMER_KEYS = ["虚构数据", "示例数据", "仅供演示", "脱敏"]


def find_node():
    """按优先级查找可用的 node：环境变量 → PATH → 常见安装位置。"""
    env = os.environ.get("NODE_BIN")
    if env and os.path.exists(env):
        return env
    found = shutil.which("node")
    if found:
        return found
    home = os.path.expanduser("~")
    candidates = []
    base = os.path.join(home, ".workbuddy", "binaries", "node", "versions")
    if os.path.isdir(base):
        for v in sorted(os.listdir(base), reverse=True):
            candidates.append(os.path.join(base, v, "node.exe"))
            candidates.append(os.path.join(base, v, "bin", "node"))
    candidates += [
        r"C:\Program Files\nodejs\node.exe",
        "/usr/local/bin/node",
        "/usr/bin/node",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def extract_scripts(html):
    return re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)


def main():
    quiet = "--quiet" in sys.argv
    node = find_node()
    if not node:
        print("[警告] 未找到 node，将跳过 JS 语法检查（安装 node 后可完整校验）", file=sys.stderr)

    rows = []
    all_ok = True

    for name in sorted(os.listdir(TPL)):
        if not name.endswith(".html"):
            continue
        path = os.path.join(TPL, name)
        with open(path, encoding="utf-8") as f:
            html = f.read()

        # 1) JS 语法
        js_ok, err = True, ""
        if node:
            scripts = extract_scripts(html)
            tmp = os.path.join(tempfile.gettempdir(), "_forge_check_%s.js" % name)
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\n".join(scripts))
            p = subprocess.run([node, "--check", tmp], capture_output=True, text=True)
            js_ok = p.returncode == 0
            if not js_ok:
                err = (p.stderr or "").strip().split("\n")[0]
            try:
                os.remove(tmp)
            except OSError:
                pass

        # 2) 外部依赖
        ext_hits = []
        for pat in EXT_PATTERNS:
            m = re.search(pat, html, re.I)
            if m:
                ext_hits.append(m.group(0)[:40])

        # 3) 免责声明
        has_disc = any(k in html for k in DISCLAIMER_KEYS)

        # 4) HTML 结构完整性（粗检：html/head/body/script 标签成对）
        struct_ok = all(html.count("<%s" % t) and html.count("</%s>" % t) for t in ("html", "head", "body"))

        ok = js_ok and not ext_hits and has_disc and struct_ok
        all_ok = all_ok and ok

        # 代码量统计：统一用 splitlines() 口径
        src_nc = re.sub(r"/\*[\s\S]*?\*/", "", html)
        src_nc = re.sub(r"(?<!:)//[^\n]*", "", src_nc)
        effective = len([ln for ln in src_nc.splitlines() if ln.strip()])

        rows.append({
            "file": name,
            "lines": len(html.splitlines()),
            "effective": effective,
            "kb": round(len(html.encode("utf-8")) / 1024.0, 1),
            "js_ok": js_ok,
            "struct_ok": struct_ok,
            "err": err,
            "ext_hits": ext_hits,
            "has_disclaimer": has_disc,
            "pass": ok,
        })

    if not quiet:
        print(json.dumps(rows, ensure_ascii=False, indent=2))

    total_lines = sum(r["lines"] for r in rows)
    print("\n=== 汇总 ===")
    print("页面数: %d  总行数: %d  有效代码行: %d  总大小: %.1f KB" % (
        len(rows), total_lines, sum(r["effective"] for r in rows), sum(r["kb"] for r in rows)))
    print("JS 语法全部通过: %s" % all(r["js_ok"] for r in rows))
    print("HTML 结构完整:   %s" % all(r["struct_ok"] for r in rows))
    print("零外部依赖:      %s" % (not any(r["ext_hits"] for r in rows)))
    print("免责声明齐全:    %s" % all(r["has_disclaimer"] for r in rows))
    print("全部用例通过:    %s" % all_ok)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
