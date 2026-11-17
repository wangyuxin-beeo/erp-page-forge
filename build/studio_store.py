# -*- coding: utf-8 -*-
"""Forge Studio 的产物仓库：工作区 / 运行归档 / 打包下载 / 运行日志。

目录布局（全部在 build/studio-out 之下，不碰其它地方）：
    studio-out/
      workspaces.json          工作区注册表
      runs.jsonl               运行日志（追加写，一行一次运行）
      <工作区 id>/<运行 id>/    一次运行的全部产物
      <运行 id>/               旧版扁平布局，仍可读、可下载

run id 全局唯一（时间戳），所以 find_run 能在任意工作区里定位它。
"""

import io
import json
import os
import re
import shutil
import time
import zipfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
# Standalone keeps its historical layout; packaged engines set a writable root.
DATA_ROOT = os.path.abspath(os.path.expanduser(os.environ["FORGE_DATA_DIR"])) if os.environ.get("FORGE_DATA_DIR", "").strip() else HERE
OUT = os.path.join(DATA_ROOT, "studio-out")
WS_FILE = os.path.join(OUT, "workspaces.json")
LOG_FILE = os.path.join(OUT, "runs.jsonl")
SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
DEFAULT_WS = "default"
RESERVED = {"workspaces.json", "runs.jsonl"}

# 产物文件的人话名字 + 是否算「成功文件」（可直接交付的东西）
KNOWN = [
    ("page.html", "生成页面（单文件，可直接打开）", True),
    ("report.html", "验收报告", True),
    ("pagespec.json", "页面结构契约", True),
    ("page-data.json", "参考网页结构、样式与素材", True),
    ("theme.json", "参考网页设计主题", True),
    ("prototype.json", "原型页面与交互关系", True),
    ("project-context.json", "项目主题与技术栈上下文", True),
    ("project-code.zip", "按项目主题生成的前端代码包", True),
    ("screenshot.png", "参考网页截图", True),
    ("reference.json", "网页还原来源", False),
    ("freeform.spec.json", "自由版式结构契约", True),
    ("skeleton.html", "自由版式 HTML 骨架", True),
    ("screenshot.spec.json", "独立截图模板结构（可再生成）", True),
    ("acceptance.json", "断言明细", False),
    ("vision.pagespec.json", "截图解析出的 PageSpec", False),
    ("reversed.pagespec.json", "HTML 反向工程出的 PageSpec", False),
    ("extract.json", "识别结果 extract", False),
    ("meta.json", "运行元数据", False),
]
LABELS = {k: v for k, v, _ in KNOWN}
DELIVERABLE = {k for k, _, d in KNOWN if d}


def _load(path, fallback):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return fallback


def _dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# 工作区
# --------------------------------------------------------------------------

def _registry():
    reg = _load(WS_FILE, None)
    if not isinstance(reg, dict) or not isinstance(reg.get("workspaces"), list):
        reg = {"workspaces": [{"id": DEFAULT_WS, "name": "默认工作区",
                               "createdAt": time.strftime("%Y-%m-%d %H:%M:%S")}]}
        _dump(WS_FILE, reg)
    return reg


def list_workspaces():
    """返回工作区列表，附带每个工作区的运行数与最后使用时间。"""
    reg = _registry()
    out = []
    for ws in reg["workspaces"]:
        d = os.path.join(OUT, ws["id"])
        runs = [r for r in (os.listdir(d) if os.path.isdir(d) else []) if _is_run_dir(d, r)]
        last = max((_run_time(os.path.join(d, r)) for r in runs), default=None)
        item = dict(ws)
        item.update({"runs": len(runs), "lastUsedAt": last})
        out.append(item)
    out.sort(key=lambda w: (w["id"] != DEFAULT_WS, w["name"]))
    return out


def ensure_workspace(wid):
    """校验工作区 id；空值回落到默认工作区。返回 (id, 错误信息)。"""
    wid = (wid or DEFAULT_WS).strip() or DEFAULT_WS
    if not SAFE_ID.match(wid) or wid in RESERVED:
        return None, "工作区 id 只能含字母、数字、. _ -"
    if wid != DEFAULT_WS and not any(w["id"] == wid for w in _registry()["workspaces"]):
        return None, "工作区不存在：%s" % wid
    os.makedirs(os.path.join(OUT, wid), exist_ok=True)
    return wid, None


