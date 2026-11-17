/* Forge Studio · 语义识别
   在像素拆层之上，按 ERP 页面的版式规律自动认出：
     侧栏 / 品牌 / 菜单分组 / 菜单 / 顶栏 / 面包屑 / 标题 / 按钮 / 查询条件 / 输入框 /
     表格 / 列头 / 操作列 / 复选框 / 表体 / 工具条 / 分页
   纯本地、确定性（同一张图结果恒定）。不做文字识别：名称给占位，可批量命名、按 extract 对齐或走 AI。
   依赖 index.html 里的全局：S pixels bgOf segs trim edgeSplit addLayer */
(function () {
const dif = (a, b) => Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]) + Math.abs(a[2] - b[2]);
const off = (d, i, bg) => Math.abs(d[i] - bg[0]) > 20 || Math.abs(d[i + 1] - bg[1]) > 20 || Math.abs(d[i + 2] - bg[2]) > 20;
const inset = (r, k) => ({ x: r.x + k, y: r.y + k, w: Math.max(1, r.w - 2 * k), h: Math.max(1, r.h - 2 * k) });

/* 区域内非底色像素的占比、彩度占比、平均色 */
function stats(r, bg) {
  const d = pixels(); let n = 0, t = 0, sat = 0, c = [0, 0, 0];
  const st = Math.max(1, Math.floor(Math.sqrt(r.w * r.h / 3000)));
  for (let y = r.y; y < r.y + r.h; y += st) for (let x = r.x; x < r.x + r.w; x += st) {
    const i = (y * S.w + x) * 4; t++;
    if (!off(d, i, bg)) continue;
    n++; c[0] += d[i]; c[1] += d[i + 1]; c[2] += d[i + 2];
    if (Math.max(d[i], d[i + 1], d[i + 2]) - Math.min(d[i], d[i + 1], d[i + 2]) > 50) sat++;
  }
  return { ink: n / Math.max(1, t), sat: n ? sat / n : 0, color: n ? c.map(v => v / n) : bg };
}
/* 一条线上非底色像素的占比（axis 0 = 横线 y，1 = 竖线 x）；thr 是逐通道差阈值 */
function lineInk(r, bg, axis, at, thr) {
  const d = pixels(), len = axis ? r.h : r.w, t = thr || 20; let n = 0;
  if (at < 0 || at >= (axis ? S.w : S.h)) return 0;
  for (let k = 0; k < len; k++) {
    const x = axis ? at : r.x + k, y = axis ? r.y + k : at, i = (y * S.w + x) * 4;
    if (Math.abs(d[i] - bg[0]) > t || Math.abs(d[i + 1] - bg[1]) > t || Math.abs(d[i + 2] - bg[2]) > t) n++;
  }
  return n / Math.max(1, len);
}
/* 有边框的盒子（输入框 / 次按钮 / 页码 / 状态标签）：四条外缘大多是非底色。
   浅灰细边框（#D9D9D9 级）常被留白切分当成底色裁掉，所以向外扩 0–2px、用低阈值再找一次边。 */
function boxed(r, bg) {
  if (r.w < 14 || r.h < 12) return false;
  // 四条边各自只向外找 0–2px：簇外接框常比真实边框缩进 1–2px；
  // 不能向内找，否则框内文字笔画会被当成边框（标题、加粗字会误判成按钮）
  const H = { x: r.x + 2, y: r.y, w: Math.max(1, r.w - 4), h: r.h };
  const V = { x: r.x, y: r.y + 2, w: r.w, h: Math.max(1, r.h - 4) };
  const near = (axis, at, dir, need) => { for (let k = 0; k <= 4; k++) if (lineInk(axis ? V : H, bg, axis, at + dir * k, 10) > need) return true; return false; };
  return near(0, r.y, -1, .8) && near(0, r.y + r.h - 1, 1, .8) && near(1, r.x, -1, .6) && near(1, r.x + r.w - 1, 1, .6);
}
/* 实心盒子（主按钮）：内部主色与外部差得多，且主色铺满内部 ——
   只看主色会把粗体大字（标题、加粗单元格）误认成按钮 */
