import {recognizeWithHost} from '../lib/host-vision.js'
import {readFileSync,mkdirSync} from 'node:fs'
import {resolve,join} from 'node:path'
import assert from 'node:assert/strict'
const root=resolve(import.meta.dirname,'../..'),out=join(root,'plugin/test-results/host-vision');mkdirSync(out,{recursive:true})
const spec=JSON.parse(readFileSync(join(root,'build/vision/screenshot-samples/form-two-tables.json'),'utf8'))
let calls=0
const host={attachments:{async saveImages(images){assert(images[0].data.length>0);return [{attachmentId:'local-test-image'}]}},llm:{async *stream(req){calls++;assert.equal(req.provider,'test-private-provider');assert.equal(req.model,'gpt-6.1-sol');assert.equal(req.messages[0].content[1].type,'image');assert(!('apiKey' in req));yield {type:'text-delta',text:JSON.stringify(spec)};yield {type:'finish',reason:{kind:'stop'}}}}}
await recognizeWithHost(host,{provider:'test-private-provider',model:'gpt-6.1-sol'},root,join(root,'build/axure/shots/out-inout.png'),'test',join(out,'screenshot.spec.json'),new AbortController().signal)
assert.equal(calls,1)
assert.deepEqual(JSON.parse(readFileSync(join(out,'screenshot.spec.json'),'utf8')),spec)
console.log('PASS host model route, image attachment, response parsing; no API key passed to plugin')
