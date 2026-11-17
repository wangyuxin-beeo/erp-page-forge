"""Click real Studio controls inside an iframe behind Desktop-style streaming forwarding."""
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'build/web-deps'))
from playwright.sync_api import sync_playwright
config = json.loads(Path(sys.argv[1]).read_text())
out = ROOT / 'plugin/test-results'
origin = '{uri.scheme}://{uri.netloc}'.format(uri=urlsplit(config['base']))
with sync_playwright() as pw:
    browser = pw.chromium.launch(channel='chrome', headless=True)
    context = browser.new_context(viewport={'width':1440,'height':1000})
    page = context.new_page()
    errors, failed, checks = [], [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('response', lambda response: failed.append({'url':response.url,'status':response.status}) if response.status >= 400 else None)
    page.goto(origin + '/test-shell', wait_until='networkidle')
    frame = page.frame_locator('iframe[title="页归工作台"]')
    frame.locator('#gBuiltin .card').first.wait_for()
    assert frame.locator('#gBuiltin .card').count() == 6
    frame.locator('#cmdIn').fill('生成销售订单列表页')
    frame.locator('#bParse').click()
    frame.locator('#parsed').filter(has_text='销售订单').wait_for()
    checks.append('iframe + Desktop streaming: parse button')
    frame.locator('#bCmd').click()
    frame.locator('#res.on').wait_for(timeout=90000)
    frame.locator('#dlBar').filter(has_text='运行').wait_for()
    assert frame.locator('#dlBar a').count() >= 1
    checks.append('iframe + Desktop streaming: generate and result panel')
    frame.locator('#bWs').click()
    import uuid
    workspace_name = '浏览器验证-' + uuid.uuid4().hex[:8]
    frame.locator('#wsNew').fill(workspace_name)
    frame.locator('#bWsAdd').click()
    frame.locator('#wsNew').wait_for()
    page.wait_for_timeout(500)
    assert workspace_name in frame.locator('#wsDlg').inner_text()
    frame.locator('#bWsClose').click()
    checks.append('workspace create button')
    frame.locator('#bHis').click()
    frame.locator('#hisDlg.on').wait_for()
    frame.locator('#hisWs').select_option('')
    frame.locator('#bHisReload').click()
    frame.locator('#hisBody .hisCk[data-run^="run-"]').first.wait_for()
    frame.locator('#bHisClose').click()
    checks.append('history button and generated run')
    frame.locator('#mWeb').click()
    frame.locator('#webCapture').wait_for(state='visible')
    frame.locator('#webAccess').select_option('prototype')
    checks.append('reference web mode switch')
    page.screenshot(path=str(out/'workbench-browser.png'),full_page=True)
    assert not errors, errors
    assert not failed, failed
    print(json.dumps({'browser':'Chrome','checks':checks,'scriptErrors':errors,'failedRequests':failed},ensure_ascii=False))
    browser.close()
