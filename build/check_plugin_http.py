#!/usr/bin/env python3
"""Isolated real-HTTP regression; never reads credentials or deletes user data.
Each invocation retains a new tempfile data root and writes its location in report.
"""
import base64
import copy
import io
import json
import os
import pathlib
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEST_ROOT = ROOT / "plugin/test-results"
TEST_ROOT.mkdir(parents=True, exist_ok=True)
DATA = pathlib.Path(tempfile.mkdtemp(prefix="forge-http-test-", dir=str(TEST_ROOT)))
os.environ["FORGE_DATA_DIR"] = str(DATA)
# No external model calls or inherited model credentials during this test.
for key in list(os.environ):
    if key.startswith("FORGE_AI_"):
        os.environ.pop(key)
sys.path.insert(0, str(ROOT / "build"))
import studio_api as api
import studio_store as store
import studio_server as server
from ext.freeform import routes as ff

RESULTS = []
httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
thread = threading.Thread(target=httpd.serve_forever, daemon=True)
thread.start()
BASE = "http://127.0.0.1:%d" % httpd.server_port


def request(path, body=None, raw=False):
    headers = {"Origin": BASE}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            data, code = r.read(), r.status
    except urllib.error.HTTPError as e:
        data, code = e.read(), e.code
    return code, data if raw else json.loads(data)


def check(name, fn):
    try:
        detail = fn()
        RESULTS.append({"name": name, "status": "pass", "detail": detail})
        print("PASS " + name, flush=True)
        return detail
    except Exception as e:
        RESULTS.append({"name": name, "status": "fail", "error": str(e)})
        print("FAIL " + name + ": " + str(e), flush=True)
        return None


def ok(path, payload):
    code, b = request(path, payload)
    assert code == 200 and b.get("ok", True), (code, b)
    return b


