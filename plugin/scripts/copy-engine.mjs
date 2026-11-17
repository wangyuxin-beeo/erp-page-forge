// Copy deployable resources without ever deleting engine runtime data.
// Existing studio-out/libraries are deliberately neither traversed nor removed.
import { cpSync, existsSync, mkdirSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, resolve, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const pluginDir = resolve(here, '..')
const repoRoot = resolve(pluginDir, '..')
const defaultDest = join(pluginDir, 'engine')
let dest = defaultDest
export const copiedResources = new Set()
const required = [
  'build/forge.py', 'build/axure.py', 'build/axure_static.py',
  'build/verify_acceptance.py', 'build/screenshot_render.py', 'build/studio_server.py',
  'skill/spec/recipes.json', 'skill/spec/archetypes.json', 'skill/skeletons/A1.html',
  'studio/index.html', 'studio/ai-prompt-screenshot.md',
  'studio/web-capture.js', 'studio/web-capture.css',
  'build/web_capture.py', 'build/web_routes.py', 'docs/web-capture.md',
  'build/prototype_capture.py', 'build/project_context.py', 'build/project_generate.py',
  'docs/dsh-plugin-usage.md', 'build/offline_deps.py',
  'build/axure/sample-prototype/data/document.js',
  'build/axure/sample-prototype/files/inout-list/data.js',
]
const missing = required.filter(p => !existsSync(join(repoRoot,p)))
if (missing.length) throw new Error('Required source assets missing:\n'+missing.join('\n'))
const excluded = new Set(['__pycache__','.git','.prof','.slidep','node_modules'])
function copyFile(src) {
  const out = join(dest,relative(repoRoot,src))
  mkdirSync(dirname(out),{recursive:true})
  cpSync(src,out)
  copiedResources.add(relative(repoRoot,src).replaceAll('\\', '/'))
}
function copyTree(rel, filter = () => true) {
  const src=join(repoRoot,rel)
  if (!existsSync(src)) return
  for(const e of readdirSync(src,{withFileTypes:true})) {
    if (excluded.has(e.name) || e.name.startsWith('.') || (e.name.startsWith('_') && e.name !== '__init__.py')) continue
    const sub=join(rel,e.name)
    if(e.isSymbolicLink()) continue // Never follow arbitrary links into local user data.
    if(e.isDirectory()) copyTree(sub,filter)
    else if(e.isFile() && filter(e.name,sub)) copyFile(join(repoRoot,sub))
  }
}
export function copyEngine(target = defaultDest) {
  dest = resolve(target)
  copiedResources.clear()
  if (dest === repoRoot || dest === pluginDir) throw new Error('Refusing an unsafe engine destination')
  mkdirSync(dest,{recursive:true})
// Python modules only at build root; no historical reports/logs/browser profiles.
for(const e of readdirSync(join(repoRoot,'build'),{withFileTypes:true}))
  if(e.isFile() && e.name.endsWith('.py') && !e.name.startsWith('_') && e.name !== 'prepare_offline_wheels.py') copyFile(join(repoRoot,'build',e.name))
for(const dir of ['skill','templates','studio']) copyTree(dir)
// Platform-specific binary dependencies are intentionally excluded.
// Public source profile: install optional dependencies separately; no bundled wheels.
// Documentation is reference material, not an application asset tree. Only root Markdown/HTML.
for(const e of readdirSync(join(repoRoot,'docs'),{withFileTypes:true}))
  if(e.isFile() && !e.name.startsWith('.') && /\.(?:md|html)$/i.test(e.name)) copyFile(join(repoRoot,'docs',e.name))
// Freeform implementation and its schema, but no historical _demo artifacts.
copyTree('build/ext', n => n.endsWith('.py') || n.endsWith('.schema.json'))
// Captured screenshot fixtures are not part of the public source profile.
copyTree('build/axure/sample-prototype')
for(const p of required) if(!existsSync(join(dest,p))) throw new Error('Missing copied resource: '+p)
console.log(`[copy-engine] resources updated in ${dest}; runtime data preserved`)
return dest
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) copyEngine()
export { required }
