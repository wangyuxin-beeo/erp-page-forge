"""Real browser hash-router prototype smoke test. Ephemeral local server only."""
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import json
from pathlib import Path
import tempfile
import threading
import web_capture


def main():
    with tempfile.TemporaryDirectory(prefix='hash-prototype-',dir=str(web_capture.HERE)) as temp:
        root=Path(temp);site=root/'site';site.mkdir()
        (site/'index.html').write_text('''<!doctype html><meta charset="utf-8"><title>Hash prototype</title>
<h1 id="title">HOME_AUDIT</h1><a href="#/detail">Detail</a><a href="#!/history">History</a><a href="#section">Section anchor</a><div id="section">Section</div>
<script>function render(){document.querySelector('h1').textContent=location.hash==='#/detail'?'DETAIL_AUDIT':location.hash==='#!/history'?'HISTORY_AUDIT':'HOME_AUDIT'}onhashchange=render;render()</script>''',encoding='utf-8')
        class Quiet(SimpleHTTPRequestHandler):
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(site)))
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            url='http://127.0.0.1:%s/index.html#/home'%server.server_address[1]
            out=root/'capture'
            data=web_capture.capture(url,out,1280,800,{'access':'prototype','maxPages':5})
            prototype=data['prototype']
            urls=[page['url'] for page in prototype['pages']]
            assert len(urls)==3,urls
            assert any(u.endswith('#/home') for u in urls)
            assert any(u.endswith('#/detail') for u in urls)
            assert any(u.endswith('#!/history') for u in urls)
            assert not any(u.endswith('#section') for u in urls)
            texts=' '.join((out/page['file']).read_text(encoding='utf-8') for page in prototype['pages'])
            assert 'HOME_AUDIT' in texts and 'DETAIL_AUDIT' in texts and 'HISTORY_AUDIT' in texts
            print(json.dumps({'ok':True,'pages':len(urls),'checks':['hash slash routes traversed','hashbang routes traversed','ordinary anchors deduplicated','distinct page DOM captured'],'urls':urls},ensure_ascii=True,indent=2))
        finally:
            server.shutdown();server.server_close()


if __name__=='__main__':main()
