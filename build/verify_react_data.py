"""
销售订单 mock 数据自洽性校验（对应 data-contract.md 第 4 节）
检查项：
  1. 含税金额 == 不含税金额 + 税额
  2. 状态种类 >= 4 且含异常态
  3. 行数 >= 12
  4. 金额量级覆盖 4 位到 7 位
  5. 客户名全部带「（示例）」后缀，编码符合 规则-年月日-序号
  6. 每个状态在 STATUS_ACTIONS 里都能找到可执行动作
"""
import re
import json
import sys
from pathlib import Path
from decimal import Decimal

SRC = Path(r"C:\Projects\example-app\src\store\modules\salesOrder.js")
text = SRC.read_text(encoding="utf-8")

# 抽出 MOCK_ROWS 数组体
m = re.search(r"const MOCK_ROWS = \[(.*?)\n\];", text, re.S)
if not m:
    print("FAIL: 未能定位 MOCK_ROWS")
    sys.exit(1)

body = m.group(1)
rows = []
for line in body.splitlines():
    line = line.strip()
    if not line.startswith("{"):
        continue
    obj = {}
    for km in re.finditer(r'([A-Za-z_][A-Za-z0-9_]*):\s*"([^"]*)"|([A-Za-z_][A-Za-z0-9_]*):\s*([0-9.]+)', line):
        if km.group(1):
            obj[km.group(1)] = km.group(2)
        else:
            obj[km.group(3)] = float(km.group(4))
    if obj:
        rows.append(obj)

# 抽出 STATUS_ACTIONS 覆盖的状态
actions_block = re.search(r"export const STATUS_ACTIONS = \{(.*?)\n\};", text, re.S)
action_states = re.findall(r"^\s{2}([^:\s]+):\s*\[", actions_block.group(1), re.M) if actions_block else []

problems = []

print(f"解析到订单行数: {len(rows)}")
if len(rows) < 12:
    problems.append(f"行数不足 12（实际 {len(rows)}），无法触发分页")

# 1. 金额自洽
for r in rows:
    gross = Decimal(str(r.get("amount", 0)))
    net = Decimal(str(r.get("netAmount", 0)))
    tax = Decimal(str(r.get("tax", 0)))
    if gross != net + tax:
        problems.append(f"{r.get('code')} 金额不自洽: {net} + {tax} = {net + tax} ≠ {gross}")

# 2. 状态覆盖
states = {r.get("status") for r in rows}
if len(states) < 4:
    problems.append(f"状态种类不足 4 种（实际 {len(states)}）")
abnormal = {"已驳回", "已取消"}
if not (states & abnormal):
    problems.append("缺少异常态数据（已驳回 / 已取消），页面没有「故事」")

# 3. 状态机覆盖
missing = states - set(action_states)
if missing:
    problems.append(f"以下状态没有定义可执行动作，按钮会缺失: {sorted(missing)}")

# 4. 金额量级
amounts = [r.get("amount", 0) for r in rows]
dec = [len(str(int(a))) for a in amounts if a]
if min(dec) > 4 or max(dec) < 7:
    problems.append(f"金额量级未覆盖 4-7 位（实际 {min(dec)}-{max(dec)} 位）")

# 5. 脱敏与编码规则
for r in rows:
    cust = r.get("customer", "")
    if "（示例）" not in cust:
        problems.append(f"{r.get('code')} 客户名未加脱敏后缀: {cust}")
    if not re.fullmatch(r"SO-\d{8}-\d{4}", r.get("code", "")):
        problems.append(f"编码不符合 规则-年月日-序号: {r.get('code')}")

# 6. 业务员复用检查
salesmen = {r.get("salesman") for r in rows}

print(f"状态种类: {len(states)} -> {sorted(states)}")
print(f"金额区间: {min(amounts):,.2f} ~ {max(amounts):,.2f}")
print(f"业务员: {sorted(salesmen)}")

if problems:
    print("\n=== 发现问题 ===")
    for p in problems:
        print(" -", p)
    sys.exit(1)

print("\n全部自洽性检查通过")
sys.exit(0)