function filled(r, bg) {
  if (r.w < 14 || r.h < 12) return false;
  const ir = inset(r, 3), c = bgOf(ir);
  if (dif(c, bg) <= 60) return false;
  const d = pixels(); let n = 0, t = 0;
  const st = Math.max(1, Math.floor(Math.sqrt(ir.w * ir.h / 1500)));
  for (let y = ir.y; y < ir.y + ir.h; y += st) for (let x = ir.x; x < ir.x + ir.w; x += st) {
    const i = (y * S.w + x) * 4; t++;
    if (Math.abs(d[i] - c[0]) + Math.abs(d[i + 1] - c[1]) + Math.abs(d[i + 2] - c[2]) < 40) n++;
  }
  return n / Math.max(1, t) > .55;
}
/* 「标签 + 输入框」挨得太近会被切成一个簇：从左往右找输入框的左边框，把簇拆成 标签 + 框 */
function splitLabeled(c, bg) {
  if (c.w < 70 || c.h < 16) return null;
  for (let x = c.x + 12; x < c.x + c.w - 40; x++) {
    if (lineInk(c, bg, 1, x, 10) < .7) continue;
    const box = { x, y: c.y, w: c.x + c.w - x, h: c.h };
    if (!boxed(box, bg)) continue;
    const lab = trim({ x: c.x, y: c.y, w: x - c.x - 1, h: c.h }, bg);
    // 标签必须是纯文字：自己带边框 / 左缘就是竖线的，是另一个按钮
    if (!lab || lab.w < 8 || boxed(lab, bg) || lineInk(lab, bg, 1, lab.x, 10) > .7) continue;
    return [Object.assign(lab, { _box: false }), Object.assign(box, { _box: true })];
  }
  return null;
}
/* 一个簇里并排的多个盒子（「批量审核 | 批量打印」「上一页 1 2 3 下一页」）：
   竖向贯通的细列 = 边框，宽列 = 实心按钮；边框两两配对成盒子 */
function splitBoxes(c, bg) {
  if (c.w < 40 || c.h < 12) return null;
  const runs = []; let s = -1;
  for (let x = c.x; x <= c.x + c.w; x++) {
    const on = x < c.x + c.w && lineInk(c, bg, 1, x, 10) > .6;
    if (on && s < 0) s = x;
    if (!on && s >= 0) { runs.push({ x: s, w: x - s }); s = -1; }
  }
  const out = [];
  for (let i = 0; i < runs.length; i++) {
    const r = runs[i];
    if (r.w >= 14) { out.push({ x: r.x, y: c.y, w: r.w, h: c.h }); continue; }      // 实心按钮
    const n = runs[i + 1];
    if (n && n.w <= 3) { out.push({ x: r.x, y: c.y, w: n.x + n.w - r.x, h: c.h }); i++; }
  }
  const ok = out.filter(b => b.w >= 14 && (boxed(b, bg) || filled(b, bg)));
  if (ok.length < 2) return null;
  // 盒子左边若还有文字（「共 36 条」），保留为文本簇
  const pre = ok[0].x - c.x > 8 ? trim({ x: c.x, y: c.y, w: ok[0].x - c.x - 2, h: c.h }, bg) : null;
  return (pre ? [Object.assign(pre, { _box: false })] : []).concat(ok.map(b => Object.assign(b, { _box: true })));
}
/* 一行里的横向元素簇（已收紧到墨迹边界） */
function clusters(r, bg) {
  return segs(r, bg, 1).map(p => trim(p, bg)).filter(t => t && t.w >= 4 && t.h >= 4);
}
function rows(r, bg) {
  return segs(r, bg, 0).map(p => trim(p, bg)).filter(t => t && t.w >= 4 && t.h >= 4);
}
const union = (a, b) => { const x = Math.min(a.x, b.x), y = Math.min(a.y, b.y);
  return { x, y, w: Math.max(a.x + a.w, b.x + b.w) - x, h: Math.max(a.y + a.h, b.y + b.h) - y }; };
