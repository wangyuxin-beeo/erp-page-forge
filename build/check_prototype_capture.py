"""Real-browser local prototype regression. No production systems or model calls."""
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import web_capture
import prototype_capture as pc


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self,*args):pass


def main():
    root=Path(tempfile.mkdtemp(prefix='prototype-fixture-',dir=str(web_capture.HERE)))
    site=root/'site';site.mkdir();out=root/'out'
    document={'document':{'sitemap':{'rootNodes':[{'pageName':'首页','url':'home.html'},{'pageName':'详情','url':'detail.html'}]}}}
    page={'page':{'name':'首页','diagram':{'objects':[{'id':'ax-order','label':'订单详情','type':'button','interactionMap':{'onClick':{'cases':[{'actions':[{'action':'linkWindow','target':{'url':'detail.html'}},{'action':'setPanelState','panelsToStates':{'panel':'state2'}},{'action':'showWidget','objectsToShow':['dialog']}]}]}}}]}}}
    (site/'start.html').write_text('<!doctype html><meta charset="utf-8"><title>Axure player</title><script src="document.js"></script><iframe name="mainFrame" src="home.html" style="width:100%;height:700px"></iframe>',encoding='utf-8')
    (site/'document.js').write_text('window.$axure={loadDocument:function(d){this.document=d.document}};$axure.loadDocument('+json.dumps(document)+');',encoding='utf-8')
    (site/'page.js').write_text('window.$axure={loadCurrentPage:function(d){this.page=d.page}};$axure.loadCurrentPage('+json.dumps(page)+');',encoding='utf-8')
    (site/'home.html').write_text('<!doctype html><meta charset="utf-8"><title>原型首页</title><style>body{color:#334455;background:#eef6ff;padding:24px}button{border-radius:8px}</style><h1>订单原型</h1><a id="detail" href="detail.html">查看订单</a><button id="tab" role="tab" aria-controls="panel">切换面板</button><div id="panel">订单明细</div><button id="submit" onclick="fetch(\'/mutate\')">提交订单</button><input name="password" value="SECRET_MUST_NOT_EXPORT"><script src="page.js"></script><img src="http://127.0.0.1:1/private.png">',encoding='utf-8')
    (site/'detail.html').write_text('<!doctype html><meta charset="utf-8"><title>订单详情</title><h1>订单详情</h1><a href="home.html">返回</a>',encoding='utf-8')
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(site)))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url='http://127.0.0.1:%s/start.html'%server.server_address[1]
    try:
        policy=pc.NetworkPolicy(url)
        assert policy.allows(url) and not policy.allows('http://127.0.0.1:1/private.png')
        assert not policy.allows('file:///C:/secret') and not policy.allows('http://10.0.0.8/ERP')
        try:web_capture.validate_url(url);raise AssertionError('public accepted private target')
        except ValueError:pass
        web_capture.capture(url,out,1280,800,{'access':'prototype','maxPages':3})
        data=json.loads((out/'page-data.json').read_text(encoding='utf-8'))
        proto=data['prototype']
        assert len(proto['pages'])>=2,proto
        assert any('detail.html' in e['to'] for e in proto['edges'])
        assert any(i.get('event')=='onClick' and i['status']=='declared' for p in proto['pages'] for i in p['interactions'])
        actions=[a.get('action') for p in proto['pages'] for i in p['interactions'] for a in i.get('actions',[])]
        assert 'linkWindow' in actions and 'setPanelState' in actions and 'showWidget' in actions,actions
        assert any(i['status']=='needs_review' for p in proto['pages'] for i in p['interactions'])
        assert (out/'page-2.json').is_file() and (out/'page-2.png').is_file()
        assert data['title']=='原型首页'
        assert 'SECRET_MUST_NOT_EXPORT' not in (out/'page-data.json').read_text(encoding='utf-8')
        assert 'mutate' not in (out/'page-data.json').read_text(encoding='utf-8')
        assert data['tokens']['colors']
        directory_out=root/'directory-out'
        web_capture.capture(str(site),directory_out,1280,800,{'access':'prototype','maxPages':2})
        assert json.loads((directory_out/'prototype.json').read_text(encoding='utf-8'))['sourceKind']=='axure-directory'
        # Headful login control is exercised with a local fixture that presses only
        # the isolated capture control. No credentials or business buttons clicked.
        (site/'login.html').write_text('<!doctype html><meta charset="utf-8"><title>Login fixture</title><h1>已登录样例</h1><script>setInterval(()=>{let h=document.getElementById("__forge_capture_control");h?.shadowRoot?.querySelector("button")?.click()},200)</script>',encoding='utf-8')
        login_out=root/'login-out'
        web_capture.capture(url.replace('start.html','login.html'),login_out,1280,800,{'access':'prototype','login':True,'loginTimeout':30})
        login=json.loads((login_out/'page-data.json').read_text(encoding='utf-8'))
        assert '__forge_capture_control' not in json.dumps(login,ensure_ascii=False)
        assert not list(login_out.glob('*cookie*'))
        result={'ok':True,'fixture':str(root),'pages':len(proto['pages']),'edges':len(proto['edges']),'actions':actions,'checks':['public default remains private-denied','same-origin private network policy','Axure player iframe primary document','Axure declaration metadata and link BFS','native href/aria-controls and unknown buttons','no business button execution/input values','top-level multi-page artifacts','directory serving','headful login isolated capture control/no cookie export']}
        (root/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=True,indent=2))
    finally:server.shutdown();server.server_close()


if __name__=='__main__':main()
