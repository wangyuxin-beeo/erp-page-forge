# -*- coding: utf-8 -*-
"""像素先验：从截图 data URI 里量出「可注入提示词的确定性锚点」。

作用：把模型从「猜坐标系」变成「对齐像素量到的位置」。分两档：
  · 页面级锚点（S5a）：画布宽高 / 侧栏宽 / 顶栏高 / 主色 / 深色占比 —— 复用 verify_visual 的子函数。
  · 元素级先验（S5b）：表格列线（垂直贯穿线聚出的列边界 x 数组）、卡片/区域候选框 —— 本文件新增。

定位与降级：这是 AI 识别服务端的**可选增强**，不是生成物依赖、也不进构建/验收脚本。
  · 解码用 Pillow（惰性导入），未装 Pillow / 非图片 / 解码失败 → 返回 None，调用方照常走纯模型识别。
  · 先验只做**锚点/候选**，最终语义以模型为准；像素与模型冲突时由调用方标进 uncertain，
    绝不硬覆盖模型判断。

与 verify_visual 的分工：verify_visual 保持「零第三方库 + 只读文件路径 + 只支持 PNG」不动，
本文件专吃 data URI（Pillow 解任意格式），复用它的 feature_* 子函数，不改它的对外契约。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import img_crop          # noqa: E402  惰性 Pillow 解码
import verify_visual     # noqa: E402  复用 feature_* 像素子函数


def _img_from_data_uri(data_uri):
    """把 data URI 解码成 verify_visual 兼容的 img dict（RGB, ch=3）。失败返回 None。"""
    Image = img_crop._load_pil()
    if Image is None:
        return None
    raw = img_crop._decode(data_uri)
    if raw is None:
        return None
    import io
    try:
        im = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        return None
    w, h = im.size
    return {"w": w, "h": h, "ch": 3, "rows": h, "buf": bytes(im.tobytes())}


def prior_from_image(data_uri):
    """页面级锚点 + 元素级先验。任何一步失败都返回 None（调用方降级）。"""
    img = _img_from_data_uri(data_uri)
    if img is None:
        return None

    # —— 页面级锚点（复用 verify_visual 的子函数，与 features() 同口径）——
    th = verify_visual.feature_topbar_height(img)
    sw = verify_visual.feature_sidebar_width(img)
    if sw is not None and sw < 100:        # 下限保护（浅色侧栏误判 3px 的坑）
        sw = None
    if not sw:
        sw = verify_visual.feature_sidebar_border(img, th=th or 0)
    colors = verify_visual.feature_colors(img)

    prior = {
        "canvas": {"w": img["w"], "h": img["h"]},
        "sidebarWidth": sw,
        "topbarHeight": th,
        "primaryHex": colors.get("primaryHex"),
        "darkRatio": colors.get("darkRatio"),
    }

    # —— 元素级先验（S5b）——
    grid = detect_grid_lines(img, sidebar_width=sw, topbar_height=th)
    if grid is not None:
        prior["grid"] = grid
    boxes = detect_region_boxes(img, sidebar_width=sw, topbar_height=th)
    if boxes:
        prior["regionBoxes"] = boxes

    return prior


def anchor_text(prior):
    """把 prior 转成一段可注入提示词的中文锚点文本。prior 为 None 时返回空串。"""
    if not prior:
        return ""
    c = prior.get("canvas") or {}
    parts = ["像素锚点（确定性测量，直接用于校准坐标，不要重猜）："]
    if c.get("w") and c.get("h"):
        parts.append("整图画布 %d×%d px，坐标原点为整图左上角。" % (c["w"], c["h"]))
    if prior.get("sidebarWidth"):
        parts.append("左侧导航宽约 %dpx（主内容区左边界≈此值）。" % prior["sidebarWidth"])
    if prior.get("topbarHeight"):
        parts.append("顶部工具栏高约 %dpx（主内容区上边界≈此值）。" % prior["topbarHeight"])
    if prior.get("primaryHex"):
        parts.append("品牌主色约 %s（用于认强调色按钮/链接）。" % prior["primaryHex"])
    grid = prior.get("grid")
    if grid:
        cols = grid.get("columnXs") or []
        if cols:
            parts.append("下方疑似表格，检测到约 %d 条列边界线，x 坐标 ≈ [%s]。"
                         % (len(cols), ", ".join(str(c) for c in cols[:20])))
    boxes = prior.get("regionBoxes") or []
    if boxes:
        parts.append("检测到约 %d 个卡片/区域候选框（bbox=[x,y,w,h]）：%s。"
                     % (len(boxes), ", ".join(str(tuple(b)) for b in boxes[:12])))
    parts.append("以上是像素级近似，若与视觉判断冲突，以视觉为准并在 uncertain 里说明。")
    return "\n".join(parts)


# --------------------------------------------------------------------------
# S5b：线段 / 列线 / 区域框检测（纯标准库，不依赖 Pillow）
# --------------------------------------------------------------------------

def detect_grid_lines(img, sidebar_width=None, topbar_height=None, threshold=60):
    """检测主内容区里的垂直「贯穿线」，聚成表格列边界 x 坐标。

    思路：在若干采样行上，找「相邻像素亮度跳变 > threshold」的 x 位置（竖线边缘），
    统计这些 x 在采样行上的命中率；命中率高（≥0.6）的 x 视为一条贯穿列线。
    只在主内容区（侧栏右、顶栏下）内扫描，避开侧栏/顶栏的干扰。
    """
    w, h, ch = img["w"], img["h"], img["ch"]
    if h < 100 or w < 200:
        return None
    x0 = max(0, int(sidebar_width or 0) + 2)
    y0 = max(0, int(topbar_height or 0) + 4)
    y1 = min(h, y0 + 500)
    if y1 - y0 < 60:
        return None

    # 采样行（内容区均匀取 10 行）
    sample_rows = [int(y0 + (y1 - y0) * i / 10.0) for i in range(10)]

    def row_edges(y):
        """一行里，水平相邻像素亮度跳变位置的 x 集合。"""
        xs = set()
        prev = None
        for x in range(x0, w - 1, 2):
            p = img["buf"][(y * w + x) * ch:(y * w + x) * ch + 3]
            l = 0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2]
            if prev is not None and abs(l - prev) > threshold:
                xs.add(x)
            prev = l
        return xs

    # 每个候选 x 的命中行数
    hits = {}
    for y in sample_rows:
        for x in row_edges(y):
            hits[x] = hits.get(x, 0) + 1

    total = len(sample_rows)
    cols = sorted(x for x, n in hits.items() if n >= int(total * 0.6))
    # 合并过于接近的列线（<6px 视为同一条线的左右边缘）
    merged = []
    for x in cols:
        if merged and x - merged[-1] < 6:
            continue
        merged.append(x)
    if len(merged) < 2:
        return None
    return {"columnXs": merged, "sampleRows": total}


def detect_region_boxes(img, sidebar_width=None, topbar_height=None, threshold=60):
    """检测卡片/区域候选框：水平线 + 垂直线围成的矩形。

    简化策略：先找内容区里的水平「贯穿线」（卡片上下边框），再找垂直「贯穿线」
    （卡片左右边框 + 列线），由相邻的水平/垂直线两两围出候选矩形，过滤掉过小/过扁的。
    这是**候选**，供模型对齐，不是最终裁决。
    """
    w, h, ch = img["w"], img["h"], img["ch"]
    x0 = max(0, int(sidebar_width or 0) + 2)
    y0 = max(0, int(topbar_height or 0) + 4)
    y1 = min(h, y0 + 600)
    if y1 - y0 < 80:
        return []

    def lum_at(x, y):
        p = img["buf"][(y * w + x) * ch:(y * w + x) * ch + 3]
        return 0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2]

    # 水平贯穿线：在若干采样列上，找「垂直相邻亮度跳变」的 y，统计贯穿率
    sample_cols = [int(x0 + (w - x0) * i / 12.0) for i in range(1, 12)]
    hy = {}
    for x in sample_cols:
        if x >= w:
            continue
        prev = None
        seen = set()
        for y in range(y0, y1, 2):
            l = lum_at(x, y)
            if prev is not None and abs(l - prev) > threshold:
                seen.add(y)
            prev = l
        for y in seen:
            hy[y] = hy.get(y, 0) + 1
    total_c = len(sample_cols)
    h_lines = sorted(y for y, n in hy.items() if n >= int(total_c * 0.5))

    # 垂直贯穿线（列线复用，但这里用更宽松的贯穿率抓卡片左右边）
    sample_rows = [int(y0 + (y1 - y0) * i / 8.0) for i in range(1, 8)]
    vx = {}
    for y in sample_rows:
        prev = None
        seen = set()
        for x in range(x0, w - 1, 2):
            l = lum_at(x, y)
            if prev is not None and abs(l - prev) > threshold:
                seen.add(x)
            prev = l
        for x in seen:
            vx[x] = vx.get(x, 0) + 1
    total_r = len(sample_rows)
    v_lines = sorted(x for x, n in vx.items() if n >= int(total_r * 0.5))

    # 合并过近的线
    def merge(xs, gap=8):
        out = []
        for x in xs:
            if out and x - out[-1] < gap:
                continue
            out.append(x)
        return out

    h_lines = merge(h_lines)
    v_lines = merge(v_lines)
    if len(h_lines) < 2 or len(v_lines) < 2:
        return []

    # 相邻水平线 + 相邻垂直线围矩形，过滤过小/过扁/过大的
    boxes = []
    for i in range(len(h_lines) - 1):
        for j in range(len(v_lines) - 1):
            bx, by = v_lines[j], h_lines[i]
            bw, bh = v_lines[j + 1] - v_lines[j], h_lines[i + 1] - h_lines[i]
            if bw < 40 or bh < 24:
                continue
            if bw > w * 0.9 or bh > h * 0.7:
                continue
            boxes.append([bx, by, bw, bh])
    # 按面积去重（嵌套矩形只保留最大的）
    boxes.sort(key=lambda b: b[2] * b[3], reverse=True)
    kept = []
    for b in boxes:
        if any(k[0] <= b[0] and k[1] <= b[1] and
               k[0] + k[2] >= b[0] + b[2] and k[1] + k[3] >= b[1] + b[3]
               for k in kept):
            continue
        kept.append(b)
        if len(kept) >= 12:
            break
    return kept