def create_workspace(name):
    name = (name or "").strip()[:40]
    if not name:
        return 400, {"error": "请填写工作区名称"}
    reg = _registry()
    if any(w["name"] == name for w in reg["workspaces"]):
        return 409, {"error": "已有同名工作区"}
    # id 用时间戳而不是名字转写：中文名无法安全映射成路径段
    wid = "ws-" + time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
    reg["workspaces"].append({"id": wid, "name": name,
                              "createdAt": time.strftime("%Y-%m-%d %H:%M:%S")})
    _dump(WS_FILE, reg)
    os.makedirs(os.path.join(OUT, wid), exist_ok=True)
    return 200, {"ok": True, "id": wid, "name": name}


def rename_workspace(wid, name):
    name = (name or "").strip()[:40]
    if not name:
        return 400, {"error": "请填写新名称"}
    reg = _registry()
    ws = next((w for w in reg["workspaces"] if w["id"] == wid), None)
    if not ws:
        return 404, {"error": "工作区不存在"}
    ws["name"] = name
    _dump(WS_FILE, reg)
    return 200, {"ok": True, "id": wid, "name": name}


def delete_workspace(wid):
    """删除工作区连同其产物。默认工作区不允许删除，避免没有落脚点。"""
    if wid == DEFAULT_WS:
        return 400, {"error": "默认工作区不能删除"}
    if not SAFE_ID.match(wid or "") or wid in RESERVED:
        return 400, {"error": "工作区 id 不合法"}
    reg = _registry()
    ws = next((w for w in reg["workspaces"] if w["id"] == wid), None)
    if not ws:
        return 404, {"error": "工作区不存在"}
    d = os.path.join(OUT, wid)
    # 只删 studio-out 下这一个工作区目录，路径先做 realpath 校验
    if os.path.isdir(d) and os.path.realpath(d).startswith(os.path.realpath(OUT) + os.sep):
        shutil.rmtree(d, ignore_errors=True)
    reg["workspaces"] = [w for w in reg["workspaces"] if w["id"] != wid]
    _dump(WS_FILE, reg)
    _log_drop(lambda e: e.get("workspace") == wid)
    return 200, {"ok": True, "deleted": ws["name"]}


# --------------------------------------------------------------------------
# 运行目录
# --------------------------------------------------------------------------

def _is_run_dir(parent, name):
    return (SAFE_ID.match(name) is not None and name.split("-", 1)[0] in ("run", "ax", "ff")
            and os.path.isdir(os.path.join(parent, name)))


def _run_time(rdir):
    m = _load(os.path.join(rdir, "meta.json"), {})
    return m.get("createdAt") or time.strftime("%Y-%m-%d %H:%M:%S",
                                               time.localtime(os.path.getmtime(rdir)))


