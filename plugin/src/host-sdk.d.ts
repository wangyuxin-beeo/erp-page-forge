// 构建期边界声明；实际实现始终由 DSH 宿主提供。
declare module '@deepseek-ai/dsh-tools' {
  export function defineTool(options: {
    name: string
    description: string
    parameters: Record<string, unknown>
    output: { schema: Record<string, unknown>; render: (args: unknown, value: Record<string, unknown>) => { type: string; text: string }[] }
    execute: (args: Record<string, unknown>, exec: any) => Promise<Record<string, unknown>>
  }): unknown
}
