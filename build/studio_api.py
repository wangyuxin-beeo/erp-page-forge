# -*- coding: utf-8 -*-
"""Forge Studio 统一系统的业务层：模板库 / 指令生成 / HTML 入场 / 模板回流 / 生成记录。

一个模板库，三个入口，同一条生成链路：
  内置模板 A1–A6 ┐
  自定义模板      ├→ PageSpec → forge.py → 四类断言 → 报告
    ← 设计图入场（vision.py）
    ← HTML 入场（reverse.py）
  自定义模板生成后可「存入模板库」，之后与内置模板一样用指令复用。
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SPEC_DIR = os.path.join(ROOT, "skill", "spec")
DATA_ROOT = os.path.abspath(os.path.expanduser(os.environ["FORGE_DATA_DIR"])) if os.environ.get("FORGE_DATA_DIR", "").strip() else HERE
OUT = os.path.join(DATA_ROOT, "studio-out")
LIB = os.path.join(DATA_ROOT, "studio-library")
TPL_DIR = os.path.join(ROOT, "templates")
PY = sys.executable
SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")

# 内置模板：A1–A6 与 templates/ 下 6 个示例页一一对应（示例页即「这个原型长什么样」）
BUILTIN = [
    {"id": "A1", "file": "01-sales-order-list.html", "entity": "salesOrder", "example": "生成销售订单列表页"},
    {"id": "A2", "file": "02-purchase-request-form.html", "entity": "purchaseRequest", "example": "生成采购申请单表单"},
    {"id": "A3", "file": "03-material-master-detail.html", "entity": "material", "example": "生成物料主数据详情页"},
    {"id": "A4", "file": "04-inventory-dashboard.html", "entity": "inventory", "example": "生成库存看板"},
    {"id": "A5", "file": "05-approval-inbox.html", "entity": "approvalTask", "example": "生成审批待办工作台"},
    {"id": "A6", "file": "06-customer-statement.html", "entity": "statement", "example": "生成客户对账单"},
]
DEFAULT_ENTITY = {b["id"]: b["entity"] for b in BUILTIN}

# 页面类型词（第一优先级）。多个命中取「最靠后」的那个 —— 中文里页面类型通常是后缀：「客户对账列表」→ 列表
PAGE_WORDS = [
    ("A1", ["列表页", "列表", "管理页", "查询页"]),
    ("A2", ["表单页", "表单", "新建页", "录入页", "编辑页", "申请单"]),
    ("A3", ["详情页", "详情", "360"]),
    ("A4", ["看板", "仪表盘", "驾驶舱", "大屏", "分析页"]),
    ("A5", ["审批工作台", "工作台", "待办", "审批中心", "审批"]),
    ("A6", ["对账单", "对账", "台账", "余额表"]),
]
PAGE_SUFFIX = {"A1": "管理", "A2": "", "A3": "详情", "A4": "看板", "A5": "", "A6": ""}
FILLER = r"帮我|请|麻烦|生成|做|创建|新建一个|来|写|一个|一张|一份|个|的|页面|页$"


def _load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _dump(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(o, f, ensure_ascii=False, indent=2)


def _decode(raw):
    """子进程输出解码。

    Python 在中文 Windows 上把 stdout 按 cp936 写管道，直接按 utf-8+replace 读会把中文
    砸成一串 U+FFFD（生成日志里出现「������д��:」就是这个原因，真正的原因句全丢了）。
    所以：先按 utf-8 严格试，失败退回 gbk，最后才允许 replace。
    """
    if not raw:
        return ""
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


CHILD_ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")


def run_tool(args, timeout=300):
    """跑一个 build/ 下的脚本，返回 (returncode, 合并输出)。

    子进程强制 UTF-8 输出，避免中文在两个进程间被编码来回翻译。
    """
    p = subprocess.run([PY] + args, cwd=ROOT, capture_output=True, timeout=timeout, env=CHILD_ENV)
    return p.returncode, _decode(p.stdout) + _decode(p.stderr)


def _run(args, timeout=300):   # 兼容原有调用名
    return run_tool(args, timeout=timeout)


sys.path.insert(0, HERE)
import studio_store  # noqa: E402  工作区 / 归档 / 运行日志


def new_run(source, workspace=None):
    """在工作区下建运行目录。返回 (run_id, 目录)；工作区 id 记在目录的上一级。"""
    rid, rdir, _ = studio_store.new_run("ax" if source == "axure" else "run", workspace)
    return rid, rdir


def record(rid, rdir, source, ok, spec=None, summary=None, steps=None, error=None):
    """每次运行（成功或失败）都写一条日志，供「历史记录」回溯。"""
    spec = spec or {}
    bad = next((s for s in (steps or []) if not s.get("ok")), None)
    msg = error
    if not msg and bad:
        lines = (bad.get("log") or "").strip().splitlines()
        msg = lines[-1] if lines else bad.get("step")
    return studio_store.log_run({
        "run": rid, "workspace": os.path.basename(os.path.dirname(rdir)), "source": source,
        "ok": bool(ok), "page": (spec.get("page") or {}).get("name"),
        "archetype": spec.get("archetype"), "summary": summary,
        "accepted": bool(ok and acceptance_state(summary) == "passed"),
        "acceptanceStatus": acceptance_state(summary),
        "failedStep": bad.get("step") if bad else None, "error": (msg or None) and str(msg)[:300],
    })


# --------------------------------------------------------------------------
# 模板库
# --------------------------------------------------------------------------

def list_templates():
    arch = _load(os.path.join(SPEC_DIR, "archetypes.json"))["archetypes"]
    recipes = _load(os.path.join(SPEC_DIR, "recipes.json"))["entities"]
    out = []
    for b in BUILTIN:
        a = arch[b["id"]]
        out.append({
            "id": b["id"], "kind": "builtin", "archetype": b["id"], "name": a["name"],
            "regions": a["regions"], "example": b["example"],
            "entity": recipes[b["entity"]]["name"],
            "preview": "/tpl/" + b["file"],
            "checks": sum(len(a.get(k, [])) for k in ("static", "render", "interaction", "data")),
        })
    if os.path.isdir(LIB):
        for tid in sorted(os.listdir(LIB), reverse=True):
            mp = os.path.join(LIB, tid, "meta.json")
            if SAFE_ID.match(tid) and os.path.isfile(mp):
                m = _load(mp)
                m.update({"id": tid, "kind": "custom", "preview": "/lib/%s/page.html" % tid,
                          "thumb": ("/lib/%s/%s" % (tid, m["thumb"])) if m.get("thumb") else None})
                out.append(m)
    # 自由版式扩展钩子：扩展不存在时返回值与原来完全一致
    try:
        from ext.freeform import routes as _ff
        out += _ff.ff_templates()
    except Exception:
        pass
    return out


def save_template(run_id, name):
    """把一次生成结果存成自定义模板：PageSpec 是模板本体，page.html 是预览，原始素材留档。"""
    if not SAFE_ID.match(run_id or ""):
        return 400, {"error": "run 不合法"}
    rdir, _ = studio_store.find_run(run_id)
    if not rdir:
        return 404, {"error": "运行不存在"}
    sp = os.path.join(rdir, "pagespec.json")
    if not os.path.isfile(sp):
        return 404, {"error": "该次生成没有 PageSpec，无法存为模板"}
    candidate = _load(sp)
    meta_path = os.path.join(rdir, "meta.json")
    if os.path.isfile(meta_path) and not _load(meta_path).get("ok", False):
        return 422, {"error": "生成或验收存在硬性失败，不能保存模板"}
    if _is_screenshot_spec(candidate):
        acc_path = os.path.join(rdir, "acceptance.json")
        if not os.path.isfile(acc_path) or _load(acc_path).get("summary", {}).get("verdict") != "PASS":
            return 422, {"error": "截图结构验收未通过，不能保存模板"}
    name = (name or "").strip()[:40] or "未命名模板"
    tid = "tpl-" + run_id.split("-", 1)[1]   # run-/ax- 前缀长度不同，不能用固定切片
    tdir = os.path.join(LIB, tid)
    os.makedirs(tdir, exist_ok=True)
    spec = _load(sp)
    is_shot = _is_screenshot_spec(spec)
    for k in [k for k in spec if k.startswith("_") and k not in ("_source",)]:
        spec.pop(k, None)                     # 去掉生成期派生字段，模板只留契约
    _dump(os.path.join(tdir, "pagespec.json"), spec)
    if is_shot:
        _dump(os.path.join(tdir, "screenshot.spec.json"), spec)
    for fn in ("page.html", "report.html"):
        if os.path.isfile(os.path.join(rdir, fn)):
            shutil.copyfile(os.path.join(rdir, fn), os.path.join(tdir, fn))
    thumb = None
    for fn in os.listdir(rdir):
        if fn.startswith("input."):
            shutil.copyfile(os.path.join(rdir, fn), os.path.join(tdir, fn))
            thumb = fn
    run_meta = _load(os.path.join(rdir, "meta.json")) if os.path.isfile(os.path.join(rdir, "meta.json")) else {}
    arch = _load(os.path.join(SPEC_DIR, "archetypes.json"))["archetypes"]
    a = "screenshot" if is_shot else spec.get("archetype", "A1")
    if is_shot:
        arch_name = "截图·原图结构还原"
        cols = []
        for e in (spec.get("elements") or []):
            if e.get("type") == "table":
                cols += [(c.get("text") if isinstance(c, dict) else c)
                         for c in (e.get("table", {}).get("columns") or [])][:10]
        meta = {"name": name, "archetype": a, "archetypeName": arch_name,
                "source": "screenshot", "fromRun": run_id, "thumb": thumb,
                "columns": cols[:10],
                "example": "用「%s」按原图结构还原 XX 页面" % name,
                "createdAt": time.strftime("%Y-%m-%d %H:%M")}
    else:
        meta = {"name": name, "archetype": a, "archetypeName": arch.get(a, {}).get("name", a),
                "source": run_meta.get("source", "unknown"), "fromRun": run_id, "thumb": thumb,
                "columns": [c.get("field") for c in spec.get("columns", [])][:10],
                "example": "用「%s」生成 XX%s" % (name, PAGE_SUFFIX.get(a, "") or "页面"),
                "createdAt": time.strftime("%Y-%m-%d %H:%M")}
    _dump(os.path.join(tdir, "meta.json"), meta)
    return 200, {"ok": True, "id": tid, "name": name}


def delete_template(tid):
    if not (SAFE_ID.match(tid or "") and tid.startswith("tpl-")):
        return 400, {"error": "只能删除自定义模板"}
    tdir = os.path.join(LIB, tid)
    if not os.path.isdir(tdir):
        return 404, {"error": "模板不存在"}
    shutil.rmtree(tdir)                       # 只作用于 build/studio-library/<tpl-*> 单个目录
    return 200, {"ok": True}


# --------------------------------------------------------------------------
# 指令 → PageSpec（确定性规则，不调模型；解析结果全部回显给用户核对）
# --------------------------------------------------------------------------

def _last_hit(text, words):
    best = -1
    for w in words:
        i = text.rfind(w)
        if i >= 0:
            best = max(best, i + len(w))
    return best


def parse_command(cmd, template_id=None):
    cmd = (cmd or "").strip()
    if not cmd and not template_id:
        return 400, {"error": "请输入指令，例如：生成销售订单列表页"}
    # 自由版式扩展钩子：指令命中自由版式模板名时整体委托；扩展不存在则原样走 A1–A6
    try:
        from ext.freeform import routes as _ff
        r = _ff.parse_command_hook(cmd)
        if r is not None:
            return r
    except Exception:
        pass
    recipes = _load(os.path.join(SPEC_DIR, "recipes.json"))["entities"]
    notes = []

    # 1) 自定义模板：显式选中，或指令里出现模板名
    custom = [t for t in list_templates() if t["kind"] == "custom"]
    tpl = next((t for t in custom if t["id"] == template_id), None)
    if not tpl:
        tpl = next((t for t in sorted(custom, key=lambda t: -len(t["name"])) if t["name"] and t["name"] in cmd), None)
    if tpl:
        spec = _load(os.path.join(LIB, tpl["id"], "pagespec.json"))
        is_shot = _is_screenshot_spec(spec)
        rest = re.sub(FILLER, "", cmd.replace(tpl["name"], "")).strip("「」\"' ，,。:：用按照套")
        if rest:
            if is_shot:
                spec.setdefault("page", {})["title"] = rest
                notes.append("套用截图模板「%s」，标题改为「%s」（按原图结构还原，不套 A1–A6）" % (tpl["name"], rest))
            else:
                spec.setdefault("page", {})["name"] = rest
                spec.setdefault("entity", {})["name"] = re.sub(r"(列表|管理|详情|看板|表单)$", "", rest) or rest
                if spec["page"].get("breadcrumb"):
                    spec["page"]["breadcrumb"][-1] = rest
                notes.append("套用自定义模板「%s」，页面名改为「%s」（结构、列、令牌保持模板原样）" % (tpl["name"], rest))
        else:
            notes.append("原样套用自定义模板「%s」" % tpl["name"])
        parsed = {"template": tpl["name"], "templateKind": "custom",
                  "archetype": ("screenshot" if is_shot else spec.get("archetype")),
                  "entity": spec.get("entity", {}).get("name") if not is_shot else None,
                  "page": (spec.get("page") or {}).get("title" if is_shot else "name"),
                  "kind": ("screenshot" if is_shot else "pagespec")}
        return 200, {"spec": spec, "parsed": parsed, "notes": notes,
                     "screenshot": is_shot}

    # 2) 内置模板：页面类型（A1–A6）+ 业务实体（配方）
    arch, pos = None, -1
    if template_id in DEFAULT_ENTITY:
        arch = template_id
    explicit_arch = re.search(r"(?<![A-Za-z0-9])(A[1-6])(?![A-Za-z0-9])", cmd, re.I)
    if explicit_arch and not arch:
        arch = explicit_arch.group(1).upper()
    for code, words in PAGE_WORDS:
        p = _last_hit(cmd, words)
        if p > pos and not (template_id in DEFAULT_ENTITY):
            arch, pos = code, p
    ent_key, ent_len = None, 0
    for k, r in recipes.items():
        for a in [r["name"]] + list(r.get("aliases", [])):
            if a and a.lower() in cmd.lower() and len(a) > ent_len:
                ent_key, ent_len = k, len(a)
    if not arch and ent_key:
        arch = recipes[ent_key].get("archetypeHint", "A1")
        notes.append("未说明页面类型，按「%s」的常用类型取 %s" % (recipes[ent_key]["name"], arch))
    if not arch:
        return 422, {"error": "没认出页面类型。请带上：列表 / 表单 / 详情 / 看板 / 审批 / 对账 之一，或在左侧选一个模板"}

    fields = []
    m = re.search(r"字段[:：]\s*(.+)$", cmd)
    if m:
        fields = [{"name": n.strip(), "type": _guess_type(n.strip())}
                  for n in re.split(r"[、,，/\s]+", m.group(1)) if n.strip()]
        cmd_main = cmd[:m.start()]
    else:
        cmd_main = cmd

    if ent_key:
        r = recipes[ent_key]
        ent_name, aliases, code_rule = r["name"], r.get("aliases", []), r.get("codeRule", "")
    else:
        # 配方里没有的实体：取去掉页面类型词与口语后的剩余文字
        words = sum([w for _, w in PAGE_WORDS], [])
        rest = re.sub(r"(?<![A-Za-z0-9])A[1-6](?![A-Za-z0-9])|模板|管理列表页?|列表模板", "", cmd_main, flags=re.I)
        for w in sorted(words, key=len, reverse=True):
            rest = rest.replace(w, "")
        ent_name = re.sub(FILLER, "", rest).strip(" ，,。") or None
        aliases, code_rule = [], ""
        if not ent_name:
            ent_key = DEFAULT_ENTITY[arch]
            r = recipes[ent_key]
            ent_name, aliases, code_rule = r["name"], r.get("aliases", []), r.get("codeRule", "")
            notes.append("未说明业务对象，用 %s 的示例对象「%s」" % (arch, ent_name))
        else:
            notes.append("「%s」不在配方库：字段走通用骨架%s，请核对" % (ent_name, "＋你给的字段" if fields else ""))

    if arch == "A1" and not ent_key and not fields:
        fields = [{"name": "编号", "type": "text"}, {"name": "名称", "type": "text"},
                  {"name": "分类", "type": "text"}, {"name": "负责人", "type": "text"},
                  {"name": "数量", "type": "number"}, {"name": "金额", "type": "money"},
                  {"name": "创建日期", "type": "date"}, {"name": "备注", "type": "text"}]
        notes.append("未指定字段：使用通用示例字段，可在指令中通过「字段：…」替换")

    page_name = ent_name + PAGE_SUFFIX[arch] if not ent_name.endswith(PAGE_SUFFIX[arch] or "\0") else ent_name
    spec = {
        "specVersion": "1.0",
        "page": {"name": page_name, "module": ent_name, "breadcrumb": [ent_name, page_name]},
        "archetype": arch,
        "entity": {"name": ent_name, "recipeKey": ent_key, "codeRule": code_rule, "aliases": aliases},
        "fields": fields,
        "stack": {"target": "standalone"},
        "_source": {"kind": "command", "command": cmd},
    }
    if not ent_key and arch == "A1":
        # 配方外实体没有 defaultActions / 状态机 → 操作列会整列为空（踩过：A1.no-empty-column 失败）
        spec["actions"] = [{"name": "查看", "scope": "row"}, {"name": "编辑", "scope": "row"},
                           {"name": "导出", "scope": "header"}, {"name": "新建", "scope": "header", "primary": True}]
        notes.append("配方外实体：补默认动作 查看/编辑/导出/新建")
    arch_name = _load(os.path.join(SPEC_DIR, "archetypes.json"))["archetypes"][arch]["name"]
    return 200, {"spec": spec, "parsed": {"template": "%s %s" % (arch, arch_name), "templateKind": "builtin",
                                          "archetype": arch, "entity": ent_name, "recipe": ent_key,
                                          "page": page_name, "fields": [f["name"] for f in fields]},
                 "notes": notes}


def _guess_type(name):
    sys.path.insert(0, HERE)
    from reverse import guess_type  # 与 HTML 入场共用同一套字段类型规则
    return guess_type(name)


# --------------------------------------------------------------------------
# 生成链路：PageSpec → forge.py → 四类断言 → 报告（三个入口最后都走这里）
# --------------------------------------------------------------------------

def acceptance_state(summary):
    """Do not confuse successful rendering with completed acceptance."""
    if not isinstance(summary, dict):
        return "missing"
    if summary.get("hardFailed", 0) or summary.get("verdict") in ("FAIL", "不通过"):
        return "failed"
    if summary.get("unsupported", 0) or summary.get("skippedCategories", 0) or summary.get("undeclaredCategories", 0):
        return "needs_review"
    return "passed"


def run_pipeline(spec, theme="default", source="command", extra_steps=None, assets=None, brand=None, run=None,
                 workspace=None):
    if theme not in ("default", "premium"):
        return 400, {"error": "theme 只支持 default / premium"}
    if brand and not re.match(r"^#[0-9A-Fa-f]{6}$", brand):
        return 400, {"error": "brand 需为 #RRGGBB"}

    rid, rdir = run or new_run(source, workspace)
    for name, blob in (assets or {}).items():          # 原始素材留档（设计图 / 上传的 HTML）
        with open(os.path.join(rdir, name), "wb") as f:
            f.write(blob)
    p_spec = os.path.join(rdir, "pagespec.json")
    p_html = os.path.join(rdir, "page.html")
    p_acc = os.path.join(rdir, "acceptance.json")
    p_rep = os.path.join(rdir, "report.html")
    # Persist the same completed contract the renderer uses, so acceptance can
    # validate recipe fields and state enumerations rather than an empty shell.
    import forge
    spec = forge.complete_spec(spec, forge.load_recipes())
    spec.pop("_recipe", None)
    acts = spec.get("actions") or []
    if spec.get("archetype") == "A1" and acts and not any(a.get("scope", "row") == "row" for a in acts):
        # 反向工程 / 截图常只抽到头部与批量动作 → 「操作」列整列为空（A1.no-empty-column 硬失败）
        spec["actions"] = acts + [{"name": "查看", "scope": "row"}, {"name": "编辑", "scope": "row"}]
    if brand:
        tok = spec.setdefault("tokens", {"mode": "inherit-visual", "values": {}})
        tok.setdefault("values", {}).setdefault("color", {})["brand"] = {"value": brand.upper(), "source": "studio-pick"}
    _dump(p_spec, spec)

    steps = list(extra_steps or [])

    def step(name, args, must=True):
        code, log = _run(args)
        steps.append({"step": name, "ok": code == 0, "log": log[-4000:]})
        return code == 0 or not must

    ok = step("确定性生成 forge.py", ["build/forge.py", "--spec", p_spec, "--theme", theme, "--out", p_html])
    summary = None
    generated = ok
    if generated:
        verified = step("四类断言 verify_acceptance.py",
             ["build/verify_acceptance.py", "--spec", p_spec, "--artifact", p_html, "--url", p_html, "--out", p_acc])
        reported = False
        if os.path.exists(p_acc):
            summary = _load(p_acc).get("summary")
            reported = step("验收报告 build_report.py", ["build/build_report.py", "--report", p_acc, "--out", p_rep])
        ok = bool(verified and reported and acceptance_state(summary) not in ("failed", "missing"))
    state = acceptance_state(summary)

    _dump(os.path.join(rdir, "meta.json"), {
        "run": rid, "source": source, "theme": theme, "archetype": spec.get("archetype"),
        "page": spec.get("page", {}).get("name"), "summary": summary, "ok": ok,
        "generated": generated, "accepted": bool(ok and state == "passed"), "acceptanceStatus": state,
        "createdAt": time.strftime("%Y-%m-%d %H:%M:%S")})
    record(rid, rdir, source, ok, spec=spec, summary=summary, steps=steps)

    url = lambda p: "/out/%s/%s" % (rid, os.path.basename(p)) if os.path.exists(p) else None
    return (200 if ok else 422), {
        "ok": ok, "generated": generated, "accepted": bool(ok and state == "passed"), "acceptanceStatus": state,
        "run": rid, "source": source, "theme": theme, "steps": steps, "summary": summary,
        "workspace": os.path.basename(os.path.dirname(rdir)),
        "files": studio_store.artifacts(rid), "zip": "/api/runs/%s/zip" % rid,
        "archetype": spec.get("archetype"), "pageName": spec.get("page", {}).get("name"),
        "canSaveTemplate": bool(ok and source in ("image", "html", "axure")),
        "page": url(p_html), "report": url(p_rep), "spec": url(p_spec),
    }


def forge_from_command(payload):
    code, r = parse_command(payload.get("command"), payload.get("templateId"))
    if code != 200:
        return code, r
    # 自由版式扩展钩子：ff_parse 返回 kind=freeform 的 spec → 走自由版式生成管线
    if r.get("kind") == "freeform":
        try:
            from ext.freeform import routes as _ff
            c2, body = _ff.generate({"templateId": r["parsed"].get("templateId"),
                                     "spec": r["spec"], "slots": r["parsed"].get("slots", {}),
                                     "name": r["spec"].get("name"), "workspace": payload.get("workspace")})
            body["parsed"] = r["parsed"]
            body["notes"] = r["notes"]
            return c2, body
        except Exception as e:
            return 500, {"error": "自由版式生成失败：%s" % e}
    # 截图独立模板：走截图渲染链路（不调 forge / 不套 A1–A6）
    if r.get("screenshot"):
        c2, body = run_screenshot_pipeline(r["spec"], source="template-custom",
                                           workspace=payload.get("workspace"))
        body["parsed"] = r["parsed"]
        body["notes"] = r["notes"]
        return c2, body
    step = {"step": "指令解析 → %s（%s）" % (r["parsed"]["template"], r["parsed"]["page"]), "ok": True, "log": ""}
    c2, body = run_pipeline(r["spec"], payload.get("theme") or "default",
                            source="template-custom" if r["parsed"]["templateKind"] == "custom" else "template",
                            extra_steps=[step], brand=payload.get("brand"), workspace=payload.get("workspace"))
    body["parsed"] = r["parsed"]
    body["notes"] = r["notes"]
    return c2, body


def forge_from_html(payload):
    """HTML 入场：上传的页面/文件夹 → reverse.py 抽 PageSpec → 同一条链路。"""
    files = payload.get("files") or []
    if not files:
        return 400, {"error": "没有收到文件"}
    try:
        rid, rdir = new_run("html", payload.get("workspace"))
    except ValueError as e:
        return 400, {"error": str(e)}
    src = os.path.join(rdir, "upload")
    kept = 0
    for f in files[:400]:
        rel = (f.get("path") or "").replace("\\", "/")
        if not rel or rel.startswith("/") or ":" in rel.split("/")[0] or ".." in rel.split("/"):
            continue
        if os.path.splitext(rel)[1].lower() not in (".html", ".htm", ".jsx", ".tsx", ".vue", ".js", ".ts", ".css"):
            continue
        dst = os.path.join(src, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write((f.get("text") or "")[:2 * 1024 * 1024])
        kept += 1
    if not kept:
        return 400, {"error": "没有可解析的文件（支持 .html/.vue/.jsx/.tsx/.js/.ts/.css）"}

    p_spec = os.path.join(rdir, "reversed.pagespec.json")
    args = ["build/reverse.py", "--artifact", src, "--out", p_spec]
    if (payload.get("rename") or "").strip():
        args += ["--rename", payload["rename"].strip()[:40]]
    code, log = _run(args)
    steps = [{"step": "反向工程 reverse.py（%d 个文件）" % kept, "ok": code == 0, "log": log[-4000:]}]
    if code != 0:
        record(rid, rdir, "html", False, steps=steps)
        return 422, {"ok": False, "run": rid, "source": "html", "steps": steps}
    spec = _load(p_spec)
    if payload.get("archetype") in DEFAULT_ENTITY:
        spec["archetype"] = payload["archetype"]
        steps.append({"step": "原型改判为 %s（人工指定）" % payload["archetype"], "ok": True, "log": ""})
    return run_pipeline(spec, payload.get("theme") or "default", source="html",
                        extra_steps=steps, brand=payload.get("brand"), run=(rid, rdir))


def forge_from_image(payload):
    """设计图入场：图片 + extract → vision.py 出 PageSpec → 同一条链路。"""
    import base64
    img = payload.get("image") or ""
    m = re.match(r"^data:image/(png|jpeg|jpg|webp);base64,(.+)$", img, re.S)
    if not m:
        return 400, {"error": "image 需为 png/jpeg/webp 的 data URI"}
    extract = payload.get("extract")
    if not isinstance(extract, dict):
        return 400, {"error": "extract 需为对象"}
    try:
        rid, rdir = new_run("image", payload.get("workspace"))
    except ValueError as e:
        return 400, {"error": str(e)}
    ext = "jpg" if m.group(1) in ("jpeg", "jpg") else m.group(1)
    p_img = os.path.join(rdir, "input." + ext)
    with open(p_img, "wb") as f:
        f.write(base64.b64decode(m.group(2)))
    p_ex, p_spec = os.path.join(rdir, "extract.json"), os.path.join(rdir, "vision.pagespec.json")
    _dump(p_ex, extract)
    code, log = _run(["build/vision.py", "--image", p_img, "--extract", p_ex, "--out", p_spec])
    steps = [{"step": "识别校验 vision.py", "ok": code == 0, "log": log[-4000:]}]
    if code != 0:
        record(rid, rdir, "image", False, steps=steps)
        return 422, {"ok": False, "run": rid, "source": "image", "steps": steps}
    return run_pipeline(_load(p_spec), payload.get("theme") or "default", source="image",
                        extra_steps=steps, brand=payload.get("brand"), run=(rid, rdir))


def _is_screenshot_spec(spec):
    """判断一份 spec 是不是「截图独立结构」（与 A1–A6 PageSpec 区分）。"""
    return isinstance(spec, dict) and (
        spec.get("kind") == "screenshot"
        or (spec.get("_source") or {}).get("kind") == "screenshot")


def run_screenshot_pipeline(spec, source="screenshot", run=None, workspace=None,
                            extra_steps=None):
    """截图独立还原链路：screenshot.spec.json → screenshot_render.py → 验收 → 报告。

    与 run_pipeline（A1–A6 + forge.py + recipes）完全隔离：不调 forge、不套原型、
    不补配方、不 premium。旧 A1–A6 extract 在此被明确拒绝（见 forge_from_screenshot）。
    """
    if not _is_screenshot_spec(spec):
        return 400, {"error": "截图独立入口只接受 kind:'screenshot' 的结构化识别结果；"
                              "旧 extract（含 archetype / fields / columns / regions 旧角色）不被接受，"
                              "请改用截图独立识别（/api/ai/analyze?mode=screenshot）重新生成"}
    rid, rdir = run or new_run("image", workspace)
    p_spec = os.path.join(rdir, "pagespec.json")     # 与内置保存/复用同名字段，便于模板回流
    p_ss = os.path.join(rdir, "screenshot.spec.json")  # 独立产物名
    p_html = os.path.join(rdir, "page.html")
    p_acc = os.path.join(rdir, "acceptance.json")
    p_rep = os.path.join(rdir, "report.html")
    _dump(p_spec, spec)
    _dump(p_ss, spec)
    steps = list(extra_steps or [])

    def step(name, args, must=True):
        code, log = _run(args)
        steps.append({"step": name, "ok": code == 0, "log": log[-4000:]})
        return code == 0 or not must

    ok = step("截图确定性渲染 screenshot_render.py",
              ["build/screenshot_render.py", "--spec", p_spec, "--out", p_html])
    summary = None
    generated = ok
    if generated:
        ok = step("截图验收 verify_screenshot.py",
             ["build/verify_screenshot.py", "--spec", p_spec, "--artifact", p_html,
              "--out", p_acc, "--report", p_rep])
        if os.path.exists(p_acc):
            summary = _load(p_acc).get("summary")

    state = acceptance_state(summary)
    ok = bool(ok and os.path.isfile(p_rep) and state not in ("failed", "missing"))
    _dump(os.path.join(rdir, "meta.json"), {
        "run": rid, "source": "screenshot", "archetype": "screenshot",
        "generated": generated, "accepted": bool(ok and state == "passed"), "acceptanceStatus": state,
        "page": (spec.get("page") or {}).get("title"), "summary": summary, "ok": ok,
        "createdAt": time.strftime("%Y-%m-%d %H:%M:%S")})
    record(rid, rdir, "screenshot", ok, spec=spec, summary=summary, steps=steps)

    url = lambda p: "/out/%s/%s" % (rid, os.path.basename(p)) if os.path.exists(p) else None
    return (200 if ok else 422), {
        "ok": ok, "generated": generated, "accepted": bool(ok and state == "passed"), "acceptanceStatus": state,
        "run": rid, "source": "screenshot", "theme": "default",
        "steps": steps, "summary": summary, "workspace": os.path.basename(os.path.dirname(rdir)),
        "files": studio_store.artifacts(rid), "zip": "/api/runs/%s/zip" % rid,
        "archetype": "screenshot", "pageName": (spec.get("page") or {}).get("title"),
        "canSaveTemplate": bool(ok),
        "page": url(p_html), "report": url(p_rep),
        "spec": url(p_spec), "screenshotSpec": url(p_ss),
    }


def forge_from_screenshot(payload):
    """截图独立还原入口：同一张原图 + 截图结构化识别结果 → 独立链路。

    旧 A1–A6 extract 明确拒绝并提示重新用截图独立识别，不静默兜底。
    """
    import base64
    img = payload.get("image") or ""
    m = re.match(r"^data:image/(png|jpeg|jpg|webp);base64,(.+)$", img, re.S)
    if not m:
        return 400, {"error": "image 需为 png/jpeg/webp 的 data URI"}
    spec = payload.get("spec")
    if not isinstance(spec, dict):
        return 400, {"error": "spec 需为截图结构化识别结果（JSON）"}
    if not _is_screenshot_spec(spec):
        return 400, {"error": "截图独立入口只接受 kind:'screenshot' 的结构化识别结果；"
                              "旧 extract（含 archetype / fields / columns）不被接受，"
                              "请改用截图独立识别（/api/ai/analyze?mode=screenshot）重新生成"}
    try:
        rid, rdir = new_run("image", payload.get("workspace"))
    except ValueError as e:
        return 400, {"error": str(e)}
    ext = "jpg" if m.group(1) in ("jpeg", "jpg") else m.group(1)
    with open(os.path.join(rdir, "input." + ext), "wb") as f:
        f.write(base64.b64decode(m.group(2)))
    return run_screenshot_pipeline(spec, source="screenshot",
                                   run=(rid, rdir), workspace=payload.get("workspace"))


def list_runs(limit=30, workspace=None, status=None, q=None):
    """运行历史（含失败记录），数据来自 runs.jsonl + 老运行的 meta.json。"""
    return studio_store.list_log(workspace=workspace, status=status, q=q, limit=limit)
