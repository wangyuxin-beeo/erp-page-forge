# 自由版式模板（第五入口：任意版式的生成）

> A1–A6 解决的是"表格/表单类页面"的确定性生成。自由版式解决的是**另一类**：
> 没有表格、没有表单的页面——空状态页、落地页、门户、营销卡片、引导页——以及
> **任何用户自定义版式**。核心承诺不变：输入结构化 → 确定性生成 → 四类断言 → 可复现可追溯。

## 1. 为什么需要它（与 A1–A6 的分工）

| | A1–A6（固定页型） | 自由版式（任意版式） |
|---|---|---|
| 模板本体 | PageSpec（字段/列/动作/状态机） | **FreeformSpec**（区块 + 文案插槽 + 视觉令牌 + 语义声明 + 原图素材） |
| 适用 | 列表/表单/详情/看板/审批/对账 | **任意版式**：空状态、落地页、门户、卡片 |
| 生成 | 骨架 + 配方 | 图片入场：区块还原；HTML 入场：原页净化 + 插槽替换 |
| 语义从哪来 | archetype 内置 | **用户/标注时声明**（`semantics` 块，方案 C 混合） |

**关键分流**：入场时先走 A1–A6；当它判定"不是 A1–A6"（抽不出 columns 等）时，不再只报错，
而是给「转为自由版式模板」入口。两条链路并行，互不破坏。

## 2. 三个入口（把"任意页面"变成模板）

```bash
# ① 一张截图（没有表格/表单的图走这条）
python build/ext/freeform/ff_image.py --image 截图.png --labels 标注.json \
    --out spec.json --forge out.html

# ② 已有 HTML（净化外链 + 自动识别文字节点为候选插槽）
python build/ext/freeform/ff_html.py --artifact 上传目录 --out spec.json

# ③ 手写 FreeformSpec（完全控制区块与语义）
# 见 build/check_freeform.py 的 hand_spec() 参考样例
```

前端设计台（studio/index.html）里，图片/HTML 生成失败时会弹「转为自由版式」确认；
自由版式模板与 A1–A6 自定义模板一起列在「我的模板」，带「自由版式」标签，可用指令复用。

## 3. FreeformSpec 结构（契约）

```json
{
  "kind": "freeform", "specVersion": "ff-1.0", "name": "页名",
  "source": {"type": "image|html|hand", "file": "可选素材"},
  "canvas": {"w": 1440, "h": 900, "bg": "#F7F8FA"},
  "tokens": {"mode": "skill-default", "values": {}},
  "regions": [
    {"id": "r1", "role": "card|text|button|nav|header|illustration|image|divider",
     "keep": "rebuild|crop", "bbox": [x0, y0, x1, y1],
     "slot": "可选插槽名", "text": "文案", "style": {...}}
  ],
  "slots": {"title": "当前文案"},
  "semantics": {
    "page": {"purpose": "页面用途", "entities": ["业务对象"]},
    "contract": [{"id": "c1", "kind": "text|absent|regex", "value": "必须出现/不得出现的文案", "from": "auto|user"}],
    "interactions": [{"id": "i1", "trigger": "r4", "type": "toggle|scroll|confirms", "target": "r5", "from": "user"}],
    "data": [{"id": "d1", "type": "count-equals", "a": ".选择器A", "b": ".选择器B", "from": "user"}]
  },
  "acceptance": {"requiredTexts": [...], "minTextLen": 30}
}
```

**硬约束**：文字区块（role=text/button）`keep` 必须是 `rebuild`（真文字），禁止 `crop` 成位图——
否则文案断言形同虚设。`crop` 只允许用于 `illustration/image`（插画/照片裁切嵌入）。

## 4. 语义声明（方案 C：混合）

自由版式没有 archetype 内置的语义，但**语义必须由声明补齐**，否则退化成"照着图复制 HTML"：

- **能自动识别的自动填**（`ff_spec.auto_semantics`）：危险操作按钮（删除/作废/停用…）→ 自动声明
  "点击必须弹确认"（confirms）；其余认不出的进入 `semanticsTodos` 交前端提示补。
- **识别不了的提示用户补**：交互期望（toggle/scroll/confirms）+ 数据自洽（count-equals）。
- **未声明 = 未声明，不是"不适用"**：报告里显式标"语义覆盖度：未声明 N 类"，不算通过也不算失败。

**交互断言是真实点击**：`ff_forge` 把声明的 toggle/scroll 直接实现为真实行为（onclick + 隐藏区块），
`serve_spa` 里的通用 driver（ff_click_toggles / ff_click_scrolls / ff_click_confirms / ff_count_equals）
在无头浏览器里真实点击验证。**scroll 若目标首屏就可见，会如实判失败**（无法证明滚动发生），不假阳性放行。

## 5. 验收（`ff_verify.py`，四类断言口径）

| 类别 | 断言 | 级别 |
|---|---|---|
| 渲染 | 不白屏、1280px 无横向溢出、无 `undefined/NaN`、无 JS 报错 | 硬性 |
| 渲染 | 产物零外链（`http(s)://` 即失败，`127.0.0.1` 放行） | 硬性 |
| 文案 | `requiredTexts` 逐条出现在 DOM；指令替换的旧值不残留 | 硬性 |
| 结构 | rebuild 区块都在 DOM（`data-ff-region`），文字区块不是图片 | 硬性 |
| 契约/交互/数据 | 见 §4：**声明即验**，复用 A1–A6 同一条 driver 探测链路 | 硬性（声明时） |
| 视觉 | 画布比例、主色、深浅版式与原图比对（复用 `verify_visual.compare`） | 软性 |

退出码 0 = 硬性项全过；拿不到浏览器/截图项标 `unsupported`，不许当通过。

## 6. 代码位置

```
build/ext/freeform/
  schema/freeform.schema.json   契约（FreeformSpec）
  ff_spec.py                    校验 + auto_semantics（方案 C）
  ff_forge.py                   确定性生成器（图片入场还原 / 语义兑现）
  ff_verify.py                  验收器（四类断言 + 语义覆盖度报告）
  ff_image.py / ff_html.py / ff_parse.py   三入口
  routes.py                     Studio 路由 + parse_command_hook 委托
build/check_freeform.py         端到端回归（19 项）
studio/ext-freeform.js          前端接入（模板卡片 / 转换入口 / 存删）
```

接入方式（增量，不破坏 A1–A6）：`studio_server.py` 合并 `routes.POST` / `routes.get_route` /
`routes.get_file`；`studio_api.list_templates` 追加 `ff_templates()`；`parse_command` 委托
`parse_command_hook`；`forge_from_command` 识别 `kind==="freeform"` 走 `routes.generate`。
