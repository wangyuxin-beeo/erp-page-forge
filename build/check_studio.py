# 设计台端到端回归：工作区 / 成果库下载 / 生成日志（成功+失败）。用法：python build/check_studio.py
import io, json, os, socket, subprocess, sys, time, urllib.request, zipfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 3075; B = "http://127.0.0.1:%d" % PORT
srv = subprocess.Popen([sys.executable, "build/studio_server.py", "--port", str(PORT)], cwd=ROOT,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
fails = 0
def chk(name, cond, extra=""):
    global fails; fails += not cond; print("  %s %s %s" % ("PASS" if cond else "FAIL", name, extra))
def get(p):
    with urllib.request.urlopen(B + p, timeout=600) as r: return r.status, r.headers, r.read()
def post(p, o):
    req = urllib.request.Request(B + p, json.dumps(o).encode(), {"Content-Type": "application/json", "Origin": B})
    try:
        with urllib.request.urlopen(req, timeout=900) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())
try:
    for _ in range(60):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", PORT)) == 0: break
        time.sleep(.3)
    print("== 工作区")
    # 上次中途崩掉留下的测试工作区先清掉；名字带时间戳，避免同名 409
    _, _, b = get("/api/workspaces")
    for x in json.loads(b)["workspaces"]:
        if x["name"].startswith("E2E 测试区"): post("/api/workspaces/delete", {"id": x["id"]})
    c, w = post("/api/workspaces/create", {"name": "E2E 测试区 " + time.strftime("%H%M%S")})
    wid = (w.get("workspace") or w).get("id")
    chk("新建工作区", c == 200 and wid, wid)
    if not wid: raise SystemExit("无法新建工作区：%s" % w)
    print("== 截图入口（成功）")
    _, _, b = get("/api/sample/extA"); smp = json.loads(b)
    c, r = post("/api/forge/image", {"image": smp["image"], "extract": smp["extract"], "workspace": wid, "theme": "premium"})
    chk("生成成功", c == 200 and r.get("ok"), r.get("run"))
    chk("落到指定工作区", r.get("workspace") == wid, r.get("workspace"))
    chk("产物清单含 page.html", any(f.get("name") == "page.html" for f in r.get("files") or []))
    _, h, z = get(r["zip"]); names = zipfile.ZipFile(io.BytesIO(z)).namelist()
    chk("ZIP 下载（附件）", "attachment" in (h.get("Content-Disposition") or ""), str(names))
    f0 = (r.get("files") or [{}])[0]
    _, h2, _ = get(f0["download"]); chk("单文件下载", "attachment" in (h2.get("Content-Disposition") or ""), f0.get("name"))
    print("== HTML 入口（成功）")
    c, r3 = post("/api/forge/html", {"files": [{"path": "x/list.html", "text": "<html><body><h1>物料管理</h1><table><tr><th>物料编码</th><th>物料名称</th><th>规格</th><th>状态</th></tr></table></body></html>"}], "workspace": wid})
    chk("HTML 生成", c == 200 and r3.get("ok"), r3.get("run"))
    print("== 截图入口（失败也要记日志）")
    c, r2 = post("/api/forge/image", {"image": smp["image"], "extract": {}, "workspace": wid})
    chk("空 extract 返回失败", c != 200 or not r2.get("ok"), str(c))
    print("== 生成日志")
    _, _, b = get("/api/runs?workspace=" + wid); runs = json.loads(b)["runs"]
    st = sorted({x.get("ok") for x in runs}, key=str)
    chk("本区记录 ≥2 条且有成功和失败", len(runs) >= 2 and st == [False, True], "%d 条" % len(runs))
    e = next(x for x in runs if x.get("ok"))
    chk("记录含时间/文件/下载", bool(e.get("time") or e.get("createdAt")) and e.get("files") and e.get("zip"), ",".join(sorted(e)))
    _, _, b = get("/api/runs?workspace=%s&status=fail" % wid)
    chk("按失败筛选", all(not x.get("ok") for x in json.loads(b)["runs"]))
    print("== 清理")
    c, d = post("/api/workspaces/delete", {"id": wid}); chk("删除测试工作区", c == 200 and d.get("ok"))
finally:
    srv.terminate()
print("RESULT", "ALL PASS" if not fails else "FAIL x%d" % fails)
