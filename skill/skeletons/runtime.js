/* ERP Page Forge · 通用运行时（骨架层）
 *
 * 只做「所有原型都会用到」的事：格式化、Toast、抽屉、自研弹窗、分页器。
 * 原型专属逻辑写在各骨架文件的 <script> 里。
 *
 * 刻意不用原生 alert/confirm/prompt：
 *   1) 原生弹窗在无头浏览器里会永久阻塞（验收跑不动）
 *   2) 原生弹窗无法定制样式，与 ERP 后台观感不符
 *   3) 自研弹窗是真实 DOM，验收断言能直接识别「驳回必须填原因」这类规则
 */
(function (w, d) {
  'use strict';
  var F = {};

  F.$ = function (sel, root) { return (root || d).querySelector(sel); };
  F.$$ = function (sel, root) {
    return Array.prototype.slice.call((root || d).querySelectorAll(sel));
  };

  /* ---------------- 格式化 ---------------- */
  F.num = function (n, p) {
    if (n === null || n === undefined || isNaN(n)) { return '—'; }
    p = (p === undefined ? 0 : p);
    return Number(n).toLocaleString('zh-CN', { minimumFractionDigits: p, maximumFractionDigits: p });
  };
  F.money = function (n) { return '¥' + F.num(n, 2); };
  F.pct = function (n, p) { return F.num(n, p === undefined ? 1 : p) + '%'; };

  /* ---------------- 状态标签（统一胶囊 + 语义色） ---------------- */
  var TONE = { brand: 't-brand', ok: 't-ok', warn: 't-warn', danger: 't-dg', purple: 't-pu', neutral: 't-nt' };
  F.tag = function (text, tone) {
    return '<span class="tag ' + (TONE[tone] || TONE.neutral) + '"><i></i>' + text + '</span>';
  };

  /* ---------------- Toast ---------------- */
  var toastEl = null, toastTimer = null;
  F.toast = function (msg) {
    if (!toastEl) {
      toastEl = d.createElement('div');
      toastEl.className = 'toast';
      d.body.appendChild(toastEl);
    }
    toastEl.textContent = msg;
    toastEl.classList.add('on');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove('on'); }, 2200);
  };

  /* ---------------- 抽屉 ---------------- */
  function ensureMask() {
    var m = F.$('#__mask');
    if (!m) {
      m = d.createElement('div');
      m.id = '__mask';
      m.className = 'mask';
      m.onclick = function () { F.closeDrawer(); };
      d.body.appendChild(m);
    }
    return m;
  }
  F.drawer = function (title, bodyHtml) {
    var dr = F.$('#__drawer');
    if (!dr) {
      dr = d.createElement('div');
      dr.id = '__drawer';
      dr.className = 'drawer';
      d.body.appendChild(dr);
    }
    dr.innerHTML =
      '<div class="drawer-h"><b>' + title + '</b>' +
      '<button class="x" aria-label="关闭" onclick="F.closeDrawer()">×</button></div>' +
      '<div class="drawer-b">' + bodyHtml + '</div>';
    ensureMask().classList.add('on');
    dr.classList.add('on');
  };
  F.closeDrawer = function () {
    var dr = F.$('#__drawer');
    if (dr) { dr.classList.remove('on'); }
    var m = F.$('#__mask');
    if (m) { m.classList.remove('on'); }
  };

  /* ---------------- 自研弹窗（Promise 风格） ---------------- */
  function ensureModal() {
    var m = F.$('#__modal');
    if (!m) {
      m = d.createElement('div');
      m.id = '__modal';
      m.className = 'modal';
      d.body.appendChild(m);
    }
    return m;
  }
  F.closeModal = function () {
    var m = F.$('#__modal');
    if (m) { m.classList.remove('on'); m.innerHTML = ''; }
  };

  /* opts = {title, note, fields:[{key,label,type,options,required,placeholder}], confirmText, danger} */
  F.modal = function (opts) {
    opts = opts || {};
    var fields = opts.fields || [];
    var rows = fields.map(function (f, i) {
      var ctrl;
      if (f.type === 'select') {
        ctrl = '<select data-i="' + i + '">' +
          (f.options || []).map(function (o) { return '<option value="' + o + '">' + o + '</option>'; }).join('') +
          '</select>';
      } else if (f.type === 'textarea') {
        ctrl = '<textarea data-i="' + i + '" rows="3" placeholder="' + (f.placeholder || '') + '"></textarea>';
      } else {
        ctrl = '<input data-i="' + i + '" type="text" placeholder="' + (f.placeholder || '') + '">';
      }
      return '<div style="margin-bottom:12px">' +
        '<label>' + f.label + '</label>' + ctrl +
        '<div class="err" data-err="' + i + '"></div></div>';
    }).join('');

    var m = ensureModal();
    m.innerHTML =
      '<div class="modal-box">' +
      '<div class="modal-h">' + (opts.title || '请确认') + '</div>' +
      '<div class="modal-b">' +
      (opts.note ? '<p style="margin:0 0 12px;font-size:13px;color:var(--text-2)">' + opts.note + '</p>' : '') +
      rows + '</div>' +
      '<div class="modal-f">' +
      '<button class="btn" data-act="cancel">取消</button>' +
      '<button class="btn ' + (opts.danger ? 'btn-dg' : 'btn-p') + '" data-act="ok">' +
      (opts.confirmText || '确定') + '</button>' +
      '</div></div>';
    m.classList.add('on');

    return new Promise(function (resolve) {
      function done(val) { F.closeModal(); resolve(val); }
      m.querySelector('[data-act="cancel"]').onclick = function () { done(null); };
      m.querySelector('[data-act="ok"]').onclick = function () {
        var out = {}, bad = false;
        fields.forEach(function (f, i) {
          var el = m.querySelector('[data-i="' + i + '"]');
          var err = m.querySelector('[data-err="' + i + '"]');
          var v = (el && el.value || '').trim();
          if (f.required && !v) {
            bad = true;
            if (err) { err.textContent = '请填写' + f.label; }
            if (el) { el.classList.add('invalid'); }
          } else if (err) { err.textContent = ''; }
          out[f.key] = v;
        });
        if (bad) { return; }
        done(out);
      };
      var first = m.querySelector('input,textarea');
      if (first) { first.focus(); }
    });
  };

  /* ---------------- 通用单元格渲染 ----------------
   * cols 是数据化的列定义（来自 PageSpec.columns + recipes 字段类型），
   * 所以「金额右对齐 + 千分位 + tabular-nums」「状态出胶囊」这些规则
   * 由运行时统一保证，不依赖每次生成时的发挥。
   */
  F.cells = function (row, cols) {
    return cols.map(function (c) {
      var raw = row[c.key];
      if (c.type === 'status') {
        var tone = row[c.toneKey || 'statusTone'] || 'neutral';
        return '<td class="c">' + F.tag(raw, tone) + '</td>';
      }
      var text;
      if (c.type === 'money') { text = F.money(raw); }
      else if (c.type === 'number') { text = F.num(raw, c.precision || 0) + (c.unit ? ' ' + c.unit : ''); }
      else if (c.type === 'percent') { text = F.pct(raw, c.precision || 0); }
      else { text = (raw === undefined || raw === null || raw === '') ? '—' : raw; }
      var cls = c.align === 'right' ? ' class="r num"' : (c.align === 'center' ? ' class="c"' : '');
      var inner = c.href ? '<span class="code">' + text + '</span>' : text;
      if (c.sub) { inner = '<div>' + inner + '</div><div class="muted">' + (row[c.sub] || '') + '</div>'; }
      return '<td' + cls + '>' + inner + '</td>';
    }).join('');
  };

  /* ---------------- 动作按钮（跟着状态走） ----------------
   * defs: [{act, label, states?, primary?, danger?, scope?}]
   * 不在 allowedStates 里的动作直接不渲染 —— 这是「已生效单据不显示编辑」的落地方式。
   */
  F.actions = function (row, defs, attrName, attrValue) {
    var out = defs.filter(function (d) {
      if (d.scope && d.scope !== 'row') { return false; }
      return !d.states || d.states.indexOf(row.status) >= 0;
    }).map(function (d) {
      var cls = 'btn btn-sm' + (d.primary ? ' btn-p' : '') + (d.danger ? ' btn-dg' : '');
      var attr = attrName ? ' ' + attrName + '="' + attrValue + '"' : '';
      return '<button class="' + cls + '"' + attr + ' data-act="' + d.act + '">' + d.label + '</button>';
    });
    return out.join(' ');
  };

  /* ---------------- 表单字段渲染（A2 用） ---------------- */
  F.field = function (f, val) {
    val = (val === undefined || val === null) ? '' : val;
    var ctrl;
    if (f.control === 'select' || f.type === 'enum') {
      ctrl = '<select data-key="' + f.key + '"' + (f.required ? ' class="req-field"' : '') + '>' +
        '<option value="">请选择</option>' +
        (f.options || []).map(function (o) {
          return '<option' + (String(o) === String(val) ? ' selected' : '') + '>' + o + '</option>';
        }).join('') + '</select>';
    } else if (f.type === 'textarea') {
      ctrl = '<textarea data-key="' + f.key + '" rows="2">' + val + '</textarea>';
    } else if (f.type === 'date') {
      ctrl = '<input data-key="' + f.key + '" type="date" value="' + val + '">';
    } else if (f.type === 'money' || f.type === 'number') {
      ctrl = '<input data-key="' + f.key + '" type="text" class="num" value="' + val + '">';
    } else {
      ctrl = '<input data-key="' + f.key + '" type="text" value="' + val + '">';
    }
    return '<div class="fld"><label class="' + (f.required ? 'req' : '') + '">' + f.name + '</label>' +
      ctrl + '<div class="err" data-err="' + f.key + '"></div></div>';
  };

  /* ---------------- 分页器 ---------------- */
  F.pager = function (host, st) {
    var totalPages = Math.max(1, Math.ceil(st.total / st.size));
    if (st.page > totalPages) { st.page = totalPages; }
    var from = st.total ? (st.page - 1) * st.size + 1 : 0;
    var to = Math.min(st.page * st.size, st.total);
    var btns = [];
    btns.push('<button class="pg" data-go="' + (st.page - 1) + '"' + (st.page <= 1 ? ' disabled' : '') + '>上一页</button>');
    var start = Math.max(1, st.page - 2), end = Math.min(totalPages, start + 4);
    start = Math.max(1, end - 4);
    for (var i = start; i <= end; i++) {
      btns.push('<button class="pg' + (i === st.page ? ' on' : '') + '" data-go="' + i + '">' + i + '</button>');
    }
    btns.push('<button class="pg" data-go="' + (st.page + 1) + '"' + (st.page >= totalPages ? ' disabled' : '') + '>下一页</button>');
    host.innerHTML =
      '<div>第 <b id="pFrom">' + from + '</b>-<b id="pTo">' + to + '</b> 条 / 共 <b id="pTotal">' + st.total + '</b> 条</div>' +
      '<div class="pages"><span style="color:var(--text-3);margin-right:6px">每页</span>' +
      '<select id="size" style="width:70px;height:28px" data-size>' +
      [10, 20, 50].map(function (n) { return '<option' + (n === st.size ? ' selected' : '') + '>' + n + '</option>'; }).join('') +
      '</select>' + btns.join('') + '</div>';

    F.$$('[data-go]', host).forEach(function (b) {
      b.onclick = function () {
        if (b.disabled) { return; }
        st.page = parseInt(b.getAttribute('data-go'), 10);
        st.onChange();
      };
    });
    var sz = F.$('[data-size]', host);
    if (sz) { sz.onchange = function () { st.size = parseInt(sz.value, 10); st.page = 1; st.onChange(); }; }
  };

  /* ---------------- 列表通用能力：筛选 / 排序 / 分页 ---------------- */
  /* cfg = {rows, columns, filterKeys, host:{tbody, pager, toolbar, empty}, onRowAction} */
  F.list = function (cfg) {
    var st = { page: 1, size: 10, sortKey: cfg.defaultSort || null, sortDir: cfg.defaultDir || 'asc' };
    var state = { q: {}, selected: {} };

    function matched() {
      return cfg.rows.filter(function (r) {
        return Object.keys(state.q).every(function (k) {
          var v = state.q[k];
          if (!v) { return true; }
          // 日期区间：键形如 `字段$from` / `字段$to`，对同一字段做上下界比较。
          // YYYY-MM-DD 是定长格式，可以直接按字符串比大小。
          if (k.indexOf('$') > 0) {
            var base = k.split('$')[0];
            var cell = String(r[base] === undefined ? '' : r[base]);
            if (!cell) { return true; }
            if (k.indexOf('$from') > 0) { return cell >= String(v); }
            return cell <= String(v);
          }
          return String(r[k] === undefined ? '' : r[k]).toLowerCase().indexOf(String(v).toLowerCase()) >= 0;
        });
      });
    }
    function sorted(rows) {
      if (!st.sortKey) { return rows; }
      var k = st.sortKey, dir = st.sortDir === 'desc' ? -1 : 1;
      return rows.slice().sort(function (a, b) {
        var x = a[k], y = b[k];
        if (typeof x === 'number' && typeof y === 'number') { return (x - y) * dir; }
        return String(x).localeCompare(String(y), 'zh-CN') * dir;
      });
    }
    function render() {
      var all = sorted(matched());
      st.total = all.length;
      var pageRows = all.slice((st.page - 1) * st.size, st.page * st.size);
      var tb = cfg.host.tbody;
      if (!all.length) {
        var span = cfg.columns.length + (cfg.selectable ? 1 : 0);
        tb.innerHTML = '<tr><td colspan="' + span + '"><div class="empty">' +
          '<div class="box">∅</div>没有符合条件的数据<br>' +
          '<button class="btn btn-sm" style="margin-top:12px" onclick="document.getElementById(\'__reset\').click()">清空筛选条件</button>' +
          '</div></td></tr>';
      } else {
        tb.innerHTML = pageRows.map(function (r) { return cfg.rowHtml(r, state); }).join('');
      }
      // 表头排序指示
      F.$$('th.sortable', cfg.host.table).forEach(function (th) {
        var ar = F.$('.ar', th);
        if (!ar) { return; }
        var k = th.getAttribute('data-key');
        ar.textContent = (st.sortKey === k) ? (st.sortDir === 'asc' ? '▲' : '▼') : '▲▼';
        ar.className = 'ar' + (st.sortKey === k ? ' act' : '');
      });
      // 工具条
      if (cfg.host.selCount) {
        var n = Object.keys(state.selected).length;
        cfg.host.selCount.textContent = n;
        var bulk = F.$$('[data-bulk]', cfg.host.toolbar);
        bulk.forEach(function (b) { b.disabled = n === 0; });
      }
      // ★ 必须把「状态对象本身」交给分页器，不能传字面量副本 ——
      //   传副本时分页器改的是副本的 page，列表状态没变，翻页与"每页条数"都会静默失效。
      st.total = all.length;
      st.onChange = render;
      F.pager(cfg.host.pager, st);
      cfg.after && cfg.after(state, st);
    }
    F.$$('th.sortable', cfg.host.table).forEach(function (th) {
      th.onclick = function () {
        var k = th.getAttribute('data-key');
        if (st.sortKey === k) { st.sortDir = st.sortDir === 'asc' ? 'desc' : 'asc'; }
        else { st.sortKey = k; st.sortDir = 'asc'; }
        render();
      };
    });
    return {
      render: render,
      setQuery: function (k, v) { state.q[k] = v; st.page = 1; },
      clearQuery: function () { state.q = {}; st.page = 1; },
      selected: function () { return Object.keys(state.selected); },
      clearSelected: function () { state.selected = {}; },
      state: state, st: st
    };
  };

  w.F = F;
})(window, document);
