#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用契约断言引擎 —— 按 PageSpec + archetype 校验**任意产物**，不再写死自家 6 个页面。

这是 `check_pages.py` 的泛化版本：check_pages.py 只认 templates/*.html 且只查四件事；
本引擎的断言来自数据（skill/spec/archetypes.json 与 PageSpec），所以换成 React 目录、
Vue 目录、别人写的单文件 HTML，一样能跑。

断言类别：契约（static 文本）。
  渲染 / 交互 / 数据三类断言由 verify_acceptance.py 汇总执行，本文件只做静态契约层。

用法:
    python build/verify_contract.py --archetype A5 --artifact <路径>
    python build/verify_contract.py --spec skill/spec/examples/license-approval.pagespec.json --artifact <路径>
    python build/verify_contract.py --spec x.json --artifact <目录> --stack react --json

退出码: 0 = 全部硬性断言通过, 1 = 存在硬性失败
"""

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC_DIR = os.path.join(ROOT, "skill", "spec")

# 目录型产物收集文本时的扩展名与排除项
TEXT_EXT = {".js", ".jsx", ".ts", ".tsx", ".vue", ".css", ".scss", ".less", ".html", ".htm", ".json", ".md"}
SKIP_DIRS = {"node_modules", "dist", "build", ".git", ".cache", "coverage", ".next", "__pycache__"}


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_archetypes():
    return load_json(os.path.join(SPEC_DIR, "archetypes.json"))


def load_spec(path):
    return load_json(path)


# --------------------------------------------------------------------------
# 产物文本收集
# --------------------------------------------------------------------------

def collect_text(artifact, stack="auto"):
    """把产物归一成一段可断言的文本。

    一个「产物单元」经常跨目录 —— 页面组件在 src/pages/X/，状态切片在 src/store/modules/。
    所以 artifact 允许传一个路径，也允许传一串（列表 / 逗号分隔）。
    """
    if isinstance(artifact, (list, tuple)) or "," in str(artifact):
        arts = artifact if isinstance(artifact, (list, tuple)) else str(artifact).split(",")
        arts = [a.strip() for a in arts if a.strip()]
    else:
        arts = [artifact]

    if len(arts) > 1:
        texts, envs = [], []
        for a in arts:
            t, e = collect_text(a, stack)
            texts.append(t)
            envs.append(e)
        return "\n".join(texts), {
            "kind": "multi", "path": " + ".join(e["path"] for e in envs),
            "stack": envs[0]["stack"], "files": sum(e["files"] for e in envs),
            "parts": [e["path"] for e in envs],
        }

    artifact = os.path.abspath(arts[0])
    if not os.path.exists(artifact):
        raise SystemExit("[错误] 产物路径不存在: %s" % artifact)

    if os.path.isfile(artifact):
        with open(artifact, encoding="utf-8", errors="replace") as f:
            text = f.read()
        detected = stack if stack != "auto" else (
            "vue" if artifact.endswith(".vue") else ("html" if artifact.endswith((".html", ".htm")) else "single"))
        return text, {"kind": "file", "path": artifact, "stack": detected, "files": 1}

    parts, files = [], []
    for dirpath, dirnames, filenames in os.walk(artifact):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in sorted(filenames):
            if os.path.splitext(fn)[1].lower() not in TEXT_EXT:
                continue
            p = os.path.join(dirpath, fn)
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    parts.append(f.read())
            except OSError:
                continue
            files.append(os.path.relpath(p, artifact))

    detected = stack
    if detected == "auto":
        joined = " ".join(files)
        if any(f.endswith(".vue") for f in files):
            detected = "vue"
        elif any(f.endswith((".jsx", ".tsx")) for f in files):
            detected = "react"
        else:
            detected = "dir"
    return "\n".join(parts), {"kind": "dir", "path": artifact, "stack": detected, "files": len(files)}


# --------------------------------------------------------------------------
# 断言执行
# --------------------------------------------------------------------------

def run_static_check(check, text):
    """执行单条 static 断言，返回 (pass, evidence)。"""
    kind = check["kind"]
    if kind == "text":
        hit = check["value"] in text
        return hit, "命中「%s」" % check["value"] if hit else "未找到「%s」" % check["value"]

    if kind == "text_all":
        miss = [v for v in check["values"] if v not in text]
        return (not miss), "全部命中" if not miss else "缺失: " + "、".join(miss)

    if kind == "text_any":
        hit = [v for v in check["values"] if v in text]
        return bool(hit), ("命中 " + "、".join(hit)) if hit else "均未命中: " + "、".join(check["values"])

    if kind == "absent":
        hit = check["value"] in text
        return (not hit), "未出现（符合预期）" if not hit else "出现了「%s」" % check["value"]

    if kind == "regex":
        m = re.search(check["pattern"], text)
        return bool(m), ("匹配 %s" % m.group(0)[:50]) if m else "未匹配 /%s/" % check["pattern"]

    if kind == "regex_count":
        n = len(re.findall(check["pattern"], text))
        need = check.get("min", 1)
        return n >= need, "命中 %d 次（需 ≥%d）" % (n, need)

    return None, "未知断言类型: %s" % kind


def spec_checks(spec):
    """从 PageSpec 推导出的契约断言 —— 声明了就必须在产物里一个不少。"""
    out = []

    for f in spec.get("fields", []):
        out.append({
            "id": "SPEC.field.%s" % f["name"], "kind": "text", "value": f["name"],
            "severity": "hard", "desc": "字段「%s」出现在产物中（声明出处: %s）" % (f["name"], f.get("provenance", "-")),
        })

    for a in spec.get("actions", []):
        out.append({
            "id": "SPEC.action.%s" % a["name"], "kind": "text", "value": a["name"],
            "severity": "hard", "desc": "动作「%s」（%s）出现在产物中" % (a["name"], a.get("scope", "-")),
        })

    sm = spec.get("statusMachine") or {}
    for s in sm.get("states", []):
        out.append({
            "id": "SPEC.state.%s" % s["name"], "kind": "text", "value": s["name"],
            "severity": "hard", "desc": "状态「%s」出现在产物中" % s["name"],
        })
    for n in sm.get("nodeFlow", []):
        out.append({
            "id": "SPEC.node.%s" % n, "kind": "text", "value": n,
            "severity": "hard", "desc": "流程节点「%s」出现在产物中" % n,
        })

    for c in spec.get("custom", []) or []:
        out.append({
            "id": "SPEC.custom.%s" % c.get("requirement", "")[:16], "kind": "text",
            "value": c.get("requirement", ""), "severity": "soft",
            "desc": "逃生舱诉求「%s」已落地（需人工确认）" % c.get("requirement", ""),
        })

    return out


def verify(spec=None, archetype=None, artifact=None, stack="auto", include_soft=True):
    """核心入口：返回结构化结果，供 CLI 与 verify_acceptance.py 共用。"""
    arch = load_archetypes()

    if not archetype:
        if spec:
            archetype = spec.get("archetype")
        if not archetype:
            raise SystemExit("[错误] 需要 --archetype 或 spec 里的 archetype")

    if archetype not in arch["archetypes"]:
        raise SystemExit("[错误] 未知原型: %s（可选 %s）" % (archetype, "/".join(arch["archetypes"])))

    a = arch["archetypes"][archetype]
    checks = list(a.get("static", []))
    if spec:
        checks = spec_checks(spec) + checks

    text, env = collect_text(artifact, stack)

    results = []
    for c in checks:
        sev = c.get("severity", "hard")
        if sev == "soft" and not include_soft:
            continue
        ok, ev = run_static_check(c, text)
        if ok is None:
            continue
        results.append({
            "id": c["id"], "category": "contract", "kind": c["kind"],
            "severity": sev, "desc": c.get("desc", ""), "pass": bool(ok), "evidence": ev,
        })

    hard_total = sum(1 for r in results if r["severity"] == "hard")
    hard_pass = sum(1 for r in results if r["severity"] == "hard" and r["pass"])

    return {
        "archetype": archetype,
        "archetypeName": a["name"],
        "regions": a.get("regions", []),
        "env": env,
        "results": results,
        "summary": {
            "total": len(results), "passed": sum(1 for r in results if r["pass"]),
            "hardTotal": hard_total, "hardPassed": hard_pass,
            "ok": hard_pass == hard_total,
        },
    }


def main():
    ap = argparse.ArgumentParser(description="通用契约断言引擎")
    ap.add_argument("--spec", help="PageSpec JSON 路径")
    ap.add_argument("--archetype", help="原型编号 A1–A6（有 spec 时可省）")
    ap.add_argument("--artifact", required=True, action="append",
                    help="产物路径，可重复传（页面单元跨目录时把 store slice 等一并传入）")
    ap.add_argument("--stack", default="auto", choices=["auto", "html", "react", "vue", "dir", "single"])
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    spec = load_spec(args.spec) if args.spec else None
    r = verify(spec=spec, archetype=args.archetype, artifact=args.artifact, stack=args.stack)

    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        s = r["summary"]
        print("原型 %s · %s   |   产物: %s (%s)" % (
            r["archetype"], r["archetypeName"], r["env"]["path"], r["env"]["stack"]))
        print("-" * 96)
        for x in r["results"]:
            mark = "通过" if x["pass"] else ("★ 失败" if x["severity"] == "hard" else "· 建议")
            print("[%s] %-34s %s" % (mark, x["id"], x["evidence"]))
        print("-" * 96)
        print("硬性断言 %d/%d 通过  总断言 %d/%d" % (s["hardPassed"], s["hardTotal"], s["passed"], s["total"]))
        print("契约层结论: %s" % ("通过" if s["ok"] else "不通过"))

    return 0 if r["summary"]["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
