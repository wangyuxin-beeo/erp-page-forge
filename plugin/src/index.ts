// ERP Page Forge · DSH 插件入口。
//
// 插件是一层薄壳：把作品的 Python 生成引擎注册成模型可调用的工具。
// 生成、验收、反向工程、截图还原、Axure 解析全部仍由 engine/ 下的原脚本完成，
// 这里只负责定位 Python、固定工作目录、把结果整理成规范 JSON 值。
import { defineTool } from '@deepseek-ai/dsh-tools'
import { copyFileSync } from 'node:fs'
import { launchStudio } from './studio.js'
import { mountWorkbench, type WorkbenchHost } from './workbench.js'
import { startModelBridge } from './model-bridge.js'
import { homedir } from 'node:os'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, isAbsolute, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { resolvePython, runEngine, type RunResult } from './python.js'
import { recognizeWithHost, type VisionHost } from './host-vision.js'

import { Config } from './config.js'
export { Config }
export const name = 'erp-page-forge'
// 宿主负责工具注册、模型路由与图片附件；插件不读取私人密钥。
export const inject = ['tools', 'llm', 'attachments']

export interface Config {
  /** 指定 Python 解释器绝对路径。留空则自动使用 DSH 内置运行时。 */
  python: string
  /** 单次引擎调用的超时上限，秒。截图识别走视觉模型时需要更长。 */
  timeoutSec: number
}

// Config 导出 Standard Schema v1，加载时校验；默认值同时供测试直接调用 apply 使用。
const DEFAULTS: Config = { python: '', timeoutSec: 300 }

interface ToolHost extends VisionHost, WorkbenchHost {
  tools: { register(def: unknown): void }
  effect(fn: () => void | (() => void)): void
}

interface ExecContext {
  signal: AbortSignal
  agent?: { session?: { requestHeader?: () => { config?: { provider: string; model: string } } } }
}

type ToolResult = Record<string, unknown>

/** 引擎根目录。开发期指向仓库根（src 的上两级），打包后指向 engine/。 */
function engineRoot(): string {
  const here = dirname(fileURLToPath(import.meta.url))
  const packed = resolve(here, '..', 'engine')
  if (existsSync(join(packed, 'build', 'forge.py'))) return packed
  const dev = resolve(here, '..', '..')
  if (existsSync(join(dev, 'build', 'forge.py'))) return dev
  throw new Error('找不到引擎：既没有打包后的 engine/，也没有开发期的仓库根。')
}

function text(s: string) {
  return [{ type: 'text', text: s }]
}

/** 把一次引擎调用的尾部输出收成给模型看的短摘要，避免把整份日志塞进上下文。 */
function tail(r: RunResult, lines = 12): string {
  return (r.stderr || r.stdout).trim().split(/\r?\n/).slice(-lines).join('\n')
}