const isBox = (c, bg) => boxed(c, bg) || filled(c, bg);
/* 把间距很小的文字簇并成一个（「仓储中心 / 出入库」这类词组）；盒子不参与合并 */
function mergeNear(cs, gap, bg) {
  const o = [];
  for (const c of cs.slice().sort((a, b) => a.x - b.x)) {
    const p = o[o.length - 1];
    if (p && !p._box && !isBox(c, bg) && c.x - (p.x + p.w) <= gap) o[o.length - 1] = Object.assign(union(p, c), { _box: false });
    else o.push(Object.assign({}, c, { _box: isBox(c, bg) }));
  }
  return o;
}
/* 某水平条带的主色（量化后众数；文字不影响） */
const domRow = (x, y, w) => bgOf({ x, y, w, h: 1 });
const domCol = (x, y, h) => bgOf({ x, y, w: 1, h });

/* 侧栏：从左缘往右扫，第一条持续的主色分界 */
function findSidebar() {
  const A = domCol(4, 0, S.h);
  for (let x = 60; x < S.w * .32; x++) {
    if (dif(domCol(x, 0, S.h), A) >= 8 && dif(domCol(Math.min(S.w - 1, x + 12), 0, S.h), A) >= 8) return { x: 0, y: 0, w: x, h: S.h };
  }
  return null;
}
/* 顶栏：主区顶部往下扫，第一条持续的主色分界（≤110px） */
function findTopbar(m) {
  const A = domRow(m.x, m.y + 3, m.w);
  for (let y = m.y + 20; y < m.y + 110 && y + 12 < m.y + m.h; y++) {
    if (dif(domRow(m.x, y, m.w), A) >= 8 && dif(domRow(m.x, y + 12, m.w), A) >= 8) return { x: m.x, y: m.y, w: m.w, h: y - m.y };
  }
  return null;
}
const PH = { "列头": "列", "菜单": "菜单", "按钮": "按钮", "查询条件": "条件", "字段": "字段", "指标卡": "指标", "菜单分组": "分组" };

function analyze() {
  const cnt = {}, made = [];
  const add = (r, kind, parent, extra) => {
    const L = addLayer(r, kind);
    L.auto = true; L.parent = parent ? parent.id : null; L.depth = parent ? (parent.depth || 0) + 1 : 0; L.conf = .8;
    if (PH[kind]) { cnt[kind] = (cnt[kind] || 0) + 1; L.ph = PH[kind] + cnt[kind]; }
    Object.assign(L, extra || {}); made.push(L); return L;
  };
  const ctx = { add, titleDone: false, made };
  const sb = findSidebar();
  const main = sb ? { x: sb.w, y: 0, w: S.w - sb.w, h: S.h } : { x: 0, y: 0, w: S.w, h: S.h };
  if (sb) doSidebar(sb, ctx);
  const tb = findTopbar(main);
  if (tb) doTopbar(tb, ctx, !sb);
  const content = tb ? { x: main.x, y: tb.y + tb.h, w: main.w, h: main.h - tb.h } : main;
  analyzeContent(content, ctx);
  // 页面里没有表格 → 查询条件其实是表单字段（A2 单据表单）
  if (!made.some(l => l.kind === "表格")) {
    let n = 0;
    made.forEach(l => { if (l.kind === "查询条件") { l.kind = "字段"; l.ph = "字段" + (++n); } if (l.kind === "查询区") l.kind = "表单区"; });
  }
  return made;
}
/* 侧栏：品牌 → 菜单分组（低对比小字）→ 菜单（选中项有色块） */
function doSidebar(sb, ctx) {
  const P = ctx.add(sb, "侧栏"), bg = bgOf(sb);
  const rs = rows(sb, bg);
  if (!rs.length) return;
  const con = rs.map(r => dif(stats(r, bg).color, bg));
  const maxC = Math.max(...con);
  let group = null;
  rs.forEach((r, i) => {
    if (i === 0 && r.y < 80) { ctx.add(r, "品牌", P); return; }
    if (filled(r, bg)) { ctx.add(r, "菜单", group || P, { active: true }); return; }
    // 分组标题 = 低对比 + 后面还有菜单（后一项是选中色块也算）
    const isGroup = con[i] < maxC * .72 && i + 1 < rs.length;
    if (isGroup) group = ctx.add(r, "菜单分组", P);
    else ctx.add(r, "菜单", group || P);
  });
}
/* 顶栏：左 → 面包屑（无侧栏时是品牌），右 → 用户信息，带框的 → 按钮 */
function doTopbar(tb, ctx, brandFirst) {
  const P = ctx.add(tb, "顶栏"), bg = bgOf(tb);
  const cs = mergeNear(clusters(tb, bg), 14, bg);
  cs.forEach((c, i) => {
    if (c._box) ctx.add(c, "按钮", P);
    else if (i === 0) ctx.add(c, brandFirst ? "品牌" : "面包屑", P);
    else if (c.x > tb.x + tb.w * .6) ctx.add(c, "用户信息", P);
    else ctx.add(c, "文本", P);
  });
}
/* 内容区按「行主色」切成竖向色带：与页面底色不同的连续色带 = 卡片。
   卡片常是白底配浅灰页面，差值 <20，像素留白切分看不见它们，必须靠主色。 */
