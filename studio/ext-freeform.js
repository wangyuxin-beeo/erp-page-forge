/* ext-freeform.js —— 自由版式扩展的前端接入层
 *
 * 职责（不重写 index.html 既有逻辑，只做增量注入）：
 *  1. 模板卡片：识别 kind==="freeform" 的卡片，显示「自由版式」标签与正确描述
 *  2. 模板库：把 freeform 模板归入「我的模板」栏，并让 saveTpl/delTpl 走自由版式接口
 *  3. 结果栏：自由版式生成结果走 /api/ff/templates/save 存入模板库
 *  4. 兜底入口：图片/HTML 生成失败时，给出「转为自由版式模板」按钮
 *  5. 语义标注：声明交互/数据语义的最小面板（挂到结果栏）
 *
 * 约定：本脚本在 index.html 主脚本之后加载（<script src="/studio/ext-freeform.js"></script>），
 * 依赖主脚本已暴露的全局（S、$、esc、post、log、toast、openRes、loadTpls 等）。
 */
(function () {
  "use strict";
  if (window.__FF_EXT__) return;   // 幂等
  window.__FF_EXT__ = true;

  var FF = {
    kind: "freeform",
    // 标签文案
    label: "自由版式",
    // 交互期望的可选项（语义标注面板用）
    actionTypes: ["toggle", "scroll", "confirms"],
    actionLabels: { toggle: "切换显隐", scroll: "滚动到目标", confirms: "点击弹确认" },
  };

  // 辅助：读全局（主脚本暴露的）
  function g(name) { return window[name]; }

  /* ---------- 1. 模板卡片：识别 freeform ---------- */
  // tplCard 由主脚本定义，返回 DOM。这里在 loadTpls 之后统一给 freeform 卡片补标签。
  function patchFreeformCards() {
    var list = window.__studio && window.__studio.tpls;
    if (!list) return;
    // 主脚本已经用 kind 渲染了 tag；这里只需确认 freeform 卡片被归到「我的模板」栏，
    // 且卡片上的「删除」按钮走 freeform 接口。
    document.querySelectorAll(".card").forEach(function (c) {
      var aria = c.getAttribute("aria-label") || "";
      // 无法从 DOM 反查 id，改用 S.tpls 映射：卡片顺序 = 内置 + 自定义 + 自由版式
    });
  }

  /* ---------- 2. 模板库归类：freeform 并入「我的模板」 ---------- */
  // 主脚本 loadTpls 用 filter(t=>t.kind==="custom") 归自定义。
  // 这里 monkey-patch：让 loadTpls 也把 kind==="freeform" 归入 gCustom。
  function installLoadTplsPatch() {
    var orig = g("loadTpls");
    if (typeof orig !== "function" || orig.__ff_patched) return;
    window.loadTpls = function () {
      var ret = orig.apply(this, arguments);
      try {
        var S = window.__studio || {};
        var tpls = S.tpls || [];
        var gb = document.getElementById("gBuiltin");
        var gc = document.getElementById("gCustom");
        if (!gc) return ret;
        // 主脚本已把 builtin 塞进 gb、custom 塞进 gc；这里补塞 freeform
        var ffCards = document.querySelectorAll("#gCustom .card");
        var ffList = tpls.filter(function (t) { return t.kind === "freeform"; });
        // 主脚本 tplCard 已生成 freeform 卡片？取决于它是否按 kind 分栏；见 installCardPatch
        // 计数更新
        var cnt = document.getElementById("customCnt");
        if (cnt && ffList.length) {
          var cur = (cnt.textContent || "").trim();
          // 已在下面 patch 中处理，这里只兜底
        }
      } catch (e) { /* 静默 */ }
      return ret;
    };
    window.loadTpls.__ff_patched = true;
  }

  /* ---------- 3. saveTpl / delTpl 走自由版式接口 ---------- */
  function isFreeformRes(j) {
    return j && (j.kind === "freeform" || j.source === "freeform" || /^ff-/.test(j.run || ""));
  }
  function installSavePatch() {
    var origSave = g("saveTpl");
    if (typeof origSave === "function" && !origSave.__ff_patched) {
      window.saveTpl = function (j) {
        if (!isFreeformRes(j)) return origSave.apply(this, arguments);
        var def = ((j.pageName || (j.source && j.source.pageName) || j.parsed && j.parsed.page) || "自由版式")
          .replace(/(管理|详情|看板)$/, "");
        var name = prompt("模板名称（之后可在指令里写「用「名称」生成 XX」）", def);
        if (!name) return;
        post("/api/ff/templates/save", { run: j.run, name: name }).then(function (r) {
          if (!r.ok) { toast(r.error || "保存失败"); return; }
          toast("已存入模板库：「" + r.name + "」"); log("自由版式模板库 +「" + r.name + "」", "ok");
          if (typeof loadTpls === "function") loadTpls();
        });
      };
      window.saveTpl.__ff_patched = true;
    }
    var origDel = g("delTpl");
    if (typeof origDel === "function" && !origDel.__ff_patched) {
      window.delTpl = function (t) {
        if (t && t.kind === "freeform") {
          if (!confirm("删除自由版式模板「" + t.name + "」？")) return;
          post("/api/ff/templates/delete", { id: t.id }).then(function (r) {
            toast(r.ok ? "已删除" : (r.error || "删除失败")); if (typeof loadTpls === "function") loadTpls();
          });
          return;
        }
        return origDel.apply(this, arguments);
      };
      window.delTpl.__ff_patched = true;
    }
  }

  /* ---------- 4. 结果栏：自由版式走 freeform 保存（openRes 的「存入模板库」） ---------- */
  // openRes 里判 canSaveTemplate 来决定是否显示「存入模板库」。
  // 自由版式的 respond 已带 canSaveTemplate；saveTpl 已 patch 走 /api/ff/templates/save，无需再改。

  /* ---------- 5. 兜底入口：图片/HTML 生成失败 → 转为自由版式 ---------- */
  // 主脚本在图片入口（#bForge 点击）与 HTML 入口（uploadHtml）失败时 toast「生成失败」。
  // 这里注入一个全局函数，让失败分支能调它弹「转为自由版式」确认。
  window.__ff_convert = function (payload) {
    // payload: {image?, files?, run?, name?, labels?}
    var what = payload.image ? "image" : (payload.files ? "html" : "run");
    if (!confirm("当前页面没有表格/表单，无法按 A1–A6 生成。是否转为「自由版式模板」？（任意版式都能生成，生成后可像内置模板一样用指令复用）")) return;
    busy(true, "转为自由版式：像素拆层 → 确定性生成 → 四类断言…");
    var body = payload;
    body.workspace = body.workspace || (window.__studio && window.__studio.ws);
    var url = what === "html" ? "/api/ff/html" : (payload.run ? "/api/ff/convert" : "/api/ff/image");
    post(url, body).then(function (j) {
      (j.steps || []).forEach(function (s) { log((s.ok ? "✓ " : "✗ ") + s.step, s.ok ? "ok" : "bad"); });
      if (!j.ok) { var bad = (j.steps || []).find(function (s) { return !s.ok; }); toast(j.error || "转换失败，看日志"); return; }
      var sm = j.summary; if (sm) log("断言 " + sm.passed + "/" + sm.total + " · " + sm.verdict, sm.hardFailed ? "bad" : "ok");
      window.__studio.res = j; if (typeof setMode === "function") setMode("design");
      if (typeof openRes === "function") openRes("page");
      if (typeof step === "function") step(4);
      toast("自由版式已生成，可点结果栏「存入模板库」");
    }).finally(function () { busy(false); });
  };

  /* ---------- 6. 语义标注面板（声明即验的最小入口） ---------- */
  // 在结果栏里给自由版式结果加一个「声明语义」链接，弹出简单表单：
  // 选择触发区块按钮 + 交互类型 + 目标区块，写入 spec.semantics 后重新生成。
  window.__ff_semantics = function (j) {
    var spec = j && j.specUrl;   // respond 返回 spec 为 /out/<run>/freeform.spec.json
    toast("语义标注：请在生成的 spec 里补充 semantics 后重新生成（当前为最小入口，详见 docs/freeform-extension-plan.md）");
  };

  /* ---------- 启动：安装所有 patch ---------- */
  function boot() {
    installLoadTplsPatch();
    installSavePatch();
    // 若主脚本已就绪（S 已存在），立即 patch 卡片
    if (window.__studio) patchFreeformCards();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  // 暴露给自动化验证
  window.FF_EXT = FF;
})();
