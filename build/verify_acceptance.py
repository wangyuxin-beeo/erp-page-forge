#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验收断言汇总器 —— 四类断言 + 验收清单前置。

这是阶段一「把三道校验泛化成对任意产物可跑」的主入口。四类断言：

  契约 contract      按 PageSpec + archetype 静态校验产物文本（verify_contract.py）
  渲染 render        真实渲染后量 1280px 横向溢出 / 脏值 / 白屏
  交互 interaction   在同源 iframe 里真实点按钮，验证"筛选真的会筛、同意真的减待办"
  数据 data          核对合计=明细之和、待办计数=列表条数这类自洽性

两种用法：

  ① 验收清单前置（生成前，给用户看"本次要过哪些项"）
     python build/verify_acceptance.py --spec skill/spec/examples/license-approval.pagespec.json --plan

  ② 跑验收（生成后）
     python build/verify_acceptance.py --spec <spec> --artifact <页面目录> --artifact <store文件> \
         --root "C:/Users/xxx/my-app/build" --route /license-approval --out build/acceptance.json

降级原则：拿不到浏览器、或控件命名不符合惯例导致无法自动驱动时，标 unsupported（需人工确认），
不计入失败。自动断言不完整时要诚实说"不知道"，而不是假通过。

退出码: 0 = 无硬性失败, 1 = 存在硬性失败
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import serve_spa                      # noqa: E402
import verify_contract                # noqa: E402
from verify_react_render import find_browser, run_browser   # noqa: E402

SPEC_DIR = os.path.join(ROOT, "skill", "spec")
CATS = [("contract", "契约断言"), ("render", "渲染断言"),
        ("interaction", "交互断言"), ("data", "数据断言")]


# --------------------------------------------------------------------------
# 验收清单前置
# --------------------------------------------------------------------------

def build_plan(spec, archetype):
    """从 spec + archetype 推导本次验收项 —— 让用户先看到"要过哪些项"。"""
    arch = verify_contract.load_archetypes()
    a = arch["archetypes"][archetype]

    plan = {"archetype": archetype, "archetypeName": a["name"], "items": {k: [] for k, _ in CATS}}

    for c in verify_contract.spec_checks(spec):
        plan["items"]["contract"].append({"id": c["id"], "desc": c["desc"], "severity": c["severity"]})
    for c in a.get("static", []):
        plan["items"]["contract"].append({"id": c["id"], "desc": c["desc"], "severity": c.get("severity", "hard")})
    for c in a.get("render", []):
        plan["items"]["render"].append({"id": c["id"], "desc": c["desc"], "severity": c.get("severity", "hard")})
    for c in a.get("interaction", []):
        plan["items"]["interaction"].append({"id": c["id"], "desc": c["desc"], "severity": c.get("severity", "hard")})
    for c in a.get("data", []):
        plan["items"]["data"].append({"id": c["id"], "desc": c["desc"], "severity": c.get("severity", "hard")})

    plan["total"] = sum(len(v) for v in plan["items"].values())
    return plan


def print_plan(plan, spec):
    p = spec.get("page", {})
    print("验收清单（生成后需逐项通过）")
    print("  页面 : %s    原型: %s · %s" % (p.get("name", "-"), plan["archetype"], plan["archetypeName"]))
    print("=" * 92)
    for key, title in CATS:
        items = plan["items"][key]
        print("\n%s · %d 项" % (title, len(items)))
        for i, it in enumerate(items, 1):
            soft = "（建议项）" if it["severity"] == "soft" else ""
            print("  %2d. %s%s" % (i, it["desc"], soft))
    print("\n" + "=" * 92)
    print("合计 %d 项。硬性项全过才算交付；建议项不过只提示。" % plan["total"])


# --------------------------------------------------------------------------
# 探测执行
# --------------------------------------------------------------------------

def _parse_accept(out):
    m = re.search(r"ACCEPT_RESULT (\{.*?\})</pre>", out, re.S)
    if not m:
        return {"ok": False, "err": "未取到验收结果（浏览器未产出或页面报错）"}
    try:
        return json.loads(m.group(1))
    except ValueError as e:
        return {"ok": False, "err": "验收结果不是合法 JSON: %s" % e}


