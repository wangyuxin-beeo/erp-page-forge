/* Reference web/prototype capture: uses Studio's current workspace and model bridge. */
(() => {
  'use strict';
  const q = id => document.getElementById(id);
  let available = false, checking = false, pending = false, captured = null;
  const captureButton = q('webCapture'), restoreButton = q('webRestore');
  const projectMode = () => q('webGenerationMode').value === 'project';

  function accessHelp() {
    const prototype = q('webAccess').value === 'prototype';
    q('webAccessHelp').textContent = prototype
      ? '仅采集你明确输入的本机或授权内网原型站点；请先确认电脑能够访问该网址。'
      : '公开网页模式保留原有公开地址采集规则。';
    q('webUrl').placeholder = prototype ? 'http://127.0.0.1:32767/start.html 或 http://prototype.example/erp' : 'https://example.com';
  }
  function generationHelp() {
    q('webGenerationHelp').textContent = projectMode()
      ? '按采集时读取的项目技术栈和主题生成代码包。下方预览是说明页面，不是项目实际运行效果，须在项目中构建验证。'
      : '生成可编辑的静态 HTML / CSS，业务交互与后端接口需另行接入。' + (captured && !captured.projectPath ? ' 如需项目代码包，请填写项目目录后重新采集。' : '');
    restoreButton.textContent = pending ? 'AI 生成中…' : (projectMode() ? 'AI 生成项目代码包' : 'AI 还原页面');
  }
  function sync() {
    captureButton.disabled = pending || checking || !available;
    restoreButton.disabled = pending || !captured || (projectMode() && !captured.projectPath);
    q('webRetryStatus').disabled = pending || checking;
    ['webUrl', 'webWidth', 'webHeight', 'webHint', 'webAccess', 'webMaxPages', 'webLogin', 'webProjectPath', 'webGenerationMode'].forEach(id => { q(id).disabled = pending; });
    q('webProjectMode').disabled = !captured || !captured.projectPath;
    q('webForm').setAttribute('aria-busy', String(pending));
    q('webCaptureResult').setAttribute('aria-busy', String(pending));
    generationHelp();
  }
  function message(text, error = false) {
    q('webMessage').textContent = text;
    q('webMessage').classList.toggle('bad', error);
    q('webMessage').setAttribute('role', error ? 'alert' : 'status');
  }
  async function request(url, body) {
    const response = await fetch(url, body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    });
    let data;
    try { data = await response.json(); }
    catch (_) { throw new Error('服务返回了无法解析的响应（HTTP ' + response.status + '），请重试。'); }
    if (!data || typeof data !== 'object' || !response.ok || data.error) throw new Error(data && data.error || '请求失败（HTTP ' + response.status + '）');
    return data;
  }
  function runId(value) {
    if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{1,160}$/.test(value)) throw new Error('服务返回了无效的运行编号。');
    return value;
  }
  function artifact(value, expected) {
    if (typeof value !== 'string' || value !== expected) throw new Error('服务返回了无效的产物地址。');
    return value;
  }
  function clearRestore() {
    q('webRestoreResult').hidden = true;
    q('webRestoreFrame').src = 'about:blank';
    q('webReport').removeAttribute('href');
    q('webCode').removeAttribute('href');
    q('webCode').hidden = true;
    q('webRestoreNotice').textContent = '';
  }
  function clearCapture() {
    captured = null;
    q('webCaptureResult').hidden = true;
    q('webScreenshot').removeAttribute('src');
    q('webScreenshot').hidden = false;
    q('webShotError').hidden = true;
    q('webHint').value = '';
    q('webWarnings').replaceChildren();
    q('webInteractionRows').replaceChildren();
    q('webInteractions').hidden = true;
    q('webProjectContext').hidden = true;
    q('webProjectFiles').replaceChildren();
    q('webGenerationMode').value = 'static';
    ['webData', 'webTheme', 'webPrototype', 'webBundle'].forEach(id => q(id).removeAttribute('href'));
    q('webPrototype').hidden = true;
    clearRestore();
  }
  async function refreshHistory() {
    try {
      await loadWs();
      if (q('hisDlg').classList.contains('on')) await loadHis();
    } catch (_) { /* Keep a successful artifact visible if history refresh fails. */ }
  }
  async function checkStatus() {
    if (checking || pending) return;
    checking = true; available = false;
    q('webAdvice').textContent = '正在检查采集环境…';
    q('webRetryStatus').hidden = true;
    sync();
    try {
      const data = await request('/api/web/status');
      available = data.available === true;
      q('webAdvice').textContent = data.advice || (available ? '采集环境已就绪。' : '采集环境暂不可用，请检查浏览器依赖。');
      q('webAdvice').classList.toggle('bad', !available);
    } catch (error) {
      q('webAdvice').textContent = '检查采集环境失败：' + error.message;
      q('webAdvice').classList.add('bad');
    } finally {
      checking = false; q('webRetryStatus').hidden = available; sync();
    }
  }
  function showProject(context) {
    if (!context || typeof context !== 'object') return;
    q('webProjectFramework').textContent = '识别框架：' + (context.framework || '未识别，请在项目中确认');
    const files = Array.isArray(context.files) ? context.files : [];
    files.slice(0, 30).forEach(file => {
      const li = document.createElement('li'); li.textContent = String(file); q('webProjectFiles').appendChild(li);
    });
    q('webProjectContext').hidden = false;
  }
  function showInteractions(items) {
    if (!Array.isArray(items) || !items.length) return;
    items.slice(0, 20).forEach(item => {
      const row = document.createElement('tr');
      const value = item && typeof item === 'object' ? item : {};
      [value.label || '未命名元素', value.event || '未声明', value.target || '未声明', '待确认' + (value.status ? '（来源标记：' + String(value.status) + '）' : '')].forEach(text => {
        const cell = document.createElement('td'); cell.textContent = String(text); row.appendChild(cell);
      });
      q('webInteractionRows').appendChild(row);
    });
    q('webInteractionNote').textContent = '显示前 ' + Math.min(items.length, 20) + ' 项；完整原型数据见下载文件。来源状态不代表本工具已执行验证。';
    q('webInteractions').hidden = false;
  }
  q('mWeb').addEventListener('click', () => { setMode('web'); });
  q('webAccess').addEventListener('change', accessHelp);
  q('webGenerationMode').addEventListener('change', sync);
  q('webRetryStatus').addEventListener('click', checkStatus);
  q('webScreenshot').addEventListener('error', () => {
    if (!captured) return;
    q('webScreenshot').hidden = true; q('webShotError').hidden = false;
  });
  q('webForm').addEventListener('submit', async event => {
    event.preventDefault();
    if (pending || checking || !available) return;
    if (!q('webForm').reportValidity()) return;
    let url;
    try {
      url = new URL(q('webUrl').value.trim());
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error();
    } catch (_) { message('请输入含 http:// 或 https:// 的完整网址，不要在网址中包含账号密码。', true); return; }
    const width = Number(q('webWidth').value), height = Number(q('webHeight').value), maxPages = Number(q('webMaxPages').value);
    if (!Number.isInteger(width) || width < 360 || width > 1920 || !Number.isInteger(height) || height < 400 || height > 1200) {
      message('视口宽度须为 360–1920、高度须为 400–1200 的整数。', true); return;
    }
    if (!Number.isInteger(maxPages) || maxPages < 1 || maxPages > 10) { message('最多采集页数须为 1–10 的整数。', true); return; }
    const access = q('webAccess').value;
    if (!['public', 'prototype'].includes(access)) { message('请选择有效的访问方式。', true); return; }
    const projectPath = q('webProjectPath').value.trim(), login = q('webLogin').checked;
    if (projectPath && !/^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\[^\\]+|\/)/.test(projectPath)) {
      message('项目目录须为本机项目的绝对路径，例如 C:\\Projects\\ERP。', true); return;
    }
    const workspace = S.ws;
    clearCapture(); pending = true; captureButton.textContent = '采集中…';
    message(login ? '请在弹出的独立浏览器中完成登录，点击“开始采集”；Studio 正在等待采集结果。' : '正在采集页面、主题与原型声明的导航关系，请稍候…');
    sync();
    try {
      const data = await request('/api/web/capture', { url: url.href, width, height, workspace, access, maxPages, login, projectPath });
      if (data.ok !== true) throw new Error('采集未完成，请重试。');
      const run = runId(data.run);
      const preview = artifact(data.preview, '/out/' + run + '/screenshot.png');
      const links = {
        webData: artifact(data.data, '/api/runs/' + run + '/file/page-data.json'),
        webTheme: artifact(data.theme, '/api/runs/' + run + '/file/theme.json'),
        webBundle: artifact(data.bundle, '/api/runs/' + run + '/zip?all=1')
      };
      if (data.prototype) links.webPrototype = artifact(data.prototype, '/api/runs/' + run + '/file/prototype.json');
      captured = { run, workspace, projectPath };
      q('webTitle').textContent = data.title || '参考网页';
      q('webSource').textContent = data.url || url.href;
      const summary = data.summary || {};
      const count = value => Number.isFinite(value) && value >= 0 ? String(value) : '—';
      q('webSummary').textContent = count(summary.pages) + ' 页 · ' + count(summary.interactions) + ' 项交互（待确认） · ' + count(summary.nodes) + ' 个节点 · ' + count(summary.assets) + ' 项素材' + (summary.truncated ? ' · 数据已截断' : '');
      Object.entries(links).forEach(([id, href]) => { q(id).href = href; });
      q('webPrototype').hidden = !links.webPrototype;
      showProject(data.projectContext); showInteractions(data.interactionSummary);
      const warnings = Array.isArray(data.warnings) ? data.warnings : [];
      warnings.forEach(text => { const li = document.createElement('li'); li.textContent = String(text); q('webWarnings').appendChild(li); });
      q('webWarnings').hidden = !warnings.length;
      q('webScreenshot').src = preview; q('webCaptureResult').hidden = false;
      message('采集完成。可导出页面、主题与原型数据，或选择生成方式。读取到的交互仍待确认。');
    } catch (error) { clearCapture(); message('采集失败：' + error.message, true); }
    finally { pending = false; captureButton.textContent = '采集网页'; sync(); await refreshHistory(); }
  });
  restoreButton.addEventListener('click', async () => {
    if (pending || !captured) return;
    const mode = projectMode() ? 'project' : 'static';
    if (mode === 'project' && !captured.projectPath) { message('请先填写项目绝对目录并重新采集，再生成项目代码包。', true); return; }
    clearRestore(); pending = true;
    message(mode === 'project' ? '正在按采集时的项目主题与框架生成代码包…' : '正在使用当前配置的模型还原静态 HTML / CSS…');
    sync();
    try {
      const data = await request('/api/web/restore', { run: captured.run, hint: q('webHint').value.trim(), mode });
      if (data.ok !== true) throw new Error('生成未完成，请重试。');
      const run = runId(data.run);
      const preview = artifact(data.preview, '/out/' + run + '/page.html');
      const report = artifact(data.report, '/out/' + run + '/report.html');
      const generationMode = data.generationMode || mode;
      if (!['static', 'project'].includes(generationMode) || generationMode !== mode) throw new Error('服务返回的生成方式与请求不一致，请检查生成结果。');
      const code = data.code ? artifact(data.code, '/api/runs/' + run + '/file/project-code.zip') : null;
      if (generationMode === 'project' && !code) throw new Error('服务未返回项目代码包，请查看运行日志。');
      q('webRestoreTitle').textContent = generationMode === 'project' ? '项目代码生成结果' : '静态页面还原结果';
      q('webRestoreNotice').textContent = generationMode === 'project'
        ? '框架：' + (data.framework || '以项目为准') + '。下方为代码包说明页面，不是实际项目运行预览；请下载后在项目中构建、运行，核对主题和交互。未验证交互仍待确认。'
        : '下方为静态 HTML / CSS 预览；不包含原系统后端接口和业务权限，视觉与交互效果须进一步验证。';
      if (code) { q('webCode').href = code; q('webCode').hidden = false; }
      q('webRestoreFrame').src = preview; q('webReport').href = report;
      q('webRestoreResult').hidden = false;
      q('webRestoreResult').scrollIntoView({ behavior: 'smooth', block: 'start' });
      message(generationMode === 'project' ? '项目代码包已生成，须在项目中构建验证；下方仅显示说明页面。' : '静态页面已生成，请对照原型检查；未验证交互仍待确认。');
    } catch (error) { clearRestore(); message('AI 生成失败：' + error.message, true); }
    finally { pending = false; sync(); await refreshHistory(); }
  });
  accessHelp(); sync(); checkStatus();
})();
