"""Render real host-model reconstruction and record practical browser checks."""
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'build/web-deps'))
from playwright.sync_api import sync_playwright
RUN=Path.home()/'.dsh/profiles/default/erp-page-forge-data/studio-out/default/run-20261112-170347-d472106b'
with sync_playwright() as p:
    browser=p.chromium.launch(channel='chrome',headless=True)
    page=browser.new_page(viewport={'width':1280,'height':800})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto((RUN/'page.html').as_uri());page.wait_for_timeout(600)
    text=page.locator('body').inner_text()
    result={'realModel':True,'run':RUN.name,'title':page.title(),'checks':{name:name in text for name in ['Axhub','Chrome','Edge','Skill']},'pageErrors':errors,'width':1280,'scrollWidth':page.evaluate('document.documentElement.scrollWidth'),'htmlBytes':(RUN/'page.html').stat().st_size}
    page.screenshot(path=str(RUN/'rendered-1280.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(200)
    result['mobile']={'width':390,'scrollWidth':page.evaluate('document.documentElement.scrollWidth')}
    page.screenshot(path=str(RUN/'rendered-mobile.png'),full_page=True)
    browser.close()
(RUN/'real-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=True,indent=2))