export function apply(ctx: ToolHost, raw?: Partial<Config>) {
  const config: Config = {
    python: raw?.python ?? DEFAULTS.python,
    timeoutSec: raw?.timeoutSec ?? DEFAULTS.timeoutSec,
  }
  const root = engineRoot()
  const dataDir = join(process.env.DSH_PROFILE_DIR ?? join(process.env.DSH_HOME ?? join(homedir(), '.dsh'), 'profiles', 'default'), 'erp-page-forge-data')
  const disposers = new Set<() => void>()
  ctx.effect(() => () => { for (const dispose of disposers) dispose(); disposers.clear() })
  const timeoutMs = Math.max(30, config.timeoutSec) * 1000

  // 解释器在首次调用时解析并缓存。加载期不阻塞：DSH 内置运行时可能晚于插件就绪。
  let pythonPromise: Promise<string> | undefined
  const python = () => (pythonPromise ??= resolvePython(config.python))

  // Web services are optional: headless hosts retain all model tools.
  if (typeof ctx.inject === 'function') mountWorkbench(ctx, { python, root, dataDir, timeoutMs })

  // 追踪在途子进程，插件卸载或热替换时全部杀掉，避免留下孤儿 python。
  const live = new Set<AbortController>()
  ctx.effect(() => () => {
    for (const c of live) c.abort()
    live.clear()
  })

  async function engine(args: string[], signal?: AbortSignal, env?: Record<string, string>): Promise<RunResult> {
    const py = await python()
    const ctl = new AbortController()
    live.add(ctl)
    const onAbort = () => ctl.abort()
    signal?.addEventListener('abort', onAbort, { once: true })
    try {
      return await runEngine({ python: py, cwd: root, args, timeoutMs, signal: ctl.signal, env: { FORGE_DATA_DIR: dataDir, ...env } })
    } finally {
      live.delete(ctl)
      signal?.removeEventListener('abort', onAbort)
    }
  }

  /** 工作区输出目录。不写回引擎目录：安装后的包在 node_modules 里，不该当数据目录。 */
  function outDir(dir: string | undefined, fallback: string): string {
    if (!dir?.trim() && fallback === 'erp-forge-out') {
      const runs = join(dataDir, 'tool-runs')
      mkdirSync(runs, { recursive: true })
      return mkdtempSync(join(runs, 'run-'))
    }
    const base = dir?.trim() || fallback
    const abs = isAbsolute(base) ? base : resolve(process.cwd(), base)
    mkdirSync(abs, { recursive: true })
    return abs
  }

  const register = (def: {
    name: string
    description: string
    parameters: Record<string, unknown>
    execute: (args: Record<string, unknown>, exec: ExecContext) => Promise<ToolResult>
  }) => {
    ctx.tools.register(defineTool({
      name: def.name,
      description: def.description,
      parameters: def.parameters,
      output: {
        schema: { type: 'object', additionalProperties: true },
        render: (_args: unknown, value: ToolResult) => text(render(def.name, value)),
      },
      async execute(args: Record<string, unknown>, exec: ExecContext) {
        return JSON.parse(JSON.stringify(await def.execute(args, exec)))
      },
    }))
  }

  async function studioAction(operation: string, payload: Record<string, unknown>, signal: AbortSignal, env?: Record<string, string>) {
    const dir = outDir(undefined, 'erp-forge-out')
    const request = join(dir, 'request.json'), response = join(dir, 'response.json')
    writeFileSync(request, JSON.stringify({ operation, payload }), 'utf8')
    const result = await engine(['build/plugin_api.py', '--request', request, '--response', response], signal, env)
    if (result.aborted) return { ok: false, aborted: true }
    if (result.code !== 0 || !existsSync(response)) return { ok: false, error: 'Studio 操作执行失败', detail: tail(result) }
    const value = JSON.parse(readFileSync(response, 'utf8'))
    return { ok: value.status < 400, ...value.body, status: value.status }
  }

  register({
    name: 'forge_studio_action',
    description: '调用 Studio 业务：web_status/web_capture（url、可选access=public/prototype、maxPages、login、projectPath、width/height/workspace；prototype支持本机内网和Axure目录）/web_restore（run、可选hint及mode=static/project，复用当前模型）、command/spec/html、freeform_generate/html/image/parse/templates/template_save/template_delete、templates/template_save/template_delete、workspaces/workspace_create/rename/delete、runs/run_delete/zip。payloadJson 为 JSON 对象字符串。删除操作须有用户明确指令；输出包含本地产物路径。',
    parameters: {
      operation: { type: 'string', required: true, description: '业务操作名，例如 templates、freeform_generate、zip' },
      payloadJson: { type: 'string', description: 'JSON 对象字符串，默认 {}；字段与 Studio 对应请求相同' },
    },
    async execute(args, exec) {
      let payload: Record<string, unknown>
      try { payload = JSON.parse(String(args.payloadJson ?? '{}')) } catch { return { ok:false, error:'payloadJson 必须是有效 JSON' } }
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return { ok:false, error:'payloadJson 必须是对象' }
      if (String(args.operation) === 'web_restore') {
        const route = exec.agent?.session?.requestHeader?.()?.config
        if (!route?.provider || !route?.model) return { ok:false, error:'当前会话没有可用的模型路由' }
        const bridge = await startModelBridge(ctx, route, timeoutMs)
        try { return await studioAction('web_restore', payload, exec.signal, bridge.env) }
        finally { bridge.close() }
      }
      return studioAction(String(args.operation), payload, exec.signal)
    },
  })

  // ---------------------------------------------------------------- 原型与配方
  register({
    name: 'list_archetypes',
    description: '列出 ERP 页面原型（A1–A6）与可套用的实体配方。生成页面前先查这个，确认原型编号和实体 key。',
    parameters: {
      query: { type: 'string', description: '可选，按名称或关键词过滤' },
    },
    async execute(args) {
      const q = String(args.query ?? '').trim().toLowerCase()
      const arch = JSON.parse(readFileSync(join(root, 'skill', 'spec', 'archetypes.json'), 'utf8'))
      const recipes = JSON.parse(readFileSync(join(root, 'skill', 'spec', 'recipes.json'), 'utf8'))
      const archetypes = Object.entries(arch.archetypes as Record<string, { name: string }>).map(([id, v]) => ({
        id, name: v.name,
      }))
      const entities = Object.entries(recipes.entities as Record<string, { name: string, archetypeHint?: string }>).map(([key, v]) => ({
        key, name: v.name, archetype: v.archetypeHint ?? '',
      }))
      const hit = (s: string) => !q || s.toLowerCase().includes(q)
      return {
        archetypes: archetypes.filter(a => hit(a.id + a.name)),
        entities: entities.filter(e => hit(e.key + e.name)),
      }
    },
  })

  // ---------------------------------------------------------------- 指令生成
  register({
    name: 'forge_page',
    description: '按自然语言指令生成一个 ERP 页面，返回单文件 HTML 路径。例：「生成销售订单列表页」「做一张采购申请单，字段：物料、数量、单价」。',
    parameters: {
      command: { type: 'string', required: true, description: '生成指令，需含页面类型（列表/表单/详情/看板/审批/对账）或实体名' },
      theme: { type: 'string', description: '主题：default 或 premium，默认 default' },
      outDir: { type: 'string', description: '输出目录；省略时写入当前 DSH profile 的独立运行目录' },
    },
    async execute(args, exec) {
      const command = String(args.command ?? '').trim()
      if (!command) return { ok: false, error: 'command 不能为空' }
      const value = await studioAction('command', {command, theme:args.theme === 'premium' ? 'premium' : 'default'}, exec.signal)
      if (args.outDir && value.runDir) {
        const dir=outDir(String(args.outDir), 'erp-forge-out')
        for (const [key, name] of [['htmlPath','page.html'],['specPath','pagespec.json'],['reportPath','report.html']] as const) {
          if (value[key] && existsSync(value[key])) { copyFileSync(value[key],join(dir,name)); value[key]=join(dir,name) }
        }
      }
      return value
    },
  })

  // ---------------------------------------------------------------- 规范生成
  register({
    name: 'forge_from_spec',
    description: '按一份 PageSpec JSON 确定性生成页面。同一份 spec 两次生成逐字节一致。',
    parameters: {
      specPath: { type: 'string', required: true, description: 'PageSpec JSON 文件的绝对路径' },
      theme: { type: 'string', description: '主题：default 或 premium' },
      outDir: { type: 'string', description: '输出目录，默认与 spec 同目录' },
    },
    async execute(args, exec) {
      const specPath = String(args.specPath ?? '')
      if (!existsSync(specPath)) return { ok: false, error: `找不到 spec：${specPath}` }
      const dir = outDir(args.outDir as string | undefined, dirname(specPath))
      const htmlFile = join(dir, 'page.html')
      const theme = args.theme === 'premium' ? 'premium' : 'default'
      const gen = await engine(
        ['build/forge.py', '--spec', specPath, '--theme', theme, '--out', htmlFile],
        exec.signal,
      )
      if (gen.aborted) return { ok: false, aborted: true }
      return {
        ok: gen.code === 0 && existsSync(htmlFile),
        htmlPath: existsSync(htmlFile) ? htmlFile : undefined,
        theme,
        detail: gen.code === 0 ? tail(gen, 6) : tail(gen),
      }
    },
  })

  // ---------------------------------------------------------------- 反向工程
  register({
    name: 'reverse_page',
    description: '从已有页面（HTML / Vue / JSX / TSX）反抽出 PageSpec，用于"照这个做，换成我的字段"。',
    parameters: {
      artifact: { type: 'string', required: true, description: '页面文件或目录的绝对路径' },
      rename: { type: 'string', description: '可选，把抽出的页面改成这个名字' },
      outDir: { type: 'string', description: '输出目录；省略时写入当前 DSH profile 的独立运行目录' },
    },
    async execute(args, exec) {
      const artifact = String(args.artifact ?? '')
      if (!existsSync(artifact)) return { ok: false, error: `找不到产物：${artifact}` }
      const dir = outDir(args.outDir as string | undefined, 'erp-forge-out')
      const specFile = join(dir, 'reversed.pagespec.json')
      const cmd = ['build/reverse.py', '--artifact', artifact, '--out', specFile]
      if (args.rename) cmd.push('--rename', String(args.rename))
      const r = await engine(cmd, exec.signal)
      if (r.aborted) return { ok: false, aborted: true }
      return {
        ok: r.code === 0 && existsSync(specFile),
        specPath: existsSync(specFile) ? specFile : undefined,
        detail: tail(r),
      }
    },
  })

  // ---------------------------------------------------------------- 验收
  register({
    name: 'verify_page',
    description: '对生成的页面跑验收：契约、渲染、交互、数据四类断言，并产出 HTML 报告。没有浏览器时渲染类断言标为需人工确认，不算通过。',
    parameters: {
      specPath: { type: 'string', required: true, description: 'PageSpec JSON 路径' },
      artifact: { type: 'string', required: true, description: '待验收的页面文件路径' },
      outDir: { type: 'string', description: '报告输出目录，默认与页面同目录' },
    },
    async execute(args, exec) {
      const specPath = String(args.specPath ?? '')
      const artifact = String(args.artifact ?? '')
      if (!existsSync(specPath)) return { ok: false, error: `找不到 spec：${specPath}` }
      if (!existsSync(artifact)) return { ok: false, error: `找不到页面：${artifact}` }
      const dir = outDir(args.outDir as string | undefined, dirname(artifact))
      const reportFile = join(dir, 'acceptance.json')
      const r = await engine([
        'build/verify_acceptance.py', '--spec', specPath, '--artifact', artifact,
        '--url', artifact, '--out', reportFile,
      ], exec.signal)
      if (r.aborted) return { ok: false, aborted: true }
      if (!existsSync(reportFile)) return { ok: false, error: '验收未产出报告', detail: tail(r) }
      const report = JSON.parse(readFileSync(reportFile, 'utf8'))
      const summary = report.summary ?? {}
      const reportHtml = join(dir, 'acceptance-report.html')
      await engine(['build/build_report.py', '--report', reportFile, '--out', reportHtml], exec.signal)
      return {
        ok: summary.hardFailed === 0 && (summary.unsupported ?? 0) === 0 && (summary.skippedCategories ?? 0) === 0,
        generated: true,
        needsReview: (summary.unsupported ?? 0) > 0 || (summary.skippedCategories ?? 0) > 0,
        passed: summary.passed ?? 0,
        failed: summary.failed ?? 0,
        hardFailed: summary.hardFailed ?? 0,
        unsupported: summary.unsupported ?? 0,
        verdict: summary.verdict,
        reportPath: existsSync(reportHtml) ? reportHtml : reportFile,
      }
    },
  })

  // ---------------------------------------------------------------- 截图还原
  register({
    name: 'forge_from_screenshot',
    description: '从后台页面截图还原页面。默认通过 DSH 模型服务复用当前会话模型与私人凭据，无需另填 Key；可指定 provider/model，模型必须支持图片。backend=external 时使用独立的 FORGE_AI_* 配置。',
    parameters: {
      image: { type: 'string', required: true, description: '截图文件的绝对路径（png/jpg/webp）' },
      hint: { type: 'string', description: '可选，给识别模型的补充说明' },
      backend: { type: 'string', description: 'host（默认，复用 DSH 私人模型配置）或 external（FORGE_AI_*）' },
      provider: { type: 'string', description: '可选，DSH 中已配置的提供方 ID，缺省用当前会话提供方' },
      model: { type: 'string', description: '可选，模型 ID，例如 gpt-6.1-sol；缺省用当前会话模型' },
      outDir: { type: 'string', description: '输出目录；省略时写入当前 DSH profile 的独立运行目录' },
    },
    async execute(args, exec) {
      const image = String(args.image ?? '')
      if (!existsSync(image)) return { ok: false, error: `找不到截图：${image}` }
      const dir = outDir(args.outDir as string | undefined, 'erp-forge-out')
      const extractFile = join(dir, 'extract.json')
      const specFile = join(dir, 'screenshot.spec.json')
      // 第一步：默认经宿主调用；凭据始终留在 DSH 的适配器内部。
      const backend = args.backend ?? 'host'
      if (backend !== 'host' && backend !== 'external') return { ok:false, error:'backend 只支持 host / external' }
      let recognized: RunResult = { code:0, stdout:'通过 DSH 已配置模型完成识别', stderr:'', aborted:false }
      if (backend === 'host') {
        const current = exec.agent?.session?.requestHeader?.()?.config
        const provider = String(args.provider ?? current?.provider ?? '')
        const model = String(args.model ?? current?.model ?? '')
        if (!provider || !model) return { ok:false, error:'无法取得当前模型路由，请指定 DSH 已配置的 provider 和 model' }
        try {
          await recognizeWithHost(ctx, {provider,model}, root, image, String(args.hint ?? ''), extractFile, exec.signal)
        } catch {
          if(exec.signal.aborted) return {ok:false,aborted:true}
          return { ok:false, error:'DSH 截图模型调用失败，请检查所选路由的凭据、网络和图片支持', provider, model }
        }
      } else {
        recognized = await engine(
          ['build/ai_recognize.py', '--image', image, '--out', extractFile, '--hint', String(args.hint ?? ''), '--mode', 'screenshot'], exec.signal)
        if (recognized.aborted) return { ok:false, aborted:true }
        if (recognized.code !== 0 || !existsSync(extractFile)) return { ok:false, error:'独立视觉接口未配置或不可用', detail:tail(recognized) }
      }
      // screenshot 模式已经返回独立截图契约；不要交给 A1–A6 的 vision.py。
      const screenshotSpec = JSON.parse(readFileSync(extractFile, 'utf8'))
      if (screenshotSpec.kind !== 'screenshot') {
        return { ok: false, error: '识别结果不是独立截图契约' }
      }
      copyFileSync(extractFile, specFile)
      // 第二步：确定性渲染（同时校验截图契约）
      const htmlFile = join(dir, 'page.html')
      const rendered = await engine(
        ['build/screenshot_render.py', '--spec', specFile, '--out', htmlFile],
        exec.signal,
      )
      if (rendered.aborted) return { ok: false, aborted: true }
      return {
        ok: rendered.code === 0 && existsSync(htmlFile),
        htmlPath: existsSync(htmlFile) ? htmlFile : undefined,
        specPath: specFile,
        detail: rendered.code === 0 ? tail(recognized, 8) : tail(rendered),
      }
    },
  })

  // ---------------------------------------------------------------- Axure
  register({
    name: 'forge_from_axure',
    description: '从 Axure 导出的原型目录解析页面结构并生成页面。读取标注与站点地图，比截图识别更准确。',
    parameters: {
      src: { type: 'string', required: true, description: 'Axure 导出目录的绝对路径' },
      page: { type: 'string', description: '可选，指定页面目录名，默认取第一页' },
      outDir: { type: 'string', description: '输出目录；省略时写入当前 DSH profile 的独立运行目录' },
    },
    async execute(args, exec) {
      const src = String(args.src ?? '')
      if (!existsSync(src)) return { ok: false, error: `找不到 Axure 目录：${src}` }
      const dir = outDir(args.outDir as string | undefined, 'erp-forge-out')
      const specFile = join(dir, 'pagespec.json')
      const htmlFile = join(dir, 'page.html')
      const cmd = ['build/axure.py', '--src', src, '--out', specFile, '--forge', htmlFile]
      if (args.page) cmd.push('--page', String(args.page))
      const r = await engine(cmd, exec.signal)
      if (r.aborted) return { ok: false, aborted: true }
      return {
        ok: r.code === 0,
        htmlPath: existsSync(htmlFile) ? htmlFile : undefined,
        specPath: existsSync(specFile) ? specFile : undefined,
        detail: tail(r),
      }
    },
  })

  // ---------------------------------------------------------------- Studio 服务
  register({
    name: 'start_studio',
    description: '启动完整 Forge Studio：页面生成、截图识别、Axure、自由版式、模板、工作区、历史和下载。默认复用当前 DSH 模型。',
    parameters: {
      port: { type: 'number', description: '端口，默认 0 自动选择空闲端口' },
      provider: { type: 'string', description: '可选，已配置的 DSH 提供方 ID' },
      model: { type: 'string', description: '可选，已配置的模型 ID' },
    },
    async execute(args, exec) {
      const port = Number(args.port ?? 0)
      if (!Number.isInteger(port) || port < 0 || port > 65535) return { ok:false, error:'端口必须是 0–65535 的整数' }
      const current=exec.agent?.session?.requestHeader?.()?.config
      const provider=String(args.provider ?? '').trim() || current?.provider || ''
      const model=String(args.model ?? '').trim() || current?.model || ''
      const ctl=new AbortController()
      const abort=()=>ctl.abort()
      exec.signal.addEventListener('abort',abort,{once:true})
      try {
        const studio=await launchStudio({python:await python(),root,dataDir,port,timeoutMs,host:ctx,
          route:provider&&model?{provider,model}:undefined,signal:ctl.signal})
        const dispose=()=>{ctl.abort();studio.close();exec.signal.removeEventListener('abort',abort)}
        disposers.add(dispose)
        return {ok:true,url:studio.url,ai:studio.ai,provider,model,dataDir,note:'Studio AI 使用启动时选定的模型；插件卸载自动停止。'}
      } catch {
        ctl.abort();exec.signal.removeEventListener('abort',abort)
        return {ok:false,error:'Studio 启动失败：检查端口、Python 与宿主模型服务'}
      }
    },
  })
}

