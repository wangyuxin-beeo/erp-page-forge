"""Full Studio browser verification against the installed engine. Model is simulated.
Also probes the user's running Studio without modifying model configuration.
"""
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import sys
import threading
import time
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
INSTALLED=Path.home()/'.dsh/profiles/desktop/node_modules/dsh-erp-page-forge/engine'
OUT=ROOT/'build'/('full-integration-'+time.strftime('%Y%m%d-%H%M%S'))
OUT.mkdir(parents=True)
project=OUT/'project';(project/'src/styles').mkdir(parents=True)
(project/'package.json').write_text(json.dumps({'dependencies':{'vue':'3.5','element-plus':'2.9','vue-router':'4'},'devDependencies':{'typescript':'5'}}))
(project/'src/styles/theme.scss').write_text(':root{--project-brand:#187a67;--project-radius:8px}\n$space: 12px;')
site=OUT/'prototype';site.mkdir()
(site/'start.html').write_text('<!doctype html><meta charset="utf-8"><title>原型播放器</title><iframe name="mainFrame" src="home.html" style="width:100%;height:700px"></iframe>')
(site/'home.html').write_text('<!doctype html><meta charset="utf-8"><title>采购原型</title><style>body{color:#187a67}</style><h1>采购管理</h1><a href="detail.html">查看明细</a><button aria-controls="dialog">详情弹窗</button><div id="dialog">订单详情</div><button>提交</button>')
(site/'detail.html').write_text('<!doctype html><meta charset="utf-8"><title>采购详情</title><h1>采购详情</h1><a href="home.html">返回</a>')
class Quiet(SimpleHTTPRequestHandler):
    def log_message(self,*args):pass
origin=ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(site)))
threading.Thread(target=origin.serve_forever,daemon=True).start()
target='http://127.0.0.1:%s/start.html'%origin.server_address[1]
os.environ['FORGE_DATA_DIR']=str(OUT/'data')
os.environ['PYTHONPATH']=str(ROOT/'build/web-deps')
sys.path.insert(0,str(INSTALLED/'build'))
sys.path.insert(0,str(ROOT/'build/web-deps'))
import studio_server
from playwright.sync_api import sync_playwright
calls=[]
def mock(conf,image,prompt):
    calls.append(prompt)
    if '"files"' in prompt:
        assert '--project-brand' in prompt and 'element-plus' in prompt and 'detail.html' in prompt
        return json.dumps({'files':[{'path':'src/views/Prototype.vue','content':'<template><el-button @click="open=true">详情</el-button><el-dialog v-model="open">采购详情</el-dialog></template><script setup lang="ts">import {ref} from "vue";const open=ref(false)</script><style scoped>button{color:var(--project-brand)}</style>'},{'path':'INTEGRATION.md','content':'Register view and routes, verify project theme, build and test. Mock API only.'}]})
    return '<!doctype html><html><head><style>h1{color:#187a67}</style></head><body><h1>采购管理</h1><script>alert(1)</script></body></html>'
