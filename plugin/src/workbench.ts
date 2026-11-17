import { request } from 'node:http'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { launchStudio } from './studio.js'
import { embedStudio } from './embedded.js'
import type { ModelRoute, VisionHost } from './host-vision.js'

export const WORKBENCH_PATH = '/erp-page-forge/'
const ROOT_URL = /(["'`(=])\/(api|studio|out|tpl|lib|fflib)\//g

/** Studio keeps its standalone URLs; only the embedded transport adds the DSH mount. */
export function rewriteStudioText(body: string): string {
  return body.replace(ROOT_URL, `$1${WORKBENCH_PATH}$2/`)
}

export interface WorkbenchHost extends VisionHost {
  effect(fn: () => void | (() => void)): void
  inject(services: string[], callback: (ctx: any) => void): void
}

export function mountWorkbench(ctx: WorkbenchHost, options: {
  python: () => Promise<string>; root: string; dataDir: string; timeoutMs: number
}) {
  ctx.inject(['webServer', 'connection'], web => {
    const lifetime = new AbortController()
    let running: Awaited<ReturnType<typeof launchStudio>> | undefined
    let starting: Promise<Awaited<ReturnType<typeof launchStudio>>> | undefined
    let selection = ''
    web.effect(() => () => { lifetime.abort(); running?.close() })

    async function studio() {
      const route: ModelRoute | undefined = web.get('agentDefaultModel')?.currentSelection()
      const next = JSON.stringify(route ?? null)
      if (running && next !== selection) { running.close(); running = undefined; starting = undefined }
      if (!starting) {
        selection = next
        starting = (async () => {
          const instance = await launchStudio({ ...options, python: await options.python(), port: 0,
            host: ctx, route, signal: lifetime.signal })
          if (lifetime.signal.aborted) { instance.close(); throw new Error('工作台已关闭') }
          running = instance
          return instance
        })().catch(error => { starting = undefined; throw error })
      }
      return starting
    }

    async function proxy(req: IncomingMessage, res: ServerResponse) {
      const admission = web.connection.admit(req)
      if ('rejection' in admission || admission.peer.principal.kind !== 'owner') {
        res.writeHead('rejection' in admission ? admission.rejection : 403)
        res.end('forbidden')
        return
      }
      if (!['GET', 'POST', 'HEAD'].includes(req.method ?? 'GET')) {
        res.writeHead(405); res.end(); return
      }
      let disconnected = false
      res.once('close', () => { if (!res.writableEnded) disconnected = true })
      try {
        // Electron forwards custom-protocol requests as streams, often without a
        // Content-Length. Python's BaseHTTPRequestHandler cannot decode chunked
        // bodies, so normalize the body before forwarding any POST.
        let requestBody: Buffer | undefined
        if (req.method === 'POST') {
          const chunks: Buffer[] = []
          let bytes = 0
          for await (const chunk of req) {
            const part = Buffer.from(chunk)
            bytes += part.length
            if (bytes > 25 * 1024 * 1024) {
              res.writeHead(413, { 'Content-Type': 'application/json; charset=utf-8' })
              res.end(JSON.stringify({ error: '请求体超过 25MB' }))
              return
            }
            chunks.push(part)
          }
          requestBody = Buffer.concat(chunks)
        }
        const instance = await studio()
        // Never accept an arbitrary upstream: the target is always this plugin's child.
        const suffix = (req.url ?? WORKBENCH_PATH).slice(WORKBENCH_PATH.length)
        const target = new URL(instance.url)
        target.pathname = '/' + suffix.split('?')[0]
        target.search = suffix.includes('?') ? suffix.slice(suffix.indexOf('?')) : ''
        const headers: import('node:http').OutgoingHttpHeaders = { ...req.headers, host: target.host, origin: target.origin }
        for (const name of ['cookie', 'authorization', 'connection', 'transfer-encoding',
          'content-length', 'accept-encoding', 'expect', 'trailer', 'upgrade']) delete headers[name]
        if (requestBody) headers['content-length'] = String(requestBody.length)
        await new Promise<void>((resolve, reject) => {
          const upstream = request(target, { method: req.method, headers }, incoming => {
            const chunks: Buffer[] = []
            incoming.on('error', reject)
            incoming.on('data', chunk => chunks.push(Buffer.from(chunk)))
            incoming.on('end', () => {
              if (res.destroyed) { resolve(); return }
              const type = String(incoming.headers['content-type'] ?? 'application/octet-stream')
              let body = Buffer.concat(chunks)
              // Rewrite navigation in Studio assets and API results, never user-generated pages.
              if (type.includes('application/json') || target.pathname === '/' ||
                target.pathname === '/index.html' || target.pathname.startsWith('/studio/')) {
                if (/json|javascript|text\/html|text\/css/.test(type)) {
                  let source = body.toString('utf8')
                  if (target.pathname === '/' || target.pathname === '/index.html') source = embedStudio(source, options.root)
                  body = Buffer.from(rewriteStudioText(source))
                }
              }
              const responseHeaders: Record<string, string | number> = {
                'Content-Type': type, 'Content-Length': body.length, 'Cache-Control': 'no-store',
                'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'SAMEORIGIN',
              }
              const download = incoming.headers['content-disposition']
              if (download) responseHeaders['Content-Disposition'] = download
              res.writeHead(incoming.statusCode ?? 502, responseHeaders)
              res.end(req.method === 'HEAD' ? undefined : body)
              resolve()
            })
          })
          upstream.on('error', reject)
          const disconnect = () => { if (!res.writableEnded) upstream.destroy() }
          res.once('close', disconnect)
          upstream.once('close', () => res.removeListener('close', disconnect))
          upstream.end(requestBody)
        })
      } catch {
        // A cancelled preview must not terminate concurrent generation or the
        // shared Studio process. Request failures remain local to this response.
        if (disconnected || req.aborted) return
        if (!res.headersSent && !res.destroyed) {
          res.writeHead(503, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' })
          res.end('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>页归</title><body style="font:16px system-ui;padding:40px"><h2>页归工作台暂时未能启动</h2><p>请确认 DSH 内置 Python 可用，然后重试。</p><button onclick="location.reload()">重新加载</button></body></html>')
        } else if (!res.destroyed) res.destroy()
      }
    }
    web.effect(() => web.webServer.register({ kind: 'prefix', path: WORKBENCH_PATH.slice(0, -1), handler: proxy }))
  })
}