try:
    def paths():
        assert pathlib.Path(api.OUT).parent == DATA
        assert pathlib.Path(api.LIB).parent == DATA
        assert pathlib.Path(ff.FF_LIB).parent == DATA
        assert pathlib.Path(ff.BUILD) == ROOT / "build"
        return {"dataRoot": str(DATA), "OUT": api.OUT, "LIB": api.LIB, "FF_LIB": ff.FF_LIB}
    check("data roots and freeform module levels", paths)
    check("Axure static import through HTTP", lambda: ok("/api/axure", {"dir": str(ROOT / "build/axure/sample-prototype"), "theme": "default"}))
    ws = check("create workspace", lambda: ok("/api/workspaces/create", {"name": "Isolated HTTP test"}))
    wid = (ws or {}).get("id", "default")
    check("rename workspace", lambda: ok("/api/workspaces/rename", {"id": wid, "name": "HTTP renamed"}))
    generated = check("generate normal A1 with actual acceptance", lambda: ok("/api/forge/command", {"command": "生成销售订单列表页", "workspace": wid}))
    if generated:
        def normal_assertions():
            assert generated["generated"] is True
            assert generated["summary"]["hardFailed"] == 0
            assert generated["accepted"] == (generated["acceptanceStatus"] == "passed")
            assert generated["summary"]["skippedCategories"] == 0
            assert request(generated["page"], raw=True)[0] == 200
            return generated["summary"]
        check("normal report and served artifact", normal_assertions)
        page = request(generated["page"], raw=True)[1].decode("utf-8")
        imported = check("HTML import", lambda: ok("/api/forge/html", {"workspace": wid, "files": [{"path": "upload.html", "text": page}]}))
        if imported:
            tpl = check("save normal template", lambda: ok("/api/templates/save", {"run": imported["run"], "name": "HTTP订单模板"}))
            if tpl:
                check("reuse normal template", lambda: ok("/api/forge/command", {"command": "", "templateId": tpl["id"], "workspace": wid}))
                check("delete normal template", lambda: ok("/api/templates/delete", {"id": tpl["id"]}))
        def history_zip():
            code, history = request("/api/runs?workspace=" + wid)
            assert code == 200 and any(r["run"] == generated["run"] for r in history["runs"])
            code, data = request(generated["zip"], raw=True)
            assert code == 200
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
            assert any(n.endswith("/page.html") for n in names)
            code, workspaces = request("/api/workspaces")
            item = next(w for w in workspaces["workspaces"] if w["id"] == wid)
            assert item["runs"] >= 1
            return {"history": len(history["runs"]), "zipEntries": len(names), "workspaceRuns": item["runs"]}
        check("workspace history and deliverable zip", history_zip)
    html = '<!doctype html><html><head><meta charset="utf-8"><style>body{margin:0;background:#fff;padding:20px;font-family:sans-serif}h1{color:#123456}</style></head><body><h1>测试标题</h1><p>这里展示自由版式页面的真实内容，用于验证原始结构保留、文字替换、模板保存和再次生成。测试页面中的业务数据全部虚构，不包含个人信息和生产信息。所有页面内容在本地生成，并通过浏览器检查文字是否出现、布局是否溢出，以及页面是否正常显示。此段说明为独立集成测试提供足够的真实可见正文。</p></body></html>'
    fhtml = check("freeform HTML import", lambda: ok("/api/ff/html", {"workspace": wid, "name": "自由测试页面", "files": [{"path": "page.html", "text": html}]}))
    if fhtml:
        ftpl = check("save freeform template", lambda: ok("/api/ff/templates/save", {"run": fhtml["run"], "name": "自由HTTP模板"}))
        if ftpl:
            spec = json.loads((pathlib.Path(store.find_run(fhtml["run"])[0]) / "freeform.spec.json").read_text(encoding="utf-8"))
            slot = next(iter(spec["slots"]))
            reused = check("freeform template slot replacement", lambda: ok("/api/ff/generate", {"workspace": wid, "templateId": ftpl["id"], "slots": {slot: "修改后的标题"}}))
            if reused:
                def inspect_ff():
                    text = request(reused["page"], raw=True)[1].decode("utf-8")
                    assert "修改后的标题" in text
                    assert reused["acceptanceStatus"] in ("passed", "needs_review")
                    assert reused["accepted"] == (reused["acceptanceStatus"] == "passed")
                    c, listing = request("/api/ff/templates")
                    assert any(t["id"] == ftpl["id"] for t in listing["templates"])
                    c, hist = request("/api/runs?workspace=" + wid)
                    assert any(r["run"] == reused["run"] for r in hist["runs"])
                    c, data = request(reused["zip"], raw=True)
                    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
                    assert any(n.endswith("/freeform.spec.json") for n in names)
                    return {"acceptanceStatus": reused["acceptanceStatus"], "unsupported": reused["summary"]["unsupported"]}
                check("freeform history zip and acceptance semantics", inspect_ff)
            check("freeform command reuses stored skeleton", lambda: ok("/api/forge/command", {"command": "用自由HTTP模板生成测试", "workspace": wid}))
            check("delete freeform template", lambda: ok("/api/ff/templates/delete", {"id": ftpl["id"]}))
    direct = {"kind": "freeform", "specVersion": "ff-1.0", "name": "自由规范测试", "canvas": {"w": 600, "h": 400, "bg": "#FFFFFF"}, "source": {"type": "manual"}, "slots": {"title": "自由规范标题"}, "regions": [{"id": "title", "role": "text", "keep": "rebuild", "bbox": [20,20,500,80], "text": "自由规范标题", "slot": "title", "style": {"fontSize": 24, "color": "#123456"}}]}
    direct["regions"].append({"id": "description", "role": "text", "keep": "rebuild", "bbox": [20,100,580,350], "text": "这是自由版式规范的完整测试内容。页面结构由明确声明的区块组成，所有文字都生成真实的网页元素。测试会核对模板保存、插槽替换、运行历史和交付压缩包。测试使用独立的数据目录，不会覆盖用户已有项目，也不会读取任何私人模型密钥。页面展示虚构说明文字，方便浏览器实际检查正文、渲染错误、布局宽度和预期文案。", "style": {"fontSize": 18, "color": "#123456"}})
    check("freeform direct spec generation", lambda: ok("/api/ff/generate", {"spec": direct, "workspace": wid}))
    def legacy_image_case():
        code, sample = request("/api/sample/extA")
        assert code == 200 and sample.get("image") and sample.get("extract"), (code, sample)
        return ok("/api/forge/image", {"image": sample["image"], "extract": sample["extract"], "workspace": wid})
    check("legacy design-image extract pipeline", legacy_image_case)
    shot = json.loads((ROOT / "build/vision/screenshot-samples/form-two-tables.json").read_text(encoding="utf-8"))
    def screenshot_case():
        image = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
        body = ok("/api/forge/screenshot", {"spec": shot, "image": image, "workspace": wid})
        assert body["accepted"], body
        return body
    screenshot = check("screenshot independent HTTP pipeline", screenshot_case)
    if screenshot:
        stpl = check("save screenshot template", lambda: ok("/api/templates/save", {"run": screenshot["run"], "name": "截图HTTP模板"}))
        if stpl:
            check("reuse screenshot template", lambda: ok("/api/forge/command", {"templateId": stpl["id"], "workspace": wid}))
            check("delete screenshot template", lambda: ok("/api/templates/delete", {"id": stpl["id"]}))
    def hard_failure():
        # Deterministic fault injection only, no real source mutation.
        original = api._run
        def fail_verify(args, timeout=300):
            if args[0] == "build/verify_acceptance.py":
                path = args[args.index("--out")+1]
                pathlib.Path(path).write_text(json.dumps({"summary": {"hardFailed": 1, "unsupported": 0, "verdict": "不通过"}, "categories": {}, "page": {}}), encoding="utf-8")
                return 1, "injected hard verification failure"
            return original(args, timeout)
        api._run = fail_verify
        try:
            code, b = api.forge_from_command({"command": "生成销售订单列表页", "workspace": wid})
            assert code == 422 and not b["ok"] and b["generated"] and not b["accepted"], b
            return {"status": code, "generated": b["generated"], "acceptanceStatus": b["acceptanceStatus"]}
        finally:
            api._run = original
    check("hard acceptance failure is not generation success", hard_failure)
    def cli_bridge():
        import plugin_api
        code, listing = plugin_api.dispatch("templates", {})
        assert code == 200 and len(listing["templates"]) >= 6
        if generated:
            code, z = plugin_api.dispatch("zip", {"run": generated["run"]})
            assert code == 200 and pathlib.Path(z["path"]).is_file()
            assert pathlib.Path(z["path"]).parent == DATA / "exports"
        code, bad = plugin_api.dispatch("not-allowed", {})
        assert code == 400
        return {"templates": len(listing["templates"]), "unknownOperationRejected": True}
    check("file bridge dispatch and zip export", cli_bridge)
    if ws and not any(r["status"] == "fail" for r in RESULTS):
        check("delete isolated test workspace", lambda: ok("/api/workspaces/delete", {"id": wid}))
finally:
    httpd.shutdown()
    httpd.server_close()
    report = {"dataRoot": str(DATA), "credentialsRead": False, "externalAICalls": False, "results": RESULTS}
    output = ROOT / "plugin/test-results/http-business.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("REPORT " + str(output), flush=True)
sys.exit(1 if any(r["status"] == "fail" for r in RESULTS) else 0)
