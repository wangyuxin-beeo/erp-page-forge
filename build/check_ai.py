# -*- coding: utf-8 -*-
"""AI 识别链路的离线回归 —— 不碰外网、不用真密钥。

用本地假模型服务（OpenAI 兼容）把每种失败都造出来，断言：
  · 成功路径能解析出 extract
  · 读超时 / 连接被拒绝 / 401 / 非 JSON 输出，各自给到可照着排查的文案 + 建议
  · 未配置时 503 且提示变量名
  · /api/ai/selftest 分步返回结论
  · 子进程中文输出不再被砸成 U+FFFD（生成日志乱码的根因）

用法：python build/check_ai.py
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import studio_server            # noqa: E402  _classify / _proxy_for / ai_conf
import studio_api               # noqa: E402  run_tool

PORT_S = 3086          # 待测的 Forge Studio
PORT_M = 3087          # 假模型服务
B = "http://127.0.0.1:%d" % PORT_S
MB = "http://127.0.0.1:%d/v1" % PORT_M
IMG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="   # 假服务不解图，只校验前缀

MODE = {"m": "ok"}
fails = []


def chk(name, cond, extra=""):
    if not cond:
        fails.append(name)
    print("  %s %s %s" % ("PASS" if cond else "FAIL", name, extra))


class FakeModel(BaseHTTPRequestHandler):
    """按 MODE 模拟：正常 / 慢 / 401 / 500 / 只回人话不回 JSON。"""

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.endswith("/models"):
            return self._json(200, {"data": [{"id": "fake-vl"}]})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace")
        m = MODE["m"]
        if m == "slow":
            time.sleep(8)
        if m == "401":
            return self._json(401, {"error": {"code": "invalid_api_key"}})
        if m == "500":
            return self._json(500, {"error": "boom"})
        if m == "prose":
            return self._json(200, {"choices": [{"message": {"content": "这是一张 ERP 截图，但我没给 JSON"}}]})
        # 两段式：按请求体里的提示词内容路由（阶段 A 含「阶段 A」，阶段 B 含「区域清单（你正在识别」）
        if m == "two-pass":
            if "阶段 A" in raw:
                return self._json(200, {"choices": [{"message": {"content":
                    '```json\n{"kind":"region-list","canvas":{"w":1600,"h":1000},'
                    '"page":{"title":"出库单管理","breadcrumb":["仓储管理","出库单管理"],"brand":"MES"},'
                    '"regions":[{"id":"r1","role":"header","title":"","bbox":[40,40,600,40],"confidence":0.95},'
                    '{"id":"r2","role":"form","title":"基本信息","bbox":[236,140,780,200],"confidence":0.9},'
                    '{"id":"r3","role":"table","title":"附件信息","bbox":[236,430,780,160],"confidence":0.9}]}'
                    '\n```'}}]})
            if "区域清单" in raw:
                return self._json(200, {"choices": [{"message": {"content":
                    '```json\n{"kind":"screenshot","elements":[{"id":"e1","type":"title",'
                    '"bbox":[40,40,600,40],"text":"出库单管理","confidence":0.95,"source":"vlm"}]}'
                    '\n```'}}]})
            return self._json(200, {"choices": [{"message": {"content":
                '```json\n{"archetype":"A1","columns":[{"key":"code","label":"编码"}]}\n```'}}]})
        self._json(200, {"choices": [{"message": {"content":
            '```json\n{"archetype":"A1","columns":[{"key":"code","label":"编码"}]}\n```'}}]})


def wait_port(port, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(.2)
    return False


def clean_env(extra=None):
    """清掉代理与 FORGE_AI_*（含测试自身环境），只留 extra —— 让每个用例的环境完全可控。"""
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("http_proxy", "https_proxy", "all_proxy") and not k.startswith("FORGE_AI_")}
    env.update(extra or {})
    return env


class Studio(object):
    """按需起停待测服务；env 变了就重启。"""

    def __init__(self):
        self.p = None

    def start(self, env_extra=None):
        self.stop()
        self.p = subprocess.Popen([sys.executable, "build/studio_server.py", "--port", str(PORT_S)],
                                  cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  env=clean_env(env_extra))
        if not wait_port(PORT_S):
            raise SystemExit("待测服务没起来")

    def stop(self):
        if self.p:
            self.p.terminate()
            try:
                self.p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.p.kill()
            self.p = None
        for _ in range(50):          # 等端口真正释放，避免下个用例连到旧进程
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", PORT_S)) != 0:
                    return
            time.sleep(.1)


def post(path, obj, timeout=90):
    req = urllib.request.Request(B + path, json.dumps(obj).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def get(path, timeout=90):
    try:
        with urllib.request.urlopen(B + path, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class QuietServer(ThreadingHTTPServer):
    """客户端提前放弃（超时用例）会让假服务写 socket 失败，那是预期内的，别刷一堆栈。"""

    def handle_error(self, request, client_address):
        pass


def model_thread():
    httpd = QuietServer(("127.0.0.1", PORT_M), FakeModel)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


# --------------------------------------------------------------------------
sil = studio_server
print("== 错误分类（纯函数，不受网络影响）")
f = sil._classify(socket.gaierror(11001, "getaddrinfo failed"), "h.invalid", None, 60, .1)
chk("DNS 失败 → 域名解析失败", f.kind == "域名解析失败", "[%s] %s" % (f.kind, f))

f = sil._classify(urllib.error.URLError(ConnectionRefusedError(10061, "refused")), "h", None, 60, .1)
chk("连接被拒 → 连接被拒绝", f.kind == "连接被拒绝", "[%s] %s" % (f.kind, f))

f = sil._classify(urllib.error.URLError(socket.timeout("timed out")), "h", "http://127.0.0.1:9", 60, .3)
chk("握手阶段超时 → 连接超时且提示代理", f.kind == "连接超时" and "FORGE_AI_NO_PROXY" in f.advice,
    "[%s] %s" % (f.kind, f.advice))

f = sil._classify(TimeoutError("timed out"), "h", None, 120, 120.2)
chk("等满上限 → 读超时且提示调大超时", f.kind == "读超时" and "FORGE_AI_TIMEOUT" in f.advice,
    "[%s] %s" % (f.kind, f.advice))

os.environ["https_proxy"] = "http://127.0.0.1:9"
conf = {"no_proxy": False}
chk("外部地址会用环境代理", sil._proxy_for("https://api.example.com/v1", conf) == "http://127.0.0.1:9")
chk("本机地址不走代理", sil._proxy_for("http://127.0.0.1:3087/v1", conf) is None)
chk("NO_PROXY=1 时不走代理", sil._proxy_for("https://api.example.com/v1", {"no_proxy": True}) is None)
del os.environ["https_proxy"]

print("== 子进程中文输出（生成日志乱码的根因）")
code, out = studio_api.run_tool(["-c", "print('中文输出：断言 20/20 通过')"])
chk("中文没被砸成 U+FFFD", code == 0 and "中文输出" in out and "\ufffd" not in out, repr(out.strip()[:60]))

httpd = model_thread()
st = Studio()
try:
    print("== 未配置模型")
    st.start()
    c, j = get("/api/ai/status")
    chk("status.configured=False", c == 200 and j.get("configured") is False, str(j))
    c, j = post("/api/ai/analyze", {"image": IMG})
    chk("analyze → 503 且提示变量名", c == 503 and "FORGE_AI_BASE" in j.get("error", ""), str(c))
    c, j = get("/api/ai/selftest")
    chk("selftest → 503 且提示变量名", c == 503 and "FORGE_AI_BASE" in j.get("error", ""), str(c))

    print("== 通道正常")
    MODE["m"] = "ok"
    st.start({"FORGE_AI_BASE": MB, "FORGE_AI_KEY": "sk-fake", "FORGE_AI_MODEL": "fake-vl"})
    c, j = get("/api/ai/status")
    chk("status.configured=True 且回显 model/超时", c == 200 and j.get("configured") and j.get("model") == "fake-vl"
        and j.get("timeout") == 300, str(j))
    c, j = post("/api/ai/analyze", {"image": IMG})
    chk("analyze 成功且解析出 extract", c == 200 and (j.get("extract") or {}).get("archetype") == "A1", str(j)[:120])
    chk("成功时回报通道与耗时", (j.get("ai") or {}).get("channel") in ("直连", "直连（代理失败后自动重试成功）")
        and (j.get("ai") or {}).get("elapsed") is not None, str(j.get("ai")))
    c, j = get("/api/ai/selftest?deep=1")
    names = [s["step"] for s in j.get("steps") or []]
    chk("selftest 逐步报（配置/DNS/端口/通道/鉴权/模型）",
        c == 200 and j.get("ok") and all(n in names for n in
        ["配置", "DNS 解析", "TCP 连接", "通道", "鉴权探测", "模型调用"]), str(names))
    chk("selftest 给结论", bool(j.get("verdict")), str(j.get("verdict"))[:60])

    print("== 两段式截图识别（先区域清单后逐区回填）")
    MODE["m"] = "two-pass"
    c, j = post("/api/ai/analyze", {"image": IMG, "mode": "screenshot"})
    ext = j.get("extract") or {}
    chk("两段式返回 kind:screenshot", c == 200 and ext.get("kind") == "screenshot", str(ext.get("kind")))
    chk("两段式合并了逐区元素", len(ext.get("elements") or []) == 3, "elements=%d" % len(ext.get("elements") or []))
    chk("两段式回报区域数", (j.get("ai") or {}).get("twoPass") is True
        and (j.get("ai") or {}).get("regions") == 3, str(j.get("ai")))
    chk("两段式保留页面信息", (ext.get("page") or {}).get("title") == "出库单管理", str(ext.get("page")))
    # S4：当前回归跑在受管 Python（无 Pillow）→ 裁剪降级，cropped 应为 0，但链路照常出结果
    chk("无 Pillow 时裁剪降级（cropped=0）但链路不断", c == 200
        and (j.get("ai") or {}).get("cropped") == 0, str(j.get("ai")))
    # S5：无 Pillow 时像素先验也算不出 → prior=False，链路照常
    chk("无 Pillow 时像素先验降级（prior=False）但链路不断", c == 200
        and (j.get("ai") or {}).get("prior") is False, str(j.get("ai")))

    print("== 低置信度分组（S6，纯函数）")
    ex_low = {
        "kind": "screenshot",
        "elements": [
            {"id": "e1", "type": "title", "text": "出库单管理", "confidence": 0.95, "source": "vlm", "bbox": [40, 40, 600, 40]},
            {"id": "e2", "type": "input", "label": "单据编号", "confidence": 0.5, "source": "vlm", "bbox": [60, 150, 300, 32]},
            {"id": "e3", "type": "panel", "confidence": 0.9, "children": [
                {"id": "e31", "type": "select", "label": "单据类型", "confidence": 0.4, "source": "vlm", "bbox": [60, 200, 300, 32]},
                {"id": "e32", "type": "button", "text": "提交"}  # 无 confidence（旧契约）
            ]},
        ],
        "uncertain": [{"elementId": "e2", "field": "label", "why": "小字模糊"}],
    }
    conf = sil.classify_confidence(ex_low)
    chk("阈值 0.7 正确过滤（0.95 不算低，嵌套 0.4 被挑出）",
        {x["id"] for x in conf["lowConfidence"]} == {"e2", "e31"},
        str([x["id"] for x in conf["lowConfidence"]]))
    chk("低置信度元素带 reasons（关联 uncertain.why）",
        any(x["id"] == "e2" and "小字模糊" in x["reasons"] for x in conf["lowConfidence"]), str(conf["lowConfidence"]))
    chk("无 confidence 的元素不误判", all(x["id"] != "e32" for x in conf["lowConfidence"]))
    conf_lo = sil.classify_confidence(ex_low, threshold=0.99)
    chk("阈值可调（0.99 时 0.9/0.95 也算低）",
        {x["id"] for x in conf_lo["lowConfidence"]} == {"e1", "e2", "e3", "e31"},
        str([x["id"] for x in conf_lo["lowConfidence"]]))
    ex_bad = {"kind": "screenshot", "elements": [{"id": "x1", "type": "input", "confidence": 1.5, "bbox": [0, 0, 10, 10]}]}
    conf_bad = sil.classify_confidence(ex_bad)
    chk("非法 confidence（越界）也进低置信度", conf_bad["count"] == 1, str(conf_bad["count"]))
    chk("非 screenshot（无 elements）返回空", sil.classify_confidence({"archetype": "A1"})["count"] == 0)

    print("== 两段式失败自动降级单段")
    MODE["m"] = "prose"  # 阶段 A 拿不到 region-list → 降级单段，单段也拿到 prose → 502
    c, j = post("/api/ai/analyze", {"image": IMG, "mode": "screenshot"})
    chk("两段式失败后仍走单段并报 JSON 错误", c == 502 and "JSON" in j.get("error", ""), str(c))

    print("== 模型只回人话、不回 JSON")
    MODE["m"] = "prose"
    c, j = post("/api/ai/analyze", {"image": IMG})
    chk("报「不是可用的 JSON」并给建议", c == 502 and "JSON" in j.get("error", "") and j.get("advice"), str(j.get("error"))[:90])

    print("== 密钥不对（401）")
    MODE["m"] = "401"
    c, j = post("/api/ai/analyze", {"image": IMG})
    chk("报 HTTP 401 且建议指向密钥", c == 502 and "401" in j.get("error", "")
        and "密钥" in (j.get("advice") or ""), "%s / %s" % (str(j.get("error"))[:60], str(j.get("advice"))[:40]))

    print("== 读超时（模型比上限慢）")
    MODE["m"] = "slow"
    # 超时下限被钳到 5s（更小的值没有意义），所以断言用 5
    st.start({"FORGE_AI_BASE": MB, "FORGE_AI_KEY": "sk-fake", "FORGE_AI_MODEL": "fake-vl",
              "FORGE_AI_TIMEOUT": "2"})
    c, j = post("/api/ai/analyze", {"image": IMG})
    ai = j.get("ai") or {}
    chk("报「读超时」并带上限", c == 502 and "读超时" in j.get("error", "") and ai.get("timeout") == 5,
        "%s / ai=%s" % (str(j.get("error"))[:70], ai))
    chk("失败文案里带上限与已等待时长", "上限 5s" in j.get("error", ""), str(j.get("error"))[:70])
    chk("建议里给出 FORGE_AI_TIMEOUT", "FORGE_AI_TIMEOUT" in (j.get("advice") or ""), str(j.get("advice"))[:60])

    print("== 端口不通")
    st.start({"FORGE_AI_BASE": "http://127.0.0.1:1/v1", "FORGE_AI_KEY": "sk-fake",
              "FORGE_AI_MODEL": "fake-vl", "FORGE_AI_TIMEOUT": "5"})
    c, j = post("/api/ai/analyze", {"image": IMG})
    chk("报连接类失败且带 host", c == 502 and (j.get("ai") or {}).get("host") == "127.0.0.1"
        and "不可达" in j.get("error", ""), "%s / %s" % (str(j.get("error"))[:70], (j.get("ai") or {}).get("kind")))
    chk("不再只有一句 TimeoutError", "TimeoutError" not in j.get("error", ""), str(j.get("error"))[:70])

    print("== 域名解析失败")
    st.start({"FORGE_AI_BASE": "http://no-such-host-forge.invalid/v1", "FORGE_AI_KEY": "sk-fake",
              "FORGE_AI_MODEL": "fake-vl", "FORGE_AI_TIMEOUT": "5"})
    c, j = post("/api/ai/analyze", {"image": IMG})
    chk("报域名解析失败并带 host", c == 502 and "解析" in j.get("error", ""), str(j.get("error"))[:70])

    print("== 生成链路的中文日志（端到端）")
    MODE["m"] = "ok"
    st.start()
    c, w = post("/api/workspaces/create", {"name": "AI 自检临时区"})
    wid = (w.get("workspace") or w).get("id")
    try:
        _, smp = get("/api/sample/extA")
        c, r = post("/api/forge/image", {"image": smp["image"], "extract": smp["extract"],
                                        "workspace": wid, "theme": "premium"}, timeout=300)
        blob = json.dumps(r, ensure_ascii=False)
        chk("生成成功且日志无 U+FFFD", c == 200 and r.get("ok") and "\ufffd" not in blob
            and any("\u4e00" <= ch <= "\u9fff" for ch in blob), r.get("run"))
    finally:
        if wid:
            post("/api/workspaces/delete", {"id": wid})
finally:
    st.stop()
    httpd.shutdown()

print("RESULT", "ALL PASS" if not fails else "FAIL x%d: %s" % (len(fails), ", ".join(fails)))
sys.exit(1 if fails else 0)
