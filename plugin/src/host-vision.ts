import { readFileSync, writeFileSync } from 'node:fs'
import { extname, join } from 'node:path'
import { randomUUID } from 'node:crypto'

export interface ModelRoute { provider: string; model: string }
export interface VisionHost {
  llm?: { stream(options: Record<string, unknown>): AsyncIterable<any> }
  attachments?: { saveImages(inputs: { data: Buffer; mediaType: string }[]): Promise<unknown[]> }
}

/** 凭据和协议转换由宿主适配器负责；这里不读取凭据文件或 API Key。 */
export async function recognizeWithHost(host: VisionHost, route: ModelRoute, root: string,
  image: string, hint: string, output: string, signal: AbortSignal): Promise<void> {
  if (!host.llm || !host.attachments) throw new Error('DSH 模型服务或图片附件服务未就绪')
  const mediaType: Record<string,string> = { '.png':'image/png', '.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.webp':'image/webp' }
  const mime = mediaType[extname(image).toLowerCase()]
  if (!mime) throw new Error('截图只支持 PNG、JPEG、WebP')
  const refs = await host.attachments.saveImages([{ data: readFileSync(image), mediaType: mime }])
  const prompt = readFileSync(join(root,'studio','ai-prompt-screenshot.md'),'utf8')
  const message = {
    id: randomUUID(), role: 'user', source: { kind: 'erp-page-forge' },
    content: [{ type:'text', text: prompt + '\n\n用户补充：' + hint + '\n仅返回完整 JSON 对象。' },
      { type:'image', attachment:refs[0] }],
  }
  let text = '', finished = false
  for await (const chunk of host.llm.stream({ ...route, messages:[message], maxTokens:16384, signal })) {
    if (chunk.type === 'text-delta') text += chunk.text
    if (chunk.type === 'finish') {
      finished = true
      const kind=chunk.reason?.kind
      if (kind === 'error' || kind === 'aborted') {
        // 只暴露稳定错误码，避免适配器的原始异常包含请求头或私人配置。
        throw new Error(`DSH 模型调用失败：${kind} (${chunk.reason?.failure?.code ?? 'UNKNOWN'})`)
      }
    }
  }
  if (signal.aborted) throw new Error('截图识别已取消')
  if (!finished || !text.trim()) throw new Error('模型未返回完整文本，请确认该模型支持图片输入')
  const start=text.indexOf('{'), end=text.lastIndexOf('}')
  if(start<0 || end<=start) throw new Error('模型未返回截图 JSON')
  let spec: any
  try { spec=JSON.parse(text.slice(start,end+1)) } catch { throw new Error('模型返回的截图 JSON 不完整或格式错误') }
  if(spec.kind !== 'screenshot') throw new Error('模型返回结果不是 kind:screenshot 契约')
  writeFileSync(output,JSON.stringify(spec,null,2),'utf8')
}
