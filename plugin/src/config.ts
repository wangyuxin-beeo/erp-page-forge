export interface PluginConfig { python: string; timeoutSec: number }
/** Standard Schema v1: validation runs during Cordis loading, before apply. */
export const Config = {
  '~standard': {
    version: 1 as const,
    vendor: 'erp-page-forge',
    validate(input: unknown) {
      const raw = input == null ? {} : input
      if (typeof raw !== 'object' || Array.isArray(raw)) return { issues: [{ message: '插件配置必须是对象' }] }
      const obj = raw as Record<string, unknown>
      if (obj.python !== undefined && typeof obj.python !== 'string') return { issues: [{ message: 'python 必须是解释器路径字符串' }] }
      const timeout = obj.timeoutSec ?? 300
      if (typeof timeout !== 'number' || !Number.isFinite(timeout) || timeout < 5 || timeout > 1800) return { issues: [{ message: 'timeoutSec 必须是 5–1800 秒之间的数值' }] }
      return { value: { python: obj.python ?? '', timeoutSec: timeout } }
    },
  },
}
