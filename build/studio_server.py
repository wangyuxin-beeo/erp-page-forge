#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Forge Studio 本地服务 —— 设计台（studio/index.html）的后端。

只绑 127.0.0.1。三件事：
  GET  /                    → 设计台页面（studio/index.html）
  GET  /out/<file>          → 生成产物 / 验收报告
  GET  /api/ai/status       → 外部视觉模型是否已配置（不回显密钥）
  POST /api/ai/analyze      → 把设计图转发给外部视觉模型，返回 extract.json（AI 识别）
  GET  /api/ai/selftest     → 模型通道自检（?deep=1 会真调一次模型）
  POST /api/forge           → 设计图 + extract → vision.py → forge.py → 四类断言 → 报告
  POST /api/axure           → Axure HTML 导出目录 → axure.py → forge.py → 四类断言 → 报告
  GET  /api/workspaces      → 工作区列表；POST /api/workspaces/{create,rename,delete}
  GET  /api/runs            → 运行日志（?workspace=&status=ok|fail&q=）；POST /api/runs/delete
  GET  /api/runs/<run>/zip  → 打包下载（默认只含交付物，?all=1 连同素材）
  GET  /api/runs/<run>/file/<fn> → 单文件下载

外部模型（可选，默认关闭；⚠ 开启后设计图会发往第三方，与项目「零外链」约定冲突，请确认有权把所选资料发送给模型）：
  OpenAI 兼容的 /chat/completions 接口，环境变量配置，**密钥只在本进程内，不下发到浏览器**：
    FORGE_AI_BASE   例 https://api.openai.com/v1   （或任一兼容网关 / 内网模型）
    FORGE_AI_KEY    API 密钥
    FORGE_AI_MODEL  例 gpt-4o / qwen-vl-max
    FORGE_AI_TIMEOUT（可选）等模型返回的上限，秒；默认 300。识图慢，先怀疑这里。
    FORGE_AI_MAX_TOKENS（可选）输出上限，默认 4096；截断会让 JSON 解析失败。
    FORGE_AI_NO_PROXY=1（可选）忽略 http(s)_proxy 直连 —— 服务若继承了不通的代理，
      表现就是「一直等，最后超时」，而错误里看不出跟代理有关，这个开关用来排除它。
  未配置时 /api/ai/analyze 返回 503，设计台退回纯本地拆层 + 手工标注，链路照常可用。
  排查顺序：点界面「自检」→ 看是 DNS / 端口 / 鉴权 / 模型哪一步断的，再按建议调。

用法:
    python build/studio_server.py [--port 3060]
    浏览器打开 http://127.0.0.1:3060/
