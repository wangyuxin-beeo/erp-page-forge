// Python 运行时定位与引擎子进程调用。
//
// 运行时优先级：插件配置 python → 随 DSH 分发的内置运行时 → PATH 上的 python。
// DSH 的 primary runtime 自带 Python 3.13 和 Pillow，这是"开箱即用"的来源，
// 插件不再另行捆绑解释器。
import { execFile } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { promisify } from 'node:util'

const execFileAsync = promisify(execFile)

export interface RunResult {
  code: number
  stdout: string
  stderr: string
  /** 进程被取消信号杀掉时为 true，调用方据此区分"用户取消"和"脚本失败"。 */
  aborted: boolean
}

/** 候选解释器，按优先级。存在性在 resolvePython 里检查。 */
function candidates(configured: string): string[] {
  const list: string[] = []
  if (configured.trim()) list.push(configured.trim())

  // DSH 安装目录下的 primary runtime。Desktop 版与源码版布局一致：
  // <resources>/runtime/primary-runtime/dependencies/python/python.exe
  const electronResources = (process as NodeJS.Process & { resourcesPath?: string }).resourcesPath
  const resources = process.env.DSH_RESOURCES
    ?? (electronResources ? join(electronResources, 'runtime') : '')
  if (resources) {
    list.push(join(resources, 'primary-runtime', 'dependencies', 'python', 'python.exe'))
  }
  // 用户主目录里的运行时缓存（dsh 自行下载依赖时落在这里）。
  const home = process.env.DSH_HOME ?? join(process.env.USERPROFILE ?? '', '.dsh')
  if (home) {
    list.push(join(home, 'dsh-runtimes', 'dsh-primary-runtime', 'dependencies', 'python', 'python.exe'))
  }
  list.push('python', 'python3')
  return list
}

/**
 * 找到一个能 `import PIL` 的解释器。引擎的截图与视觉链路依赖 Pillow，
 * 没有它就在加载期失败，而不是等到第一次调用才报。
 */
export async function resolvePython(configured: string): Promise<string> {
  const tried: string[] = []
  for (const py of candidates(configured)) {
    tried.push(py)
    if (py !== 'python' && py !== 'python3' && !existsSync(py)) continue
    try {
      await execFileAsync(py, ['-c', 'import PIL'], { timeout: 20000, windowsHide: true })
      return py
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code
      if (code === 'EPERM' || code === 'EACCES') throw new Error('当前执行环境不允许启动 Python 或捕获其输出，请检查 DSH 执行权限')
      if (configured.trim() === py) throw new Error('配置的 Python 无法运行或缺少 Pillow，请检查解释器路径')
      // 自动发现候选不可用时尝试下一个。
    }
  }
  throw new Error(
    '找不到带 Pillow 的 Python 解释器，ERP Page Forge 无法加载。\n'
    + '已尝试：\n  ' + tried.join('\n  ')
    + '\n可在插件配置 python 中指定解释器绝对路径。',
  )
}

export interface RunOptions {
  python: string
  /** 引擎根目录，即 engine/，脚本用它的父级关系找 skill/spec。cwd 固定于此。 */
  cwd: string
  args: string[]
  timeoutMs: number
  signal?: AbortSignal
  /** 额外环境变量，例如截图识别需要的 FORGE_AI_*。 */
  env?: Record<string, string>
}

/** 跑一次引擎脚本。cwd 固定为引擎根，保证脚本之间的相对路径与 __file__ 反推都成立。 */
export async function runEngine(opts: RunOptions): Promise<RunResult> {
  const { python, cwd, args, timeoutMs, signal, env } = opts
  if (signal?.aborted) return { code: -1, stdout: '', stderr: '', aborted: true }
  return new Promise(resolve => {
    let onAbort: (() => void) | undefined
    const child = execFile(python, args, {
      cwd,
      timeout: timeoutMs,
      windowsHide: true,
      maxBuffer: 32 * 1024 * 1024,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1', ...env },
    }, (err, stdout, stderr) => {
      if (onAbort) signal?.removeEventListener('abort', onAbort)
      const aborted = signal?.aborted === true
      const code = aborted ? -1 : (err && 'code' in err && typeof err.code === 'number' ? err.code : err ? 1 : 0)
      resolve({ code, stdout: String(stdout ?? ''), stderr: String(stderr ?? ''), aborted })
    })
    onAbort = () => { child.kill() }
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