def new_run(prefix="run", workspace=None):
    """在指定工作区下新建运行目录，返回 (run_id, 目录, 工作区 id)。"""
    wid, err = ensure_workspace(workspace)
    if err:
        raise ValueError(err)
    rid = time.strftime(prefix + "-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
    rdir = os.path.join(OUT, wid, rid)
    os.makedirs(rdir, exist_ok=True)
    return rid, rdir, wid


def find_run(rid):
    """按 run id 定位目录：先找各工作区，再找旧版扁平布局。返回 (目录, 工作区 id)。"""
    if not SAFE_ID.match(rid or "") or rid in RESERVED:
        return None, None
    for ws in _registry()["workspaces"]:
        d = os.path.join(OUT, ws["id"], rid)
        if os.path.isdir(d):
            return d, ws["id"]
    d = os.path.join(OUT, rid)
    if _is_run_dir(OUT, rid):
        return d, DEFAULT_WS  # 旧数据归入默认工作区展示
    return None, None


def run_url(rid, fn):
    return "/out/%s/%s" % (rid, fn)


def artifacts(rid):
    """列出一次运行可下载的文件（只列顶层文件；raw/、upload/ 等素材目录打包时才带上）。"""
    rdir, _ = find_run(rid)
    if not rdir:
        return []
    order = {k: i for i, (k, _, _) in enumerate(KNOWN)}
    out = []
    for fn in os.listdir(rdir):
        p = os.path.join(rdir, fn)
        if not os.path.isfile(p) or not SAFE_ID.match(fn) or fn.endswith(".tmp"):
            continue
        label = LABELS.get(fn) or ("原始设计图" if fn.startswith("input.") else fn)
        out.append({"name": fn, "label": label, "size": os.path.getsize(p),
                    "deliverable": fn in DELIVERABLE, "url": run_url(rid, fn),
                    "download": "/api/runs/%s/file/%s" % (rid, fn)})
    out.sort(key=lambda a: (order.get(a["name"], 99), a["name"]))
    return out


def zip_run(rid, deliverable_only=False):
    """把一次运行打成 zip（内存中），返回 (字节, 文件名)。"""
    rdir, _ = find_run(rid)
    if not rdir:
        return None, None
    buf = io.BytesIO()
    real_root = os.path.realpath(rdir)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for base, dirs, files in os.walk(rdir):
            dirs[:] = [d for d in dirs if d != "ud"]  # 跳过浏览器临时目录
            for fn in files:
                p = os.path.join(base, fn)
                rel = os.path.relpath(p, rdir).replace(os.sep, "/")
                if fn.endswith(".tmp") or not os.path.realpath(p).startswith(real_root):
                    continue
                if deliverable_only and rel not in DELIVERABLE:
                    continue
                z.write(p, rid + "/" + rel)
    return buf.getvalue(), rid + ".zip"


# --------------------------------------------------------------------------
# 运行日志（runs.jsonl）
# --------------------------------------------------------------------------

def log_run(entry):
    """追加一条运行记录。entry 至少含 run / workspace / source / ok。"""
    e = dict(entry)
    e.setdefault("createdAt", time.strftime("%Y-%m-%d %H:%M:%S"))
    os.makedirs(OUT, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return e


def _read_log():
    if not os.path.isfile(LOG_FILE):
        return []
    out = []
    with open(LOG_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue  # 坏行跳过，不让整份日志失效
    return out


def _log_drop(pred):
    keep = [e for e in _read_log() if not pred(e)]
    tmp = LOG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for e in keep:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    os.replace(tmp, LOG_FILE)


def _legacy_entries(seen):
    """日志功能上线前的老运行：从 meta.json 补出记录，保证历史可回溯。"""
    out = []
    for parent, wid in [(OUT, DEFAULT_WS)] + [(os.path.join(OUT, w["id"]), w["id"])
                                              for w in _registry()["workspaces"]]:
        if not os.path.isdir(parent):
            continue
        for rid in os.listdir(parent):
            if rid in seen or not _is_run_dir(parent, rid):
                continue
            rdir = os.path.join(parent, rid)
            m = _load(os.path.join(rdir, "meta.json"), {})
            ok = m.get("ok", os.path.isfile(os.path.join(rdir, "page.html")))
            out.append({"run": rid, "workspace": wid,
                        "source": m.get("source") or ("axure" if rid.startswith("ax-") else "unknown"),
                        "ok": ok, "page": m.get("page"), "archetype": m.get("archetype"),
                        "summary": m.get("summary"), "accepted": m.get("accepted", False),
                        "acceptanceStatus": m.get("acceptanceStatus", "unknown"),
                        "createdAt": _run_time(rdir), "legacy": True})
            seen.add(rid)
    return out


def list_log(workspace=None, status=None, q=None, limit=200):
    """查询运行日志：可按工作区、成功/失败、关键字过滤；按时间倒序。"""
    entries = _read_log()
    seen = {e.get("run") for e in entries}
    entries += _legacy_entries(seen)
    if workspace:
        entries = [e for e in entries if e.get("workspace") == workspace]
    if status in ("ok", "fail"):
        entries = [e for e in entries if bool(e.get("ok")) == (status == "ok")]
    if q:
        q = q.lower()
        entries = [e for e in entries if q in json.dumps(e, ensure_ascii=False).lower()]
    entries.sort(key=lambda e: e.get("createdAt") or "", reverse=True)
    names = {w["id"]: w["name"] for w in _registry()["workspaces"]}
    out = []
    for e in entries[:limit]:
        e = dict(e)
        rdir, _ = find_run(e.get("run"))
        e["exists"] = bool(rdir)
        e["workspaceName"] = names.get(e.get("workspace"), e.get("workspace"))
        e["files"] = [a for a in artifacts(e["run"]) if a["deliverable"]] if rdir else []
        e["zip"] = "/api/runs/%s/zip" % e["run"] if rdir else None
        out.append(e)
    return out


def delete_run(rid):
    rdir, _ = find_run(rid)
    if not rdir:
        return 404, {"error": "运行不存在"}
    if not os.path.realpath(rdir).startswith(os.path.realpath(OUT) + os.sep):
        return 400, {"error": "路径不合法"}
    shutil.rmtree(rdir, ignore_errors=True)
    _log_drop(lambda e: e.get("run") == rid)
    return 200, {"ok": True}


def delete_runs(rids):
    """批量删除多次运行（复用 delete_run 的路径白名单校验，逐条删）。"""
    if not isinstance(rids, list) or not rids:
        return 400, {"error": "请提供要删除的运行列表"}
    if len(rids) > 500:
        return 400, {"error": "一次最多删除 500 条"}
    deleted = missing = 0
    for rid in rids:
        if not isinstance(rid, str) or not rid:
            continue
        code, _ = delete_run(rid)
        if code == 200:
            deleted += 1
        else:
            missing += 1
    return 200, {"ok": True, "deleted": deleted, "missing": missing}
