// Does not touch the installed engine or delete anything; all fixtures live under a new test directory.
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, readdirSync } from 'node:fs'
import { join, resolve } from 'node:path'
import assert from 'node:assert/strict'
import { copyEngine, required } from './copy-engine.mjs'
const root=resolve(import.meta.dirname,'..','test-results');mkdirSync(root,{recursive:true})
const target=mkdtempSync(join(root,'pack-resource-test-'))
const engine=join(target,'engine')
const protectedFiles=['build/studio-out/customer-run/page.html','build/studio-library/tpl-private/meta.json','build/studio-library-ff/tpl-private/page.html']
for(const p of protectedFiles){mkdirSync(resolve(engine,p,'..'),{recursive:true});writeFileSync(join(engine,p),'private-sentinel')}
copyEngine(engine)
for(const p of protectedFiles) assert.equal(readFileSync(join(engine,p),'utf8'),'private-sentinel')
for(const p of required) assert(existsSync(join(engine,p)),p)
const fresh=join(target,'fresh-engine');copyEngine(fresh)
for(const p of protectedFiles) assert(!existsSync(join(fresh,p)),p)
assert(existsSync(join(fresh,'build/ext/freeform/__init__.py')))
assert(!existsSync(join(fresh,'build/ext/freeform/_demo.page.html')))
assert(!existsSync(join(fresh,'build/vision/shots/input-user-outorder.png')))
assert(!existsSync(join(fresh,'docs/Forge Studio 使用教程')))
for(const e of readdirSync(join(fresh,'docs'),{withFileTypes:true})) {
  assert(e.isFile(), 'docs must not include subdirectories: '+e.name)
  assert(/\.(md|html)$/i.test(e.name), 'docs contains non-reference asset: '+e.name)
}
function verifyNoHidden(dir) {
  for(const e of readdirSync(dir,{withFileTypes:true})) {
    assert(!e.name.startsWith('.'), 'hidden/cache asset copied: '+join(dir,e.name))
    assert(!/^(?:push\.sh|.*\.pptx)$/i.test(e.name), 'publishing/presentation asset copied: '+e.name)
    if(e.isDirectory()) verifyNoHidden(join(dir,e.name))
  }
}
verifyNoHidden(fresh)
console.log('PASS resource completeness, runtime preservation, fresh tree omits .cache/hidden docs/slides/publishing scripts:',target)
