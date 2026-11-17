"""区域裁剪 + 放大（可选依赖 Pillow）。

作用：截图两段式识别的阶段 B，把「单个区域」从整图里裁出来、放大，
让密集小字（列头 / 行内文字 / 繁体术语）不再被整图缩糊。

定位：这是 AI 识别服务端链路的**可选增强**，不是生成物依赖、也不是
构建/验收脚本依赖。Pillow 未安装时一律返回 None（调用方自动降级为
「整图 + 区域清单」，即 S3 现状），不抛异常、不破坏「构建脚本零第三方库」约定。

因此这里**不 import PIL 在模块顶部**，而是运行时惰性导入。
"""
import base64
import binascii
import io


def _load_pil():
    """惰性导入 Pillow；未安装返回 None。"""
    try:
        from PIL import Image
        return Image
    except Exception:
        return None


def _decode(data_uri):
    """把 data URI 解码成原始字节。格式非法返回 None。"""
    if not (data_uri or "").startswith("data:image/"):
        return None
    try:
        head, b64 = data_uri.split(",", 1)
        return base64.b64decode(b64)
    except (ValueError, binascii.Error):
        return None


def _mime(data_uri):
    """取 data URI 的 MIME 类型（如 image/png）。"""
    try:
        return data_uri.split(";", 1)[0].split(":", 1)[1]
    except Exception:
        return "image/png"


def crop_region(data_uri, bbox, target_long_edge=1024, pad=8):
    """从整图 data URI 裁出 bbox 区域并放大，返回新的 data URI；失败/未装 Pillow 返回 None。

    bbox: [x, y, w, h]，原点整图左上角，单位像素（与提示词契约一致）。
    pad: 裁剪时向外扩的留白像素，避免文字贴边被切。
    target_long_edge: 放大后长边目标尺寸（超过原图则不放大，仅原尺寸）。
    """
    Image = _load_pil()
    if Image is None:
        return None
    raw = _decode(data_uri)
    if raw is None:
        return None
    try:
        img = Image.open(io.BytesIO(raw))
    except Exception:
        return None

    W, H = img.size
    try:
        x, y, w, h = (int(v) for v in bbox)
    except Exception:
        return None
    if w <= 0 or h <= 0:
        return None

    # 裁剪框向外扩 pad，再夹紧到整图范围
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(W, x + w + pad)
    y1 = min(H, y + h + pad)
    if x1 <= x0 or y1 <= y0:
        return None

    crop = img.convert("RGB").crop((x0, y0, x1, y1))

    # 放大：长边对齐 target_long_edge，但不超过原图（避免放大模糊反而变糊）
    cw, ch = crop.size
    long_side = max(cw, ch)
    if long_side < target_long_edge:
        scale = target_long_edge / float(long_side)
        nw, nh = max(1, int(cw * scale)), max(1, int(ch * scale))
        crop = crop.resize((nw, nh), Image.LANCZOS)

    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    mime = _mime(data_uri)
    # 裁剪后统一输出 PNG，避免 JPEG 反复压缩
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def decode_size(data_uri):
    """返回整图 (w, h)；失败返回 None。用于调试/断言。"""
    Image = _load_pil()
    if Image is None:
        return None
    raw = _decode(data_uri)
    if raw is None:
        return None
    try:
        img = Image.open(io.BytesIO(raw))
        return img.size
    except Exception:
        return None
