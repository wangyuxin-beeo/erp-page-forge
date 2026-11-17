# -*- coding: utf-8 -*-
"""img_crop 裁剪能力的回归（需要 Pillow，用 venv Python 跑）。

验证 S4 的「区域裁剪 + 放大」：构造一张真实 PNG data URI，断言：
  · 裁剪出正确区域、向外扩 pad
  · 小图放大到目标长边
  · bbox 越界被夹紧
  · 非法输入 / 非图片降级返回 None
  · decode_size 返回整图尺寸

用法（需带 Pillow 的 venv）：
  <venv>/Scripts/python.exe build/check_img_crop.py
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import img_crop  # noqa: E402

fails = []


def chk(name, cond, extra=""):
    if not cond:
        fails.append(name)
    print("  %s %s %s" % ("PASS" if cond else "FAIL", name, extra))


def make_png(w, h, color=(120, 160, 200)):
    """用 Pillow 造一张纯色 PNG 的 data URI。"""
    from PIL import Image
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    import base64
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


# 前提：Pillow 必须可用，否则本脚本没有意义
img = img_crop._load_pil()
chk("Pillow 已可用", img is not None, "PIL loaded" if img else "NO PIL")


uri = make_png(1600, 1000)

print("== 裁剪出正确区域（含 pad）")
# 原图 1600x1000，裁 [300, 200, 400, 300]（右下角 700,500）
out = img_crop.crop_region(uri, [300, 200, 400, 300], target_long_edge=0)
sz = img_crop.decode_size(out)
# pad=8，夹紧后裁剪框 x0=292,y0=192,x1=708,y1=508 → 416 x 316
chk("裁剪尺寸 = bbox + 2*pad", sz == (416, 316), str(sz))

print("== 放大到目标长边")
# 裁一个 100x50 的小区域，放大到长边 512
out2 = img_crop.crop_region(uri, [100, 100, 100, 50], target_long_edge=512)
sz2 = img_crop.decode_size(out2)
chk("小图放大到长边 512", sz2 is not None and max(sz2) == 512, str(sz2))

print("== bbox 越界被夹紧")
# bbox 部分在整图外 → 裁剪框被夹到整图内
out3 = img_crop.crop_region(uri, [1500, 900, 300, 300], target_long_edge=0)
sz3 = img_crop.decode_size(out3)
chk("越界 bbox 夹紧到整图内", sz3 is not None and sz3[0] <= 100 + 16 and sz3[1] <= 100 + 16, str(sz3))

print("== 非法输入降级")
chk("非 data URI 返回 None", img_crop.crop_region("not-a-uri", [0, 0, 10, 10]) is None)
chk("坏 bbox 返回 None", img_crop.crop_region(uri, [0, 0, 0, 0]) is None)
chk("负尺寸 bbox 返回 None", img_crop.crop_region(uri, [0, 0, -5, 10]) is None)
chk("解码失败返回 None", img_crop.crop_region("data:image/png;base64,!!!!", [0, 0, 10, 10]) is None)

print("== decode_size")
chk("decode_size 返回整图尺寸", img_crop.decode_size(uri) == (1600, 1000), str(img_crop.decode_size(uri)))

print("RESULT", "ALL PASS" if not fails else "FAIL x%d: %s" % (len(fails), ", ".join(fails)))
sys.exit(1 if fails else 0)
