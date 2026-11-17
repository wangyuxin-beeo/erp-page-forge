// Safe release: assemble a fresh staging tree from controlled sources, never engine/ runtime data.
// Usage: node scripts/pack-plugin.mjs [--pnpm /path/to/pnpm.mjs] [--node /path/to/node] [--out /path]
// Reads PNPM_SCRIPT, NODE_BIN or npm_execpath as defaults; no developer machine paths.
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import { copyEngine } from './copy-engine.mjs'

const plugin=resolve(dirname(fileURLToPath(import.meta.url)),'..')
function arg(name,fallback) { const i=process.argv.indexOf(name);return i>=0 ? process.argv[i+1] : fallback }
const node=arg('--node',process.env.NODE_BIN || process.execPath)
const pnpm=arg('--pnpm',process.env.PNPM_SCRIPT || process.env.npm_execpath)
const out=resolve(arg('--out',join(plugin,'release')))
mkdirSync(out,{recursive:true})
// Fresh directory only; retained for review. Never remove users' runtime or staging data.
const stagingRoot=mkdtempSync(join(out,'staging-'))
const stage=join(stagingRoot,'package');mkdirSync(stage)
const manifest=JSON.parse(readFileSync(join(plugin,'package.json'),'utf8'))
if(!existsSync(join(plugin,'lib','index.js'))) throw new Error('Build the plugin first; lib/index.js is missing')
cpSync(join(plugin,'lib'),join(stage,'lib'),{recursive:true})
cpSync(join(plugin,'cordis.patch.yml'),join(stage,'cordis.patch.yml'))
copyEngine(join(stage,'engine'))
// Publish metadata only. No lifecycle install/build scripts or development dependencies in the release.
const release={...manifest,files:['lib','engine','cordis.patch.yml','README.md','LICENSE','LICENSES']}
delete release.scripts;delete release.devDependencies;delete release.private
for(const name of readdirSync(plugin)) if (/^(?:README(?:\.[\w-]+)?\.md|LICEN[CS]E(?:\.[\w.-]+)?|NOTICE(?:\.[\w.-]+)?|LICENSES)$/i.test(name)) {
  cpSync(join(plugin,name),join(stage,name),{recursive:true})
  if(!release.files.includes(name)) release.files.push(name)
}
writeFileSync(join(stage,'package.json'),JSON.stringify(release,null,2)+'\n')
console.log('[pack-plugin] clean staging:',stage)
if(process.argv.includes('--stage-only')) process.exit(0)
// pnpm runs with inherited stdio (no captured pipes), and lifecycle scripts were stripped above.
let result
if(pnpm) {
  if(!/\.(?:m?js|cjs)$/i.test(pnpm)) throw new Error('--pnpm must be the pnpm JavaScript CLI path (pnpm.mjs)')
  result=spawnSync(node,[resolve(pnpm),'pack','--pack-destination',out],{cwd:stage,stdio:'inherit',env:process.env})
} else if(process.platform !== 'win32') {
  result=spawnSync('pnpm',['pack','--pack-destination',out],{cwd:stage,stdio:'inherit',env:process.env})
} else {
  throw new Error('Set PNPM_SCRIPT or pass --pnpm <path/to/pnpm.mjs>; staging is ready for review')
}
if(result.error) throw result.error
if(result.status !== 0) throw new Error('pnpm pack failed: '+result.status)
console.log('[pack-plugin] tarball output:',out)
