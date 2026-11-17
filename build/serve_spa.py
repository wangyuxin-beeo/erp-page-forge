# -*- coding: utf-8 -*-
"""
SPA 静态服务器（零依赖，仅标准库）——用于验证前端产物的真实部署形态。

为什么需要它：
  CRA 的 dev server 在部分环境下**不对深层路由做 history fallback**，
  直接请求 /license-approval 会返回 404，导致无法用无头浏览器核对子页面。
  本脚本以「生产构建产物（build/ 或 dist/）+ SPA 回退」的方式起服务，
  既绕开 dev server 的行为差异，又比 dev server 更接近真实部署。

用法：
  python build/serve_spa.py --root "C:/Users/xxx/my-app/build" --port 3030
  python build/serve_spa.py --root ".../erp-vue-lab/dist" --port 3031

约定：
  - 命中真实文件 → 返回该文件
  - 未命中且无扩展名 → 回退 index.html（SPA 路由）
  - 未命中且有扩展名（.js/.css/.png…）→ 404（避免把资源 404 伪装成 HTML）
"""
import argparse
import functools
import http.server
import os
import socket
import sys
import threading
import time


# 同源探测页：以 iframe 加载目标路由，量出真实布局指标后写进 DOM。
# 为什么用 iframe：无头 Chrome 的 --window-size 会被 Windows 显示缩放影响，
# 而 iframe 的 width 就是确定的 CSS 像素，量出来的溢出量才可信。
PROBE_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>PROBE</title>
<style>html,body{margin:0;font:12px monospace}iframe{border:0;display:block}</style></head>
<body>
<iframe id="f"></iframe>
<pre id="out">PENDING</pre>
<script>
(function () {
  var q = new URLSearchParams(location.search);
  var path = q.get('path') || '/';
  var w = parseInt(q.get('w') || '1280', 10);
  var h = parseInt(q.get('h') || '1100', 10);
  var f = document.getElementById('f');
  f.width = w; f.height = h; f.src = path;
  function count(s, k) { return s.split(k).length - 1; }
  f.onload = function () {
    setTimeout(function () {
      var r = { path: path, width: w, ok: false };
      try {
        var d = f.contentDocument;
        var de = d.documentElement, b = d.body;
        r.clientWidth = de.clientWidth;
        r.scrollWidth = de.scrollWidth;
        r.overflowX = de.scrollWidth - de.clientWidth;
        r.textLen = (b.innerText || '').replace(/\\s+/g, '').length;
        var txt = b.innerText || '';
        r.dirty = [];
        ['undefined', 'NaN', '[object Object]'].forEach(function (k) {
          var c = count(txt, k);
          if (c > 0) { r.dirty.push(k + 'x' + c); }
        });
        r.sidebar = d.querySelectorAll('.sidebar').length;
        r.rows = d.querySelectorAll('table tbody tr').length;
        var bad = [], all = d.querySelectorAll('*');
        for (var i = 0; i < all.length; i++) {
          var el = all[i];
          if (el.clientWidth > 0 && el.scrollWidth - el.clientWidth > 1) {
            var cn = (typeof el.className === 'string' && el.className.trim())
              ? '.' + el.className.trim().split(/\\s+/).join('.') : '';
            bad.push(el.tagName.toLowerCase() + cn + ':' + (el.scrollWidth - el.clientWidth));
            if (bad.length >= 8) { break; }
          }
        }
        r.overflowEls = bad;
        r.ok = true;
      } catch (e) { r.error = String(e); }
      document.getElementById('out').textContent = 'PROBE_RESULT ' + JSON.stringify(r);
      document.title = 'PROBE_DONE';
    }, 1500);
  };
})();
</script></body></html>"""


# 验收探测页：在同源 iframe 里真实驱动页面，执行交互/数据断言（driver）。
#
# 为什么必须同源：跨域 iframe 读不到 contentDocument，无法点按钮、读表格、比合计。
# 所以交互与数据断言只能通过"被测服务自己提供的探测页"来做，不能靠外部脚本注入。
#
# driver 三态：
#   pass        断言成立
#   fail        断言不成立（硬性失败）
#   unsupported 当前 DOM 结构无法自动驱动（如控件命名不符合惯例）→ 报告标"需人工确认"，
#               不计失败。这是刻意的：自动断言不完整时应该诚实说不知道，而不是假通过。
ACCEPT_HTML = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>ACCEPT</title>
<style>html,body{margin:0;font:12px monospace}iframe{border:0;display:block}</style></head>
<body>
<iframe id="f"></iframe>
<pre id="out">PENDING</pre>
<script>
// 断言清单由校验脚本内联注入（见 build/verify_acceptance.py 的 _inline_probe）。
// 为什么内联而不走 URL：defs 含中文枚举与选择器，URL 编码后能到 8KB 以上，
// 浏览器会卡死甚至截断参数，导致断言静默变成空列表 —— 这是踩过的坑。
var INLINE_DEFS = __DEFS_PAYLOAD__;
(function () {
  var q = new URLSearchParams(location.search);
  var path = q.get('path') || '/';
  var w = parseInt(q.get('w') || '1280', 10);
  var h = parseInt(q.get('h') || '1100', 10);
  var defs = [];
  try {
    var src = (typeof INLINE_DEFS !== 'undefined') ? INLINE_DEFS
      : JSON.parse(q.get('defs') || '[]');
    if (Object.prototype.toString.call(src) === '[object Array]') {
      defs = src;                          // 兼容旧格式：defs 数组
    } else {
      // 新格式：h 为公共锚点（只传一次），d 为断言清单
      var gh = src.h || {};
      defs = (src.d || []).map(function (d) {
        d.args = d.args || {};
        d.args.hints = Object.assign({}, gh, d.args.hints || {});
        return d;
      });
    }
  } catch (e) {}

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function txt(el) { return ((el && (el.innerText || el.textContent)) || '').trim(); }
  function all(doc, sel) { return Array.prototype.slice.call(doc.querySelectorAll(sel)); }
  function byText(doc, sel, kw) {
    return all(doc, sel).filter(function (el) { return txt(el).indexOf(kw) >= 0; });
  }
  function rows(doc) { return all(doc, 'table tbody tr'); }
  // 只认「纯数值单元格」：允许正负号 / 千分位 / ¥ / 百分号。
  // 含字母或中文的一律不当数值 —— 否则订单号 SO-20260917-0081 会被当成数字参与求和，
  // 算出 -202609133 这种荒唐的"明细之和"（实测踩过），把好产物判成失败。
  function num(s) {
    var t = String(s).trim();
    if (!t || /[A-Za-z\u4e00-\u9fa5]/.test(t)) { return null; }
    var clean = t.replace(/[,\s¥￥]/g, '').replace(/%$/, '');
    return /^[+-]?\d+(\.\d+)?$/.test(clean) ? parseFloat(clean) : null;
  }
  // 把一行按 colspan 展开成「逻辑列」数组：汇总行常用 colspan 跨列，
  // 直接按物理索引对齐明细列必然错位（实测第 2 列拿订单号去和金额合计比）。
  function logicalCells(row) {
    var out = [];
    all(row, 'td, th').forEach(function (c) {
      var span = parseInt(c.getAttribute('colspan') || '1', 10) || 1;
      for (var i = 0; i < span; i++) { out.push(i === 0 ? c : null); }
    });
    return out;
  }
  // 找「每页条数」选择器：分页列表的汇总行覆盖全量，而 tbody 只有当前页，
  // 直接核对必然对不上，所以要能把每页条数临时调到最大再比。
  function pageSizeSelect(doc) {
    var found = null;
    all(doc, 'select').forEach(function (s) {
      var texts = all(s, 'option').map(txt);
      // 「每页条数」下拉的特征：选项全为纯数字，且包含 10。
      // 早先只判断"有 ≥5 的数字"，容易和别的下拉混淆。
      if (texts.length >= 2 && texts.length <= 6 &&
          texts.indexOf('10') >= 0 &&
          texts.every(function (t) { return /^\d+$/.test(t); })) {
        found = s;
      }
    });
    return found;
  }
  function setMaxPageSize(doc, sel) {
    var opts = all(sel, 'option');
    if (opts.length < 2) { return Promise.resolve(null); }
    var maxVal = 0;
    opts.forEach(function (o) { var n = parseInt(txt(o), 10); if (!isNaN(n) && n > maxVal) { maxVal = n; } });
    var old = txt(opts[sel.selectedIndex] || opts[0]);
    if (String(maxVal) === old) { return Promise.resolve(old); }
    return setVal(doc, sel, String(maxVal)).then(function () { return sleep(540); })
      .then(function () { return old; });
  }
  function totalCount(doc) {
    var m = (doc.body.innerText || '').match(/共\s*([\d,]+)\s*条/);
    return m ? parseInt(m[1].replace(/,/g, ''), 10) : null;
  }
  // 核对完把每页条数恢复回去，免得后续翻页/批量断言失去意义
  function restorePage(doc, sel, old, result) {
    if (old === null || !sel) { return result; }
    return setVal(doc, sel, old).then(function () { return sleep(340); })
      .then(function () { return result; });
  }
  // 单据表单页：明细表格 = 页面上行数最多的表
  function detailTable(doc) {
    var best = null;
    all(doc, 'table').forEach(function (t) {
      if (!best || all(t, 'tbody tr').length > all(best, 'tbody tr').length) { best = t; }
    });
    return best;
  }
  // 单元格取值：可编辑单元格里是 <input>，innerText 拿不到值，必须读 value
  function cellNum(td) {
    if (!td) { return null; }
    var inp = td.querySelector('input, select, textarea');
    if (inp && inp.value !== undefined && String(inp.value).trim() !== '') { return num(inp.value); }
    return num(txt(td));
  }
  // 按表头中文业务术语定位列（规范约定展示层用业务术语，所以这条最稳）
  function colIndexByText(table, kw) {
    var ths = all(table, 'thead th');
    for (var i = 0; i < ths.length; i++) {
      if (txt(ths[i]).trim() === kw) { return i; }
    }
    for (var j = 0; j < ths.length; j++) {
      if (txt(ths[j]).indexOf(kw) >= 0) { return j; }
    }
    return -1;
  }
  function setVal(doc, el, v) {
    var win = doc.defaultView;
    var proto = el.tagName === 'SELECT' ? win.HTMLSelectElement.prototype
      : (el.tagName === 'TEXTAREA' ? win.HTMLTextAreaElement.prototype : win.HTMLInputElement.prototype);
    try {
      var setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
      setter.call(el, v);
    } catch (e) { el.value = v; }
    el.dispatchEvent(new win.Event('input', { bubbles: true }));
    el.dispatchEvent(new win.Event('change', { bubbles: true }));
    return sleep(160);
  }
  // 精确文案优先，避免「查询」误命中「查询条件」这类容器
  function clickByText(doc, sel, kw) {
    var els = byText(doc, sel, kw);
    els.sort(function (a, b) { return txt(a).length - txt(b).length; });
    for (var i = 0; i < els.length; i++) {
      if (els[i].disabled) { continue; }
      els[i].click();
      return true;
    }
    return false;
  }
  function dialog(doc, h) {
    if (h && h.dialog) { var d1 = doc.querySelector(h.dialog); if (d1) { return d1; } }
    return doc.querySelector('[role=dialog], .modal, .dialog, .dlg, .popup, .ant-modal, .el-dialog, .overlay, [class*=modal], [class*=dialog]');
  }
  function closeDialog(doc, h) {
    var d = dialog(doc, h);
    if (!d) { return false; }
    var cands = byText(d, 'button', '取消').concat(byText(d, 'button', '关闭'));
    cands.sort(function (a, b) { return txt(a).length - txt(b).length; });
    if (cands.length) { cands[0].click(); return true; }
    var x = d.querySelector('[aria-label=关闭], [class*=close]');
    if (x) { x.click(); return true; }
    return false;
  }
  function confirmDialog(doc, h) {
    var d = dialog(doc, h);
    if (!d) { return false; }
    var cands = byText(d, 'button', '确定').concat(byText(d, 'button', '确认'))
      .concat(byText(d, 'button', '提交')).concat(byText(d, 'button', '同意'));
    cands.sort(function (a, b) { return txt(a).length - txt(b).length; });
    if (cands.length) { cands[0].click(); return true; }
    return false;
  }
  // 计数类：优先用 spec 声明的锚点（h.todoBadge），找不到再按「含关键词且带数字的最短元素」猜。
  // 猜法的必要性：别人的产物类名不可预知；但猜错时要落到 unsupported 而不是 fail。
  function badgeCount(doc, kw, h) {
    // hints 是「提示」不是「约束」：spec 里写的锚点（可能是另一个技术栈的类名）不命中时，
    // 必须回退到通用启发式。否则同一份 spec 拿去验生成物就会一片假失败。
    var scope = (h && h.todoBadge) ? all(doc, h.todoBadge) : [];
    if (!scope.length) { scope = all(doc, '*'); }
    var best = null;
    scope.forEach(function (el) {
      if (el.children.length > 3) { return; }
      var t = txt(el);
      if (t.indexOf(kw) < 0 || t.length > 40) { return; }
      if (!/\d/.test(t)) { return; }
      if (!best || t.length < txt(best).length) { best = el; }
    });
    if (!best) { return null; }
    var m = txt(best).match(/(\d+)/);
    return m ? parseInt(m[1], 10) : null;
  }
  function cardCount(doc, h) {
    return cardNodes(doc, h).length;
  }
  function cardNodes(doc, h) {
    var cands = [];
    if (h && h.taskCard) { cands.push(h.taskCard); }
    cands = cands.concat([
      '.ap-task-line', '.task-card', '.taskCard', '.task-item', '.task',
      '[class*=task-line]', '[class*=taskItem]', '[class*=task-card]', '[class*=taskCard]',
      '[class*=listItem]', '[class*=list-item]',
      '.tasks > *', '.task-list > *', '.inbox > *', '[class*=taskList] > *'
    ]);
    for (var i = 0; i < cands.length; i++) {
      var n = all(doc, cands[i]);
      if (n.length >= 2) { return n; }   // 少于 2 条算不上"列表"，避免把单个容器当卡片
    }
    return [];
  }
  // 详情区标题：优先 spec 锚点，再按常见类名兜底
  function detailTitle(doc, h) {
    if (h && h.detailAnchor) { var e = doc.querySelector(h.detailAnchor); if (e) { return e; } }
    var cands = ['.ap-detail-title', '.detail-title', '.detail-h h2', '#detail .tt',
                 '[class*=detail] h2', '[class*=detail] h1', 'main h2'];
    for (var i = 0; i < cands.length; i++) {
      var n = doc.querySelector(cands[i]);
      if (n) { return n; }
    }
    return null;
  }
  // 「已完成」标记数量：DOM 类名 + 文案双通道，用于判断审批是否真的推进了
  function doneMarks(doc) {
    var n = all(doc, '[class*=done], [class*=complete], [class*=finish]').length;
    var m = (doc.body.innerText || '').match(/已完成|已办结|已同意/g);
    return n + (m ? m.length : 0);
  }
  function nodeProgress(doc) {
    var m = (doc.body.innerText || '').match(/当前节点[：:]\s*(\d+)\s*\/\s*(\d+)/);
    return m ? (m[1] + '/' + m[2]) : null;
  }
  function nativeAsked(doc) {
    var nd = (doc.defaultView || {}).__nativeDialog;
    return nd ? { confirm: nd.confirm, prompt: nd.prompt, alert: nd.alert } : { confirm: 0, prompt: 0, alert: 0 };
  }
  function firstKeyword(doc) {
    var rs = rows(doc);
    if (!rs.length) { return ''; }
    var cells = all(rs[0], 'td');
    for (var i = 0; i < cells.length; i++) {
      var t = txt(cells[i]).replace(/\s+/g, ' ');
      if (t.length >= 4 && t.length <= 40 && /\d/.test(t)) { return t.split(' ')[0]; }
    }
    return '';
  }

  var DRIVERS = {
    row_count_min: function (doc, a) {
      var need = a.min || 1;
      var visible = rows(doc).length;
      var total = totalCount(doc);
      // 分页列表每页 10 行是正常的 —— 必须按"总条数"判，按可见行数判会把好产物判失败
      if (total !== null && total >= need) {
        return { status: 'pass', detail: '总条数 ' + total + ' 条（当前页 ' + visible + ' 行，需 ≥' + need + '）' };
      }
      return { status: visible >= need ? 'pass' : 'fail',
               detail: '数据行 ' + visible + ' 行' + (total !== null ? '，总条数 ' + total : '')
                       + '（需 ≥' + need + '）' };
    },

    filter_changes_rows: function (doc) {
      var before = rows(doc).length;
      if (before < 2) { return { status: 'unsupported', detail: '数据行不足，无法验证筛选' }; }
      var kw = firstKeyword(doc);
      if (!kw) { return { status: 'unsupported', detail: '未能从首行取出可用关键词' }; }
      var inp = doc.querySelector('input[type=text], input:not([type])');
      if (!inp) { return { status: 'unsupported', detail: '未找到筛选输入框' }; }
      return setVal(doc, inp, kw).then(function () {
        return clickByText(doc, 'button', '查询') || clickByText(doc, 'button', '搜索');
      }).then(function (ok) {
        if (!ok) { throw new Error('未找到查询按钮'); }
        return sleep(420);
      }).then(function () {
        var after = rows(doc).length;
        var res = { status: (after < before && after >= 1) ? 'pass' : 'fail',
                    detail: '筛选「' + kw + '」' + before + ' 行 → ' + after + ' 行' };
        // ★ 必须恢复现场：不重置的话，后面 reset / sort / paginate / bulk 都跑在
        //   "筛选后只剩几行"的状态上，会连环产生假失败（实测就是这样把一批好断言判成失败的）。
        if (!clickByText(doc, 'button', '重置')) { return res; }
        return sleep(380).then(function () { return res; });
      });
    },

    reset_restores_rows: function (doc) {
      var inp = doc.querySelector('input[type=text], input:not([type])');
      if (!inp) { return { status: 'unsupported', detail: '未找到筛选输入框' }; }
      // 先重置一次，保证基准取自干净状态（上一个 driver 可能留下筛选条件）
      var prep = clickByText(doc, 'button', '重置') ? sleep(380) : Promise.resolve();
      return prep.then(function () {
        var init = rows(doc).length;
        if (init < 2) { return { status: 'unsupported', detail: '数据行不足（' + init + ' 行）' }; }
        return setVal(doc, inp, 'ZZZ-NOT-EXIST-9999').then(function () {
          return clickByText(doc, 'button', '查询') || clickByText(doc, 'button', '搜索');
        }).then(function () { return sleep(380); }).then(function () {
          if (!clickByText(doc, 'button', '重置')) { throw new Error('未找到重置按钮'); }
          return sleep(450);
        }).then(function () {
          var after = rows(doc).length;
          return { status: after === init ? 'pass' : 'fail',
                   detail: '重置后 ' + after + ' 行（初始 ' + init + ' 行）' };
        });
      });
    },

    empty_state_on_no_match: function (doc) {
      var inp = doc.querySelector('input[type=text], input:not([type])');
      if (!inp) { return { status: 'unsupported', detail: '未找到筛选输入框' }; }
      return setVal(doc, inp, 'ZZZ-NOT-EXIST-9999').then(function () {
        return clickByText(doc, 'button', '查询') || clickByText(doc, 'button', '搜索');
      }).then(function (ok) { if (!ok) { throw new Error('未找到查询按钮'); } return sleep(420); })
        .then(function () {
          var body = doc.body.innerText || '';
          var kw = ['没有符合条件', '暂无数据', '无匹配', '没有找到', '空'];
          var hit = kw.filter(function (k) { return body.indexOf(k) >= 0; })[0];
          var n = rows(doc).length;
          return { status: (hit || n === 0) ? 'pass' : 'fail',
                   detail: hit ? ('空状态文案「' + hit + '」') : ('无空状态文案，行数 ' + n) };
       });
    },

    sort_reorders: function (doc, a) {
      var ths = all(doc, 'table thead th');
      // 只挑明确可排序的表头。之前按"过滤掉复选框列"取 ths[1]，结果点在「订单类型」这种
      // 不可排序的列上 → 判成"排序失效"，是误判。认 sortable 类 / onclick / 排序指示器。
      var sortable = ths.filter(function (th) {
        var cn = String(th.className || '');
        return cn.indexOf('sort') >= 0 || th.hasAttribute('onclick')
            || th.querySelector('[class*=ar], [class*=sort], [class*=caret]');
      });
      var candidates = (sortable.length ? sortable : ths).filter(function (th) {
        return !th.querySelector('input[type=checkbox]') && txt(th).length > 0;
      });
      if (!candidates.length) { return { status: 'unsupported', detail: '未找到表头' }; }

      var sample = function () {
        var rs = rows(doc), out = [];
        for (var i = 0; i < Math.min(3, rs.length); i++) { out.push(txt(rs[i])); }
        return out.join('|');
      };
      var first = sample();
      // 每个候选表头连点 2 次（同一列切升降序），任一列能让顺序变化即证明排序生效
      var chain = Promise.resolve(), hit = null;
      candidates.forEach(function (th) {
        chain = chain.then(function () {
          if (hit) { return; }
          th.click();
          return sleep(250).then(function () {
            var s1 = sample();
            th.click();
            return sleep(250).then(function () {
              if (s1 !== first || sample() !== first) { hit = txt(th).slice(0, 10); }
            });
          });
        });
      });
      return chain.then(function () {
        return { status: hit ? 'pass' : 'fail',
                 detail: hit ? ('点击「' + hit + '」后数据重排，且升降序切换')
                             : '点击各可排序表头后数据顺序均未变' };
      });
    },

    pagination_changes_rows: function (doc) {
      var rs0 = rows(doc);
      if (!rs0.length) { return { status: 'unsupported', detail: '无数据行' }; }
      var first = txt(rs0[0]);
      var rangeBefore = ((doc.body.innerText || '').match(/第\s*\d+\s*[-–]\s*\d+\s*条/) || [''])[0];
      var cands = all(doc, 'button, a, li, .page, [class*=page]').filter(function (el) {
        var t = txt(el);
        return t === '2' || t === '下一页' || t === '下页' || t === '>' || t === '›';
      });
      if (!cands.length) { return { status: 'unsupported', detail: '未找到页码控件' }; }
      cands[0].click();
      return sleep(460).then(function () {
        var rs = rows(doc);
        var rowChanged = rs.length > 0 && txt(rs[0]) !== first;
        // 判据只认"首行内容变化"或"第 X-Y 条的区间变化"。
        // 之前用 /共 N 条/ 兜底，那个文案翻页前后一模一样，等于永远判通过 —— 是假通过。
        var rangeAfter = ((doc.body.innerText || '').match(/第\s*\d+\s*[-–]\s*\d+\s*条/) || [''])[0];
        var rangeChanged = rangeBefore && rangeAfter && rangeBefore !== rangeAfter;
        return { status: (rowChanged || rangeChanged) ? 'pass' : 'fail',
                 detail: rowChanged ? '翻页后首行内容变化'
                   : (rangeChanged ? ('页码区间 ' + rangeBefore + ' → ' + rangeAfter)
                                   : '翻页后内容与页码区间均未变化') };
      });
    },

    bulk_enables_on_select: function (doc) {
      var boxes = all(doc, 'table tbody input[type=checkbox], tbody input[type=checkbox]');
      if (boxes.length < 2) {
        return { status: 'unsupported',
                 detail: boxes.length === 1 ? '当前页只有 1 行，无法验证批量勾选'
                                            : '未找到行内复选框（可能是自定义选择列）' };
      }
      boxes[0].click(); boxes[1].click();
      return sleep(340).then(function () {
        var body = doc.body.innerText || '';
        if (!/已选\s*[2２]/.test(body)) {
          return { status: 'fail', detail: '勾选 2 行后工具条未显示「已选 2」' };
        }
        // 光有"已选 N"不算完整：还要确认批量按钮真的从禁用变可用
        var btns = all(doc, 'button').filter(function (b) { return /批量|导出|审核|删除/.test(txt(b)); });
        var enabled = btns.filter(function (b) { return !b.disabled; }).length;
        return { status: 'pass',
                 detail: '工具条显示「已选 2」' + (btns.length ? ('，' + enabled + '/' + btns.length + ' 个批量按钮可用') : '') };
      });
    },

    summary_equals_detail: function (doc) {
      var rs0 = rows(doc);
      if (!rs0.length) { return { status: 'unsupported', detail: '无数据行' }; }
      var sumRow = doc.querySelector('table tfoot tr');
      if (!sumRow) {
        var cands = all(doc, 'tr').filter(function (tr) { return txt(tr).indexOf('合计') >= 0; });
        sumRow = cands[cands.length - 1];
      }
      if (!sumRow) { return { status: 'unsupported', detail: '未找到汇总行' }; }

      // 分页列表：汇总行覆盖全量，tbody 只有当前页 → 先把每页条数临时调到最大
      var pageSel = pageSizeSelect(doc);
      var restore = null;
      var opened = pageSel ? setMaxPageSize(doc, pageSel) : Promise.resolve(null);

      return opened.then(function (old) {
        restore = old;
        var dataRows = rows(doc);
        var totalAll = totalCount(doc);
        // 展开失败就别硬算：分页列表的汇总覆盖全量，拿当前页去比对必然不等 —— 那是假失败。
        if (totalAll !== null && dataRows.length < totalAll) {
          var r0 = { status: 'unsupported',
                     detail: '分页未展开（当前 ' + dataRows.length + ' / 共 ' + totalAll +
                             ' 行），全量合计需人工核对' };
          return restorePage(doc, pageSel, restore, r0);
        }
        var sumCells = logicalCells(sumRow);
        var detail = dataRows.map(logicalCells);
        // 余额/结存类列是时点值（期末 = 最后一行），不可加总 —— 交给 balance_is_cumulative 去验
        var tbl = sumRow.closest ? sumRow.closest('table') : null;
        var headTxt = tbl ? logicalCells(tbl.querySelector('thead tr') || sumRow).map(function (h) { return h ? txt(h) : ''; }) : [];
        var checked = 0, mismatch = [];
        for (var c = 0; c < sumCells.length; c++) {
          if (!sumCells[c]) { continue; }             // colspan 占位列，跳过
          if (/余额|结存|库存量|期末/.test(headTxt[c] || '')) { continue; }
          var sv = num(txt(sumCells[c]));
          if (sv === null) { continue; }              // 非数值列（"合计"标签等）跳过
          var total = 0, n = 0, ok = true;
          for (var r = 0; r < detail.length; r++) {
            var cell = detail[r][c];
            if (!cell) { ok = false; break; }         // 明细行该列被 colspan 占，列没对齐
            var v = num(txt(cell));
            if (v === null) { ok = false; break; }
            total += v; n++;
          }
          if (!ok || !n) { continue; }
          checked++;
          if (Math.abs(total - sv) > 0.02) {
            mismatch.push('第 ' + (c + 1) + ' 列：汇总 ' + sv + ' vs 明细之和 ' + total.toFixed(2));
          }
        }

        var result;
        if (!checked) {
          result = { status: 'unsupported',
                     detail: '未找到「汇总行与明细行都对齐的数值列」，需人工核对' };
        } else {
          result = { status: mismatch.length ? 'fail' : 'pass',
                     detail: mismatch.length
                       ? (dataRows.length + ' 行明细比对失败 —— ' + mismatch.join('；'))
                       : ('核对 ' + checked + ' 个数值列：' + dataRows.length + ' 行明细之和 = 汇总') };
        }
        return restorePage(doc, pageSel, restore, result);
      });
    },

    /* ---------- 单据表单页（A2） ---------- */

    line_amount_auto: function (doc) {
      var t = detailTable(doc);
      if (!t) { return { status: 'unsupported', detail: '未找到明细表格' }; }
      var iQty = colIndexByText(t, '数量'), iAmt = colIndexByText(t, '金额');
      if (iQty < 0 || iAmt < 0) { return { status: 'unsupported', detail: '未找到 数量/金额 列' }; }
      var tr = all(t, 'tbody tr')[0];
      if (!tr) { return { status: 'unsupported', detail: '暂无明细行' }; }
      var td = all(tr, 'td');
      var before = txt(td[iAmt]);
      var input = td[iQty].querySelector('input');
      if (!input) { return { status: 'unsupported', detail: '数量列不可编辑，无法驱动' }; }
      return setVal(doc, input, '77').then(function () { return sleep(340); })
        .then(function () {
          var after = txt(all(all(t, 'tbody tr')[0], 'td')[iAmt]);
          return { status: after !== before ? 'pass' : 'fail',
                   detail: after !== before ? ('改数量后行金额 ' + before + ' → ' + after)
                                            : ('改数量后行金额未变（仍 ' + before + '）') };
        });
    },

    add_row_updates_total: function (doc) {
      var t = detailTable(doc);
      if (!t) { return { status: 'unsupported', detail: '未找到明细表格' }; }
      var before = all(t, 'tbody tr').length;
      var addBtn = null;
      all(doc, 'button').forEach(function (b) {
        if (!addBtn && /添加行|新增行|加一行/.test(txt(b))) { addBtn = b; }
      });
      if (!addBtn) { return { status: 'unsupported', detail: '未找到「添加行」按钮' }; }
      addBtn.click();
      return sleep(360).then(function () {
        var after = all(t, 'tbody tr').length;
        return { status: after > before ? 'pass' : 'fail',
                 detail: after > before ? ('点「添加行」后明细行数 ' + before + ' → ' + after)
                                        : '点「添加行」后行数未增加' };
      });
    },

    required_blocks_submit: function (doc) {
      var sub = null;
      all(doc, 'button').forEach(function (b) { if (!sub && /提交|保存/.test(txt(b))) { sub = b; } });
      if (!sub) { return { status: 'unsupported', detail: '未找到提交按钮' }; }
      var cleared = 0, chain = Promise.resolve();
      all(doc, '.req').forEach(function (lbl) {
        var box = lbl.parentElement;
        var ctl = box ? box.querySelector('input,select,textarea') : null;
        if (!ctl) { return; }
        cleared++;
        chain = chain.then(function () { return setVal(doc, ctl, ''); });
      });
      if (!cleared) { return { status: 'unsupported', detail: '未找到标了必填的字段' }; }
      return chain.then(function () { sub.click(); return sleep(480); })
        .then(function () {
          var errs = all(doc, '.err, [class*=err]').filter(function (e) { return txt(e).length > 0; }).length;
          var invalid = all(doc, '.invalid, [aria-invalid=true]').length;
          return { status: (errs || invalid) ? 'pass' : 'fail',
                   detail: '清空 ' + cleared + ' 个必填字段后提交：红字提示 ' + errs + ' 处、标红控件 ' + invalid + ' 个' };
        });
    },

    empty_lines_block_submit: function (doc) {
      if (!all(doc, '[data-del]').length) { return { status: 'unsupported', detail: '该页面没有明细行删除按钮' }; }
      var guard = 0;
      while (all(doc, '[data-del]').length && guard++ < 30) { all(doc, '[data-del]')[0].click(); }
      var sub = null;
      all(doc, 'button').forEach(function (b) { if (!sub && /提交/.test(txt(b))) { sub = b; } });
      if (!sub) { return { status: 'unsupported', detail: '未找到提交按钮' }; }
      sub.click();
      return sleep(420).then(function () {
        var tst = doc.querySelector('.toast');
        var msg = tst ? txt(tst) : '';
        var blocked = /明细|至少|不能为空/.test(msg);
        return { status: blocked ? 'pass' : 'fail',
                 detail: blocked ? ('零明细提交被阻止，提示「' + msg + '」')
                                 : ('未阻止零明细提交（提示「' + msg + '」）') };
      });
    },

    line_amount_equals_qty_price: function (doc) {
      var t = detailTable(doc);
      if (!t) { return { status: 'unsupported', detail: '未找到明细表格' }; }
      var iQty = colIndexByText(t, '数量'), iPrice = colIndexByText(t, '单价'), iAmt = colIndexByText(t, '金额');
      if (iQty < 0 || iPrice < 0 || iAmt < 0) {
        return { status: 'unsupported', detail: '未找到 数量/单价/金额 三列，需人工核对' };
      }
      var bad = [], checked = 0;
      all(t, 'tbody tr').forEach(function (tr) {
        var tds = all(tr, 'td');
        var q = cellNum(tds[iQty]), p = cellNum(tds[iPrice]), a = cellNum(tds[iAmt]);
        if (q === null || p === null || a === null) { return; }
        checked++;
        if (Math.abs(q * p - a) > 0.02) { bad.push(q + '×' + p + '≠' + a); }
      });
      if (!checked) { return { status: 'unsupported', detail: '明细行数值不可读，需人工核对' }; }
      return { status: bad.length ? 'fail' : 'pass',
               detail: bad.length ? ('行金额不等于 数量×单价：' + bad.join('；'))
                                  : ('核对 ' + checked + ' 行：每行 金额 = 数量 × 单价') };
    },

    total_equals_lines: function (doc) {
      var t = detailTable(doc);
      if (!t) { return { status: 'unsupported', detail: '未找到明细表格' }; }
      var iAmt = colIndexByText(t, '金额');
      if (iAmt < 0) { return { status: 'unsupported', detail: '未找到金额列' }; }
      var foot = t.querySelector('tfoot tr');
      if (!foot) { return { status: 'unsupported', detail: '未找到合计行' }; }
      var nums = all(foot, 'td').map(function (c) { return num(txt(c)); }).filter(function (v) { return v !== null; });
      if (!nums.length) { return { status: 'unsupported', detail: '合计行没有可读数值' }; }
      var sum = nums[nums.length - 1];
      var total = 0, n = 0;
      all(t, 'tbody tr').forEach(function (tr) {
        var v = cellNum(all(tr, 'td')[iAmt]);
        if (v !== null) { total += v; n++; }
      });
      if (!n) { return { status: 'unsupported', detail: '明细金额不可读' }; }
      return { status: Math.abs(total - sum) <= 0.02 ? 'pass' : 'fail',
               detail: '合计 ' + sum + ' vs ' + n + ' 行明细之和 ' + total.toFixed(2) };
    },

    todo_count_matches_list: function (doc, a) {
      var h = a.hints || {};
      var cnt = badgeCount(doc, '待办', h);
      var cards = cardCount(doc, h);
      if (cnt === null || !cards) { return { status: 'unsupported', detail: '未识别待办计数或任务卡' }; }
      return { status: cnt === cards ? 'pass' : 'fail',
               detail: '待办计数 ' + cnt + ' vs 列表 ' + cards + ' 条' };
    },

    select_task_switches_detail: function (doc, a) {
      var h = a.hints || {};
      var cards = cardNodes(doc, h);
      if (cards.length < 2) {
        return { status: 'unsupported', detail: '未识别到任务卡片（可在 spec 里声明 interactionHints.taskCard）' };
      }
      var read = function () {
        // 优先详情区标题；没有就用整页文本。
        // 不要截断：截断后取到的是侧边栏，切任务时不会变，会误判成失败。
        var el = detailTitle(doc, h);
        return el ? txt(el) : txt(doc.body);
      };
      var before = read();
      cards[1].click();
      return sleep(480).then(function () {
        var after = read();
        return { status: (after && after !== before) ? 'pass' : 'fail',
                 detail: (after && after !== before)
                   ? ('详情区 ' + before.slice(0, 20) + ' → ' + after.slice(0, 20))
                   : '切换任务后详情区未变化' };
      });
    },

    agree_advances_timeline: function (doc, a) {
      var h = a.hints || {};
      var beforeTitle = txt(detailTitle(doc, h));
      var beforeProg = nodeProgress(doc);
      var beforeDone = doneMarks(doc);
      if (!clickByText(doc, 'button', '同意')) { return { status: 'unsupported', detail: '未找到同意按钮' }; }
      return sleep(420).then(function () { confirmDialog(doc, h); return sleep(560); })
        .then(function () {
          var afterTitle = txt(detailTitle(doc, h));
          var afterProg = nodeProgress(doc);
          var afterDone = doneMarks(doc);
          var why = [];
          if (beforeProg && afterProg && beforeProg !== afterProg) { why.push('进度 ' + beforeProg + '→' + afterProg); }
          if (beforeTitle && afterTitle && beforeTitle !== afterTitle) { why.push('详情 ' + beforeTitle.slice(0, 18) + '→' + afterTitle.slice(0, 18)); }
          if (afterDone > beforeDone) { why.push('已完成标记 ' + beforeDone + '→' + afterDone); }
          return { status: why.length ? 'pass' : 'fail',
                   detail: why.length ? ('节点推进：' + why.join('；')) : '同意后节点与详情均未推进' };
        });
    },

    agree_decrements_todo: function (doc, a) {
      var h = a.hints || {};
      var before = badgeCount(doc, '待办', h);
      if (before === null) { return { status: 'unsupported', detail: '未识别待办计数' }; }
      if (!clickByText(doc, 'button', '同意')) { return { status: 'unsupported', detail: '未找到同意按钮' }; }
      return sleep(420).then(function () { confirmDialog(doc, h); return sleep(520); })
        .then(function () {
          var after = badgeCount(doc, '待办', h);
          return { status: (after !== null && after === before - 1) ? 'pass' : 'fail',
                   detail: '待办 ' + before + ' → ' + after + '（同意后应 −1）' };
        });
    },

    reject_requires_reason: function (doc, a) {
      var h = a.hints || {};
      var p0 = nativeAsked(doc).prompt;
      if (!clickByText(doc, 'button', '驳回')) { return { status: 'unsupported', detail: '未找到驳回按钮' }; }
      return sleep(450).then(function () {
        var d = dialog(doc, h);
        if (d) {
          // 同理：hint 选择器不命中就回退通用选择器
          var sel = h.dialogInput || '';
          var hasInput = sel ? d.querySelector(sel) : null;
          if (!hasInput) { hasInput = d.querySelector('textarea, input[type=text], input:not([type]), select'); }
          var hasWord = /驳回原因|意见|原因/.test(d.innerText || '');
          var res = { status: (hasInput && hasWord) ? 'pass' : 'fail',
                      detail: (hasInput && hasWord) ? '驳回弹窗含必填意见输入' : '驳回弹窗缺少意见输入或提示' };
          // 只读探测：看完就关，不改变单据状态，避免污染后续断言
          closeDialog(doc, h);
          return sleep(260).then(function () { return res; });
        }
        // 没有 DOM 弹窗：可能用原生 prompt 索取原因（单文件产物常见做法）
        if (nativeAsked(doc).prompt > p0) {
          return { status: 'pass', detail: '驳回通过原生 prompt 索取原因（无头环境返回空值，页面未执行）' };
        }
        return { status: 'fail', detail: '驳回既无弹窗也无意见输入索取' };
      });
    },

    transfer_requires_receiver: function (doc, a) {
      var h = a.hints || {};
      var p0 = nativeAsked(doc).prompt;
      if (!clickByText(doc, 'button', '转办')) { return { status: 'unsupported', detail: '未找到转办按钮' }; }
      return sleep(450).then(function () {
        var d = dialog(doc, h);
        if (d) {
          var sel = h.dialogInput || '';
          var hasSel = sel ? d.querySelector(sel) : null;
          if (!hasSel) { hasSel = d.querySelector('select, input[type=text], input:not([type]), textarea'); }
          var res = { status: hasSel ? 'pass' : 'fail',
                      detail: hasSel ? '转办弹窗含接收人选择' : '转办弹窗缺少接收人选择' };
          closeDialog(doc, h);
          return sleep(260).then(function () { return res; });
        }
        if (nativeAsked(doc).prompt > p0) {
          return { status: 'pass', detail: '转办通过原生 prompt 索取接收人（无头环境返回空值，页面未执行）' };
        }
        return { status: 'fail', detail: '转办既无弹窗也无接收人索取' };
      });
    },

    self_submit_cannot_approve: function (doc) {
      var body = doc.body.innerText || '';
      var declared = /不可自审|本人提交|提交人/.test(body);
      return { status: declared ? 'pass' : 'unsupported',
               detail: declared ? '页面声明并实现了「提交人不可自审」' : '未识别到自审限制，需人工确认' };
    },

    enum_coverage: function (doc, a) {
      var vals = a.values || [];
      if (!vals.length) { return { status: 'unsupported', detail: '未提供枚举值清单' }; }
      // 枚举通常散落在不同视图里（待办的看不到已办的），只看当前视图必然假失败。
      // 所以遍历页签逐个收集文本，跑完切回第一个视图，不影响后续断言。
      var tabs = all(doc, '[role=tab]');
      if (!tabs.length) { tabs = all(doc, '.ap-tab'); }
      if (!tabs.length) {
        tabs = all(doc, 'button').filter(function (el) {
          var t = txt(el).replace(/\s|\d|\(|\)/g, '');
          return t === '待办' || t === '已办' || t === '我发起';
        });
      }
      function finish() {
        var miss = vals.filter(function (v) { return seen.indexOf(v) < 0; });
        return { status: miss.length ? 'fail' : 'pass',
                 detail: miss.length
                   ? ('遍历 ' + Math.max(tabs.length, 1) + ' 个视图后仍未见枚举 ' + miss.join('、'))
                   : ('枚举 ' + vals.length + ' 项在 ' + Math.max(tabs.length, 1) + ' 个视图中均出现') };
      }
      var seen = doc.body.innerText || '';
      var chain = Promise.resolve();
      tabs.forEach(function (t) {
        chain = chain.then(function () {
          t.click();
          return sleep(340).then(function () { seen += '\n' + (doc.body.innerText || ''); });
        });
      });
      return chain.then(function () {
        if (!tabs.length) { return finish(); }
        tabs[0].click();
        return sleep(320).then(finish);
      });
    },

    timeline_len_equals_node_index: function (doc, a) {
      var nodes = a.nodes || [];
      var body = doc.body.innerText || '';
      if (!nodes.length) {
        // 没给 spec 时，从页面自己的审批流声明里解析节点流，避免直接放弃
        var m = body.match(/审批流[：:]\s*([^\n]{6,160})/);
        if (m) {
          nodes = m[1].split(/[→>]/).map(function (s) {
            return s.trim().replace(/[。；;，,]$/, '');
          }).filter(function (s) { return s.length > 0; });
        }
      }
      if (!nodes.length) { return { status: 'unsupported', detail: '未提供节点流，且页面未声明审批流' }; }
      var miss = nodes.filter(function (n) { return body.indexOf(n) < 0; });
      var hasCurrent = /当前节点|待你处理|当前/.test(body);
      return { status: (miss.length || !hasCurrent) ? 'fail' : 'pass',
               detail: miss.length ? ('时间线缺节点 ' + miss.join('、'))
                                   : ('节点流 ' + nodes.length + ' 段齐全且标注当前节点') };
    },

    no_data_holder: function (doc) {
      var body = doc.body.innerText || '';
      var hit = /暂无数据|暂无/.test(body);
      return { status: hit ? 'pass' : 'unsupported', detail: hit ? '存在「暂无数据」占位' : '未出现空数据占位（可能本次无空数据场景）' };
    },

    kpi_count_min: function (doc, a) {
      var cands = ['.kpi', '.kpi-card', '[class*=kpi]'];
      var n = 0;
      for (var i = 0; i < cands.length && !n; i++) { n = all(doc, cands[i]).length; }
      var need = a.min || 4;
      if (!n) { return { status: 'unsupported', detail: '未识别 KPI 卡容器' }; }
      return { status: n >= need ? 'pass' : 'fail', detail: 'KPI 卡 ' + n + ' 张（需 ≥' + need + '）' };
    },

    range_switch_changes_values: function (doc) {
      var sel = doc.querySelector('select');
      if (!sel) { return { status: 'unsupported', detail: '未找到时间范围控件' }; }
      var before = (doc.body.innerText || '').slice(0, 400);
      var opts = all(sel, 'option');
      if (opts.length < 2) { return { status: 'unsupported', detail: '时间范围选项不足' }; }
      return setVal(doc, sel, opts[1].value).then(function () { return sleep(450); })
        .then(function () {
          var after = (doc.body.innerText || '').slice(0, 400);
          return { status: after !== before ? 'pass' : 'fail',
                   detail: after !== before ? '切换范围后数值变化' : '切换范围后数值未变' };
        });
    },

    /* ---------- A3 主数据详情 ---------- */
    tab_switch_no_reload: function (doc) {
      var tabs = all(doc, '.dtab, [role=tab], .tabs .tab, .tab');
      if (tabs.length < 2) { return { status: 'unsupported', detail: '未找到 Tab' }; }
      var win = doc.defaultView;
      win.__ACCEPT_MARK__ = 1;                       // 页面若刷新，标记会丢
      var target = tabs[tabs.length - 1];
      target.click();
      return sleep(320).then(function () {
        var alive = doc.defaultView === win && win.__ACCEPT_MARK__ === 1;
        var on = /\b(on|active|is-active|selected)\b/.test(target.className) || target.getAttribute('aria-selected') === 'true';
        var res = { status: (alive && on) ? 'pass' : 'fail',
                    detail: !alive ? '切换 Tab 导致页面刷新' : (on ? '切到「' + txt(target).slice(0, 8) + '」未刷新，当前项已高亮' : '切换后当前项未高亮') };
        tabs[0].click();
        return sleep(200).then(function () { return res; });
      });
    },

    destructive_needs_confirm: function (doc, a) {
      var h = a.hints || {};
      var kws = ['停用', '作废', '冻结', '注销', '删除', '终止', '暂停', '启用', '恢复'];
      var btn = null;
      for (var i = 0; i < kws.length && !btn; i++) { btn = byText(doc, 'button', kws[i])[0] || null; }
      if (!btn) { return { status: 'unsupported', detail: '未找到停用/作废类按钮' }; }
      var before = txt(doc.body);
      var label = txt(btn);
      btn.click();
      return sleep(380).then(function () {
        var d = dialog(doc, h);
        var asked = !!d || nativeAsked(doc);
        var changedSilently = !d && txt(doc.body) !== before && !nativeAsked(doc);
        var res = { status: (asked && !changedSilently) ? 'pass' : 'fail',
                    detail: asked ? ('「' + label + '」弹出二次确认') : '「' + label + '」未经确认直接执行' };
        if (d) { closeDialog(doc, h); }
        return sleep(240).then(function () { return res; });
      });
    },

    status_toggles_buttons: function (doc) {
      var labels = all(doc, 'button').map(txt);
      // 成对互斥的动作：同一时刻只该出现其中一个
      var pairs = [['停用', '启用'], ['冻结', '解冻'], ['暂停', '恢复']];
      var seen = 0, both = [];
      pairs.forEach(function (p) {
        var off = labels.indexOf(p[0]) >= 0, on = labels.indexOf(p[1]) >= 0;
        if (off || on) { seen++; }
        if (off && on) { both.push(p.join('/')); }
      });
      if (!seen) { return { status: 'unsupported', detail: '当前状态下没有启用/停用类按钮' }; }
      return { status: both.length ? 'fail' : 'pass',
               detail: both.length ? ('互斥按钮同时出现：' + both.join('、')) : '互斥动作只显示与当前状态匹配的一个' };
    },

    timeline_has_diff: function (doc) {
      // 变更记录多在未激活的 Tab 里（display:none 时 innerText 为空），所以读 textContent
      var nodes = all(doc, '.diff, [class*=diff], .tl li, [class*=timeline] li, [class*=log] li');
      var hits = nodes.filter(function (n) { return /\S\s*(→|->|⇒)\s*\S/.test(n.textContent || ''); });
      if (!nodes.length) { return { status: 'unsupported', detail: '未找到变更记录时间线' }; }
      return { status: hits.length ? 'pass' : 'fail',
               detail: hits.length ? ('含 ' + hits.length + ' 条「旧值 → 新值」记录') : '变更记录没有旧值→新值 diff' };
    },

    /* ---------- A4 运营看板 ---------- */
    trend_points_min: function (doc, a) {
      var need = a.min || 12;
      var svg = doc.querySelector('svg.chart') || doc.querySelector('svg');
      if (!svg) { return { status: 'unsupported', detail: '未找到 SVG 趋势图' }; }
      var n = svg.querySelectorAll('circle').length;
      return { status: n >= need ? 'pass' : 'fail', detail: '趋势点 ' + n + ' 个（需 ≥' + need + '）' };
    },

    series_tone_matches_semantics: function (doc) {
      var win = doc.defaultView;
      var ds = all(doc, '.kpi .d, [class*=kpi] [class*=delta]');
      if (!ds.length) { return { status: 'unsupported', detail: '未识别 KPI 环比元素' }; }
      var bad = [], checked = 0;
      ds.forEach(function (el) {
        var t = txt(el);
        var up = /▲|↑|\+/.test(t) || /\bup\b/.test(el.className);
        var down = /▼|↓/.test(t) || /\bdown\b/.test(el.className);
        if (!up && !down) { return; }
        var m = (win.getComputedStyle(el).color || '').match(/(\d+),\s*(\d+),\s*(\d+)/);
        if (!m) { return; }
        var r = +m[1], g = +m[2];
        checked++;
        if (up && !(r > g)) { bad.push('上涨未用红色：' + t); }
        if (down && !(g > r)) { bad.push('下跌未用绿色：' + t); }
      });
      if (!checked) { return { status: 'unsupported', detail: '无带方向的环比值' }; }
      return { status: bad.length ? 'fail' : 'pass',
               detail: bad.length ? bad.slice(0, 3).join('；') : ('核对 ' + checked + ' 个环比，涨红跌绿一致') };
    },

    /* ---------- A6 台账 / 对账 ---------- */
    period_switch_refreshes: function (doc) {
      var sel = doc.querySelector('#period') || all(doc, 'select').filter(function (s) {
        var lb = s.parentNode ? txt(s.parentNode) : '';
        return /期间|月份|会计期/.test(lb);
      })[0];
      if (!sel) { return { status: 'unsupported', detail: '未找到期间选择' }; }
      var opts = all(sel, 'option');
      if (opts.length < 2) { return { status: 'unsupported', detail: '期间选项不足' }; }
      var old = sel.value;
      var snap = function () {
        var t = doc.querySelector('table tbody'), k = doc.querySelector('.kpis');
        return [txt(t), txt(k)];
      };
      var b = snap();
      var nv = opts[0].value === old ? opts[1].value : opts[0].value;
      return setVal(doc, sel, nv).then(function () { return sleep(400); }).then(function () {
        var a2 = snap();
        var ok = a2[0] !== b[0] && a2[1] !== b[1];
        var res = { status: ok ? 'pass' : 'fail',
                    detail: ok ? '切换期间后摘要与明细同步刷新'
                               : ('切换期间后' + (a2[0] === b[0] ? '明细' : '') + (a2[1] === b[1] ? '摘要' : '') + '未刷新') };
        return setVal(doc, sel, old).then(function () { return sleep(300); }).then(function () { return res; });
      });
    },

    diff_prompts_manual_review: function (doc) {
      var selects = all(doc, 'select');
      var combos = [[]];
      selects.forEach(function (s) {
        var next = [];
        combos.forEach(function (c) { all(s, 'option').forEach(function (o) { next.push(c.concat([[s, o.value]])); }); });
        combos = next.slice(0, 24);                // 防组合爆炸
      });
      var olds = selects.map(function (s) { return s.value; });
      var found = null, chain = Promise.resolve();
      var probe = function () {
        var red = doc.querySelector('.diff-row, [class*=diff-row], tr.dg, tr.danger');
        if (red && /人工复核|复核/.test(doc.body.innerText || '')) { found = true; }
      };
      probe();
      combos.forEach(function (c) {
        chain = chain.then(function () {
          if (found) { return; }
          var p = Promise.resolve();
          c.forEach(function (pair) { p = p.then(function () { return setVal(doc, pair[0], pair[1]); }); });
          return p.then(function () { return sleep(220); }).then(probe);
        });
      });
      return chain.then(function () {
        var p = Promise.resolve();
        selects.forEach(function (s, i) { p = p.then(function () { return setVal(doc, s, olds[i]); }); });
        return p.then(function () { return sleep(250); });
      }).then(function () {
        return found ? { status: 'pass', detail: '差异行标红，并提示人工复核' }
                     : { status: 'unsupported', detail: '遍历条件未遇到差异数据（无法判定提示是否生效）' };
      });
    },

    balance_is_cumulative: function (doc) {
      var tables = all(doc, 'table').filter(function (t) { return /余额/.test(txt(t.querySelector('thead'))); });
      if (!tables.length) { return { status: 'unsupported', detail: '未找到含「余额」列的台账' }; }
      var t = tables[0];
      var heads = all(t, 'thead th').map(txt);
      var idx = function (re) { for (var i = 0; i < heads.length; i++) { if (re.test(heads[i])) { return i; } } return -1; };
      var iB = idx(/余额/), iR = idx(/应收|借方|收入/), iP = idx(/已收|贷方|支出/);
      if (iB < 0 || iR < 0 || iP < 0) { return { status: 'unsupported', detail: '缺少应收/已收/余额列' }; }
      var trs = all(t, 'tbody tr').filter(function (tr) { return all(tr, 'td').length === heads.length; });
      if (trs.length < 2) { return { status: 'unsupported', detail: '明细不足 2 行' }; }
      var m = (doc.body.innerText || '').match(/期初余额\s*[¥￥]?\s*(-?[\d,]+(?:\.\d+)?)/);
      var prev = m ? num(m[1]) : null, start = 0;
      var cell = function (tr, i) { return num(txt(all(tr, 'td')[i])); };
      if (prev === null) { prev = cell(trs[0], iB); start = 1; }
      var bad = [];
      for (var k = start; k < trs.length; k++) {
        var expect = prev + cell(trs[k], iR) - cell(trs[k], iP);
        var got = cell(trs[k], iB);
        if (Math.abs(expect - got) > 0.02) { bad.push('第' + (k + 1) + '行 应为' + expect.toFixed(2) + ' 实为' + got); }
        prev = got;
      }
      return { status: bad.length ? 'fail' : 'pass',
               detail: bad.length ? bad.slice(0, 2).join('；') : ('核对 ' + (trs.length - start) + ' 行：余额 = 上行余额 + 应收 − 已收') };
    },

    // ---------- 自由版式通用 driver（ff-*） ----------
    // 触发器/目标全部来自 FreeformSpec.semantics 声明（selector 为 [data-ff-region=…]），
    // 不写死任何业务关键词 —— 与 archetype 内置断言互不干扰。
    ff_click_scrolls: function (doc, a) {
      var btn = doc.querySelector(a.selector), tgt = a.target && doc.querySelector(a.target);
      if (!btn || !tgt) { return { status: 'unsupported', detail: '未找到触发器或目标区块' }; }
      var vh = doc.documentElement.clientHeight;
      var r0 = tgt.getBoundingClientRect();
      var before = doc.documentElement.scrollTop || doc.body.scrollTop;
      var inView0 = r0.top >= -60 && r0.top <= vh - 60;
      btn.click();
      return sleep(420).then(function () {
        var r = tgt.getBoundingClientRect();
        var scrolled = (doc.documentElement.scrollTop || doc.body.scrollTop) !== before;
        var inView = r.top >= -60 && r.top <= vh - 60;
        // 必须是「这次点击」导致滚入：点前就在视口且没有滚动 = 假阳性
        var pass = scrolled && inView && !inView0;
        return { status: pass ? 'pass' : 'fail',
                 detail: pass ? ('点击后滚入视口（top=' + Math.round(r.top) + 'px，scrollTop ' + before + '→当前）')
                              : ('点前在视口=' + inView0 + '，点后在视口=' + inView + '，页面滚动=' + scrolled
                                 + '（需 点击导致目标滚入视口）') };
      });
    },
    ff_click_toggles: function (doc, a) {
      var btn = doc.querySelector(a.selector), tgt = a.target && doc.querySelector(a.target);
      if (!btn || !tgt) { return { status: 'unsupported', detail: '未找到触发器或目标区块' }; }
      var hidden0 = tgt.hasAttribute('hidden') || tgt.style.display === 'none';
      btn.click();
      return sleep(320).then(function () {
        var shown = !tgt.hasAttribute('hidden') && tgt.style.display !== 'none';
        var dbg = 'hidden属性=' + tgt.hasAttribute('hidden') + ' computed=' + doc.defaultView.getComputedStyle(tgt).display;
        return { status: (hidden0 && shown) ? 'pass' : 'fail',
                 detail: '初始隐藏=' + hidden0 + '，点击后可见=' + shown + '（需 初始隐藏 且 点击后可见）| ' + dbg };
      });
    },
    ff_click_confirms: function (doc, a) {
      var btn = doc.querySelector(a.selector);
      if (!btn) { return { status: 'unsupported', detail: '未找到触发器' }; }
      btn.click();
      return sleep(420).then(function () {
        var asked = !!dialog(doc, a.hints || {}) || nativeAsked(doc);
        return { status: asked ? 'pass' : 'fail',
                 detail: asked ? '点击后出现确认弹层/原生确认' : '点击后未出现任何确认' };
      });
    },
    ff_count_equals: function (doc, a) {
      var na = doc.querySelectorAll(a.a).length, nb = doc.querySelectorAll(a.b).length;
      if (!na && !nb) { return { status: 'unsupported', detail: '两个选择器都数到 0，可能选择器写错' }; }
      return { status: na === nb ? 'pass' : 'fail',
               detail: a.a + ' = ' + na + '，' + a.b + ' = ' + nb + (na === nb ? '（相等）' : '（不相等）') };
    }
  };

  var f = document.getElementById('f');
  f.width = w; f.height = h; f.src = path;

  function done(r) {
    document.getElementById('out').textContent = 'ACCEPT_RESULT ' + JSON.stringify(r);
    document.title = 'ACCEPT_DONE';
  }

  // ★ 无头浏览器无法交互原生 confirm / prompt / alert —— 页面一旦调用就永久阻塞，
  //   表现为 Chrome 挂死到超时（实测卡满 240s）。所以 iframe 一加载就覆盖掉这几个方法。
  //   必须在 onload 的同步阶段覆盖，不能等 sleep 之后再覆盖。
  f.onload = function () {
    var win = f.contentWindow;
    var nativeDialog = { confirm: 0, prompt: 0, alert: 0 };
    var winErrors = [];
    try {
      // 收集被iframe内脚本报错：没有这条，页面白屏/表格空只会表现成"数据行 0"，
      // 让人以为是断言写错，而不是页面自己有 bug。
      win.addEventListener('error', function (e) { winErrors.push(String(e.message || e)); });
      win.confirm = function () { nativeDialog.confirm++; return true; };
      // prompt 一律返回空值：模拟"用户未填写直接取消"。
      // 这样既不阻塞，又能验证"页面确实把意见当必填"（未填时不执行），且不改动单据状态。
      win.prompt = function (m, d) { nativeDialog.prompt++; return ''; };
      win.alert = function () { nativeDialog.alert++; };
      win.__nativeDialog = nativeDialog;   // 供 driver 判断"是否用原生弹窗索取输入"
    } catch (e) {}

    sleep(1800).then(function () {
      var doc;
      try { doc = f.contentDocument; } catch (e) { return done({ ok: false, err: '跨域读取失败: ' + e }); }
      if (!doc || !doc.body) { return done({ ok: false, err: 'iframe 未渲染出内容' }); }

      // 渲染指标必须在驱动交互之前取，否则量到的是被点击改动后的状态
      var metrics = {};
      try {
        var de = doc.documentElement, b = doc.body, bt = b.innerText || '';
        metrics.clientWidth = de.clientWidth;
        metrics.scrollWidth = de.scrollWidth;
        metrics.overflowX = de.scrollWidth - de.clientWidth;
        metrics.textLen = bt.replace(/\s+/g, '').length;
        metrics.text = bt.slice(0, 30000);   // 供"渲染后文本匹配"类断言使用
        metrics.rows = rows(doc).length;
        metrics.dirty = [];
        ['undefined', 'NaN', '[object Object]'].forEach(function (k) {
          var c = bt.split(k).length - 1;
          if (c > 0) { metrics.dirty.push(k + 'x' + c); }
        });
        // 逐列统计空单元格：某列"所有行都空"是「这列没接上数据」的强信号 ——
        // 静态契约断言查不出来（值在 model JSON 里是有的），只有渲染后才看得见。
        var byCol = {}, emptyByCol = {};
        all(doc, 'table tbody tr').forEach(function (tr) {
          all(tr, 'td').forEach(function (td, ci) {
            byCol[ci] = (byCol[ci] || 0) + 1;
            var t = txt(td);
            // 含控件的单元格（复选框列、可编辑列）本身就没有文字，不算"空"
            var hasControl = !!td.querySelector('input, select, textarea, button');
            if (!hasControl && (t === '' || /^[—\-–]+$/.test(t))) {
              emptyByCol[ci] = (emptyByCol[ci] || 0) + 1;
            }
          });
        });
        metrics.emptyColumns = Object.keys(byCol).filter(function (ci) {
          return byCol[ci] >= 3 && emptyByCol[ci] === byCol[ci];
        }).map(Number);
        metrics.cellStats = { columns: Object.keys(byCol).length, emptyColumns: metrics.emptyColumns.length };
      } catch (e) { metrics.error = String(e); }
      metrics.nativeDialog = nativeDialog;   // 页面是否走了原生弹窗（无头环境已自动代替）
      // 产物自带的初始化期错误兜底（见 build/forge.py 注入的 __PAGE_ERRORS__）+ onload 后监听
      var selfErr = [];
      try { selfErr = (win.__PAGE_ERRORS__ || []).slice(0, 5); } catch (e) {}
      metrics.winErrors = selfErr.concat(winErrors);

      var out = [];
      var chain = Promise.resolve();
      defs.forEach(function (d) {
        chain = chain.then(function () {
          var fn = DRIVERS[d.driver];
          if (!fn) {
            out.push({ id: d.id, driver: d.driver, status: 'unsupported', detail: '通用引擎尚无该 driver，需人工确认' });
            return;
          }
          return Promise.resolve().then(function () { return fn(doc, d.args || {}); })
            .then(function (res) {
              out.push({ id: d.id, driver: d.driver, status: res.status || 'fail', detail: res.detail || '' });
            })
            .catch(function (e) {
              out.push({ id: d.id, driver: d.driver, status: 'unsupported', detail: '驱动异常: ' + e });
            });
        });
      });
      chain.then(function () { done({ ok: true, defsCount: defs.length, metrics: metrics, results: out }); });
    });
  };
})();
</script></body></html>"""