function cardBands(area) {
  // 卡片内的表格行线 / 分隔线只有 1–3px，与页面底色相近；连续 ≥6px 非卡片色才算卡片结束
  const page = domRow(area.x, area.y + 2, area.w), out = [];
  let s = -1, gap = 0;
  for (let y = area.y; y <= area.y + area.h; y++) {
    const isCard = y < area.y + area.h && dif(domRow(area.x, y, area.w), page) >= 12;
    if (isCard) { if (s < 0) s = y; gap = 0; continue; }
    if (s >= 0 && (++gap >= 6 || y === area.y + area.h)) {
      const e = y - gap + 1; if (e - s >= 24) out.push({ y: s, h: e - s }); s = -1; gap = 0;
    }
  }
  return { page, bands: out };
}
/* 一条色带里横向并排的卡片（看板的指标卡） */
function cardsInBand(area, band, page) {
  const out = []; let s = -1;
  for (let x = area.x; x <= area.x + area.w; x++) {
    const isCard = x < area.x + area.w && dif(domCol(x, band.y + 2, band.h - 4), page) >= 12;
    if (isCard && s < 0) s = x;
    if (!isCard && s >= 0) { if (x - s >= 40) out.push({ x: s, y: band.y, w: x - s, h: band.h }); s = -1; }
  }
  return out;
}
function analyzeContent(area, ctx) {
  const { page, bands } = cardBands(area);
  if (!bands.length) { analyzeArea(rows(area, page), page, null, ctx); return; }
  let y = area.y;
  const flat = r => analyzeArea(rows(r, page), page, null, ctx);
  for (const b of bands) {
    if (b.y - y > 8) flat({ x: area.x, y, w: area.w, h: b.y - y });          // 卡片之间的页面区（标题行）
    const cards = cardsInBand(area, b, page);
    const kpi = cards.length >= 3 && b.h < 220;
    for (const c of cards) {
      const C = ctx.add(c, kpi ? "指标卡" : "卡片");
      const cbg = bgOf(inset(c, 4));
      analyzeArea(rows(inset(c, 2), cbg), cbg, C, ctx);
    }
    y = b.y + b.h;
  }
  if (area.y + area.h - y > 8) flat({ x: area.x, y, w: area.w, h: area.y + area.h - y });
  // 卡片按内容改名（第二遍统一做：表格可能在其后的卡片里才被认出，第一遍判不了"页内有没有表格"）
  for (const C of ctx.made.filter(l => ["卡片", "查询区", "表单区"].includes(l.kind))) {
    const kids = ctx.made.filter(l => l.parent === C.id && !l.noise);
    const ks = kids.map(l => l.kind);
    if (ks.includes("表格") || ks.includes("指标卡")) continue;              // 表格卡片 / 指标卡保持原名
    const nq = ks.filter(k => k === "查询条件").length;
    if (nq >= 8) {                       // 「标签+框」≥8 对 → 表单卡（A2 单据表单的字段网格）
      C.kind = "表单区"; let fn = 0;
      kids.forEach(l => { if (l.kind === "查询条件") { l.kind = "字段"; l.ph = "字段" + (++fn); } });
    } else if (nq > 0) C.kind = "查询区";  // 列数少的是查询卡（列表页查询区一般 4–6 个条件）
    else if (ctx.made.some(l => l.kind === "表格")) {
      // 白底白框的表单：输入框碎成边框细条认不出「标签+框」，但文字标签密集（≥8）的卡片按表单卡处理
      const labels = kids.filter(l => l.kind === "文本" && l.h <= 32 && l.w <= 160);
      if (labels.length >= 8) {
        C.kind = "表单区"; let fn = 0;
        kids.forEach(l => { if (l.kind === "文本" && l.h <= 32 && l.w <= 160) { l.kind = "字段"; l.ph = "字段" + (++fn); } });
      }
    }
  }
}
/* 一组行 → 逐行判语义。连续 ≥3 行、每行 ≥4 簇的是表格（首行 = 表头）。 */
function rowInfo(r, bg) {
  let cs = clusters(r, bg), rb = bg;
  if (cs.length === 1 && cs[0].w > r.w * .8) {          // 整行一个簇：多半是带底色的表头，换行内底色再切
    const ib = bgOf(inset(r, 1));
    if (dif(ib, bg) > 6) { const c2 = clusters(r, ib); if (c2.length > 1) { cs = c2; rb = ib; } }
  }
  cs = mergeNear(cs, 10, rb);
  // 先拆并排按钮组（整组分页 / 批量按钮常被误判成一个大框，所以 _box 的也要拆），再拆「标签 + 框」
  cs = cs.reduce((a, c) => a.concat(splitBoxes(c, rb) || [c]), []);
  cs = cs.reduce((a, c) => a.concat(!c._box && splitLabeled(c, rb) || [c]), []);
  return { r, cs, rb, boxes: cs.filter(c => c._box).length };
}
function analyzeArea(rs, bg, P, ctx) {
  const info = rs.map(r => rowInfo(r, bg));
  // ?probe：行级识别过程数据（簇数/盒数），供 check_semantic 与调参用，不影响正常使用
  if (window.__semDbg) info.forEach(o => window.__semDbg.push(
    { zone: P ? P.kind : "page", y: o.r.y, h: o.r.h, n: o.cs.length, b: o.boxes,
      xs: o.cs.slice(0, 14).map(c => c.x + ":" + c.w + (c._box ? "B" : "")) }));
  const isT = k => k < info.length && info[k].cs.length >= 4 && info[k].boxes <= 2;
  // A2 明细表格：表头是矮的纯文字行（≥5 簇、无框、h≤24），表体行允许带行内输入框（可编辑单元格，
  // 框数 < 簇数六成）；要求表头下连续 ≥3 行等高 —— 表单网格的输入行（h≥26）和标签行（簇数=列数）不会误判
  const isHead2 = o => o && o.r.h <= 24 && o.cs.length >= 5 && o.boxes === 0;
  const isBody2 = o => o && o.cs.length >= 4 && o.boxes <= o.cs.length * .6;
  for (let i = 0; i < info.length;) {
    if (isT(i) && isT(i + 1)) {
      const ref = info[i + 1].r.h; let j = i + 1;
      while (isT(j + 1) && Math.abs(info[j + 1].r.h - ref) <= Math.max(8, ref * .5)) j++;
      if (j - i >= 2) { doTable(info.slice(i, j + 1), bg, P, ctx); i = j + 1; continue; }
    }
    if (isHead2(info[i]) && isBody2(info[i + 1]) && isBody2(info[i + 2]) && isBody2(info[i + 3])) {
      const ref = info[i + 1].r.h;
      if (Math.abs(info[i + 2].r.h - ref) <= Math.max(8, ref * .5) &&
          Math.abs(info[i + 3].r.h - ref) <= Math.max(8, ref * .5)) {
        let j = i + 3;
        while (isBody2(info[j + 1]) && Math.abs(info[j + 1].r.h - ref) <= Math.max(8, ref * .5)) j++;
        doTable(info.slice(i, j + 1), bg, P, ctx); i = j + 1; continue;
      }
    }
    doRow(info[i], bg, P, ctx); i++;
  }
}
function doTable(ri, bg, P, ctx) {
  const all = ri.map(x => x.r).reduce(union);
  const T = ctx.add(all, "表格", P), head = ri[0];
  const H = ctx.add(head.r, "表头", T);
  const body = ri.slice(1).map(x => x.r).reduce(union);
  ctx.add(body, "表体", T, { rows: ri.length - 1 });
  head.cs.forEach((c, k) => {
    if (k === 0 && c.w <= 22 && c.h <= 22) ctx.add(c, "复选框", H);
    else ctx.add(c, "列头", H);
  });
  // 末列表体是彩色文字链接（审核 / 编辑 / 打印）→ 操作列，不算业务列
  const last = ctx.made.filter(l => l.parent === H.id && l.kind === "列头").pop();
  if (last) {
    const xr = { x: Math.max(all.x, last.x - 6), y: body.y, w: 0, h: body.h };
    xr.w = Math.max(10, Math.min(Math.max(last.w + 80, 100), all.x + all.w - xr.x));
    if (stats(xr, bg).sat > .3) { last.kind = "操作列"; last.ph = "操作"; }
  }
}
function doRow(o, bg, P, ctx) {
  const { r, cs } = o; if (!cs.length) return;
  const boxes = cs.filter(c => c._box);
  const afterTable = ctx.made.some(l => l.kind === "表格" && l.parent === (P ? P.id : null) && r.y - (l.y + l.h) < 48 && r.y >= l.y + l.h - 2);
  if (afterTable && boxes.length >= 2 && boxes.every(b => b.x > r.x + r.w * .45)) {
    // 分页含"裸页码"窄盒（1/2/3，≤36px）；动作条最窄也是 2 字按钮（≈50px），不会出现窄盒
    const mn = Math.min.apply(null, boxes.map(b => b.w));
    if (mn > 36) { // 宽按钮条（生成标准包/下达/提交/保存…）→ 表单动作条，不是分页
      const G = ctx.add(r, "工具条", P);
      cs.forEach(c => ctx.add(c, c._box ? "按钮" : "文本", G)); return;
    }
    const G = ctx.add(r, "分页", P);
    cs.forEach(c => ctx.add(c, c._box ? "页码" : "文本", G)); return;
  }
  if (cs.length === 1 && cs[0].h > 100) { ctx.add(cs[0], "区块", P); return; } // 图表 / 大图
  // 「标签 + 框」成对 → 查询条件；行里剩下的框是查询按钮
  const items = []; let pairs = 0;
  for (let k = 0; k < cs.length; k++) {
    const c = cs[k], n = cs[k + 1];
    if (!c._box && n && n._box && n.x - (c.x + c.w) < 48 && n.w >= 60) { items.push([union(c, n), "查询条件"]); pairs++; k++; }
    else items.push([c, c._box ? "按钮" : "文本"]);
  }
  if (pairs) {
    const G = P || ctx.add(r, "查询区", null);
    items.forEach(([c, k]) => ctx.add(c, k === "按钮" ? "查询按钮" : k, G)); return;
  }
  const G = cs.length >= 2 && boxes.length ? ctx.add(r, "工具条", P) : P;
  cs.forEach(c => {
    if (c._box) { ctx.add(c, "按钮", G); return; }
    const noTable = !ctx.made.some(l => l.kind === "表格");
    if (!ctx.titleDone && noTable && c.h >= 15 && c.x < r.x + r.w * .5) { ctx.titleDone = true; ctx.add(c, "标题", G); return; }
    ctx.add(c, "文本", G);
  });
}

