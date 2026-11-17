#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""截图识别 CLI 包装器 —— 让插件能调用 studio_server 的 ai_analyze，不重复逻辑。

用法：
    python build/ai_recognize.py --image shot.png --out extract.json [--hint "补充说明"] [--mode screenshot]

这个脚本只是一个适配层：把 --image 转成 data URI，调 ai_analyze()，把结果写成 JSON。
模型调用的通道配置全部来自环境变量（FORGE_AI_*），与 Studio 服务保持一致。
"""

import argparse
import base64
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import studio_server  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="截图识别 CLI 包装器")
    ap.add_argument("--image", required=True, help="截图路径 (png/jpg/webp)")
    ap.add_argument("--out", required=True, help="输出 extract JSON 路径")
    ap.add_argument("--hint", default="", help="可选，补充说明")
    ap.add_argument("--mode", default="extract", choices=["extract", "screenshot"],
                    help="extract: A1–A6 旧识别；screenshot: 独立结构识别")
    args = ap.parse_args()

    if not os.path.exists(args.image):
        sys.stderr.write("[错误] 图片文件不存在: %s\n" % args.image)
        return 1

    # 读成 data URI
    with open(args.image, "rb") as f:
        data = f.read()
    ext = os.path.splitext(args.image)[1].lower()
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp"}.get(ext, "image/png")
    data_uri = "data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii"))

    # 调 ai_analyze
    code, body = studio_server.ai_analyze(data_uri, args.hint, args.mode)
    if code != 200:
        sys.stderr.write("[错误] 识别失败 (%d): %s\n" % (code, body.get("error", "")))
        if body.get("advice"):
            sys.stderr.write("建议: %s\n" % body["advice"])
        return 1

    # 写结果
    extract = body.get("extract")
    if not extract:
        sys.stderr.write("[错误] 模型未返回 extract\n")
        return 1

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(extract, f, ensure_ascii=False, indent=2)

    print("识别完成: %s" % args.out)
    print("  模型: %s" % body.get("model"))
    if body.get("ai"):
        ai = body["ai"]
        print("  耗时: %.1f 秒 · 通道: %s" % (ai.get("elapsed", 0), ai.get("channel", "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
