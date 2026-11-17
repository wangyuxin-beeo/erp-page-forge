// npm pack must never silently include runtime files left in engine/.
import { existsSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'
import { copyEngine, copiedResources } from './copy-engine.mjs'
if (!existsSync(new URL('../lib/index.js', import.meta.url))) throw new Error('Run npm run build before npm pack')
copyEngine()
function verify(dir, allowed, prefix = '') {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const rel = prefix + entry.name
    if (entry.isSymbolicLink()) throw new Error('Refusing to pack a symlink: ' + rel)
    if (entry.isDirectory()) verify(join(dir, entry.name), allowed, rel + '/')
    else if (!entry.isFile() || !allowed.has(rel)) throw new Error('Unexpected file in build output; use a clean checkout or clean staging: ' + rel)
  }
}
verify(fileURLToPath(new URL('../engine/', import.meta.url)), copiedResources)
const source = readdirSync(new URL('../src/', import.meta.url)).filter(n => n.endsWith('.ts') && !n.endsWith('.d.ts')).map(n => n.slice(0, -3) + '.js')
verify(fileURLToPath(new URL('../lib/', import.meta.url)), new Set([...source, 'client.js']))
console.log('[prepare-pack] engine and lib contain only expected source-derived files')
