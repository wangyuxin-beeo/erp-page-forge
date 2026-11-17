import { readFileSync } from 'node:fs'
import { join } from 'node:path'

/** Ship the embedded app as one document, independent of subresource loading. */
export function embedStudio(html: string, root: string): string {
  html = html.replace(/<script src="\/studio\/([\w-]+\.js)"><\/script>/g, (_tag, file) =>
    '<script>\n' + readFileSync(join(root, 'studio', file), 'utf8').replace(/<\/script/gi, '<\\/script') + '\n</script>')
  html = html.replace(/<link rel="stylesheet" href="\/studio\/([\w-]+\.css)">/g, (_tag, file) =>
    '<style>' + readFileSync(join(root, 'studio', file), 'utf8') + '</style>')
  const bootstrap = `<script>
(() => {
  window.__FORGE_EMBEDDED__ = '1.7.2';
  const nativeFetch = window.fetch.bind(window);
  let transport = nativeFetch;
  try {
    if (parent !== window && parent.location.origin === location.origin) transport = parent.fetch.bind(parent);
  } catch (_) {}
  function showError(message) {
    let box = document.getElementById('forgeConnectionError');
    if (!box) {
      box = document.createElement('div'); box.id = 'forgeConnectionError'; box.setAttribute('role','alert');
      box.style.cssText = 'position:fixed;bottom:12px;left:12px;right:12px;z-index:99999;padding:12px 16px;background:#fff1f0;border:1px solid #ffb4ac;border-radius:8px;color:#922;font:14px system-ui;white-space:pre-wrap';
      (document.body || document.documentElement).appendChild(box);
    }
    box.textContent = '页归连接异常：' + message + '。请点击工作台顶部“重新加载”重试。';
  }
  window.addEventListener('error', event => { if (event.message) showError(event.message); });
  window.addEventListener('unhandledrejection', event => showError(String(event.reason?.message || event.reason)));
  document.addEventListener('DOMContentLoaded', () => {
    const header = document.querySelector('.app > header');
    if (header) new ResizeObserver(() => document.documentElement.style.setProperty('--forge-header-height', header.getBoundingClientRect().height + 'px')).observe(header);
  });
  window.fetch = async (input, init) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    try {
      const response = await transport(url.href, init);
      if ((response.status === 401 || response.status === 403 || response.status === 404 || response.status >= 500) && url.pathname.includes('/api/')) showError(url.pathname + ' 返回 HTTP ' + response.status);
      return response;
    } catch (error) { showError(url.pathname + '：' + error.message); throw error; }
  };
})();
</script>`
  return html.replace(/<head>/i, '<head>' + bootstrap).replace('</head>', `<style>
html,body{width:100%;min-width:0;max-width:100%;overflow-x:hidden}
.app{width:100%;min-width:0;grid-template-rows:auto minmax(0,1fr)}
.libv,.webv{top:var(--forge-header-height,56px)!important}
header{flex-wrap:wrap;height:auto!important;min-height:56px;gap:8px!important}
header .steps{flex-wrap:wrap}
@media(max-width:1600px){header .steps{display:none!important}}
#forgeConnectionError{box-sizing:border-box}
</style></head>`)
}
