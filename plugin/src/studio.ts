import { spawn } from 'node:child_process'
import type { VisionHost, ModelRoute } from './host-vision.js'
import { startModelBridge } from './model-bridge.js'

export async function launchStudio(options: { python: string; root: string; dataDir: string; port: number;
  timeoutMs: number; host: VisionHost; route?: ModelRoute; signal: AbortSignal }) {
  const bridge = options.route ? await startModelBridge(options.host, options.route, options.timeoutMs) : undefined
  const child = spawn(options.python, ['-u','build/studio_server.py','--port',String(options.port)], {
    cwd:options.root, windowsHide:true, stdio:['ignore','pipe','pipe'],
    env:{...process.env,PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8',FORGE_DATA_DIR:options.dataDir, ...bridge?.env},
  })
  let closed=false
  const close=()=>{if(closed)return;closed=true;child.kill();bridge?.close();options.signal.removeEventListener('abort',close)}
  options.signal.addEventListener('abort',close,{once:true})
  if(options.signal.aborted)close()
  child.once('exit',()=>{bridge?.close();options.signal.removeEventListener('abort',close)})
  try {
    const url=await new Promise<string>((ok,fail)=>{
      let buffer=''
      const timer=setTimeout(()=>fail(new Error('Studio 15 秒内未就绪')),15000)
      child.once('error',()=>{clearTimeout(timer);fail(new Error('Studio 子进程启动失败'))})
      child.once('exit',()=>{clearTimeout(timer);fail(new Error('Studio 在就绪前退出，端口可能被占用'))})
      child.stdout.on('data',b=>{buffer+=b.toString();const m=buffer.match(/Forge Studio: (http:\/\/127\.0\.0\.1:\d+\/)/);if(m){clearTimeout(timer);ok(m[1])}})
      child.stderr.on('data',()=>{})
    })
    const response=await fetch(url+'api/templates',{signal:AbortSignal.timeout(5000)})
    if(!response.ok)throw new Error('Studio 就绪检查失败')
    return { url, close, ai:bridge?'DSH':'external-or-manual' }
  } catch(e){close();throw e}
}