"""

import argparse
import base64
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import studio_api  # noqa: E402  模板库 / 指令生成 / HTML 入场 / 模板回流
import studio_store  # noqa: E402  工作区 / 归档下载 / 运行日志
import img_crop  # noqa: E402  区域裁剪放大（可选依赖 Pillow，未装则降级）
import pixel_prior  # noqa: E402  像素先验（锚点 + 列线/区域框，注入提示词）
import web_capture  # noqa: E402
import web_routes  # noqa: E402

# 自由版式扩展路由（可选；扩展不存在时回退为 None，行为与改造前一致）
try:
    from ext.freeform import routes as _ff_routes  # noqa: E402
except Exception:
    _ff_routes = None
ROOT = os.path.dirname(HERE)
STUDIO = os.path.join(ROOT, "studio")
OUT = studio_store.OUT
PY = sys.executable
MAX_BODY = 25 * 1024 * 1024
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")

PROMPT_PATH = os.path.join(STUDIO, "ai-prompt.md")
SCREENSHOT_PROMPT_PATH = os.path.join(STUDIO, "ai-prompt-screenshot.md")
SCREENSHOT_REGIONS_PROMPT_PATH = os.path.join(STUDIO, "ai-prompt-screenshot-regions.md")

# S6：低置信度元素阈值。confidence < 该值的元素进「低置信度概览」，前端画布高亮 + 列表核对。
CONFIDENCE_THRESHOLD = 0.7


DEFAULT_TIMEOUT = 300       # 秒。等模型返回的上限；识图本来就慢（大图 + 长 JSON），别设太小
DEFAULT_MAX_TOKENS = 4096


def ai_conf():
    """读环境变量。缺任一必填项返回 None —— 此时「AI 识别」按钮不可用，链路照常走本地拆层。"""
    bridge = os.environ.get("FORGE_HOST_BRIDGE", "").strip().rstrip("/")
    token = os.environ.get("FORGE_HOST_TOKEN", "")
    if bridge and token:
        return {"base": bridge, "key": token,
                "model": os.environ.get("FORGE_HOST_MODEL", "DSH 当前模型"),
                "timeout": int(os.environ.get("FORGE_AI_TIMEOUT", "300")),
                "max_tokens": 16384, "no_proxy": True, "host_bridge": True}
    base = os.environ.get("FORGE_AI_BASE", "").strip().rstrip("/")
    key = os.environ.get("FORGE_AI_KEY", "").strip()
    model = os.environ.get("FORGE_AI_MODEL", "").strip()
    if not (base and key and model):
        return None

    def num(name, dflt, lo, hi):
        try:
            return max(lo, min(hi, int(float(os.environ.get(name, "") or dflt))))
        except ValueError:
            return dflt

    flag = (os.environ.get("FORGE_AI_NO_PROXY", "") or "").strip().lower()
    return {"base": base, "key": key, "model": model,
            "timeout": num("FORGE_AI_TIMEOUT", DEFAULT_TIMEOUT, 5, 1800),
            "max_tokens": num("FORGE_AI_MAX_TOKENS", DEFAULT_MAX_TOKENS, 256, 32768),
            "no_proxy": flag not in ("", "0", "false", "no")}


# 推荐配置（给人看，不回显密钥）：Qwen2.5-VL 走 DashScope 的 OpenAI 兼容端点。
#   北京：FORGE_AI_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
#         FORGE_AI_MODEL=qwen-vl-max   （或 qwen-vl-plus / qwen2.5-vl-72b-instruct）
#   上海：FORGE_AI_BASE=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
# 注意：API Key 必须与 base_url 所属地域一致，否则 401 invalid_api_key。
# 可选：
#   FORGE_AI_TIMEOUT=300      等模型返回的上限（秒）。识图慢，容易在这里超时，别设太小。
#   FORGE_AI_MAX_TOKENS=4096  结构化 regions + bbox 的 JSON 很长，截断会导致解析失败。
#   FORGE_AI_NO_PROXY=1       忽略 http(s)_proxy 直连。urllib 默认会读环境代理，
#                             服务若继承了不通的代理（内网/残留），表现就是一直等到超时。
AI_HINT = (
    "未配置视觉模型。要开启 AI 识别，请设置环境变量：\n"
    "  FORGE_AI_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1\n"
    "  FORGE_AI_KEY=<你的 DashScope API Key>\n"
    "  FORGE_AI_MODEL=qwen-vl-max\n"
    "（推荐 Qwen2.5-VL；也可填任意 OpenAI 兼容端点的 base/key/model）\n"
    "可选：FORGE_AI_TIMEOUT（秒，默认 %d）/ FORGE_AI_MAX_TOKENS（默认 %d）/ FORGE_AI_NO_PROXY=1\n"
    "配好后点右侧「自检」可以逐步看通道到底卡在哪。" % (DEFAULT_TIMEOUT, DEFAULT_MAX_TOKENS)
)


def extract_json(text):
    """模型常把 JSON 包在 ```json 里，或前后带说明文字 —— 取第一个完整对象。"""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e <= s:
        raise ValueError("模型没有返回 JSON")
    return json.loads(text[s:e + 1])


class AiFail(Exception):
    """带分类与建议的模型调用失败 —— 日志要能自己说清是「哪一种不通」。"""

    def __init__(self, kind, msg, advice=""):
        Exception.__init__(self, msg)
        self.kind = kind
        self.advice = advice


def _proxy_for(url, conf):
    """本次请求实际会走的代理。

    urllib 默认读 http(s)_proxy：服务若继承了残留/不通的代理，表现就是「一直等，最后超时」，
    而错误信息里看不出跟代理有关。这里显式算出来，写进日志。
    """
    if conf["no_proxy"]:
        return None
    host = urllib.parse.urlsplit(url).hostname or ""
    if host in ("127.0.0.1", "localhost", "::1"):   # 本机自测不该走代理
        return None
    return urllib.request.getproxies().get(urllib.parse.urlsplit(url).scheme) or None


def _chat_once(url, body, conf, direct, timeout):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if direct else \
        urllib.request.build_opener()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + conf["key"],
                                          "User-Agent": "ForgeStudio/1.0"})
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _classify(exc, host, proxy, timeout, elapsed):
    """把底层网络异常翻成「照着就能排查」的一句 + 一句建议。

    urllib 会把它包成 URLError(reason=...)，所以 exc 和 reason 都要认。
    """
    detail = str(exc) or type(exc).__name__
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else None
    inner = reason if reason is not None else exc
    timed_out = (isinstance(inner, (TimeoutError, socket.timeout))
                 or isinstance(exc, (TimeoutError, socket.timeout))
                 or "timed out" in detail.lower())
    if timed_out:
        # 连上后把上限等满 = 读超时（模型太慢）；没握手成功 = 连接超时（网络/代理不通）
        if elapsed >= timeout - 0.5:
            return AiFail("读超时", "等模型返回超过上限 %ds（已等 %.0fs）——识图越复杂越慢" % (timeout, elapsed),
                          "把 FORGE_AI_TIMEOUT 调大（例：600）后重启服务；或先用小一点的截图试")
        return AiFail("连接超时", "%s 在 %.0fs 内没握手成功" % (host, elapsed),
                      ("当前环境代理 %s 可能不通；可设 FORGE_AI_NO_PROXY=1 直连" % proxy) if proxy
                      else "确认网关地址与网络可达性")
    if isinstance(inner, socket.gaierror):
        return AiFail("域名解析失败", host, "检查 FORGE_AI_BASE 拼写与本机 DNS/代理")
    if isinstance(inner, ConnectionRefusedError):
        return AiFail("连接被拒绝", host, "网关地址或端口不对，或服务没起来")
    return AiFail("网络异常", "%s：%s" % (type(exc).__name__, detail),
                  ("可试 FORGE_AI_NO_PROXY=1 绕开代理 %s" % proxy) if proxy else "把原文发给负责人一起看")


def _http_fail(e, meta):
    detail = ""
    try:
        detail = e.read().decode("utf-8", "replace")[:400]
    except Exception:
        pass
    advice = ("密钥不对，或密钥与服务地域不匹配（北京的 key 配了上海的 base）" if e.code in (401, 403)
              else "模型名可能不存在/未开通，或请求体不合网关要求" if e.code in (400, 404)
              else "触到配额或并发上限，稍后重试" if e.code == 429
              else "网关自己出错，看下面的原文")
    return 502, {"error": "模型接口返回 HTTP %d：%s" % (e.code, detail or "(无内容)"),
                 "advice": advice, "ai": dict(meta, kind="HTTP", elapsed=None, channel="HTTP")}


def _ai_fail(fail, meta, channel, elapsed):
    return 502, {"error": "模型接口不可达（%s）：%s" % (fail.kind, fail),
                 "advice": fail.advice,
                 "ai": dict(meta, kind=fail.kind, elapsed=round(elapsed, 1), channel=channel)}


def classify_confidence(extract, threshold=CONFIDENCE_THRESHOLD):
    """S6：从 screenshot 契约里挑出低置信度元素，供前端概览 + 高亮。

    递归遍历 elements（含 children），挑出 confidence < threshold 的元素；
    关联 uncertain 条目（按 elementId 匹配 why）。返回：
      {"lowConfidence": [{id,type,label,text,confidence,source,bbox,reasons[]}], "count": N, "threshold": T}
    无 confidence 字段的元素（旧契约）不判低置信度，不误报。
    """
    low = []
    els = extract.get("elements") or []
    # 先建 uncertain 的 elementId → reasons 索引（兼容字符串与结构化两种形式）
    un_map = {}
    for u in extract.get("uncertain") or []:
        if isinstance(u, dict) and u.get("elementId"):
            un_map.setdefault(u["elementId"], []).append(u.get("why") or u.get("field") or "")
        elif isinstance(u, str):
            un_map.setdefault("*", []).append(u)

    def walk(nodes):
        for e in nodes:
            if not isinstance(e, dict):
                continue
            conf = e.get("confidence")
            # 判定是否低置信度：① 非法值（非数字 / bool / 越界）一律算低，避免脏数据漏报；
            # ② 合法值但 < threshold。无 confidence 字段（旧契约）不算低、不误报。
            bad = conf is not None and (not isinstance(conf, (int, float)) or isinstance(conf, bool)
                                        or conf < 0 or conf > 1)
            is_low = bad or (conf is not None and conf < threshold)
            if is_low:
                eid = e.get("id") or ""
                low.append({
                    "id": eid,
                    "type": e.get("type"),
                    "label": e.get("label"),
                    "text": e.get("text"),
                    "confidence": conf,
                    "source": e.get("source"),
                    "bbox": e.get("bbox"),
                    "reasons": list(un_map.get(eid) or un_map.get("*") or []),
                })
            walk(e.get("children") or [])

    walk(els)
    return {"lowConfidence": low, "count": len(low), "threshold": threshold}


def _chat_prompt(conf, image_data_uri, prompt, extra_ctx=""):
    """发一次「提示词 + 图」请求，返回模型原文 content。

    两段式与单段共用的底层：负责拼 body、走代理/直连、错误分类与代理重试。
    失败抛 AiFail（带 kind/advice）或 urllib.error.HTTPError，由上层决定降级或回传。
    """
    if extra_ctx:
        prompt = prompt + "\n\n" + extra_ctx
    url = conf["base"] + "/chat/completions"
    body = json.dumps({
        "model": conf["model"], "temperature": 0,
        "max_tokens": conf["max_tokens"],
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_data_uri}},
        ]}],
    }, ensure_ascii=False).encode("utf-8")
    proxy = _proxy_for(url, conf)
    timeout = conf["timeout"]
    t0 = time.time()
    try:
        resp = _chat_once(url, body, conf, direct=(proxy is None), timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except Exception as e:
        fail = _classify(e, urllib.parse.urlsplit(url).hostname or "", proxy, timeout, time.time() - t0)
        if proxy:   # 代理不通：自动改直连再试一次
            try:
                resp = _chat_once(url, body, conf, direct=True, timeout=timeout)
            except urllib.error.HTTPError:
                raise
            except Exception as e2:
                raise AiFail(fail.kind, "代理失败后直连重试仍失败：%s" % e2,
                             _classify(e2, urllib.parse.urlsplit(url).hostname or "", None, timeout, 0).advice)
        else:
            raise fail
    content = resp["choices"][0]["message"]["content"]
    return content


def ai_analyze(image_data_uri, hint="", mode="extract"):
    conf = ai_conf()
    if not conf:
        return 503, {"error": AI_HINT}
    if not (image_data_uri or "").startswith("data:image/"):
        return 400, {"error": "image 需为 png/jpeg/webp 的 data URI"}
    # 两段式：先区域清单、后逐区回填（任一步失败自动降级单段）
    if mode == "screenshot":
        try:
            code, body = ai_analyze_two_pass(conf, image_data_uri, hint)
            if code == 200:
                return code, body
        except Exception:
            pass  # 两段式失败 → 降级单段，继续往下走
    # mode=screenshot → 走独立的「截图结构」提示词（返回 kind:screenshot，不套 A1–A6）
    prompt_path = SCREENSHOT_PROMPT_PATH if mode == "screenshot" else PROMPT_PATH
    if not os.path.exists(prompt_path):
        return 500, {"error": "缺少提示词文件: " + prompt_path}
    with open(prompt_path, encoding="utf-8") as f:
        prompt = f.read()
    if hint:
        prompt += "\n\n## 用户补充\n" + hint[:2000]
    url = conf["base"] + "/chat/completions"
    host = urllib.parse.urlsplit(url).hostname or conf["base"]
    meta = {"host": host, "model": conf["model"], "timeout": conf["timeout"],
            "proxy": _proxy_for(url, conf), "payloadMB": 0.0}
    used = "直连" if not meta["proxy"] else ("代理 " + meta["proxy"])
    t0 = time.time()
    try:
        content = _chat_prompt(conf, image_data_uri, prompt)
    except urllib.error.HTTPError as e:
        return _http_fail(e, meta)
    except AiFail as fail:
        return _ai_fail(fail, meta, used, time.time() - t0)
    elapsed = time.time() - t0
    try:
        ext = extract_json(content)
        ai_meta = dict(meta, kind="ok", elapsed=round(elapsed, 1), channel=used)
        if mode == "screenshot":
            ai_meta["conf"] = classify_confidence(ext)
        return 200, {"extract": ext, "model": conf["model"], "ai": ai_meta}
    except Exception as e:
        return 502, {"error": "模型有输出，但不是可用的 JSON：%s" % e,
                     "advice": ("输出可能被 max_tokens 截断，把 FORGE_AI_MAX_TOKENS 调大（例：8192）"
                                if len(content) > 3500 else "确认模型是视觉模型、且遵循 studio/ai-prompt.md 的输出要求"),
                     "ai": dict(meta, kind="parse", elapsed=round(elapsed, 1),
                                channel=used, chars=len(content))}


def ai_analyze_two_pass(conf, image_data_uri, hint=""):
    """两段式截图识别：阶段 A 出区域清单，阶段 B 逐区回填，合并成一份 screenshot 契约。

    任一步失败抛异常或返回非 200，由 ai_analyze 捕获后降级单段。
    S4：阶段 B 逐区时先按区域 bbox 裁剪放大（img_crop.crop_region，可选依赖 Pillow），
    拿到裁剪图就用它读字；拿不到（未装 Pillow / 非图片 / 解码失败）自动退回整图，
    把「整图 + 该区域清单片段」一起发给模型按 bbox 定位 —— 即 S3 现状，链路不断。
    """
    if not os.path.exists(SCREENSHOT_REGIONS_PROMPT_PATH):
        raise AiFail("缺文件", "缺少阶段 A 提示词: " + SCREENSHOT_REGIONS_PROMPT_PATH)
    with open(SCREENSHOT_REGIONS_PROMPT_PATH, encoding="utf-8") as f:
        prompt_a = f.read()
    if hint:
        prompt_a += "\n\n## 用户补充\n" + hint[:2000]

    # S5：像素先验（页面锚点 + 列线/区域框）。算不出（无 Pillow/非图片）→ prior=None，链路照常。
    prior = None
    try:
        prior = pixel_prior.prior_from_image(image_data_uri)
    except Exception:
        prior = None
    prior_anchor = pixel_prior.anchor_text(prior) if prior else ""

    # 阶段 A：区域清单（注入像素锚点，帮模型对齐坐标系）
    content_a = _chat_prompt(conf, image_data_uri, prompt_a, extra_ctx=prior_anchor)
    regions_obj = extract_json(content_a)
    if regions_obj.get("kind") != "region-list":
        raise AiFail("bad_region_list", "阶段 A 未返回 region-list")
    regions = regions_obj.get("regions") or []
    if not regions:
        raise AiFail("empty_regions", "阶段 A 返回空区域清单")

    with open(SCREENSHOT_PROMPT_PATH, encoding="utf-8") as f:
        prompt_b = f.read()

    # 阶段 B：逐区回填
    all_elements = []
    all_uncertain = []
    page = regions_obj.get("page") or {}
    canvas = regions_obj.get("canvas") or {}
    cropped_count = 0
    for r in regions:
        # S4：先尝试按区域 bbox 裁剪放大；拿不到就退回整图（S3 现状）
        region_img = image_data_uri
        bbox = r.get("bbox")
        if bbox:
            cropped = img_crop.crop_region(image_data_uri, bbox)
            if cropped:
                region_img = cropped
                cropped_count += 1
        ctx = ("\n\n## 区域清单（你正在识别其中一个区域）\n" + json.dumps({
            "kind": "region-list", "canvas": canvas, "page": page,
            "regions": [r]}, ensure_ascii=False))
        if region_img is not image_data_uri:
            # 输入是裁剪放大图：提醒模型坐标仍按整图左上角报，别按裁剪图局部报
            ctx += ("\n\n注意：当前输入是该区域的**裁剪放大图**，"
                    "但你输出的 bbox 仍必须按**整图左上角**为原点换算，不要按裁剪图局部坐标报。")
        content_b = _chat_prompt(conf, region_img, prompt_b, extra_ctx=ctx)
        part = extract_json(content_b)
        els = part.get("elements") or []
        for e in els:
            e.setdefault("confidence", r.get("confidence"))
        all_elements.extend(els)
        all_uncertain.extend(part.get("uncertain") or [])

    merged = {
        "kind": "screenshot",
        "version": "screenshot-1.0",
        "source": {"model": conf["model"], "note": "AI 语义视觉识别·两段式（先区域清单后逐区回填），结构与样式按原图，交互与业务内容待人工确认"},
        "canvas": canvas,
        "page": page,
        "elements": all_elements,
        "uncertain": all_uncertain,
    }
    return 200, {"extract": merged, "model": conf["model"],
                 "ai": {"kind": "ok", "twoPass": True, "regions": len(regions),
                        "elements": len(all_elements), "cropped": cropped_count,
                        "prior": bool(prior), "model": conf["model"],
                        "conf": classify_confidence(merged)}}


def ai_selftest(deep=False):
    """模型通道自检：一步步报，省得靠猜（对应界面上的「自检」按钮）。

    浅检只碰网络与鉴权；deep=True 真调一次模型（极短输出），用来分辨
    「网络不通」和「模型名/网关不对」——这两者的表象都是 AI 识别失败。
    """
    conf = ai_conf()
    steps = []

    def add(name, ok, ms, detail):
        steps.append({"step": name, "ok": bool(ok), "ms": int(ms), "detail": detail})

    if not conf:
        return 503, {"ok": False, "steps": steps, "error": AI_HINT}

    if conf.get("host_bridge"):
        add("DSH 模型路由", True, 0, "使用 DSH 已配置模型：%s；私人密钥由宿主管理" % conf["model"])
        if not deep:
            return 200, {"ok": True, "steps": steps,
                         "verdict": "宿主模型桥接已配置；此项未调用远端模型，点击深度自检可验证真实连通性。"}
        t0 = time.time()
        body = json.dumps({"messages": [{"role": "user", "content": "Reply only OK"}], "max_tokens": 256}).encode("utf-8")
        try:
            _chat_once(conf["base"] + "/chat/completions", body, conf, direct=True, timeout=conf["timeout"])
            add("宿主模型调用", True, (time.time() - t0) * 1000, "DSH 模型已返回内容")
            return 200, {"ok": True, "steps": steps, "verdict": "宿主模型文本调用成功；图片能力仍需识图验证。"}
        except Exception:
            add("宿主模型调用", False, (time.time() - t0) * 1000, "请检查 DSH 模型配置、网络或提供方服务")
            return 200, {"ok": False, "steps": steps, "verdict": "宿主模型调用失败；未读取或展示私人凭据。"}

    url = conf["base"] + "/chat/completions"
    parts = urllib.parse.urlsplit(conf["base"])
    host = parts.hostname or "?"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    add("配置", True, 0, "base=%s · model=%s · 密钥已设置（%d 字符）· 超时上限 %ds"
        % (conf["base"], conf["model"], len(conf["key"]), conf["timeout"]))

    t0 = time.time()
    try:
        ip = socket.gethostbyname(host)
        add("DNS 解析", True, (time.time() - t0) * 1000, "%s → %s" % (host, ip))
    except Exception as e:
        add("DNS 解析", False, (time.time() - t0) * 1000, "%s：%s" % (host, e))
        return 200, {"ok": False, "steps": steps,
                     "verdict": "域名解析不出来：先核对 FORGE_AI_BASE 拼写，再查本机 DNS / 代理"}

    t0 = time.time()
    try:
        sk = socket.create_connection((host, port), timeout=min(conf["timeout"], 15))
        sk.close()
        add("TCP 连接", True, (time.time() - t0) * 1000, "%s:%d 可连" % (host, port))
    except Exception as e:
        add("TCP 连接", False, (time.time() - t0) * 1000, "%s:%d 连不上：%s" % (host, port, e))
        return 200, {"ok": False, "steps": steps,
                     "verdict": "端口不通：网关没起来 / 被防火墙或代理挡住（代理场景可设 FORGE_AI_NO_PROXY=1 再试）"}

    proxy = _proxy_for(url, conf)
    add("通道", True, 0, ("走环境代理 " + proxy) if proxy else "直连（未使用环境代理）")

    # 鉴权探测：/models 是最便宜的鉴权请求。有的网关没实现，404 不算错。
    t0 = time.time()
    try:
        req = urllib.request.Request(parts.scheme + "://" + parts.netloc + "/models",
                                     headers={"Authorization": "Bearer " + conf["key"]})
        op = urllib.request.build_opener() if proxy else \
            urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(req, timeout=min(conf["timeout"], 30)) as r:
            add("鉴权探测", True, (time.time() - t0) * 1000, "HTTP 200：密钥可用")
    except urllib.error.HTTPError as e:
        note = ("该网关未实现 /models，不代表不能用" if e.code == 404
                else "密钥不对，或与 base 地域不匹配" if e.code in (401, 403) else "看原文")
        add("鉴权探测", e.code == 404, (time.time() - t0) * 1000, "HTTP %d：%s" % (e.code, note))
    except Exception as e:
        t = time.time() - t0
        fail = _classify(e, host, proxy, min(conf["timeout"], 30), t)
        add("鉴权探测", False, t * 1000, str(fail))
        return 200, {"ok": False, "steps": steps, "verdict": fail.advice or str(fail)}

    if not deep:
        return 200, {"ok": True, "steps": steps,
                     "verdict": "通道是通的。若「AI 识别」仍失败，多半是模型响应慢超过 %ds：把 FORGE_AI_TIMEOUT 调大后重启。" % conf["timeout"]}

    # 深检：真调一次模型，极短输出，用来分辨「网络不通」和「模型名/网关不对」
    t0 = time.time()
    body = json.dumps({"model": conf["model"], "max_tokens": 8,
                       "messages": [{"role": "user", "content": "ping"}]}).encode("utf-8")
    try:
        resp = _chat_once(url, body, conf, direct=(proxy is None), timeout=conf["timeout"])
        txt = ""
        try:
            txt = (resp["choices"][0]["message"]["content"] or "").strip()[:40]
        except Exception:
            pass
        add("模型调用", True, (time.time() - t0) * 1000, "模型回了「%s」" % txt)
        return 200, {"ok": True, "steps": steps,
                     "verdict": "模型可用（超时上限 %ds）。识图比这慢得多，失败时先调大 FORGE_AI_TIMEOUT。" % conf["timeout"]}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        add("模型调用", False, (time.time() - t0) * 1000, "HTTP %d：%s" % (e.code, detail or "(无内容)"))
        return 200, {"ok": False, "steps": steps,
                     "verdict": ("密钥或模型名不被接受" if e.code in (400, 401, 403, 404) else "网关出错，看这一步的原文")
                     + "：HTTP %d" % e.code}
    except Exception as e:
        t = time.time() - t0
        fail = _classify(e, host, proxy, conf["timeout"], t)
        add("模型调用", False, t * 1000, str(fail))
        return 200, {"ok": False, "steps": steps, "verdict": fail.advice or str(fail)}


def _run(args, timeout=300):
    # 解码交给 studio_api.run_tool：中文 Windows 上子进程默认写 cp936，
    # 按 utf-8 + errors=replace 读会把中文砸成 U+FFFD（日志里全是「������д��:」）。
    return studio_api.run_tool(args, timeout=timeout)


AXURE_MARK = os.path.join("data", "document.js")
AXURE_SAMPLE = os.path.join(HERE, "axure", "sample-prototype")


def axure_dir(raw):
    """校验用户给的是不是 Axure「生成 HTML 原型」的根目录。

    用目录路径而不是上传整个文件夹：一个真实原型的导出有几百个文件、几十 MB，
    走浏览器上传既慢又容易丢资源；本服务只在本机跑，直接读磁盘最稳。
    """
    d = (raw or "").strip().strip('"').strip("'")
    if not d:
        return None, "请填写 Axure 导出目录"
    d = os.path.abspath(os.path.expanduser(d))
    if not os.path.isdir(d):
        return None, "目录不存在：%s" % d
    if not os.path.exists(os.path.join(d, AXURE_MARK)):
        return None, ("该目录下没有 data/document.js —— 请选 Axure 发布的 HTML 原型根目录"
                      "（里面应有 index.html / start.html / data/ / files/）")
    return d, None


def axure_pages(raw_dir):
    """从提取产物的 sitemap.json 里读页面清单（name 用于定位，label 给人看）。"""
    p = os.path.join(raw_dir, "sitemap.json")
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            sm = json.load(f)
    except (ValueError, OSError):
        return []
    out = []
    for it in sm.get("pages") or []:
        name = str(it.get("url") or "").replace(".html", "")
        if name:
            out.append({"name": name, "label": it.get("pageName") or name})
    return out


def axure_pipeline(payload):
    """Axure HTML 导出目录 → PageSpec → 页面 → 四类断言 → 报告。

    与设计图入口的差别：字段/列/动作/状态来自产品经理写在 Axure 里的标注文字，
    不是从像素猜的，所以这一路不需要人工拆层标注。
    """
    src, err = axure_dir(payload.get("dir"))
    if err:
        return 400, {"error": err}
    page = (payload.get("page") or "").strip()
    if page and not SAFE_NAME.match(page):
        return 400, {"error": "页面名只能含字母、数字、. _ -"}
    theme = payload.get("theme") or "premium"
    if theme not in ("default", "premium"):
        return 400, {"error": "theme 只支持 default / premium"}

    try:
        run_id, rdir, wid = studio_store.new_run("ax", payload.get("workspace"))
    except ValueError as e:
        return 400, {"error": str(e)}
    raw = os.path.join(rdir, "raw")
    os.makedirs(raw, exist_ok=True)
    p_spec, p_html = os.path.join(rdir, "pagespec.json"), os.path.join(rdir, "page.html")
    p_acc, p_rep = os.path.join(rdir, "acceptance.json"), os.path.join(rdir, "report.html")

    steps = []

    def step(name, args, must=True, timeout=300):
        code, log = _run(args, timeout=timeout)
        steps.append({"step": name, "ok": code == 0, "log": log[-4000:]})
        return code == 0 or not must

    ax = ["build/axure.py", "--src", src, "--out", p_spec, "--raw-dir", raw]
    if page:
        ax += ["--page", page]
    ok = step("读 Axure 标注 axure.py", ax, timeout=600)
    pages = axure_pages(raw)
    if not ok:
        studio_api.record(run_id, rdir, "axure", False, steps=steps)
        return 422, {"ok": False, "run": run_id, "workspace": wid, "steps": steps, "pages": pages}

    with open(p_spec, encoding="utf-8") as f:
        spec = json.load(f)
    status, body = studio_api.run_pipeline(spec, theme=theme, source="axure",
        extra_steps=steps, run=(run_id, rdir), workspace=wid)
    body["pages"] = pages
    body["source"] = {"dir": src, "page": spec.get("_source", {}).get("page"),
        "pageName": spec.get("page", {}).get("name"), "archetype": spec.get("archetype"),
        "fields": len(spec.get("fields") or []), "columns": len(spec.get("columns") or [])}
    return status, body


class Handler(BaseHTTPRequestHandler):
    server_version = "ForgeStudio/1.0"

    def log_message(self, fmt, *a):
        sys.stderr.write("[studio] %s\n" % (fmt % a))

    def _send(self, code, body, ctype="application/json; charset=utf-8", download=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if download:
            # RFC 6266：filename* 支持非 ASCII；download 已过 SAFE_NAME 白名单
            self.send_header("Content-Disposition", "attachment; filename=\"%s\"; filename*=UTF-8''%s"
                             % (download, urllib.parse.quote(download)))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path):
        types = {".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8",
                 ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp",
                 ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".md": "text/plain; charset=utf-8"}
        if not os.path.isfile(path):
            return self._send(404, {"error": "not found"})
        with open(path, "rb") as f:
            self._send(200, f.read(), types.get(os.path.splitext(path)[1].lower(), "application/octet-stream"))

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._file(os.path.join(STUDIO, "index.html"))
        if path.startswith("/api/sample/"):
            # 内置示例：截图 + 已校对的 extract，让没配模型的人也能一键跑通全链路
            name = path.rsplit("/", 1)[1]
            samples = {"extA": ("input-extA.png", "extA-wms-inout.json"),
                       "extB": ("input-extB.png", "extB-crm-customer.json"),
                       # extC：A2 单据表单（表单卡 + 明细表格）本地回归样本，仅 check_semantic 用，不进 UI 轮播
                       "extC": ("input-extC.png", "extC-purchase-request.json")}
            if name not in samples:
                return self._send(404, {"error": "no such sample"})
            png, ex = samples[name]
            if not all(os.path.isfile(os.path.join(HERE, "vision", folder, file)) for folder, file in (("shots", png), ("extracts", ex))):
                return self._send(404, {"error": "Screenshot fixtures are not distributed in the public source profile; upload your own image."})
            with open(os.path.join(HERE, "vision", "shots", png), "rb") as f:
                uri = "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
            with open(os.path.join(HERE, "vision", "extracts", ex), encoding="utf-8") as f:
                extract = json.load(f)
            return self._send(200, {"image": uri, "extract": extract})
        if path == "/api/axure/sample":
            # 内置的 Axure 导出样例目录，方便没有原型的人一键体验
            return self._send(200, {"dir": AXURE_SAMPLE})
        if path == "/api/sample/ssform":
            # 截图独立还原样例：表单 + 两张表 + 同一张占位原图（不上传外网）
            p = os.path.join(HERE, "vision", "screenshot-samples", "form-two-tables.json")
            if not os.path.exists(p):
                return self._send(404, {"error": "no such sample"})
            with open(p, encoding="utf-8") as f:
                spec = json.load(f)
            png = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
                   "AAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
            return self._send(200, {"image": png, "spec": spec})
        if path == "/api/web/status":
            return self._send(200, web_capture.status())
        if path == "/api/ai/status":
            c = ai_conf()
            return self._send(200, {"configured": bool(c),
                                    "model": c["model"] if c else None,
                                    "base": c["base"] if c else None,
                                    "timeout": c["timeout"] if c else None})
        if path == "/api/ai/selftest":
            q = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            deep = (q.get("deep", ["0"])[0] or "0").lower() not in ("", "0", "false", "no")
            return self._send(*ai_selftest(deep))
        if path == "/api/templates":
            return self._send(200, {"templates": studio_api.list_templates()})
        # 自由版式扩展 GET 路由（/api/ff/* 与 /fflib/<id>/<fn>）
        if _ff_routes is not None:
            if path.startswith("/api/ff/"):
                r = _ff_routes.get_route(path)
                if r is not None:
                    return self._send(r[0], r[1])
            fparts = path.strip("/").split("/")
            if fparts and fparts[0] == "fflib":
                fr = _ff_routes.get_file(fparts)
                if fr is not None:
                    return self._file(fr[1])
        qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
        q1 = lambda k: (qs.get(k) or [""])[0]
        if path == "/api/runs":
            ws = q1("workspace") or None
            if ws and not SAFE_NAME.match(ws):
                return self._send(400, {"error": "workspace 不合法"})
            return self._send(200, {"runs": studio_api.list_runs(
                limit=min(500, int(q1("limit") or 200) if q1("limit").isdigit() else 200),
                workspace=ws, status=q1("status") or None, q=q1("q")[:60] or None)})
        if path == "/api/workspaces":
            return self._send(200, {"workspaces": studio_store.list_workspaces()})
        parts = path.strip("/").split("/")
        # /api/runs/<run>/zip[?all=1] —— 打包下载；默认只含可交付文件，all=1 连同素材
        if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "zip" and SAFE_NAME.match(parts[2]):
            data, name = studio_store.zip_run(parts[2], deliverable_only=q1("all") != "1")
            if not data:
                return self._send(404, {"error": "运行不存在"})
            return self._send(200, data, "application/zip", download=name)
        # /api/runs/<run>/file/<fn> —— 单文件下载（附件形式）
        if len(parts) == 5 and parts[:2] == ["api", "runs"] and parts[3] == "file" \
                and SAFE_NAME.match(parts[2]) and SAFE_NAME.match(parts[4]):
            rdir, _ = studio_store.find_run(parts[2])
            p = os.path.join(rdir, parts[4]) if rdir else None
            if not p or not os.path.isfile(p):
                return self._send(404, {"error": "文件不存在"})
            with open(p, "rb") as f:
                return self._send(200, f.read(), "application/octet-stream", download=parts[4])
        # /tpl/<file> —— 内置 A1–A6 示例页（模板预览）
        if len(parts) == 2 and parts[0] == "tpl" and SAFE_NAME.match(parts[1]):
            return self._file(os.path.join(studio_api.TPL_DIR, parts[1]))
        # /lib/<tpl-id>/<file> —— 自定义模板库
        if len(parts) == 3 and parts[0] == "lib" and all(SAFE_NAME.match(p) for p in parts[1:]):
            return self._file(os.path.join(studio_api.LIB, parts[1], parts[2]))
        # /out/<run>/<file> —— 逐段白名单，杜绝路径穿越
        if len(parts) == 3 and parts[0] == "out" and all(SAFE_NAME.match(p) for p in parts[1:]):
            rdir, _ = studio_store.find_run(parts[1])
            return self._file(os.path.join(rdir, parts[2]) if rdir else "")
        if len(parts) == 2 and parts[0] == "studio" and SAFE_NAME.match(parts[1]):
            return self._file(os.path.join(STUDIO, parts[1]))
        self._send(404, {"error": "not found"})

    def do_POST(self):
        # 只接受同源请求：防止别的网页借浏览器打本机接口（CSRF / DNS rebinding）
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if not host.startswith("127.0.0.1:") or (origin and origin != "http://" + host):
            return self._send(403, {"error": "forbidden"})
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            return self._send(413, {"error": "请求体为空或超过 25MB"})
        try:
            payload = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return self._send(400, {"error": "请求体不是 JSON"})
        if self.path == "/api/ai/analyze":
            code, body = ai_analyze(payload.get("image") or "", payload.get("hint") or "",
                                    payload.get("mode") or "extract")
            return self._send(code, body)
        if self.path == "/api/axure":
            code, body = axure_pipeline(payload)
            return self._send(code, body)
        routes = {
            "/api/web/capture": web_routes.capture,
            "/api/web/restore": lambda p: web_routes.restore(p, ai_conf(), _chat_prompt),
            "/api/parse": lambda p: _parse_preview(p),                    # 只解析指令，不生成
            "/api/forge/command": studio_api.forge_from_command,          # 入口 ①：模板 + 指令
            "/api/forge/image": studio_api.forge_from_image,              # 入口 ②：设计图（A1–A6 内置链路）
            "/api/forge": studio_api.forge_from_image,                    # 旧路径兼容
            "/api/forge/screenshot": studio_api.forge_from_screenshot,    # 入口 ②b：截图独立还原（kind:screenshot）
            "/api/forge/html": studio_api.forge_from_html,                # 入口 ③：HTML 文件/文件夹
            "/api/templates/save": lambda p: studio_api.save_template(p.get("run"), p.get("name")),
            "/api/templates/delete": lambda p: studio_api.delete_template(p.get("id")),
            "/api/workspaces/create": lambda p: studio_store.create_workspace(p.get("name")),
            "/api/workspaces/rename": lambda p: studio_store.rename_workspace(p.get("id"), p.get("name")),
            "/api/workspaces/delete": lambda p: studio_store.delete_workspace(p.get("id")),
            "/api/runs/delete": lambda p: studio_store.delete_runs(p.get("runs"))
            if isinstance(p.get("runs"), list) else studio_store.delete_run(p.get("run")),
        }
        # 自由版式扩展 POST 路由（/api/ff/*）
        if _ff_routes is not None:
            routes.update(getattr(_ff_routes, "POST", {}) or {})
        fn = routes.get(self.path)
        if not fn:
            return self._send(404, {"error": "not found"})
        try:
            code, body = fn(payload)
        except Exception as e:  # 业务层异常不让服务挂掉
            code, body = 500, {"error": "%s: %s" % (type(e).__name__, e)}
        self._send(code, body)


def _parse_preview(p):
    code, r = studio_api.parse_command(p.get("command"), p.get("templateId"))
    if code == 200:
        r.pop("spec", None)
    return code, r


def main():
    ap = argparse.ArgumentParser(description="Forge Studio 本地服务（仅 127.0.0.1）")
    ap.add_argument("--port", type=int, default=3060)
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    class StudioHTTPServer(ThreadingHTTPServer):
        allow_reuse_address = False

        def server_bind(self):
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

    httpd = StudioHTTPServer(("127.0.0.1", args.port), Handler)
    c = ai_conf()
    print("Forge Studio: http://127.0.0.1:%d/" % httpd.server_port, flush=True)
    print("外部视觉模型：%s" % ("已配置（%s，超时上限 %ds，点界面「自检」可逐步排查）" % (c["model"], c["timeout"])
                            if c else "未配置 → 走本地拆层 + 手工标注"))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
