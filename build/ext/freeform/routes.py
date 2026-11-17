#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自由版式扩展的路由与生成管线（自由版式扩展 · 接入层）。

被 studio_server / studio_api 以钩子方式调用（见 docs/freeform-extension-plan.md §6）：
  - POST /api/ff/*      → POST 表里的函数
  - GET  /api/ff/*      → get_route()
  - GET  /fflib/<id>/<fn> → get_file()（模板预览文件）
本文件不存在时系统行为与改造前一致。

生成管线（与 A1–A6 的 run_pipeline 同构）：
  FreeformSpec → ff_forge（图片入场）/ 插槽替换（HTML 入场）→ ff_verify → 报告
"""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.dirname(os.path.dirname(HERE))
ROOT = os.path.dirname(BUILD)
sys.path.insert(0, BUILD)
sys.path.insert(0, HERE)

import ff_spec        # noqa: E402
import ff_forge       # noqa: E402
import ff_verify      # noqa: E402
import ff_html        # noqa: E402
import studio_store   # noqa: E402
import studio_api     # noqa: E402

FF_LIB = os.path.join(studio_store.DATA_ROOT, "studio-library-ff")
SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
PY = sys.executable


def _load(p, fallback=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return fallback


def _dump(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(o, f, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# 模板库
# --------------------------------------------------------------------------

def ff_templates():
    out = []
    if os.path.isdir(FF_LIB):
        for tid in sorted(os.listdir(FF_LIB), reverse=True):
            mp = os.path.join(FF_LIB, tid, "meta.json")
            if SAFE_ID.match(tid) and os.path.isfile(mp):
                m = _load(mp) or {}
                m.update({"id": tid, "kind": "freeform",
                          "preview": "/fflib/%s/page.html" % tid,
                          "thumb": ("/fflib/%s/%s" % (tid, m["thumb"])) if m.get("thumb") else None})
                out.append(m)
    return out


def find_template(tid):
    if not SAFE_ID.match(tid or ""):
        return None
    d = os.path.join(FF_LIB, tid)
    sp = os.path.join(d, "freeform.spec.json")
    return d if os.path.isfile(sp) else None


def save_template(run_id, name):
    if not SAFE_ID.match(run_id or ""):
        return 400, {"error": "run 不合法"}
    rdir, _ = studio_store.find_run(run_id)
    if not rdir:
        return 404, {"error": "运行不存在"}
    sp = os.path.join(rdir, "freeform.spec.json")
    if not os.path.isfile(sp):
        return 404, {"error": "该次生成不是自由版式，无法存为自由版式模板"}
    meta = _load(os.path.join(rdir, "meta.json")) or {}
    if not meta.get("ok"):
        return 422, {"error": "自由版式生成或验收存在硬性失败，不能保存模板"}
    spec = _load(sp) or {}
    name = (name or "").strip()[:40] or spec.get("name") or "未命名自由版式"
    tid = "ff-" + run_id.split("-", 1)[1]
    tdir = os.path.join(FF_LIB, tid)
    os.makedirs(tdir, exist_ok=True)
    _dump(os.path.join(tdir, "freeform.spec.json"), spec)
    for fn in ("page.html", "report.html", "skeleton.html"):
        if os.path.isfile(os.path.join(rdir, fn)):
            shutil.copyfile(os.path.join(rdir, fn), os.path.join(tdir, fn))
    thumb = None
    for fn in os.listdir(rdir):
        if fn.startswith("input."):
            shutil.copyfile(os.path.join(rdir, fn), os.path.join(tdir, fn))
            thumb = fn
    slots = spec.get("slots") or {}
    ex = "用「%s」生成 XX" % name
    if slots:
        first = list(slots.items())[0]
        ex += " %s：%s" % (first[0], first[1])
    _dump(os.path.join(tdir, "meta.json"), {
        "name": name, "source": "freeform", "thumb": thumb, "slots": list(slots.keys()),
        "example": ex, "fromRun": run_id,
        "createdAt": time.strftime("%Y-%m-%d %H:%M")})
    return 200, {"ok": True, "id": tid, "name": name}


def delete_template(tid):
    if not (SAFE_ID.match(tid or "") and tid.startswith("ff-")):
        return 400, {"error": "只能删除自由版式模板"}
    tdir = os.path.join(FF_LIB, tid)
    if not os.path.isdir(tdir):
        return 404, {"error": "模板不存在"}
    shutil.rmtree(tdir)
    return 200, {"ok": True}


# --------------------------------------------------------------------------
# 生成管线
# --------------------------------------------------------------------------

def _resolve_source_file(spec, rdir):
    """spec.source.file → 绝对路径（模板里的素材在模板目录，运行里的在运行目录）。"""
    rel = (spec.get("source") or {}).get("file")
    if not rel:
        return None
    for cand in (os.path.join(rdir, rel),):
        if os.path.isfile(cand):
            return cand
    return None


def _run_steps(rid, rdir, spec, replaced=None):
    """生成 + 验收 + 报告，写齐产物与日志。返回 (ok, steps, summary)。"""
    p_spec = os.path.join(rdir, "freeform.spec.json")
    p_html = os.path.join(rdir, "page.html")
    p_acc = os.path.join(rdir, "acceptance.json")
    p_rep = os.path.join(rdir, "report.html")
    src_file = _resolve_source_file(spec, rdir)
    # HTML skeleton is the layout source, not a bitmap comparison baseline.
    source_image = None if (spec.get("source") or {}).get("type") == "html" else src_file
    steps = []

    try:
        if (spec.get("source") or {}).get("type") == "html":
            # HTML 入场：版式 100% 保留净化后的原页，只替换 data-ff-slot 文字
            skel_path = os.path.join(rdir, "skeleton.html")
            if not os.path.isfile(skel_path):
                raise ValueError("HTML 入场缺少 skeleton.html")
            with open(skel_path, encoding="utf-8") as f:
                skeleton = f.read()
            html, rep_map = ff_html.apply_slots(skeleton, spec.get("slots") or {})
            ok = True
            steps.append({"step": "插槽替换（HTML 入场）", "ok": True,
                          "log": "替换 %d 个 slot；版式保留原页" % len(rep_map)})
        else:
            html = ff_forge.build_html(spec, src_file)
            ok = True
            steps.append({"step": "确定性生成 ff_forge.py", "ok": True,
                          "log": "%.1f KB，区块 %d 个" % (len(html.encode("utf-8")) / 1024.0,
                                                         len(spec.get("regions") or []))})
    except ValueError as e:
        html, ok = "", False
        steps.append({"step": "确定性生成", "ok": False, "log": str(e)})

    summary = None
    verified = False
    if ok:
        with open(p_html, "w", encoding="utf-8", newline="\n") as f:
            f.write(html)
        rep, code = ff_verify.verify(spec, p_html, source_image, replaced)
        verified = code == 0
        _dump(p_acc, rep)
        summary = rep["summary"]
        steps.append({"step": "自由版式验收 ff_verify.py", "ok": code == 0,
                      "log": "%s：%d 项，通过 %d，硬性失败 %d，需人工 %d"
                             % (summary["verdict"], summary["total"], summary["passed"],
                                summary["hardFailed"], summary["unsupported"])})
        with open(p_rep, "w", encoding="utf-8", newline="\n") as f:
            f.write(ff_verify.render_report(rep))

    generated = ok
    state = studio_api.acceptance_state(summary)
    ok = bool(generated and verified and summary is not None and summary.get("hardFailed", 0) == 0)
    meta = {"run": rid, "source": "freeform", "ok": ok, "kind": "freeform",
            "generated": generated, "accepted": bool(ok and state == "passed"), "acceptanceStatus": state,
            "archetype": "FF", "page": spec.get("name"), "summary": summary,
            "createdAt": time.strftime("%Y-%m-%d %H:%M:%S")}
    _dump(os.path.join(rdir, "meta.json"), meta)
    studio_api.record(rid, rdir, "freeform", ok,
                      spec={"page": {"name": spec.get("name")}, "archetype": "FF"},
                      summary=summary, steps=steps)
    return ok, steps, summary


def _respond(rid, rdir, ok, steps, summary, extra=None):
    url = lambda p: "/out/%s/%s" % (rid, os.path.basename(p)) if os.path.exists(p) else None
    meta = _load(os.path.join(rdir, "meta.json")) or {}
    body = {"ok": ok, "run": rid, "source": "freeform", "kind": "freeform",
            "generated": meta.get("generated", False),
            "accepted": meta.get("accepted", False),
            "acceptanceStatus": studio_api.acceptance_state(summary),
            "steps": steps, "summary": summary,
            "workspace": os.path.basename(os.path.dirname(rdir)),
            "files": studio_store.artifacts(rid), "zip": "/api/runs/%s/zip" % rid,
            "page": url(os.path.join(rdir, "page.html")),
            "report": url(os.path.join(rdir, "report.html")),
            "spec": url(os.path.join(rdir, "freeform.spec.json")),
            "canSaveTemplate": bool(ok)}
    body.update(extra or {})
    return (200 if ok else 422), body


def generate(payload):
    """入口：{templateId, slots?, name?} 或 {spec}。"""
    tid = payload.get("templateId")
    overrides = payload.get("slots") or {}
    if tid:
        tdir = find_template(tid)
        if not tdir:
            return 404, {"error": "自由版式模板不存在：%s" % tid}
        spec = _load(os.path.join(tdir, "freeform.spec.json"))
        # 模板素材随行：源图 / skeleton.html
        rid, rdir, _wid = studio_store.new_run("ff", payload.get("workspace"))
        for fn in os.listdir(tdir):
            if fn.startswith("input.") or fn in ("skeleton.html",):
                shutil.copyfile(os.path.join(tdir, fn), os.path.join(rdir, fn))
    else:
        spec = payload.get("spec")
        if not isinstance(spec, dict):
            return 400, {"error": "需要 templateId 或 spec"}
        rid, rdir, _wid = studio_store.new_run("ff", payload.get("workspace"))
        src = (spec.get("source") or {}).get("file")
        if src and os.path.isfile(src):           # 手写 spec 带绝对路径素材时随行
            shutil.copyfile(src, os.path.join(rdir, os.path.basename(src)))
            spec["source"]["file"] = os.path.basename(src)

    if payload.get("name"):
        spec["name"] = str(payload["name"])[:40]
    errs, _w = ff_spec.validate(spec)
    if errs:
        return 400, {"error": "FreeformSpec 校验失败：" + "；".join(errs)}

    # 方案 C：语义自动识别 —— 用户没声明交互时，把「有把握」的自动填（from=auto），
    # 认不出的记入 semanticsTodos 交前端提示补声明。
    if not ((spec.get("semantics") or {}).get("interactions")):
        proposals, todos = ff_spec.auto_semantics(spec)
        if proposals["interactions"]:
            spec.setdefault("semantics", {})["interactions"] = proposals["interactions"]
            spec["semanticsTodos"] = todos
        elif todos:
            spec["semanticsTodos"] = todos

    replaced = {}
    if overrides:
        slots = spec.setdefault("slots", {})
        for k, v in overrides.items():
            if k not in slots:
                return 422, {"error": "「%s」不是本模板的插槽。可用：%s"
                             % (k, "、".join(slots.keys()))}
            replaced[slots[k]] = str(v)
            slots[k] = str(v)
            for r in spec.get("regions") or []:
                if r.get("slot") == k:
                    r["text"] = str(v)
        # 文案断言清单随替换更新
        acc = spec.setdefault("acceptance", {})
        acc["requiredTexts"] = [replaced.get(t, t) for t in (acc.get("requiredTexts")
                                 or ff_spec.required_texts(spec))]

    _dump(os.path.join(rdir, "freeform.spec.json"), spec)
    ok, steps, summary = _run_steps(rid, rdir, spec, replaced or None)
    return _respond(rid, rdir, ok, steps, summary,
                    {"parsed": {"templateKind": "freeform", "page": spec.get("name"),
                                "slots": overrides}})


def from_image(payload):
    """图片入场：{run}（转换失败的既有运行）或 {image: dataURI, labels}。"""
    labels = payload.get("labels") or {}
    rid = rdir = None
    img_path = None
    if payload.get("run"):
        if not SAFE_ID.match(payload["run"]):
            return 400, {"error": "run 不合法"}
        rdir, _ = studio_store.find_run(payload["run"])
        if not rdir:
            return 404, {"error": "运行不存在"}
        for fn in os.listdir(rdir):
            if fn.startswith("input."):
                img_path = os.path.join(rdir, fn)
        if not img_path:
            return 404, {"error": "该运行没有原始图片"}
        rid = payload["run"]
    else:
        m = re.match(r"^data:image/(png);base64,(.+)$", payload.get("image") or "", re.S)
        if not m:
            return 400, {"error": "image 需为 png 的 data URI（自由版式像素量测只支持 PNG）"}
        rid, rdir, _wid = studio_store.new_run("ff", payload.get("workspace"))
        img_path = os.path.join(rdir, "input.png")
        with open(img_path, "wb") as f:
            f.write(base64.b64decode(m.group(2)))
        if not labels:
            labels = {k: v for k, v in (("name", payload.get("name")),
                                        ("texts", payload.get("texts")),
                                        ("buttons", payload.get("buttons"))) if v}

    p_spec = os.path.join(rdir, "freeform.spec.json")
    step = {"step": "像素拆层 ff_image.py", "ok": True, "log": ""}
    try:
        spec = ff_image_build(img_path, labels, payload.get("name"))
        _dump(p_spec, spec)
        step["log"] = "画布 %dx%d · 区块 %d · slot %d" % (
            spec["canvas"]["w"], spec["canvas"]["h"], len(spec["regions"]), len(spec["slots"]))
    except SystemExit as e:
        step["ok"], step["log"] = False, str(e)
        studio_api.record(rid, rdir, "freeform", False, steps=[step])
        return 422, {"ok": False, "run": rid, "steps": [step]}
    ok, steps, summary = _run_steps(rid, rdir, spec)
    return _respond(rid, rdir, ok, [step] + steps, summary)


def ff_image_build(img_path, labels, name):
    import ff_image
    return ff_image.build_spec(img_path, labels or {}, name)


def from_html(payload):
    """HTML 入场：files[] → 净化 → FreeformSpec → 生成。"""
    files = payload.get("files") or []
    if not files:
        return 400, {"error": "没有收到文件"}
    rid, rdir, _wid = studio_store.new_run("ff", payload.get("workspace"))
    src = os.path.join(rdir, "upload")
    kept = 0
    for f in files[:200]:
        rel = (f.get("path") or "").replace("\\", "/")
        if not rel or rel.startswith("/") or ":" in rel.split("/")[0] or ".." in rel.split("/"):
            continue
        if os.path.splitext(rel)[1].lower() not in (".html", ".htm"):
            continue
        dst = os.path.join(src, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write((f.get("text") or "")[:2 * 1024 * 1024])
        kept += 1
    if not kept:
        return 400, {"error": "没有可解析的 .html 文件"}
    step = {"step": "HTML 净化 ff_html.py（%d 个文件）" % kept, "ok": True, "log": ""}
    try:
        spec, cleaned, notes = ff_html.build_spec(src, payload.get("name"))
        spec["source"]["file"] = "skeleton.html"
        with open(os.path.join(rdir, "skeleton.html"), "w", encoding="utf-8", newline="\n") as f:
            f.write(cleaned)
        _dump(os.path.join(rdir, "freeform.spec.json"), spec)
        step["log"] = "slot %d 个；净化：%s" % (len(spec["slots"]),
                                              "；".join(notes[:6]) or "原页干净")
    except SystemExit as e:
        step["ok"], step["log"] = False, str(e)
        studio_api.record(rid, rdir, "freeform", False, steps=[step])
        return 422, {"ok": False, "run": rid, "steps": [step]}
    ok, steps, summary = _run_steps(rid, rdir, spec)
    return _respond(rid, rdir, ok, [step] + steps, summary)


def parse_preview(payload):
    cmd = payload.get("command") or ""
    tpls = [{"id": t["id"], "name": t["name"],
             "spec": _load(os.path.join(FF_LIB, t["id"], "freeform.spec.json")) or {},
             "slotAliases": t.get("slotAliases")}
            for t in ff_templates()]
    import ff_parse
    return ff_parse.parse(cmd, tpls)


def parse_command_hook(cmd):
    """studio_api.parse_command 的委托钩子：指令命中自由版式模板名时返回解析结果，
    否则返回 None（studio_api 继续走 A1–A6）。"""
    tpls = [{"id": t["id"], "name": t["name"],
             "spec": _load(os.path.join(FF_LIB, t["id"], "freeform.spec.json")) or {},
             "slotAliases": t.get("slotAliases")}
            for t in ff_templates()]
    if not tpls:
        return None
    # 只在指令确实点名了某个自由版式模板时才介入，避免抢走 A1–A6 的指令
    if not any(t["name"] and t["name"] in (cmd or "") for t in tpls):
        return None
    import ff_parse
    code, body = ff_parse.parse(cmd, tpls)
    if code != 200:
        # 命中模板名但解析失败（如未知 slot）→ 也返回错误，不退回 A1–A6 误判
        return code, body
    return 200, {"kind": "freeform", "spec": body["spec"],
                 "parsed": body["parsed"], "notes": body.get("notes", [])}


# --------------------------------------------------------------------------
# 服务路由
# --------------------------------------------------------------------------

POST = {
    "/api/ff/generate": generate,
    "/api/ff/image": from_image,
    "/api/ff/convert": from_image,          # 语义别名：失败运行转自由版式
    "/api/ff/html": from_html,
    "/api/ff/parse": parse_preview,
    "/api/ff/templates/save": lambda p: save_template(p.get("run"), p.get("name")),
    "/api/ff/templates/delete": lambda p: delete_template(p.get("id")),
}


def get_route(path):
    """GET /api/ff/* 的处理；返回 None 表示不认识。"""
    if path == "/api/ff/templates":
        return 200, {"templates": ff_templates()}
    return None


def get_file(parts):
    """GET /fflib/<id>/<fn>；返回 (code, 绝对路径) 或 None。"""
    if len(parts) == 3 and parts[0] == "fflib":
        tid, fn = parts[1], parts[2]
        if SAFE_ID.match(tid) and SAFE_ID.match(fn):
            p = os.path.join(FF_LIB, tid, fn)
            if os.path.isfile(p):
                return 200, p
    return None
