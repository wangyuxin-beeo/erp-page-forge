import { createServer, type Server } from 'node:http'
import { randomBytes, randomUUID } from 'node:crypto'
import type { ModelRoute, VisionHost } from './host-vision.js'

/** Only reachable on loopback; random per-instance capability never leaves child environment. */
export async function startModelBridge(host: VisionHost, route: ModelRoute, timeoutMs: number) {
  if (!host.llm || !host.attachments) throw new Error('DSH 模型和附件服务尚未就绪')
  const token = randomBytes(32).toString('hex')
  const active = new Set<AbortController>()
  const server: Server = createServer(async (req, res) => {
    if (req.method !== 'POST' || req.url !== '/chat/completions' || req.headers.authorization !== `Bearer ${token}` || req.headers.origin) {
      res.writeHead(403); res.end(); return
    }
    const ctl = new AbortController(); active.add(ctl)
    const timer = setTimeout(() => ctl.abort(), timeoutMs)
    res.on('close', () => { if (!res.writableEnded) ctl.abort() })
    try {
      const chunks: Buffer[] = []; let size = 0
      for await (const part of req) { size += part.length; if (size > 32 * 1024 * 1024) throw new Error('请求过大'); chunks.push(part) }
      const body = JSON.parse(Buffer.concat(chunks).toString('utf8'))
      const messages = []
      for (const m of body.messages ?? []) {
        const content = []
        for (const b of typeof m.content === 'string' ? [{ type: 'text', text: m.content }] : m.content ?? []) {
          if (b.type === 'text') content.push({ type: 'text', text: String(b.text) })
          else if (b.type === 'image_url') {
            const match = /^data:(image\/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=\r\n]+)$/.exec(b.image_url?.url ?? '')
            if (!match) throw new Error('只接受内嵌图片')
            const [ref] = await host.attachments!.saveImages([{ mediaType: match[1], data: Buffer.from(match[2], 'base64') }])
            content.push({ type: 'image', attachment: ref })
          }
        }
        messages.push({ id: randomUUID(), role: m.role === 'system' ? 'system' : 'user', source: { kind: 'erp-page-forge' }, content })
      }
      let text = '', finished = false
      for await (const c of host.llm!.stream({ ...route, messages, maxTokens: Math.min(32768, Math.max(256, Number(body.max_tokens) || 16384)), signal: ctl.signal })) {
        if (c.type === 'text-delta') text += c.text
        if (c.type === 'finish') { finished = true; if (['error', 'aborted'].includes(c.reason?.kind)) throw new Error('DSH_MODEL_CALL_FAILED') }
      }
      if (!finished || !text || ctl.signal.aborted) throw new Error('DSH_MODEL_INCOMPLETE')
      res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ choices: [{ message: { role: 'assistant', content: text } }] }))
    } catch { if (!res.headersSent) res.writeHead(502, { 'content-type': 'application/json' }); res.end(JSON.stringify({ error: { message: 'DSH 模型调用失败，请检查模型路由、图片支持或网络' } })) }
    finally { clearTimeout(timer); active.delete(ctl) }
  })
  await new Promise<void>((ok, fail) => { server.once('error', fail); server.listen(0, '127.0.0.1', ok) })
  const address = server.address(); if (!address || typeof address === 'string') throw new Error('桥接启动失败')
  return {
    env: { FORGE_HOST_BRIDGE: `http://127.0.0.1:${address.port}`, FORGE_HOST_TOKEN: token, FORGE_HOST_MODEL: route.model },
    close() { for (const c of active) c.abort(); server.closeAllConnections(); server.close() },
  }
}
