"""
A5 许可证变更审批页 · 数据契约与自洽性校验

用法：
    python build/verify_a5_data.py [--file <licenseChange.js 路径>]

做法：把源文件里的 @reduxjs/toolkit import 换成桩函数，落成临时 .mjs 交给 node 执行，
     真实读回 INITIAL_ROWS / NODE_FLOW，再逐条核对数据契约（不靠肉眼看代码）。

核对项：
  1. 数据契约：待办 6 条、已办 4 条
  2. 自洽不变量：nodeIndex === timeline.length（当前节点下标 = 已完成节点数）
  3. 时限分布：至少 1 条已超期、至少 1 条 <12h（A5 交互清单第 2 条要求分色）
  4. 枚举合法：许可证类型 ∈ {Z39,Z15,Z89}
  5. 字段完整：用户指定的 7 个字段一个不缺
  6. 计数一致：待办/已办/我发起 三桶之和 = 总条数
  7. 脱敏：无手机号 / 无真实企业名特征
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

DEFAULT_SRC = os.path.join(
    os.path.expanduser("~"), "my-app", "src", "store", "modules", "licenseChange.js"
)

STUB = """
function createSlice(cfg) {
  return { name: cfg.name, reducer: (s, a) => s, actions: {}, _init: cfg.initialState };
}
function createSelector(deps, fn, ...rest) {
  return (...args) => fn(...args);
}
"""

PROBE = r"""
const rows = INITIAL_ROWS;
const out = {
  rows,
  NODE_FLOW,
  LICENSE_TYPES,
  counts: {
    todo: rows.filter((r) => r.bucket === "todo").length,
    done: rows.filter((r) => r.bucket === "done").length,
    mine: rows.filter((r) => r.bucket === "mine").length,
  },
};
console.log(JSON.stringify(out));
"""


def find_node():
    for cand in (
        os.path.join(os.path.expanduser("~"), ".workbuddy", "binaries", "node", "versions", "22.22.2-3", "node.exe"),
    ):
        if os.path.isfile(cand):
            return cand
    import shutil

    return shutil.which("node")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=DEFAULT_SRC)
    args = ap.parse_args()

    src_path = args.file
    if not os.path.isfile(src_path):
        print("找不到源文件: %s" % src_path)
        return 2

    src = open(src_path, encoding="utf-8").read()
    # 去掉 toolkit 的真实 import，换成桩
    src = re.sub(r'^import\s*\{[^}]*\}\s*from\s*"@reduxjs/toolkit";\s*$', STUB, src, flags=re.M)
    src += PROBE

    node = find_node()
    if not node:
        print("找不到 node，无法执行数据校验")
        return 2

    with tempfile.TemporaryDirectory() as td:
        tmp = os.path.join(td, "probe.mjs")
        open(tmp, "w", encoding="utf-8").write(src)
        proc = subprocess.run(
            [node, tmp], capture_output=True, text=True, encoding="utf-8", errors="replace"
        )

    if proc.returncode != 0:
        print("执行失败：")
        print(proc.stderr[-2000:])
        return 2

    line = [l for l in proc.stdout.splitlines() if l.strip().startswith("{")]
    if not line:
        print("没拿到输出：")
        print(proc.stdout[:1000])
        return 2

    data = json.loads(line[-1])
    rows = data["rows"]
    flow = data["NODE_FLOW"]
    counts = data["counts"]

    fails = []
    notes = []

    def check(ok, ok_msg, fail_msg):
        if ok:
            notes.append("  OK   " + ok_msg)
        else:
            fails.append(fail_msg)

    print("=" * 84)
    print("A5 许可证变更审批页 · 数据校验")
    print("=" * 84)
    print("源文件: %s" % src_path)
    print("总条数: %d   待办 %d / 已办 %d / 我发起 %d" % (len(rows), counts["todo"], counts["done"], counts["mine"]))
    print("审批节点流: %s" % " → ".join(flow))
    print("-" * 84)

    # 1 数据契约
    check(counts["todo"] == 6, "待办 6 条", "待办应为 6 条，实际 %d 条" % counts["todo"])
    check(counts["done"] == 4, "已办 4 条", "已办应为 4 条，实际 %d 条" % counts["done"])

    # 2 nodeIndex === timeline.length
    bad = [r["code"] for r in rows if r.get("nodeIndex") != len(r.get("timeline", []))]
    check(not bad, "nodeIndex === timeline.length（全部 %d 条）" % len(rows),
          "节点下标与轨迹长度不一致: %s" % ", ".join(bad))

    over = [r["code"] for r in rows if r.get("nodeIndex", 0) > len(flow)]
    check(not over, "节点下标未越界", "节点下标越界: %s" % ", ".join(over))

    # 3 时限分布
    overdue = [r["code"] for r in rows if r.get("bucket") == "todo" and r.get("remainHours", 1) <= 0]
    urgent_lt12 = [r["code"] for r in rows if r.get("bucket") == "todo" and 0 < r.get("remainHours", 99) < 12]
    check(bool(overdue), "存在已超期待办 %s" % overdue, "缺少已超期的待办（A5 要求 ≥1 条）")
    check(bool(urgent_lt12), "存在剩余 <12h 的待办 %s" % urgent_lt12, "缺少剩余 <12h 的待办（A5 要求 ≥1 条）")

    urgent = [r["code"] for r in rows if r.get("urgency") == "紧急"]
    check(bool(urgent), "存在紧急任务 %d 条" % len(urgent), "缺少紧急任务")

    # 4 枚举合法
    types = sorted({r.get("licenseType") for r in rows})
    check(set(types) <= set(data["LICENSE_TYPES"]),
          "许可证类型合法：%s" % "/".join(types),
          "出现非法许可证类型: %s" % types)
    check(set(types) == set(data["LICENSE_TYPES"]),
          "三种类型 Z39/Z15/Z89 全覆盖", "许可证类型未覆盖全部三档: %s" % types)

    # 5 用户指定字段完整性
    need = ["licenseType", "licenseNo", "plant", "category", "funcLoc", "changeContent", "changeNode"]
    label = {
        "licenseType": "许可证类型", "licenseNo": "许可证号", "plant": "工厂",
        "category": "类别", "funcLoc": "功能位置", "changeContent": "修改内容", "changeNode": "修改节点",
    }
    miss = []
    for r in rows:
        for k in need:
            if not str(r.get(k, "")).strip():
                miss.append("%s.%s" % (r.get("code", "?"), label[k]))
    check(not miss, "7 个指定字段全部非空", "字段缺失: %s" % ", ".join(miss[:8]))

    # 6 计数一致
    check(counts["todo"] + counts["done"] + counts["mine"] == len(rows),
          "三桶计数之和 = 总条数", "三桶计数与总条数不符")

    # 7 脱敏
    blob = json.dumps(rows, ensure_ascii=False)
    phones = re.findall(r"1[3-9]\d{9}", blob)
    check(not phones, "无完整手机号", "发现疑似真实手机号: %s" % phones)
    realnames = re.findall(r"(有限公司|股份有限公司|集团有限责任公司)", blob)
    check(not realnames, "无真实企业名特征", "发现疑似真实企业名: %s" % set(realnames))

    # 明细：逐条列出
    print()
    print("%-20s %-8s %-5s %-6s %-6s %-9s %-14s %s" % ("申请单号", "桶", "节点", "剩余", "紧急", "类型", "工厂/功能位置", "提交人"))
    print("-" * 108)
    for r in rows:
        print("%-20s %-8s %-5s %-6s %-6s %-9s %-14s %s" % (
            r["code"], r["bucket"], "%d/%d" % (r["nodeIndex"], len(flow)),
            ("超期%d" % abs(r["remainHours"])) if r["remainHours"] <= 0 else ("%dh" % r["remainHours"]),
            r["urgency"], r["licenseType"],
            "%s/%s" % (r["plant"], r["funcLoc"]), r["submitter"],
        ))

    print()
    for n in notes:
        print(n)
    print("-" * 84)
    if fails:
        for f in fails:
            print("FAIL " + f)
        print("数据校验：%d 项未通过" % len(fails))
        return 1
    print("数据校验：全部通过（%d 项）" % len(notes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
