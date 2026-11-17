# 模板库端到端回归：内置 A1–A6 指令生成 / 自定义模板回流（HTML → 存模板 → 指令复用）/ 前端渲染。
# 用法：python build/check_library.py
import json, os, socket, subprocess, sys, time, urllib.request, urllib.error
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 3076; B = "http://127.0.0.1:%d" % PORT
srv = subprocess.Popen([sys.executable, "build/studio_server.py", "--port", str(PORT)], cwd=ROOT,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
fails = 0
def chk(name, cond, extra=""):
    global fails; fails += not cond; print("  %s %s %s" % ("PASS" if cond else "FAIL", name, extra))
def get(p):
    with urllib.request.urlopen(B + p, timeout=600) as r: return r.status, r.read()
def post(p, o):
    req = urllib.request.Request(B + p, json.dumps(o).encode(), {"Content-Type": "application/json", "Origin": B})
    try:
        with urllib.request.urlopen(req, timeout=900) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())
CMDS = {"A1": "生成销售订单列表页", "A2": "做一个采购申请单表单", "A3": "物料主数据详情页",
        "A4": "库存看板", "A5": "审批待办工作台", "A6": "客户对账单"}
tid = None
try:
    for _ in range(60):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", PORT)) == 0: break
        time.sleep(.3)
    print("== 模板库")
    _, b = get("/api/templates"); ts = json.loads(b)["templates"]
    bi = [t for t in ts if t["kind"] == "builtin"]
    chk("内置模板 = A1–A6", [t["id"] for t in bi] == list(CMDS), ",".join(t["id"] for t in bi))
    for t in bi:
        c, _ = get(t["preview"]); chk("预览可打开 %s" % t["id"], c == 200, t["preview"])
    print("== 指令 → 内置模板生成（四类断言硬性项全过）")
    for a, cmd in CMDS.items():
        c, r = post("/api/forge/command", {"command": cmd})
        sm = r.get("summary") or {}
        chk("%s「%s」" % (a, cmd), c == 200 and r.get("archetype") == a and sm.get("hardFailed") == 0,
            "%s/%s %s" % (sm.get("passed"), sm.get("total"), sm.get("verdict")))
    c, r = post("/api/forge/command", {"command": "生成一个设备点检列表页 字段：设备编码、设备名称、点检日期、点检结果、负责人"})
    sm = r.get("summary") or {}
    chk("配方外实体 + 自带字段", c == 200 and sm.get("hardFailed") == 0, "%s/%s" % (sm.get("passed"), sm.get("total")))
    c, r = post("/api/parse", {"command": "随便来点什么"})
    chk("认不出类型时给出提示而不是乱生成", c == 422 and "页面类型" in r.get("error", ""))
    print("== 自定义模板回流")
    html = open(os.path.join(ROOT, "build/vision/samples/external-light-ant.html"), encoding="utf-8").read()
    c, r = post("/api/forge/html", {"files": [{"path": "crm/index.html", "text": html}]})
    chk("HTML 入场生成", c == 200 and (r.get("summary") or {}).get("hardFailed") == 0 and r.get("canSaveTemplate"), r.get("run"))
    c, s = post("/api/templates/save", {"run": r.get("run"), "name": "回归测试模板"})
    tid = s.get("id"); chk("存入模板库", c == 200 and tid, tid)
    _, b = get("/api/templates")
    chk("模板库出现该模板", any(t["id"] == tid and t["kind"] == "custom" for t in json.loads(b)["templates"]))
    c, r = post("/api/forge/command", {"command": "用「回归测试模板」生成供应商名录"})
    sm = r.get("summary") or {}
    chk("指令复用自定义模板", c == 200 and (r.get("parsed") or {}).get("templateKind") == "custom"
        and r.get("pageName") == "供应商名录" and sm.get("hardFailed") == 0, "%s/%s" % (sm.get("passed"), sm.get("total")))
    c, r = post("/api/templates/delete", {"id": "A1"}); chk("内置模板不可删", c == 400)
finally:
    if tid:
        c, d = post("/api/templates/delete", {"id": tid}); chk("清理测试模板", c == 200 and d.get("ok"))
    srv.terminate()
print("RESULT", "ALL PASS" if not fails else "FAIL x%d" % fails)
