#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Axure 原型 → PageSpec（第四个输入入口）

与截图入口（`build/vision.py`）的分工是刻意的：

  截图入口：从像素里"看"出页面有什么 —— 通用于任何来源，但精度受限于图像
  本入口  ：读 Axure 自带的标注与交互定义 —— 产品经理亲手写的文字，精度更高

Axure 原型比截图多三样东西，正好都是我们生成页面最需要的：

  notes.json         → 产品经理写的字段标注（语义、控件类型、枚举取值、按钮可见规则）
  interactions.json  → 交互映射（点击跳哪里、什么状态可点什么）
  theme.json         → 设计令牌（颜色/字体/间距/圆角）

链路：

  Axure 导出目录 / 原型 URL
    ↓ Python axure_static：读取 data/document.js 与 files/*/data.js 的数据字面量
    ↓ 不执行 JavaScript；无需 Node、外部 skill 或 Playwright
    ↓ 可显式 --parser legacy 使用已安装旧提取器（仅可信原型）
    sitemap.json / pages/{p}/data.json / notes.json / interactions.json
    ↓ 标注 → 字段/列/动作/状态机（provenance=axure）
    PageSpec
    ↓ build/forge.py 渲染 + 四类断言 + 视觉断言
    可运行页面 + 验收报告

用法:
    # 只看提取（不生成）
    python build/axure.py --src <Axure导出目录> --page inout-list --extract-only

    # 提取 → PageSpec
    python build/axure.py --src <目录或URL> --page inout-list --out spec.json

    # 一步到底：提取 → PageSpec → 确定性生成
    python build/axure.py --src <目录或URL> --page inout-list --out spec.json --forge out.html
"""

import argparse
import functools
import http.server
import json
import os
import re
import socket
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

SKILL_NAME = "extract-axure-data"


def _skill_bases():
    """技能可能装在三个位置，逐个找。

    项目级装在 **workspace 根** 的 .workbuddy/skills（即 erp-page-forge 的上一级），
    不是项目目录里 —— 这一点踩过坑，所以三个候选都列上。
    """
    return [
        os.path.join(os.path.dirname(ROOT), ".workbuddy", "skills"),   # workspace 根（当前用这个）
        os.path.join(ROOT, ".workbuddy", "skills"),                    # 项目目录内
        os.path.expanduser(os.path.join("~", ".workbuddy", "skills")),  # 用户级
    ]


def skill_dir():
    for base in _skill_bases():
        d = os.path.join(base, SKILL_NAME)
        if os.path.isdir(d):
            return d
    return os.path.join(_skill_bases()[-1], SKILL_NAME)


def _load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------
# Node 运行时
# --------------------------------------------------------------------------

def find_node():
    """找可用的 node。优先托管版本，其次系统安装。"""
    cands = []
    base = os.path.expanduser(os.path.join("~", ".workbuddy", "binaries", "node", "versions"))
    if os.path.isdir(base):
        for v in sorted(os.listdir(base), reverse=True):
            cands.append(os.path.join(base, v, "node.exe"))
            cands.append(os.path.join(base, v, "bin", "node"))
    cands += [
        r"C:\Program Files\nodejs\node.exe",
        "/usr/local/bin/node",
        "/usr/bin/node",
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    return None


# --------------------------------------------------------------------------
# Playwright 降级占位
# --------------------------------------------------------------------------

def ensure_no_playwright():
    """让 extract-axure-data 走它自己的降级分支，而不是下载 300MB Chromium。

    它的 `ensureDependencies()` 判断的是 `~/.cache/axure-extractor/node_modules/playwright`
    目录是否存在；而真正加载时又会去 import 该目录下的 `index.mjs`。
    所以我们放一个**只有 package.json、没有 index.mjs** 的占位目录：

      - ensureDependencies() 看到目录存在 → 直接返回，不下载
      - loadPlaywright() 找不到 index.mjs → 返回 null → 脚本自己打印
        "Playwright 不可用，将跳过截图和主题提取" 并降级

    这是**利用它已有的降级设计**，不改它一行代码，升级也不会冲突。
    截图与设计令牌由我们自己的本机 Chrome 通道补（build/verify_visual.py）。

    返回 (是否首次创建, 占位路径)
    """
    pkg_dir = os.path.join(os.path.expanduser("~"), ".cache", "axure-extractor",
                           "node_modules", "playwright")
    pkg_json = os.path.join(pkg_dir, "package.json")
    if os.path.exists(pkg_json):
        return False, pkg_dir
    os.makedirs(pkg_dir, exist_ok=True)
    with open(pkg_json, "w", encoding="utf-8") as f:
        json.dump({
            "name": "playwright",
            "version": "0.0.0-stub",
            "private": True,
            "description": ("占位包：erp-page-forge 刻意不下载 Chromium。"
                            "截图与设计令牌改由项目自身的本机 Chrome 通道完成；"
                            "本占位用于让 extract-axure-data 走它的降级分支。"),
        }, f, ensure_ascii=False, indent=2)
    return True, pkg_dir


# --------------------------------------------------------------------------
# 本地静态服务（Node 的 fetch 不支持 file://）
# --------------------------------------------------------------------------

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def start_static(root, port=None):
    port = port or free_port()
    handler = functools.partial(_Quiet, directory=root)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


# --------------------------------------------------------------------------
# 调 extract-axure-data
# --------------------------------------------------------------------------

def run_extract(url, out_dir, pages=None, advanced=True, timeout=240, verbose=False):
    """优先使用不执行 JS 的 Python 解析器，兼容旧的静态提取调用接口。"""
    from axure_static import extract, StaticDataError
    try:
        result = extract(url, out_dir, pages, advanced)
        return True, json.dumps(result, ensure_ascii=False), ""
    except (StaticDataError, OSError, ValueError) as error:
        # 不把解析失败的 JS 交给会执行 JS 的旧提取器；仅明确选择 legacy 时使用旧路径。
        return False, "", "Python 静态提取失败: %s。可显式选择 --parser legacy（仅可信原型）。" % error


def run_extract_legacy(url, out_dir, pages=None, advanced=True, timeout=240, verbose=False):
    """兼容已安装的旧提取器，仅用于用户显式选择的可信原型。"""
    node = find_node()
    if not node:
        raise SystemExit("[错误] 未找到 node，无法运行 extract-axure-data")
    script = os.path.join(skill_dir(), "scripts", "extract.mjs")
    if not os.path.exists(script):
        raise SystemExit("[错误] 未找到提取脚本: %s" % script)

    cmd = [node, script, url, "--all", "-o", out_dir]
    if advanced:
        cmd.append("--advanced")
    if pages:
        cmd += ["--pages", ",".join(pages)]
    if verbose:
        cmd.append("--verbose")

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, env=env)
    return proc.returncode == 0, proc.stdout or "", proc.stderr or ""


# --------------------------------------------------------------------------
# 读提取结果
# --------------------------------------------------------------------------

def load_extraction(out_dir, page):
    """把 extract-axure-data 的产物读成统一结构。"""
    ex = {"sitemap": None, "pages": [], "logo": "", "page": page}

    sp = os.path.join(out_dir, "sitemap.json")
    if os.path.exists(sp):
        s = _load(sp)
        ex["sitemap"] = s.get("sitemap")
        ex["pages"] = s.get("pages") or []
        ex["logo"] = s.get("logo") or ""

    pdir = os.path.join(out_dir, "pages", page)
    for key, fname in (("data", "data.json"), ("notes", "notes.json"),
                       ("interactions", "interactions.json"), ("theme", "theme.json")):
        fp = os.path.join(pdir, fname)
        if os.path.exists(fp):
            try:
                ex[key] = _load(fp)
            except (ValueError, OSError):
                ex[key] = None
    if os.path.exists(os.path.join(pdir, "screenshot.png")):
        ex["screenshot"] = os.path.join(pdir, "screenshot.png")
    return ex


# --------------------------------------------------------------------------
# 标注 → PageSpec
# --------------------------------------------------------------------------

# Axure 标注里的类型 → 我们 PageSpec 的类型
TYPE_MAP = {
    "code": "code", "text": "text", "textarea": "textarea", "enum": "enum",
    "select": "enum", "date": "date", "datetime": "datetime", "number": "number",
    "int": "number", "money": "money", "currency": "money", "person": "person",
    "user": "person", "org": "org", "dept": "org", "bool": "bool",
    "status": "status", "action": "action", "button": "action",
}

TONE_BY_NAME = [
    (("待审核", "待审批", "待处理", "预警", "临期"), "warn"),
    (("已驳回", "驳回", "作废", "终止", "停用", "超期"), "danger"),
    (("已转办", "转办"), "purple"),
    (("已完成", "已通过", "已生效", "正常", "启用", "合作中"), "ok"),
]


def tone_of(state_name):
    for keys, tone in TONE_BY_NAME:
        if any(k in state_name for k in keys):
            return tone
    return "brand"


def parse_notes(notes):
    """把提取到的标注拆成 字段 / 动作 两组，保持 Axure 里的顺序（= 列顺序）。

    标注结构（见 extract-axure-data 的 extractNotes）：
        {"page": {...页面级说明...},
         "id-docNo": {"fieldName": "单据号", "type": "code", "description": "...", ...}}
    """
    fields, actions = [], []
    for key, val in (notes or {}).items():
        if key == "page" or not isinstance(val, dict):
            continue
        name = val.get("fieldName") or val.get("name") or ""
        if not name:
            continue
        raw_type = str(val.get("type") or "text").lower()
        t = TYPE_MAP.get(raw_type, "text")
        item = {
            "name": name,
            "key": _camel(name),
            "type": t,
            "description": val.get("description") or "",
            "provenance": "axure",
            "_scriptId": key.replace("id-", ""),
        }
        for extra in ("options", "control", "align", "placeholder", "codePattern",
                      "precision", "unit", "required"):
            if val.get(extra) not in (None, "", []):
                item[extra] = val[extra]
        if t == "action":
            item["scope"] = val.get("scope") or "row"
            item["allowedStates"] = val.get("allowedStates") or []
            if val.get("primary"):
                item["primary"] = True
            actions.append(item)
        else:
            fields.append(item)
    return fields, actions


def _camel(name):
    """中文列名 → 稳定的英文 key。与 forge._camel 保持一致的策略。

    拼音库是零依赖约束下不可接受的依赖，所以走"按字符码点编码"的方案：
    产出形如 f_5355_636e_53f7 的稳定 key，可读性交给 fieldName 展示。
    对纯英文名直接转小驼峰。
    """
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9 _\-]*", name or ""):
        parts = re.split(r"[ _\-]+", name.strip())
        return parts[0].lower() + "".join(p.capitalize() for p in parts[1:])
    return "f_" + "_".join("%x" % ord(ch) for ch in name)


def nav_from_sitemap(ex):
    """把 Axure 的站点地图变成侧栏菜单。

    Axure 的 sitemap 本身就是一套现成的信息架构：Folder = 菜单分组，Page = 菜单项。
    不接它的话，侧栏会退回 Skill 的通用模块列表（销售/采购/库存…），
    跟原型根本不是一个系统 —— 菜单是第一眼印象，这个不准前面都白做。
    """
    # 注意：extract-axure-data 取的是 axure.sitemap.rootNodes，所以 sitemap.json 里
    # 的 `sitemap` 字段直接就是节点数组（不是 {rootNodes:[...]}）。这里两种都兼容。
    roots = ex.get("sitemap") or []
    if isinstance(roots, dict):
        roots = roots.get("rootNodes") or []

    def disp(node):
        return node.get("pageName") or ""

    def leaves(node, depth=0):
        out = []
        for ch in node.get("children") or []:
            if ch.get("type") == "Folder":
                if depth < 1:                      # 只展开一层，再深就不像侧栏了
                    out.extend(leaves(ch, depth + 1))
            else:
                nm = disp(ch)
                if nm:
                    out.append(nm)
        return out

    groups, loose = [], []
    for node in roots:
        if node.get("type") == "Folder":
            items = leaves(node)
            if items:
                groups.append({"group": disp(node), "items": items})
        else:
            nm = disp(node)
            if nm:
                loose.append(nm)
    if loose:
        groups.insert(0, {"group": "", "items": loose})
    return groups


def build_spec(page, ex, src_label):
    notes = ex.get("notes") or {}
    pnotes = notes.get("page") if isinstance(notes.get("page"), dict) else {}
    fields, actions = parse_notes(notes)

    page_name = (pnotes.get("pageName") or pnotes.get("title")
                 or _page_display_name(ex, page) or page)
    module = pnotes.get("module") or "业务"
    crumb = pnotes.get("breadcrumb")
    if isinstance(crumb, str):
        crumb = [c.strip() for c in re.split(r"[/／>]", crumb) if c.strip()]
    if not crumb:
        crumb = [module, page_name]

    # 列 = 非 action 字段，顺序沿用 Axure 标注顺序（产品经理的排列就是业务上该有的顺序）
    columns = [{"field": f["name"], "sortable": f["type"] not in ("textarea",)}
               for f in fields]

    # 查询条件：标注里声明了 control 的字段优先；否则按类型给默认控件
    conds = []
    for f in fields:
        ctrl = f.get("control")
        if not ctrl:
            if f["type"] == "enum" and len(f.get("options") or []) <= 8:
                ctrl = "select"
            elif f["type"] == "date":
                ctrl = "date-range"
        if ctrl:
            conds.append({"field": f["name"], "control": ctrl})

    # 状态机：从 status 类型的字段取枚举值，保持 Axure 里的顺序
    sm = None
    status_field = next((f for f in fields if f["type"] == "status"), None)
    if status_field and status_field.get("options"):
        states = [{"name": s, "tone": tone_of(s)} for s in status_field["options"]]
        sm = {"states": states, "initial": states[0]["name"], "transitions": []}

    # 动作：Axure 标注里的按钮定义直接可用
    acts = []
    for a in actions:
        acts.append({
            "name": a["name"], "scope": a.get("scope", "row"),
            "allowedStates": a.get("allowedStates") or [],
            "primary": bool(a.get("primary")),
        })

    # 编码格式：从标注里的 codePattern 解析（如 RK-2026-100371）
    entity = {"name": page_name.replace("管理", "") or page_name}
    code_field = next((f for f in fields if f["type"] == "code"), None)
    if code_field and code_field.get("codePattern"):
        sys.path.insert(0, HERE)
        from vision import parse_code_pattern
        entity.update(parse_code_pattern(code_field["codePattern"]))

    spec = {
        "specVersion": "1.0",
        "_source": {
            "kind": "axure",
            "src": src_label,
            "page": page,
            "note": ("字段/列/动作来自 Axure 标注（provenance=axure）—— 是产品经理写的文字定义，"
                     "不是从像素猜的。确认后即为该页面的唯一生成输入。"),
        },
        "page": {"name": page_name, "module": module, "menu": page_name,
                 "breadcrumb": crumb,
                 "brand": (pnotes.get("brand") or "")},
        "archetype": pnotes.get("archetype") or guess_archetype(fields, actions),
        "entity": entity,
        "fields": fields,
        "columns": columns,
        "actions": acts or [{"name": "编辑", "scope": "row", "allowedStates": []}],
        "data": {
            "minRows": pnotes.get("rowCount") or 12,
            "currency": "¥", "dateFormat": "YYYY-MM-DD",
            "mustHaveException": True,
            "disclaimer": "演示用虚构数据，与任何真实企业无关",
            "user": pnotes.get("user") or "", "userRole": pnotes.get("userRole") or "",
        },
        "stack": {"target": "standalone"},
        "custom": [],
    }
    if conds:
        spec["query"] = {"conditions": conds}
    if sm:
        spec["statusMachine"] = sm
    nav = nav_from_sitemap(ex)
    if nav:
        spec["nav"] = nav
    if ex.get("theme"):
        spec["_axureTheme"] = summarize_theme(ex["theme"])
    return spec


def _page_display_name(ex, page):
    """从 sitemap 里找该页的中文显示名。"""
    for p in ex.get("pages") or []:
        url = str(p.get("url") or "")
        if url.replace(".html", "") == page:
            return p.get("pageName") or ""
    return ""


def guess_archetype(fields, actions):
    """按标注内容判断原型。判不出来就老实说 A1（最常见的列表页），并让上层提示确认。"""
    names = [f["name"] for f in fields]
    has_form = any(f["type"] in ("textarea",) for f in fields)
    has_status = any(f["type"] == "status" for f in fields)
    has_money = any(f["type"] == "money" for f in fields)
    scopes = {a.get("scope") for a in actions}
    if scopes & {"form-footer"} or (has_form and not has_status):
        return "A2"
    if len(names) >= 5 and (has_status or has_money):
        return "A1"
    return "A1"


def summarize_theme(theme):
    """把 theme.json 压成我们能用的几个值（颜色 + 圆角 + 间距）。"""
    def top(lst, n=3):
        out = []
        for it in (lst or [])[:n]:
            v = it.get("value") if isinstance(it, dict) else it
            if v:
                out.append(v)
        return out
    colors = theme.get("colors") or {}
    return {
        "background": top(colors.get("background")),
        "text": top(colors.get("text")),
        "border": top(colors.get("border")),
        "radius": top(theme.get("radius")),
        "spacing": top(theme.get("spacing")),
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Axure 原型 → PageSpec（第四输入入口）")
    ap.add_argument("--src", required=True, help="Axure 导出目录（或已在跑的 http URL）")
    ap.add_argument("--page", help="页面名（= files/{page} 的目录名）。默认取第一页")
    ap.add_argument("--out", help="输出 PageSpec JSON")
    ap.add_argument("--forge", dest="forge_out", help="顺便确定性生成到该 HTML")
    ap.add_argument("--raw-dir", help="提取目录；默认输出文件旁的 axure-raw，不写安装目录")
    ap.add_argument("--parser", choices=["python", "legacy"], default="python",
                    help="默认 Python 静态解析；legacy 依赖外部 skill，仅用于可信原型")
    ap.add_argument("--extract-only", action="store_true", help="只提取，不生成 PageSpec")
    ap.add_argument("--no-advanced", action="store_true",
                    help="不取交互/标注/文本（只取 sitemap，通常用不到）")
    args = ap.parse_args()

    # 默认纯 Python 路径不安装依赖，不在用户 home 创建占位目录。

    httpd = None
    if re.match(r"^https?://", args.src):
        base = args.src
        src_label = args.src
    else:
        root = os.path.abspath(args.src)
        if not os.path.isdir(root):
            raise SystemExit("[错误] 目录不存在: %s" % root)
        if not os.path.exists(os.path.join(root, "data", "document.js")):
            raise SystemExit("[错误] %s 下没有 data/document.js —— 这不像 Axure 导出目录" % root)
        base = root
        if args.parser == "legacy":
            httpd, port = start_static(root)
            base = "http://127.0.0.1:%d/" % port
        src_label = root

    try:
        target = args.out or args.forge_out
        default_raw = os.path.join(os.path.dirname(os.path.abspath(target)) if target else os.getcwd(), "axure-raw")
        out_dir = os.path.abspath(args.raw_dir or default_raw)
        print("提取中 [%s]: %s → %s" % (args.parser, base, out_dir), file=sys.stderr)
        extractor = run_extract_legacy if args.parser == "legacy" else run_extract
        ok, out, err = extractor(base, out_dir, pages=[args.page] if args.page else None,
                                 advanced=not args.no_advanced)
        tail = "\n".join([l for l in (out or "").splitlines() if l.strip()][-14:])
        print(tail, file=sys.stderr)
        if err.strip():
            print("[脚本stderr] " + err.strip()[-800:], file=sys.stderr)
        if not ok:
            raise SystemExit("[错误] extract-axure-data 执行失败")

        sm_path = os.path.join(out_dir, "sitemap.json")
        if not os.path.exists(sm_path):
            raise SystemExit("[错误] 未产出 sitemap.json，提取失败")
        sm = _load(sm_path)
        pages = [str(p.get("url") or "").replace(".html", "") for p in (sm.get("pages") or [])]
        page = args.page or (pages[0] if pages else None)
        if not page:
            raise SystemExit("[错误] sitemap 里没有页面")
        if args.page and pages and page not in pages:
            print("[警告] 指定的页面 %s 不在 sitemap（%s），仍按该名字读取" % (page, pages),
                  file=sys.stderr)

        ex = load_extraction(out_dir, page)
        n_notes = len([k for k in (ex.get("notes") or {}) if k != "page"])
        print("\n提取摘要:", file=sys.stderr)
        print("  站点页面 : %s" % (pages or "无"), file=sys.stderr)
        print("  目标页面 : %s" % page, file=sys.stderr)
        print("  标注条数 : %d" % n_notes, file=sys.stderr)
        print("  交互映射 : %s" % ("有" if ex.get("interactions") else "无"), file=sys.stderr)
        print("  设计令牌 : %s（Axure 自带 theme.json；无则由本机 Chrome 通道补）"
              % ("有" if ex.get("theme") else "无"), file=sys.stderr)

        if args.extract_only:
            return 0

        spec = build_spec(page, ex, src_label)
        if not spec["fields"]:
            raise SystemExit("[错误] 没有从标注里解析出任何字段 —— "
                             "确认 Axure 原型的页面是否有组件标注（notes）")
        text = json.dumps(spec, ensure_ascii=False, indent=2)
        if args.out:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            print("\nPageSpec 已生成: %s" % os.path.abspath(args.out), file=sys.stderr)
        else:
            print(text)

        print("  原型     : %s" % spec["archetype"], file=sys.stderr)
        print("  字段 %d · 列 %d · 动作 %d · 状态 %d" % (
            len(spec["fields"]), len(spec["columns"]), len(spec["actions"]),
            len((spec.get("statusMachine") or {}).get("states", []))), file=sys.stderr)

        if args.forge_out:
            import forge
            html, model, _ = forge.build_from_spec(spec)
            dst = os.path.abspath(args.forge_out)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(html)
            print("已生成页面: %s（%.1f KB）" % (dst, len(html.encode("utf-8")) / 1024.0),
                  file=sys.stderr)
        return 0
    finally:
        if httpd:
            httpd.shutdown()


if __name__ == "__main__":
    sys.exit(main())
