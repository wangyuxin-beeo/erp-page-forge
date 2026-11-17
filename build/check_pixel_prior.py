# -*- coding: utf-8 -*-
"""pixel_prior 像素先验的回归（需要 Pillow，用 venv Python 跑）。

验证 S5a（页面锚点）+ S5b（列线/区域框）：
  · 页面级锚点：构造「深色侧栏 + 顶栏 + 主色按钮」图，断言 sidebarWidth/topbarHeight/primaryHex
  · 列线检测：构造带垂直表格线的图，断言 columnXs
  · 区域框检测：构造带卡片边框的图
  · anchor_text 把 prior 转成可注入提示词的中文
  · 降级：非 data URI / 坏输入返回 None

用法（需带 Pillow 的 venv）：
  <venv>/Scripts/python.exe build/check_pixel_prior.py
"""
import base64
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pixel_prior  # noqa: E402

fails = []


def chk(name, cond, extra=""):
    if not cond:
        fails.append(name)
    print("  %s %s %s" % ("PASS" if cond else "FAIL", name, extra))


def to_uri(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


from PIL import Image, ImageDraw

# —— 图 1：深色侧栏 216px + 顶栏 56px + 右侧浅色内容区 + 主色按钮 ——
W, H = 1600, 900
img1 = Image.new("RGB", (W, H), (245, 246, 248))          # 浅色内容区
d = ImageDraw.Draw(img1)
d.rectangle([0, 0, 216, H], fill=(34, 48, 77))            # 深色侧栏
d.rectangle([0, 0, W, 56], fill=(255, 255, 255))          # 白色顶栏
d.rectangle([300, 200, 500, 236], fill=(15, 118, 110))    # 主色按钮 #0F766E
uri1 = to_uri(img1)

print("== 页面级锚点（S5a）")
prior1 = pixel_prior.prior_from_image(uri1)
chk("prior 非空", prior1 is not None)
chk("画布 1600x900", prior1["canvas"] == {"w": 1600, "h": 900}, str(prior1["canvas"]))
chk("侧栏宽 ≈216（±6）", prior1["sidebarWidth"] is not None and abs(prior1["sidebarWidth"] - 216) <= 6,
    str(prior1["sidebarWidth"]))
chk("顶栏高 ≈56（±6）", prior1["topbarHeight"] is not None and abs(prior1["topbarHeight"] - 56) <= 6,
    str(prior1["topbarHeight"]))
chk("主色 #0F766E", prior1["primaryHex"] == "#0F766E", str(prior1["primaryHex"]))

print("== anchor_text 转提示词")
txt = pixel_prior.anchor_text(prior1)
chk("锚点文本含画布", "1600×900" in txt or "1600" in txt, txt[:80])
chk("锚点文本含侧栏", "侧栏" in txt or "导航" in txt, txt[:80])
chk("锚点文本含主色", "#0F766E" in txt, txt[:80])

# —— 图 2：带垂直表格线（列边界 x=300,400,500,600,700），横贯内容区 ——
# 加真实深色侧栏（216px）+ 顶栏，模拟真实 ERP 截图，避免列线被误判成侧栏。
img2 = Image.new("RGB", (1200, 800), (250, 250, 250))
d2 = ImageDraw.Draw(img2)
d2.rectangle([0, 0, 216, 800], fill=(34, 48, 77))            # 深色侧栏
d2.rectangle([0, 0, 1200, 56], fill=(255, 255, 255))         # 顶栏
for x in (300, 400, 500, 600, 700):
    d2.line([(x, 100), (x, 700)], fill=(60, 60, 60), width=2)   # 垂直列线
d2.line([(300, 100), (700, 100)], fill=(60, 60, 60), width=2)   # 表头横线
uri2 = to_uri(img2)

print("== 表格列线检测（S5b）")
prior2 = pixel_prior.prior_from_image(uri2)
grid = prior2.get("grid") if prior2 else None
cols = (grid or {}).get("columnXs") or []
chk("检测到列线", len(cols) >= 4, "columns=%s" % cols)
chk("列线含 300 附近", any(abs(c - 300) <= 6 for c in cols), str(cols))
chk("列线含 700 附近", any(abs(c - 700) <= 6 for c in cols), str(cols))

# —— 图 3：卡片边框（一个矩形框），加侧栏 + 顶栏 ——
img3 = Image.new("RGB", (1200, 800), (250, 250, 250))
d3 = ImageDraw.Draw(img3)
d3.rectangle([0, 0, 216, 800], fill=(34, 48, 77))
d3.rectangle([0, 0, 1200, 56], fill=(255, 255, 255))
d3.line([(0, 56), (1200, 56)], fill=(220, 220, 220), width=1)  # 顶栏底边分隔线
d3.rectangle([260, 150, 1000, 600], outline=(60, 60, 60), width=2)  # 卡片框（在内容区）
uri3 = to_uri(img3)

print("== 区域框检测（S5b）")
prior3 = pixel_prior.prior_from_image(uri3)
boxes = (prior3 or {}).get("regionBoxes") or []
chk("检测到卡片候选框", len(boxes) >= 1, "boxes=%s" % boxes)

print("== 降级")
chk("非 data URI → None", pixel_prior.prior_from_image("not-a-uri") is None)
chk("坏 base64 → None", pixel_prior.prior_from_image("data:image/png;base64,!!!!") is None)
chk("anchor_text(None) 返回空串", pixel_prior.anchor_text(None) == "")

print("RESULT", "ALL PASS" if not fails else "FAIL x%d: %s" % (len(fails), ", ".join(fails)))
sys.exit(1 if fails else 0)