/** 各工具面向模型的文字摘要。规范值里的路径仍是完整的，这里只给可读结论。 */
function render(tool: string, v: ToolResult): string {
  if (v.aborted) return `${tool}：已取消。`
  if (v.ok === false) return `${tool} 失败：${v.error ?? ''}\n${v.detail ?? ''}`.trim()
  switch (tool) {
    case 'list_archetypes': {
      const a = (v.archetypes as { id: string, name: string }[]) ?? []
      const e = (v.entities as { key: string, name: string }[]) ?? []
      return `原型 ${a.length} 个：${a.map(x => `${x.id} ${x.name}`).join('、')}\n实体配方 ${e.length} 个。`
    }
    case 'forge_page':
    case 'forge_from_spec':
    case 'forge_from_screenshot':
    case 'forge_from_axure':
      return `${v.pageName ? v.pageName + ' · ' : ''}已生成：${v.htmlPath ?? '（无输出）'}`
    case 'reverse_page':
      return `已反抽 PageSpec：${v.specPath}`
    case 'verify_page':
      return `验收${v.ok ? '通过' : '未通过'}：通过 ${v.passed}，失败 ${v.failed}，需人工确认 ${v.unsupported}。报告：${v.reportPath}`
    case 'start_studio':
      return `Forge Studio 已启动：${v.url}`
    default:
      return JSON.stringify(v)
  }
}

// 指令解析直接复用引擎里的 parse_command，避免在 TypeScript 侧重写一套规则。
// 通过 python -c 调用，把结果落成 JSON 文件，插件再读。这样插件不依赖 studio 的运行目录。
const PARSE_SNIPPET = [
  'import io, json, sys',
  'sys.path.insert(0, "build")',
  'import studio_api',
  'code, res = studio_api.parse_command(sys.argv[1])',
  'out = {"code": code, **res}',
  'if code == 200:',
  '    out["spec"]["_notes"] = res.get("notes", [])',
  'io.open(sys.argv[2], "w", encoding="utf-8").write(json.dumps(out if code != 200 else res["spec"], ensure_ascii=False, indent=2))',
  'sys.exit(0 if code == 200 else 1)',
].join('\n')