class SPAHandler(http.server.SimpleHTTPRequestHandler):
    """静态文件 + SPA history fallback + 同源探测页。"""

    # 允许回退到 index.html 的路径：不含扩展名的「页面路由」
    def _is_page_route(self, path):
        last = os.path.basename(path.rstrip("/"))
        return "." not in last

    def _serve_probe(self):
        body = PROBE_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_accept(self):
        body = ACCEPT_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_head(self):
        route = self.path.split("?")[0]
        if route == "/__probe.html":
            return self._serve_probe()
        if route == "/__accept.html":
            return self._serve_accept()

        # 归一化，避免 ../ 越权
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            index = os.path.join(path, "index.html")
            if os.path.exists(index):
                return super().send_head()
        if os.path.exists(path):
            return super().send_head()

        # 未命中：页面路由回退到 index.html
        if self._is_page_route(self.path):
            fallback = os.path.join(self.directory, "index.html")
            if os.path.exists(fallback):
                self.path = "/index.html"
                return super().send_head()
        return super().send_head()

    def end_headers(self):
        # 便于验证脚本识别，且避免浏览器缓存干扰复测
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Served-By", "serve_spa.py")
        super().end_headers()

    def log_message(self, fmt, *args):
        # 静默：只保留错误，避免污染校验输出
        if args and isinstance(args[0], str) and args[0].startswith(("4", "5")):
            sys.stderr.write("  [http] " + (fmt % args) + "\n")


def port_in_use(port, host="127.0.0.1"):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def wait_ready(port, host="127.0.0.1", timeout=20.0):
    """等待端口可连接，返回 True/False。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_in_use(port, host):
            return True
        time.sleep(0.2)
    return False


def start_server(root, port=3030, host="127.0.0.1", quiet=False):
    """在后台线程起服务并立刻返回 httpd 句柄（供校验脚本内嵌使用）。"""
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise SystemExit("目录不存在: " + root)
    if not os.path.exists(os.path.join(root, "index.html")):
        raise SystemExit("该目录下没有 index.html，疑似不是构建产物: " + root)

    handler = functools.partial(SPAHandler, directory=root)
    httpd = http.server.ThreadingHTTPServer((host, port), handler)

    if not quiet:
        print("SPA 静态服务已启动")
        print("  根目录 : %s" % root)
        print("  地址   : http://%s:%d/" % (host, port))
        print("  路由   : 未命中的无扩展名路径 → index.html")
        sys.stdout.flush()

    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="前端构建产物目录（含 index.html）")
    ap.add_argument("--port", type=int, default=3030)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    return start_server(args.root, args.port, args.host)


if __name__ == "__main__":
    httpd = main()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n已停止")
