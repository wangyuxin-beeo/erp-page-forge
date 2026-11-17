"""Verify fresh offline install and Edge prototype capture without PyPI or pip.
Python outbound DNS/HTTP is blocked except loopback; Edge is independently
restricted by the prototype network policy and visits only a local fixture.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from functools import partial
ROOT=Path(__file__).resolve().parents[1]
ENGINE=Path(os.environ.get('FORGE_TEST_ENGINE',str(ROOT)))
sys.path.insert(0,str(ENGINE/'build'))
OUT=Path(tempfile.mkdtemp(prefix='offline-validation-',dir=str(ROOT/'build')))
os.environ['FORGE_DATA_DIR']=str(OUT/'data')
os.environ.pop('PYTHONPATH',None)
os.environ['PIP_NO_INDEX']='1'
# Keep browser artifacts in the explicit test data root rather than global TEMP.
(OUT/'browser-temp').mkdir()
os.environ['TEMP']=str(OUT/'browser-temp');os.environ['TMP']=str(OUT/'browser-temp')
os.environ['HTTP_PROXY']='http://127.0.0.1:1';os.environ['HTTPS_PROXY']='http://127.0.0.1:1'
# Instrument subprocess calls: no pip executable/module allowed.
original_run=subprocess.run
commands=[]
def guarded_run(args,*a,**k):
    commands.append(list(map(str,args)))
    if any(str(v)=='pip' or 'pip.exe' in str(v) for v in args):raise AssertionError('pip must not run')
    return original_run(args,*a,**k)
subprocess.run=guarded_run
original_dns=socket.getaddrinfo
blocked=[]
def local_dns(host,*a,**k):
    if host not in ('127.0.0.1','localhost','::1',None):
        blocked.append(str(host));raise AssertionError('External DNS prohibited: '+str(host))
    return original_dns(host,*a,**k)
socket.getaddrinfo=local_dns
import web_capture
from offline_deps import ensure_offline
site=OUT/'site';site.mkdir()
(site/'start.html').write_text('<!doctype html><meta charset="utf-8"><title>Offline prototype</title><h1>Offline browser test</h1><a href="detail.html">Detail</a>')
(site/'detail.html').write_text('<!doctype html><title>Offline detail</title><h1>Second page</h1>')
class Quiet(SimpleHTTPRequestHandler):
    def log_message(self,*a):pass
server=ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(site)))
threading.Thread(target=server.serve_forever,daemon=True).start()
result={'offline':True,'dataDirectoryWasEmpty':not (OUT/'data').exists(),'checks':[]}
try:
    status=web_capture.status();assert status['available'],status
    dependency=ensure_offline(web_capture.DEPS);assert dependency['available']
    result['checks'].append('Fresh package dependency auto extraction and import, no pip')
    result['dependencyPath']=str(dependency['path'])
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page();page.goto('http://127.0.0.1:%s/start.html'%server.server_address[1]);assert page.title()=='Offline prototype';browser.close()
    result['checks'].append('Existing Microsoft Edge starts without browser download')
    data=web_capture.capture('http://127.0.0.1:%s/start.html'%server.server_address[1],OUT/'capture',1280,800,{'access':'prototype','maxPages':2})
    assert len(data['prototype']['pages'])==2
    result['checks'].append('Worker process discovers offline dependencies; two-page local prototype captured')
    assert not blocked,blocked
    result['checks'].append('No parent Python external DNS or pip; unusable HTTP proxies and PIP_NO_INDEX set')
    assert web_capture.status()['available']
    result['checks'].append('Second status check reuses validated offline install')
    result.update(ok=True,python=sys.version.split()[0],pages=2,commands=len(commands))
finally:
    server.shutdown();server.server_close()
    (OUT/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True,indent=2));print('Evidence:',OUT/'result.json')
