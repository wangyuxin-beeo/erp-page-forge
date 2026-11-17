"""Real Chromium/Studio smoke test; reconstruction uses a named mock model.
Runs an ephemeral local Studio with isolated output and closes it on completion.
"""
import json
import os
from pathlib import Path
import sys
import threading
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'build'/'web-browser-validation'
OUTPUT.mkdir(exist_ok=True)
os.environ['FORGE_DATA_DIR']=str(OUTPUT)
sys.path.insert(0,str(ROOT/'build'))
sys.path.insert(0,str(ROOT/'build'/'web-deps'))
import studio_server
from playwright.sync_api import sync_playwright

studio_server.ai_conf=lambda:{'model':'browser-test-mock','base':'http://127.0.0.1','timeout':30}
studio_server._chat_prompt=lambda conf,image,prompt:'<!doctype html><html><head><title>Mock reconstruction</title><style>body{font-family:Arial;background:#f0faf6;padding:32px}h1{color:#008f5d}</style></head><body><h1>Mock reconstruction</h1><p>Browser pipeline verification only.</p><script>fetch("https://example.com")</script></body></html>'
server=studio_server.ThreadingHTTPServer(('127.0.0.1',0),studio_server.Handler)
thread=threading.Thread(target=server.serve_forever,daemon=True)
thread.start()
url='http://127.0.0.1:%s/'%server.server_address[1]
results={'model':'mock (not real model quality validation)','studio':url,'checks':[]}
try:
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000})
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(url,wait_until='networkidle')
        page.click('#mWeb')
        page.wait_for_function("!document.querySelector('#webCapture').disabled")
        results['checks'].append('Studio page and optional dependency status ready')
        page.fill('#webUrl','https://axhub.im/chrome/')
        page.click('#webCapture')
        page.wait_for_function("!document.querySelector('#webCaptureResult').hidden || document.querySelector('#webMessage').classList.contains('bad')",timeout=125000)
        if page.locator('#webCaptureResult').is_hidden():raise AssertionError(page.locator('#webMessage').inner_text())
        title=page.locator('#webTitle').inner_text()
        assert title and int(page.locator('#webScreenshot').evaluate('(el)=>el.naturalWidth'))>0
        results['checks'].append('Real public website capture and screenshot preview: '+title)
        for selector,key in [('#webData','page-data'),('#webTheme','theme'),('#webBundle','bundle')]:
            href=page.locator(selector).get_attribute('href')
            response=page.request.get(url.rstrip('/')+href)
            assert response.ok
            body=response.body()
            assert len(body)>10
            if key=='page-data':assert response.json()['nodes']
            if key=='theme':assert response.json()['tokens']['colors']
            if key=='bundle':assert body[:2]==b'PK'
            results['checks'].append('Download '+key)
        page.fill('#webHint','Preserve the main heading')
        page.click('#webRestore')
        page.wait_for_function("!document.querySelector('#webRestoreResult').hidden || document.querySelector('#webMessage').classList.contains('bad')",timeout=15000)
        if page.locator('#webRestoreResult').is_hidden():raise AssertionError(page.locator('#webMessage').inner_text())
        frame=page.frame_locator('#webRestoreFrame')
        assert frame.locator('h1').inner_text()=='Mock reconstruction'
        report=page.request.get(url.rstrip('/')+page.locator('#webReport').get_attribute('href')).text()
        assert '待人工验收' in report
        html=page.request.get(url.rstrip('/')+page.locator('#webRestoreFrame').get_attribute('src')).text()
        assert '<script>' not in html
        results['checks'].append('Mock model restoration renders in sandbox; report remains pending review')
        assert not errors,errors
        results['checks'].append('No Studio JavaScript page errors')
        page.screenshot(path=str(OUTPUT/'studio-web-capture.png'),full_page=True)
        browser.close()
    results['ok']=True
finally:
    server.shutdown();server.server_close()
    (OUTPUT/'browser-result.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(results,ensure_ascii=True,indent=2))