def _inline_probe(defs):
    """把断言清单内联进探测页，返回 (html, payload)。

    为什么不用 URL 传参：defs 含中文枚举与选择器，URL 编码后能到 8KB 以上，
    浏览器会卡死或静默截断参数，断言就变成空列表 —— 踩过这个坑。
    """
    hints, items = {}, []
    for d in defs:
        args = {}
        for k, v in (d.get("args") or {}).items():
            if v in (None, {}, [], ""):
                continue
            if k == "hints":
                # hints 对所有断言是同一份，提到顶层只传一次；
                # _note 这类只给人看的元数据不往下传。
                hints = {hk: hv for hk, hv in v.items() if not hk.startswith("_")}
                continue
            args[k] = v
        items.append({"id": d["id"], "driver": d["driver"], "args": args})
    payload = json.dumps({"h": hints, "d": items}, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")          # 防止提前闭合 script 标签
    return serve_spa.ACCEPT_HTML.replace("__DEFS_PAYLOAD__", payload), len(payload)


def probe_accept(browser, base_url, route, defs, width, height=1100,
                 workdir=None, probe_dir=None, timeout=240):
    """请求同源验收探测页并回读结果。

    三条路径：
      file://   → 探测页写在临时目录，配合 --allow-file-access-from-files
      http+目录 → 探测页内联后写进被测服务目录（同源），用完删除（推荐）
      http 无目录 → 复用被测服务的 /__accept.html，清单走 URL（长清单可能被截断）
    """
    html, plen = _inline_probe(defs)
    temps = []
    extra = []

    if base_url.startswith("file://"):
        probe = os.path.join(workdir, "accept_probe.html")
        with open(probe, "w", encoding="utf-8") as f:
            f.write(html)
        temps.append(probe)
        url = "file:///" + probe.replace("\\", "/").replace(" ", "%20")
        extra = ["--allow-file-access-from-files"]
        url += "?path=%s&w=%d&h=%d" % (urllib.parse.quote(route, safe=""), width, height)
    elif probe_dir:
        # 探测页必须与目标同源才能读 contentDocument，所以写进服务目录
        probe = os.path.join(probe_dir, "__accept_probe.html")
        with open(probe, "w", encoding="utf-8") as f:
            f.write(html)
        temps.append(probe)
        url = "%s/__accept_probe.html?path=%s&w=%d&h=%d" % (
            base_url.rstrip("/"), urllib.parse.quote(route, safe=""), width, height)
    else:
        url = "%s/__accept.html?path=%s&w=%d&h=%d&defs=%s" % (
            base_url.rstrip("/"), urllib.parse.quote(route, safe=""), width, height,
            urllib.parse.quote(json.dumps(
                [{"id": d["id"], "driver": d["driver"], "args": d.get("args") or {}} for d in defs],
                ensure_ascii=False, separators=(",", ":")), safe=""))

    try:
        dom, err = run_browser(browser, url, (width + 160, height + 300),
                               extra=extra, dump=True, timeout=timeout)
    finally:
        for p in temps:
            try:
                os.remove(p)
            except OSError:
                pass

    r = _parse_accept(dom)
    r["payloadLen"] = plen
    if not r.get("ok"):
        r["stderr"] = (err or "").strip()[:200]
    return r


def run_render(archetype, metrics, results):
    """把 metrics 映射到 archetype 的 render 断言。"""
    arch = verify_contract.load_archetypes()
    checks = arch["archetypes"][archetype].get("render", [])
    out = []
    if not metrics or metrics.get("error"):
        reason = (metrics or {}).get("error", "未取得渲染指标")
        for c in checks:
            out.append({"id": c["id"], "category": "render", "severity": c.get("severity", "hard"),
                        "desc": c["desc"], "status": "unsupported", "detail": "渲染指标不可用: %s" % reason})
        return out

    for c in checks:
        spec_dom = c.get("domSpec", {})
        ok, detail = True, []
        if "maxOverflowPx" in spec_dom:
            ov = metrics.get("overflowX", 0)
            if ov > spec_dom["maxOverflowPx"]:
                ok = False
            detail.append("%dpx 横向溢出（容差 %dpx）" % (ov, spec_dom["maxOverflowPx"]))
        if spec_dom.get("forbidDirty"):
            d = metrics.get("dirty") or []
            if d:
                ok = False
            detail.append("脏值 " + (",".join(d) if d else "无"))
        if spec_dom.get("forbidScriptErrors"):
            errs = metrics.get("winErrors") or []
            if errs:
                ok = False
            detail.append("脚本错误 " + ("；".join(errs[:3]) if errs else "无"))
        if "minTextLen" in spec_dom:
            tl = metrics.get("textLen", 0)
            if tl < spec_dom["minTextLen"]:
                ok = False
            detail.append("正文 %d 字（下限 %d）" % (tl, spec_dom["minTextLen"]))
        if "textMatch" in spec_dom:
            # 有些元素是运行时渲染的（分页器、空状态、抽屉内容），静态源里根本没这段文案，
            # 必须对"渲染后的正文"做匹配 —— 否则会把好产物判失败。
            body = metrics.get("text") or ""
            m = re.search(spec_dom["textMatch"], body)
            if not m:
                ok = False
            detail.append("渲染文本 /%s/ %s" % (spec_dom["textMatch"], ("命中 " + m.group(0)[:30]) if m else "未命中"))
        if spec_dom.get("noAllEmptyColumns"):
            cols = metrics.get("emptyColumns") or []
            st = metrics.get("cellStats") or {}
            if cols:
                ok = False
            detail.append("空列 %s（表共 %s 列）" % (
                ("第 " + "、".join(str(c + 1) for c in cols) + " 列") if cols else "无",
                st.get("columns", "-")))
        out.append({"id": c["id"], "category": "render", "severity": c.get("severity", "hard"),
                    "desc": c["desc"], "status": "pass" if ok else "fail", "detail": "；".join(detail)})
    return out


def collect_driver_defs(spec, archetype):
    """把 interaction/data 断言转成 driver 调用定义，并把 spec 携带的参数注入 driver。

    spec 可以为空（只验原型契约的场景），此时用 archetype 的默认参数。
    """
    spec = spec or {}
    arch = verify_contract.load_archetypes()
    a = arch["archetypes"][archetype]
    sm = spec.get("statusMachine") or {}
    data = spec.get("data") or {}
    hints = spec.get("interactionHints") or {}

    # 枚举覆盖只要求「初始态 + 终态」。中间态（如「流转中」）只在流转过程中出现，
    # 初始 mock 数据不覆盖它是正常的 —— 写死要求会造出假失败。
    states = [s for s in sm.get("states", []) if not s.get("intermediate")]

    inject = {
        "row_count_min": {"min": data.get("minRows", 12)},
        "enum_coverage": {"values": [s["name"] for s in states]},
        "timeline_len_equals_node_index": {"nodes": sm.get("nodeFlow", [])},
        "kpi_count_min": {"min": 4},
    }

    defs = []
    # ★ 顺序有语义：data 是只读观察，interaction 会点按钮改状态。
    #   实测踩过 —— data 排在 interaction 之后时，前序的同意/驳回把页面改乱，
    #   时间线与枚举断言看到的是被操作过的页面，产生假失败。
    for cat in ("data", "interaction"):
        for c in a.get(cat, []):
            drv = c["driver"]
            args = dict(inject.get(drv, {}))
            args["hints"] = hints
            defs.append({
                "id": c["id"], "category": cat, "driver": drv,
                "desc": c["desc"], "severity": c.get("severity", "hard"),
                "args": args,
            })
    return defs


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def run(args):
    spec = verify_contract.load_spec(args.spec) if args.spec else None
    archetype = args.archetype or (spec or {}).get("archetype")
    if not archetype:
        raise SystemExit("[错误] 需要 --archetype 或 spec 里的 archetype")

    report = {
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "archetype": archetype,
        "page": (spec or {}).get("page", {}),
        "artifact": {"inputs": args.artifact or []},
        "categories": {},
    }

    # ① 契约
    contract = verify_contract.verify(spec=spec, archetype=archetype,
                                      artifact=args.artifact, stack=args.stack)
    report["artifact"]["env"] = contract["env"]
    report["archetypeName"] = contract["archetypeName"]
    report["categories"]["contract"] = {
        "total": len(contract["results"]),
        "passed": sum(1 for r in contract["results"] if r["pass"]),
        "results": [{"id": r["id"], "desc": r["desc"], "severity": r["severity"],
                     "status": "pass" if r["pass"] else "fail", "detail": r["evidence"]}
                    for r in contract["results"]],
    }

    # ②③④ 渲染 / 交互 / 数据 —— 需要真实运行环境
    driver_defs = collect_driver_defs(spec, archetype)
    target, cleanup = resolve_target(args, report)
    if target is None:
        for key in ("render", "interaction", "data"):
            report["categories"][key] = {
                "total": 0, "passed": 0, "skipped": True,
                "reason": report["artifact"].get("skipReason", "未提供可运行目标"),
                "results": [],
            }
    else:
        browser = find_browser()
        base_url, route = target
        probe = probe_accept(browser, base_url, route, driver_defs, args.width,
                             workdir=cleanup["workdir"], probe_dir=args.root)
        if not probe.get("ok"):
            reason = probe.get("err") or probe.get("stderr") or "探测失败"
            for key in ("render", "interaction", "data"):
                report["categories"][key] = {"total": 0, "passed": 0, "skipped": True,
                                             "reason": reason, "results": []}
        else:
            # 渲染
            rr = run_render(archetype, probe.get("metrics"), probe.get("results"))
            report["categories"]["render"] = {
                "total": len(rr), "passed": sum(1 for r in rr if r["status"] == "pass"),
                "metrics": probe.get("metrics"), "results": rr,
            }
            # 交互 / 数据
            by_id = {r["id"]: r for r in (probe.get("results") or [])}
            for cat in ("interaction", "data"):
                items = []
                for d in driver_defs:
                    if d["category"] != cat:
                        continue
                    got = by_id.get(d["id"], {})
                    items.append({
                        "id": d["id"], "desc": d["desc"], "severity": d["severity"],
                        "driver": d["driver"], "status": got.get("status", "unsupported"),
                        "detail": got.get("detail", "未返回结果"),
                    })
                report["categories"][cat] = {
                    "total": len(items), "passed": sum(1 for r in items if r["status"] == "pass"),
                    "results": items,
                }

    # 汇总
    tot = passed = failed = unsupported = skipped = 0
    hard_failed = soft_failed = 0
    for key, _ in CATS:
        c = report["categories"].get(key) or {}
        if c.get("skipped"):
            skipped += 1
            continue
        for r in c.get("results", []):
            tot += 1
            st = r["status"]
            if st == "pass":
                passed += 1
            elif st == "fail":
                failed += 1
                if r.get("severity", "hard") == "hard":
                    hard_failed += 1
                else:
                    soft_failed += 1
            else:
                unsupported += 1

    report["summary"] = {
        "total": tot, "passed": passed, "failed": failed,
        "hardFailed": hard_failed, "softFailed": soft_failed,
        "unsupported": unsupported, "skippedCategories": skipped,
        "accepted": hard_failed == 0 and unsupported == 0 and skipped == 0,
        # 未执行项不得显示为完整通过；建议项失败仍保留为提示。
        "verdict": ("不通过（%d 项硬性失败）" % hard_failed if hard_failed else
                    "需人工确认（存在未覆盖断言）" if unsupported or skipped else "通过"),
    }

    if cleanup.get("httpd"):
        try:
            cleanup["httpd"].shutdown()
        except Exception:
            pass
    if cleanup.get("workdir"):
        shutil.rmtree(cleanup["workdir"], ignore_errors=True)

    return report


def resolve_target(args, report):
    """决定到哪去探测真实页面：内部起服务 / 复用外部服务 / file:// 单文件。"""
    workdir = tempfile.mkdtemp(prefix="forge_accept_")
    cleanup = {"workdir": workdir, "httpd": None}

    if args.root:
        try:
            httpd = serve_spa.start_server(args.root, args.port, quiet=True)
        except SystemExit as e:
            report["artifact"]["skipReason"] = str(e)
            return None, cleanup
        if not serve_spa.wait_ready(args.port):
            cleanup["httpd"] = httpd
            report["artifact"]["skipReason"] = "内置服务未就绪（端口 %d）" % args.port
            return None, cleanup
        cleanup["httpd"] = httpd
        report["artifact"]["servedBy"] = "http://127.0.0.1:%d  ←  %s" % (args.port, args.root)
        return ("http://127.0.0.1:%d" % args.port, args.route), cleanup

    if args.url:
        u = args.url
        if u.endswith((".html", ".htm")) and "://" not in u:
            u = "file:///" + os.path.abspath(u).replace("\\", "/")
        if u.startswith("file://"):
            report["artifact"]["servedBy"] = u
            return (u, u), cleanup
        report["artifact"]["servedBy"] = u
        return (u, args.route), cleanup

    # 退而求其次：产物里唯一的单文件 HTML
    htmls = [p for p in (args.artifact or []) if p.lower().endswith((".html", ".htm"))]
    if len(htmls) == 1:
        u = "file:///" + os.path.abspath(htmls[0]).replace("\\", "/")
        report["artifact"]["servedBy"] = u
        return (u, u), cleanup

    report["artifact"]["skipReason"] = (
        "未提供运行环境：请给 --root <构建产物目录>（内部起服务，推荐）"
        "或 --url <地址|单文件.html>；只有静态源码时，渲染/交互/数据三类断言无法执行")
    return None, cleanup


def print_report(report):
    s = report["summary"]
    print("页面 : %s    原型: %s" % (report["page"].get("name", "-"), report["archetype"]))
    if report["artifact"].get("env"):
        e = report["artifact"]["env"]
        print("产物 : %s (%s)" % (e.get("path", "-"), e.get("stack", "-")))
    if report["artifact"].get("servedBy"):
        print("运行 : %s" % report["artifact"]["servedBy"])
    print("=" * 100)

    for key, title in CATS:
        c = report["categories"].get(key) or {}
        if c.get("skipped"):
            print("\n%s · 跳过 —— %s" % (title, c.get("reason", "")))
            continue
        results = c.get("results", [])
        ok = sum(1 for r in results if r["status"] == "pass")
        note = ""
        if key == "render" and c.get("metrics"):
            m = c["metrics"]
            note = "   [溢出 %spx · 正文 %s 字 · 脏值 %s]" % (
                m.get("overflowX"), m.get("textLen"), ",".join(m.get("dirty") or []) or "无")
        print("\n%s · %d/%d 通过%s" % (title, ok, len(results), note))
        for r in results:
            mark = {"pass": "通过", "fail": "★ 失败", "unsupported": "? 需人工"}[r["status"]]
            extra = "（建议项）" if r.get("severity") == "soft" else ""
            print("  [%s] %-30s %s%s" % (mark, r["id"], r["detail"], extra))

    print("\n" + "=" * 100)
    print("断言 %d 项：通过 %d · 硬性失败 %d · 建议项未过 %d · 需人工确认 %d" % (
        s["total"], s["passed"], s.get("hardFailed", 0), s.get("softFailed", 0), s["unsupported"]))
    if s["skippedCategories"]:
        print("有 %d 类断言被跳过（缺运行环境），结论仅覆盖已执行部分" % s["skippedCategories"])
    print("验收结论：%s" % s["verdict"])


def main():
    ap = argparse.ArgumentParser(description="四类断言汇总 + 验收清单前置")
    ap.add_argument("--spec", help="PageSpec JSON 路径")
    ap.add_argument("--archetype", help="原型编号 A1–A6（有 spec 时可省）")
    ap.add_argument("--artifact", action="append", default=[], help="产物路径，可重复")
    ap.add_argument("--stack", default="auto", choices=["auto", "html", "react", "vue", "dir", "single"])
    ap.add_argument("--root", help="构建产物目录（内部起服务，推荐）")
    ap.add_argument("--route", default="/", help="被测路由，配合 --root")
    ap.add_argument("--port", type=int, default=3040)
    ap.add_argument("--url", help="直接指定目标：http 地址或单文件 .html")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--out", help="验收报告 JSON 输出路径")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--plan", action="store_true", help="只输出验收清单（生成前用）")
    args = ap.parse_args()

    spec = verify_contract.load_spec(args.spec) if args.spec else None
    archetype = args.archetype or (spec or {}).get("archetype")
    if not archetype:
        raise SystemExit("[错误] 需要 --archetype 或 spec 里的 archetype")

    if args.plan:
        plan = build_plan(spec or {}, archetype)
        if args.json:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
        else:
            print_plan(plan, spec or {})
        return 0

    if not args.artifact:
        raise SystemExit("[错误] 需要至少一个 --artifact（契约断言要有校验对象）")

    report = run(args)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)
        if args.out:
            print("\n报告已写入: %s" % os.path.abspath(args.out))

    return 0 if report["summary"]["hardFailed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
