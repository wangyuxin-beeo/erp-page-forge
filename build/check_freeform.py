#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自由版式扩展端到端回归（ff_* 全套：契约 / 三入场 / 确定性 / 语义断言 / 模板库）。

覆盖：
  1. FreeformSpec 校验：合法通过、非法（kind/target 悬空/空选择器/文字 crop）卡住
  2. 手写 spec 生成：确定性（3 次一致）、rebuild 区块带 id、文字是真文字
  3. 语义断言：契约 + toggle 交互 + count-equals 数据（复用 driver 引擎真实点击）
  4. 方案 C 自动识别：危险按钮自动填 confirms，其余进 todos
  5. 图片入场：ff_image（用内置样本 extA 或既有失败截图）
  6. HTML 入场：ff_html 净化 + 插槽替换
  7. 后端路由：/api/ff/* POST + /fflib GET + 指令复用（存模板 → 用模板名生成）
  8. 既有回归不受影响：A1–A6 模板列表仍完整（抽检）

用法：python build/check_freeform.py
"""

import base64
import io
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
sys.path.insert(0, BUILD)
sys.path.insert(0, os.path.join(BUILD, "ext", "freeform"))

import ff_spec
import ff_forge
import ff_html

PORT = 3077
B = "http://127.0.0.1:%d" % PORT
fails = 0


def chk(name, cond, extra=""):
    global fails
    fails += not cond
    print("  %s %s %s" % ("PASS" if cond else "FAIL", name, extra))


def post(p, o):
    req = urllib.request.Request(B + p, json.dumps(o).encode(),
                                 {"Content-Type": "application/json", "Origin": B})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def get(p):
    with urllib.request.urlopen(B + p, timeout=600) as r:
        return r.status, r.read()


# --------------------------------------------------------------------------
# 手工 spec（空状态 + 语义）
# --------------------------------------------------------------------------
def hand_spec():
    return {
        "kind": "freeform", "specVersion": "ff-1.0", "name": "采购订单空状态页",
        "source": {"type": "hand"},
        "canvas": {"w": 1440, "h": 900, "bg": "#F7F8FA"},
        "tokens": {"mode": "skill-default", "values": {}},
        "regions": [
            {"id": "r1", "role": "card", "keep": "rebuild", "bbox": [480, 240, 960, 700],
             "style": {"bg": "#FFFFFF", "radius": 12}},
            {"id": "r2", "role": "text", "keep": "rebuild", "bbox": [560, 380, 880, 430],
             "slot": "title", "text": "暂无数据", "style": {"fontSize": 28, "align": "center", "bold": True}},
            {"id": "r3", "role": "text", "keep": "rebuild", "bbox": [540, 440, 900, 470],
             "text": "点击下方按钮打开导入面板", "style": {"fontSize": 14, "align": "center"}},
            {"id": "r4", "role": "button", "keep": "rebuild", "bbox": [650, 500, 790, 540],
             "text": "批量导入", "style": {"fontSize": 14}},
            {"id": "r5", "role": "card", "keep": "rebuild", "bbox": [520, 560, 920, 660],
             "text": "支持 CSV / Excel 模板批量导入", "style": {"bg": "#EEF3FF", "fontSize": 14}},
        ],
        "slots": {"title": "暂无数据"},
        "semantics": {
            "page": {"purpose": "采购订单空状态页", "entities": ["采购订单"]},
            "interactions": [{"id": "i1", "trigger": "r4", "type": "toggle", "target": "r5",
                              "desc": "点击批量导入切换导入面板显隐", "from": "user"}],
            "data": [{"id": "d1", "type": "count-equals", "a": ".ff-button",
                      "b": "[data-ff-region='r4']", "desc": "按钮容器数 = 批量导入区块数", "from": "user"}],
        },
        "acceptance": {"minTextLen": 30},
    }


def main():
    print("== 1. FreeformSpec 契约校验")
    spec = hand_spec()
    errs, warns = ff_spec.validate(spec)
    chk("合法 spec 零错误", not errs, ";".join(errs[:2]))

    bad = json.loads(json.dumps(spec)); bad["kind"] = "page"
    chk("非法 kind 被卡", bool(ff_spec.validate(bad)[0]))
    bad = json.loads(json.dumps(spec)); bad["regions"][2]["keep"] = "crop"
    chk("文字区块 crop 被卡", bool(ff_spec.validate(bad)[0]))
    bad = json.loads(json.dumps(spec)); bad["semantics"]["interactions"][0]["target"] = "r999"
    chk("交互 target 悬空被卡", bool(ff_spec.validate(bad)[0]))
    bad = json.loads(json.dumps(spec)); bad["semantics"]["data"][0]["a"] = ""
    chk("数据断言空选择器被卡", bool(ff_spec.validate(bad)[0]))

    print("== 2. 确定性生成 + 语义兑现")
    h1 = ff_forge.build_html(spec)
    h2 = ff_forge.build_html(spec)
    h3 = ff_forge.build_html(spec)
    chk("3 次生成 md5 一致", h1 == h2 == h3)
    chk("区块输出 id（onclick 目标可达）", 'id="r4"' in h1 and 'id="r5"' in h1)
    chk("toggle 目标初始 hidden", 'data-ff-region="r5"' in h1 and 'hidden' in h1)
    chk("按钮渲染为真文字", "批量导入" in h1)

    print("== 3. 方案 C 自动识别")
    spec_nosem = json.loads(json.dumps(spec))
    spec_nosem["semantics"] = {"page": {"purpose": "x"}}
    spec_nosem["regions"].append({"id": "r9", "role": "button", "keep": "rebuild",
                                  "bbox": [700, 700, 800, 740], "text": "删除", "style": {}})
    proposals, todos = ff_spec.auto_semantics(spec_nosem)
    chk("危险按钮自动填 confirms", any(i.get("type") == "confirms" for i in proposals.get("interactions", [])))

    print("== 4. 后端路由 + 模板库存取 + 指令复用")
    srv = subprocess.Popen([sys.executable, "build/studio_server.py", "--port", str(PORT)],
                           cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", PORT)) == 0:
                    break
            time.sleep(.3)

        # 模板列表：A1–A6 仍完整（既有回归不受影响）
        _, b = get("/api/templates")
        ts = json.loads(b)["templates"]
        bi = [t["id"] for t in ts if t["kind"] == "builtin"]
        chk("A1–A6 仍在内置模板", bi == ["A1", "A2", "A3", "A4", "A5", "A6"], ",".join(bi))

        # 手写 spec 生成
        c, r = post("/api/ff/generate", {"spec": spec})
        sm = r.get("summary") or {}
        chk("ff/generate 硬性全过", c == 200 and sm.get("hardFailed") == 0,
            "%s/%s %s" % (sm.get("passed"), sm.get("total"), sm.get("verdict")))
        run_id = r.get("run")

        # 存模板 → 列表出现 freeform 模板 → 指令复用
        c, r = post("/api/ff/templates/save", {"run": run_id, "name": "采购空状态"})
        chk("存自由版式模板", c == 200 and r.get("ok"))
        _, b = get("/api/ff/templates")
        fft = json.loads(b)["templates"]
        chk("ff 模板列表含新模板", any(t["kind"] == "freeform" and t["name"] == "采购空状态" for t in fft))

        c, r = post("/api/forge/command", {"command": "用「采购空状态」生成 物料空状态 title：暂无物料"})
        sm = r.get("summary") or {}
        chk("指令复用自由版式模板", c == 200 and r.get("kind") == "freeform"
            and sm.get("hardFailed") == 0, "%s" % (r.get("error") or sm.get("verdict")))

        # 未知 slot 报错
        c, r = post("/api/forge/command", {"command": "用「采购空状态」生成 nonexist：xxx"})
        chk("未知 slot 报错不静默", c != 200, str(r.get("error", ""))[:60])

        # 删除模板
        if fft:
            tid = next((t["id"] for t in fft if t["name"] == "采购空状态"), None)
            if tid:
                c, r = post("/api/ff/templates/delete", {"id": tid})
                chk("删除自由版式模板", c == 200 and r.get("ok"))
    finally:
        srv.terminate()

    print("== 5. 图片入场（内置样本或既有截图）")
    # 优先用失败那张落地页截图；不存在则用内置 extA 样本
    img_candidates = [
        os.path.join(BUILD, "studio-out", "default", "run-20261012-115043-927", "input.png"),
        os.path.join(BUILD, "vision", "shots", "input-extA.png"),
    ]
    img = next((p for p in img_candidates if os.path.isfile(p)), None)
    if img:
        sys.path.insert(0, os.path.join(BUILD, "ext", "freeform"))
        import ff_image
        try:
            fspec = ff_image.build_spec(img, {"name": "自由版式样本",
                                              "texts": ["标题示例"], "buttons": ["确认"]})
            chk("图片入场出 spec", fspec.get("kind") == "freeform"
                and fspec.get("regions") and fspec.get("canvas"), "区块 %d" % len(fspec.get("regions", [])))
            fhtml = ff_forge.build_html(fspec, img)
            chk("图片入场生成 HTML", "data-ff-region" in fhtml and len(fhtml) > 1000)
        except SystemExit as e:
            chk("图片入场出 spec", False, "SystemExit: %s" % e)
    else:
        chk("图片入场（跳过：无样本图）", True, "无样本图")

    print("== 6. HTML 入场（净化 + 插槽替换）")
    html_in = ('<!DOCTYPE html><html><body><h1 data-ff-slot="title">旧标题</h1>'
               '<p>正文</p><img src="https://evil.example/x.png"></body></html>')
    cleaned, notes = ff_html.sanitize(html_in)
    chk("HTML 净化去除外链", "evil.example" not in cleaned, "notes=%s" % notes[:1])
    rep, rep_map = ff_html.apply_slots(cleaned, {"title": "新标题"})
    chk("插槽替换生效", "新标题" in rep and "旧标题" not in rep, "替换 %d 处" % len(rep_map))

    print("== 7. 结果汇总")
    print("  %s" % ("全部通过" if fails == 0 else "%d 项失败" % fails))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
