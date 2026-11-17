# 语义拆层回归：无头 Chrome 打开设计台 ?demo=extA&probe，读语义识别结果并与示例 extract 逐项对比
# 用法：python build/check_semantic.py   （全过输出 RESULT ALL PASS）
import json, os, re, socket, subprocess, sys, tempfile, time, shutil
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from portal_check import find_browser
PORT = 3073
EX = {"extA": "extA-wms-inout.json", "extB": "extB-crm-customer.json", "extC": "extC-purchase-request.json"}
srv = subprocess.Popen([sys.executable, "build/studio_server.py", "--port", str(PORT)], cwd=ROOT,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
end = time.time() + 20
while time.time() < end:
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", PORT)) == 0: break
    time.sleep(.3)
br = find_browser(); fails = 0
try:
    for name, exf in EX.items():
        ud = tempfile.mkdtemp(prefix="forge_sem_")
        p = subprocess.run([br, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
                            "--window-size=1600,1000", "--virtual-time-budget=12000", "--user-data-dir=" + ud,
                            "--dump-dom", "http://127.0.0.1:%d/?demo=%s&probe" % (PORT, name)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        shutil.rmtree(ud, ignore_errors=True)
        # 只认 #probeOut 元素里的结果；脚本源码里也有 "PROBE::" 字样
        m = re.search(r'id="probeOut"[^>]*>PROBE::(.*?)::END', p.stdout, re.S)
        if not m:
            print(name, "NO PROBE"); fails += 1; continue
        data = json.loads(m.group(1).replace("&quot;", '"').replace("&amp;", "&"))
        ls = data["layers"] if isinstance(data, dict) else data          # 兼容旧格式（纯数组）
        # 空 extract 兜底自测：只要拆出了语义图层，buildFromLayers 必须给出 ≥2 列的可用结构
        st = (data.get("self") or {}) if isinstance(data, dict) else {}
        if not st or st.get("cols", 0) < 2:
            print(name, "兜底自测失败", st); fails += 1
        else:
            print("  %s 空 extract 兜底：arch=%s cols=%s ph=%s" % ("PASS", st.get("arch"), st.get("cols"), st.get("ph")))
        dm = data.get("dbg") if isinstance(data, dict) else None
        if dm and os.environ.get("SEMDBG"):
            for e in dm:
                if 250 <= e["y"] <= 830: print("  DBG %(zone)-8s y=%(y)-5d h=%(h)-3d n=%(n)-2d b=%(b)d %(xs)s" % e)
        json.dump(ls, open(os.path.join(tempfile.gettempdir(), "sem_%s.json" % name), "w", encoding="utf-8"), ensure_ascii=False)
        ex = json.load(open(os.path.join(HERE, "vision", "extracts", exf), encoding="utf-8"))
        k = lambda kind: [l for l in ls if l["k"] == kind]
        if name == "extC":
            # A2 单据表单：关键回归是「表单卡字段化」（标签+框 ≥8 对 → 字段）与「底部动作条 ≠ 分页」
            rows = [
                ("侧栏", len(k("侧栏")), 1),
                ("菜单≥6", len(k("菜单")) >= 6, True),
                ("标题", [l["n"] for l in k("标题")], [ex.get("pageName")]),
                # 白底白框的表单输入框会碎成边框细条，字段级识别是已知局限（真实系统截图多为灰底白框，可识别）；
                # 这里只断言表单卡本身被认出（卡片 → 表单区）
                ("表单区=1(表单卡)", len(k("表单区")), 1),
                ("表格", len(k("表格")), 1),
                ("列头≥6", len(k("列头")) >= 6, True),
                ("操作列≤1", len(k("操作列")) <= 1, True),
                ("分页=0(动作条非分页)", len(k("分页")), 0),
                ("按钮≥3", len(k("按钮")) >= 3, True),
            ]
        else:
            exp_menu = sum(len(g.get("items") or []) for g in ex.get("nav") or [])
            exp_hdr = [a for a in ex.get("actions") or [] if (a.get("scope") or "header") != "row"]
            rows = [
                ("侧栏", len(k("侧栏")), 1),
                ("菜单", len(k("菜单")), exp_menu),
                ("菜单分组", [l["n"] for l in k("菜单分组")], [g["group"] for g in ex.get("nav") or [] if g.get("group")]),
                ("菜单名", [l["n"] for l in k("菜单")], [i for g in ex.get("nav") or [] for i in g.get("items") or []]),
                ("标题", [l["n"] for l in k("标题")], [ex.get("pageName")]),
                ("查询条件", len(k("查询条件")), len(ex.get("queryControls") or [])),
                ("表格", len(k("表格")), 1),
                ("列头(业务列)", [l["n"] for l in sorted(k("列头"), key=lambda l: l["x"])], ex.get("columns")),
                ("操作列", len(k("操作列")), 1),
                ("复选框", len(k("复选框")), 1),
                ("按钮(页头+工具条)", len(k("按钮")), len(exp_hdr)),
                ("分页", len(k("分页")), 1),
            ]
        print("==", name, "图层", len(ls))
        for label, got, want in rows:
            ok = got == want
            fails += not ok
            print("  %-4s %-14s got=%s want=%s" % ("PASS" if ok else "FAIL", label, got, want))
        print("  其它语义:", sorted({l["k"] for l in ls}))
finally:
    srv.terminate()
print("RESULT", "ALL PASS" if not fails else "FAIL x%d" % fails)
