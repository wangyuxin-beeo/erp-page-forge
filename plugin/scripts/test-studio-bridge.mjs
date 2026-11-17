import { launchStudio } from '../lib/studio.js'
import {readFileSync,mkdirSync,writeFileSync} from 'node:fs'
import {resolve,join} from 'node:path'
import assert from 'node:assert/strict'
const root=resolve(import.meta.dirname,'../..'),dir=join(root,'plugin/test-results/studio-bridge');mkdirSync(dir,{recursive:true})
const spec=JSON.parse(readFileSync(join(root,'build/vision/screenshot-samples/form-two-tables.json'),'utf8'))
let calls=0
const host={attachments:{async saveImages(inputs){assert(inputs[0].data.length);return [{attachmentId:'test-only'}]}},llm:{async *stream(req){calls++;assert.equal(req.model,'gpt-6.1-sol');assert(req.messages.some(m=>m.content.some(b=>b.type==='image')));assert(!('apiKey' in req));yield {type:'text-delta',text:JSON.stringify(spec)};yield {type:'finish',reason:{kind:'stop'}}}}}
const ctl=new AbortController();let studio
try {
 studio=await launchStudio({python:process.env.FORGE_TEST_PYTHON,root,dataDir:dir,port:0,timeoutMs:30000,host,route:{provider:'test-route',model:'gpt-6.1-sol'},signal:ctl.signal})
 const get=async p=>(await fetch(studio.url+p)).json()
 assert((await get('api/ai/status')).configured)
 const selftest=await get('api/ai/selftest');assert.equal(selftest.ok,true);assert.match(selftest.verdict,/宿主/);assert.equal(calls,0)
 const image='data:image/png;base64,'+readFileSync(join(root,'build/axure/shots/out-inout.png')).toString('base64')
 const r=await fetch(studio.url+'api/ai/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image,mode:'screenshot'})});const result=await r.json();assert.equal(r.status,200,JSON.stringify(result));assert.equal(result.extract.kind,'screenshot');assert(calls>0)
 console.log('PASS Studio HTTP -> Python -> authenticated loopback -> DSH model service -> screenshot JSON',JSON.stringify({calls,ai:studio.ai}))
 // verify a second launch cannot falsely claim the occupied address
 const port=Number(new URL(studio.url).port)
 await assert.rejects(()=>launchStudio({python:process.env.FORGE_TEST_PYTHON,root,dataDir:dir,port,timeoutMs:30000,host,signal:new AbortController().signal}))
 console.log('PASS port conflict rejected')
 writeFileSync(join(dir,'result.json'),JSON.stringify({passed:true,calls,externalAPI:false},null,2))
}finally{studio?.close();ctl.abort()}