/* 用 extract（AI / 示例 / 手填）的文字按顺序给同语义图层命名 —— 拆层认位置，extract 给文字 */
function applyNames(ex) {
  if (!ex) return 0;
  const live = S.layers.filter(l => !l.removed);
  const pick = k => live.filter(l => l.kind === k);
  const byX = a => a.sort((p, q) => p.x - q.x), byYX = a => a.sort((p, q) => p.y - q.y || p.x - q.x);
  const lbl = v => typeof v === "string" ? v : (v && (v.name || v.label)) || "";
  let n = 0;
  const put = (ls, names) => ls.forEach((l, i) => { if (names[i] && (!l.name || l.autoName)) { l.name = names[i]; l.autoName = true; n++; } });
  put(byX(pick("列头")), (ex.columns || []).map(lbl));
  put(byYX(pick("查询条件")), (ex.queryControls || []).map(lbl));
  put(byYX(pick("按钮")), (ex.actions || []).filter(a => (a.scope || "header") !== "row").map(lbl));
  const nav = ex.nav || [];
  put(byYX(pick("菜单分组")), nav.map(g => g.group || ""));
  put(byYX(pick("菜单")), nav.reduce((a, g) => a.concat(g.items || []), []).map(lbl));
  put(pick("标题"), [ex.pageName || ""]);
  return n;
}
window.Semantic = { analyze, applyNames };
})();