studio_server.ai_conf=lambda:{'model':'integration-mock','base':'http://127.0.0.1','timeout':30}
studio_server._chat_prompt=mock
server=studio_server.ThreadingHTTPServer(('127.0.0.1',0),studio_server.Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
base='http://127.0.0.1:%s'%server.server_address[1]
result={'installedEngine':str(INSTALLED.resolve()),'model':'simulated; no quality/build claim','checks':[],'artifacts':str(OUT)}
try:
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(base,wait_until='networkidle');page.click('#mWeb')
        page.wait_for_function("!document.getElementById('webCapture').disabled")
        page.select_option('#webAccess','prototype');page.fill('#webUrl',target)
        page.fill('#webProjectPath',str(project));page.fill('#webMaxPages','3')
        page.click('#webCapture')
        page.wait_for_function("!document.getElementById('webCaptureResult').hidden || document.getElementById('webMessage').classList.contains('bad')",timeout=100000)
        assert not page.locator('#webCaptureResult').is_hidden(),page.locator('#webMessage').inner_text()
        assert '2 页' in page.locator('#webSummary').inner_text()
        assert 'vue' in page.locator('#webProjectFramework').inner_text()
        assert 'src/styles/theme.scss' in page.locator('#webProjectFiles').inner_text()
        result['checks'].append('Installed engine + real Studio UI: local nonstandard-port iframe prototype, two pages, interactions, Vue theme detected')
        capture_run=None
        for selector in ('#webData','#webTheme','#webPrototype','#webBundle'):
            href=page.locator(selector).get_attribute('href');response=page.request.get(base+href)
            assert response.ok
            if selector=='#webData':
                data=response.json();assert data['title']=='采购原型';assert data['prototype']['pages'];capture_run=href.split('/')[3]
            if selector=='#webBundle':
                with zipfile.ZipFile(io.BytesIO(response.body())) as z:
                    assert any(n.endswith('/page-2.json') for n in z.namelist()) and any(n.endswith('/project-context.json') for n in z.namelist())
        result['checks'].append('Data/theme/prototype/full bundle downloads, second-page evidence and project context archived')
        page.screenshot(path=str(OUT/'capture-ui.png'))
        page.select_option('#webGenerationMode','project');page.click('#webRestore')
        page.wait_for_function("!document.getElementById('webRestoreResult').hidden || document.getElementById('webMessage').classList.contains('bad')",timeout=20000)
        assert not page.locator('#webRestoreResult').is_hidden(),page.locator('#webMessage').inner_text()
        code=page.request.get(base+page.locator('#webCode').get_attribute('href'))
        with zipfile.ZipFile(io.BytesIO(code.body())) as z:
            content=z.read('src/views/Prototype.vue').decode();assert '--project-brand' in content and '@click' in content
            assert 'forge/prototype.json' in z.namelist()
        assert '不是实际项目运行预览' in page.locator('#webRestoreNotice').inner_text()
        result['checks'].append('Project generation: captured theme, component library and multi-page relations reach mock model; code zip downloads; UI marks build review required')
        page.select_option('#webGenerationMode','static');page.click('#webRestore')
        page.wait_for_function("!document.getElementById('webRestoreResult').hidden || document.getElementById('webMessage').classList.contains('bad')",timeout=20000)
        page.frame_locator('#webRestoreFrame').locator('h1').wait_for()
        html=page.request.get(base+page.locator('#webRestoreFrame').get_attribute('src')).text()
        assert '<script>' not in html and 'Content-Security-Policy' in html
        result['checks'].append('Static generation: sanitized HTML renders in sandbox and retains pending acceptance')
        # Same-origin guards and input errors via actual HTTP routes.
        for payload,status in [({'url':target,'access':'public'},400),({'url':target,'access':'prototype','maxPages':11},400),({'url':target,'access':'prototype','projectPath':str(OUT/'missing')},400)]:
            assert page.request.post(base+'/api/web/capture',data=payload).status==status
        assert page.request.post(base+'/api/web/capture',headers={'Origin':'http://untrusted.invalid'},data={'url':target}).status==403
        assert page.request.post(base+'/api/web/restore',data={'run':'run-missing'}).status==404
        result['checks'].append('HTTP errors: public rejects private target, invalid limits/project path rejected, foreign origin denied, missing run rejected')
        # Verify live user Studio readiness and unconfigured model handling without altering it.
        live='http://127.0.0.1:60642'
        status=page.request.get(live+'/api/web/status').json();assert status['available']
        ai=page.request.get(live+'/api/ai/status').json();result['liveModelConfigured']=ai['configured']
        if not ai['configured']:
            assert page.request.post(live+'/api/web/restore',data={'run':'run-missing'}).status==503
        result['checks'].append('User running Studio: dependency ready; absent real model returns clear 503 rather than fake success')
        assert not errors,errors
        result['checks'].append('No Studio browser JavaScript errors')
        browser.close()
    result['ok']=True
finally:
    server.shutdown();server.server_close();origin.shutdown();origin.server_close()
    (OUT/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True,indent=2))
