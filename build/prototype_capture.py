"""Opt-in same-origin prototype capture, bounded interaction metadata and link BFS.
This module never clicks business controls or evaluates serialized prototype actions.
"""
import base64
from collections import deque
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import re
import socket
import threading
import time
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit


def normalize_options(options=None):
    if options is None: options={}
    if not isinstance(options,dict): raise ValueError('options 必须是 JSON 对象')
    access=options.get('access','public')
    if access not in ('public','prototype'): raise ValueError('access 仅支持 public/prototype')
    def number(key,default,lo,hi):
        value=options.get(key,default)
        if isinstance(value,bool) or not isinstance(value,int) or not lo<=value<=hi:
            raise ValueError('%s 必须是 %s–%s 的整数'%(key,lo,hi))
        return value
    result={'access':access,'maxPages':number('maxPages',1,1,10),'login':options.get('login',False),
            'loginTimeout':number('loginTimeout',120,30,180)}
    if not isinstance(result['login'],bool):raise ValueError('login 必须为布尔值')
    if access=='public' and result['login']:raise ValueError('手动登录仅支持 prototype 模式')
    if 'projectContext' in options:
        serialized=json.dumps(options['projectContext'],ensure_ascii=False)
        if len(serialized)>64000:raise ValueError('projectContext 超过 64KB')
        result['projectContext']=options['projectContext']
    return result


def validate_target(url,options=None):
    options=normalize_options(options)
    if options['access']=='public':
        from web_capture import validate_url
        return validate_url(url)
    if not isinstance(url,str) or not url or len(url)>4096:raise ValueError('请输入原型网址或目录')
    if not url.lower().startswith(('http://','https://')):
        path=Path(url).expanduser()
        if not path.is_dir():raise ValueError('原型目录不存在')
        if not any((path/name).is_file() for name in ('start.html','index.html')):raise ValueError('原型目录缺少 start.html 或 index.html')
        return str(path.resolve())
    origin(url)
    return url


def origin(url):
    p=urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:
        raise ValueError('原型地址须为不含账号密码的 http/https URL')
    if len(url)>4096:raise ValueError('原型地址过长')
    return (p.scheme.lower(),p.hostname.lower(),p.port or (443 if p.scheme=='https' else 80))


def canonical_key(url):
    """Keep SPA route hashes, but deduplicate ordinary in-page anchors."""
    parts=urlsplit(url)
    fragment=parts.fragment if parts.fragment.startswith(('/', '!/')) else ''
    return urlunsplit(parts._replace(fragment=fragment))


class NetworkPolicy:
    """Private requests must match the explicitly entered origin; public resources allowed.
    Browser DNS is separate from validation: intended for trusted local development only.
    """
    def __init__(self,url):
        self.allowed_origin=origin(url)
    def allows(self,url):
        try:
            if urlsplit(url).scheme in ('data','blob'):return True
            target=origin(url)
            if target==self.allowed_origin:return True
            addresses=socket.getaddrinfo(target[1],target[2],type=socket.SOCK_STREAM)
            return bool(addresses) and all(ipaddress.ip_address(a[4][0]).is_global for a in addresses)
        except (ValueError,OSError):return False
    def same_origin(self,url):
        try:return origin(url)==self.allowed_origin
        except ValueError:return False


# Only own data properties are traversed: no getters, methods or user code invoked.
GLOBAL_METADATA = r"""() => {
 const output=[],seen=new WeakSet();let total=0;
 function clone(value,depth=0){
  if(++total>12000||depth>18)return '[truncated]';
  if(value===null||typeof value==='boolean'||typeof value==='number')return value;
  if(typeof value==='string')return value.slice(0,2000);
  if(typeof value!=='object'||seen.has(value))return undefined;
  seen.add(value);const dst=Array.isArray(value)?[]:{};
  for(const key of Object.keys(value).slice(0,150)){
   if(['__proto__','constructor','prototype','value','password','cookie','storage'].includes(key.toLowerCase()))continue;
   const descriptor=Object.getOwnPropertyDescriptor(value,key);
   if(!descriptor||!('value' in descriptor))continue;
   const v=clone(descriptor.value,depth+1);if(v!==undefined)dst[key]=v;
  }return dst;
 }
 const ax=Object.getOwnPropertyDescriptor(window,'$axure');
 if(ax&&'value' in ax&&ax.value&&typeof ax.value==='object'){
  for(const key of ['document','page','pageData','currentPage','sitemap']){
   const d=Object.getOwnPropertyDescriptor(ax.value,key);
   if(d&&'value' in d&&typeof d.value==='object')output.push({source:'$axure.'+key,data:clone(d.value)});
  }
 }
 for(const key of ['_axureDocument','_axurePage']){
  const d=Object.getOwnPropertyDescriptor(window,key);
  if(d&&'value' in d&&d.value&&typeof d.value==='object')output.push({source:key,data:clone(d.value)});
 }
 return output;
}"""

DOM_INTERACTIONS = r"""() => {
 const items=[];
 for(const el of document.querySelectorAll('a[href],button,[role],[aria-controls],[data-target],[data-toggle],[data-label],[data-type]')){
  if(items.length>=700)break;
  const id=el.id||null, role=el.getAttribute('role'), tag=el.tagName.toLowerCase();
  const text=(el.innerText||el.getAttribute('aria-label')||el.getAttribute('data-label')||'').trim().slice(0,160);
  if(tag==='a'&&el.getAttribute('href')&&!/^(javascript:|mailto:|tel:)/i.test(el.getAttribute('href'))){
   items.push({kind:'navigate',event:'click',source:id,label:text,target:el.href,status:'declared',evidence:'DOM href'});
  }
  for(const attr of ['aria-controls','data-target']){
   const value=el.getAttribute(attr);if(!value)continue;
   const ids=attr==='aria-controls'?value.split(/\s+/):[value.replace(/^#/,'')];
   for(const target of ids.slice(0,10))items.push({kind:'control',event:'click',source:id,label:text,target,status:'declared',evidence:'DOM '+attr,targetExists:!!document.getElementById(target),role});
  }
  if((tag==='button'||['button','tab','menuitem'].includes(role))&&!el.getAttribute('aria-controls')&&!el.getAttribute('data-target')){
   items.push({kind:'unknown',event:'click',source:id,label:text,role,status:'needs_review',evidence:'Visible control; no declared target'});
  }
 }
 return items;
}"""

LOGIN_CONTROL = r"""() => {
 if(document.getElementById('__forge_capture_control'))return;
 const host=document.createElement('div');host.id='__forge_capture_control';
 host.style.cssText='position:fixed;right:20px;top:20px;z-index:2147483647';
 const shadow=host.attachShadow({mode:'open'}),button=document.createElement('button');
 button.textContent='登录完成，开始采集';button.style.cssText='background:#6254ef;color:white;padding:16px 20px;border:2px solid white;border-radius:12px;box-shadow:0 3px 20px #333;font:16px sans-serif;cursor:pointer';
 button.onclick=()=>window.__forgeLoginReady();shadow.appendChild(button);document.documentElement.appendChild(host);
}"""


def parse_axure_source(text,source):
    """Parse JSON arguments only. JS expressions and executable payloads are ignored."""
    if len(text)>3*1024*1024:return []
    result=[]
    for match in re.finditer(r'\$axure\.(?:loadDocument|loadCurrentPage)\s*\(\s*',text):
        try:
            value,_=json.JSONDecoder().raw_decode(text,match.end())
            if isinstance(value,dict):result.append({'source':source,'data':value})
        except (ValueError,RecursionError):pass
        if len(result)>=3:break
    return result


def extract_axure(metadata,base_url):
    interactions=[];links=[];unknown=[];visited=0
    def bounded(value,depth=0):
        if depth>10:return '[truncated]'
        if isinstance(value,str):return value[:2000]
        if isinstance(value,list):return [bounded(v,depth+1) for v in value[:60]]
        if isinstance(value,dict):return {str(k)[:80]:bounded(v,depth+1) for k,v in list(value.items())[:100] if str(k).lower() not in ('password','cookie','value','variables','globalvariables')}
        return value if value is None or isinstance(value,(bool,int,float)) else None
    def walk(value,source,owner=None):
        nonlocal visited
        visited+=1
        if visited>18000:return
        if isinstance(value,list):
            for item in value[:400]:walk(item,source,owner)
        elif isinstance(value,dict):
            current={'id':value.get('id') or value.get('scriptId'),'label':value.get('label') or value.get('name'),'type':value.get('type')}
            current={k:str(v)[:200] for k,v in current.items() if isinstance(v,(str,int))} or owner
            url=value.get('url')
            if isinstance(url,str) and url and len(url)<4096 and not url.startswith(('javascript:','mailto:','tel:')):
                target=urljoin(base_url,url)
                links.append({'url':target,'label':str(value.get('pageName') or value.get('name') or value.get('label') or '')[:160],'evidence':source+' url'})
            interaction=value.get('interactionMap')
            if isinstance(interaction,dict):
                for event,detail in list(interaction.items())[:30]:
                    if len(interactions)>=600:break
                    item={'kind':'axure','event':str(event)[:80],'source':current,'status':'declared','evidence':source+' interactionMap','declaration':bounded(detail)}
                    interactions.append(item)
                    find_actions(detail,item)
            for key,item in list(value.items())[:300]:
                if key!='interactionMap':walk(item,source,current)
    def find_actions(value,item):
        if isinstance(value,list):
            for child in value[:100]:find_actions(child,item)
        elif isinstance(value,dict):
            action=value.get('action')
            if isinstance(action,str):
                item.setdefault('actions',[])
                if len(item['actions'])<60:item['actions'].append(bounded(value))
                if action in ('linkWindow','openLink'):
                    target=value.get('target',{})
                    target=target.get('url') if isinstance(target,dict) else target
                    target=target or value.get('url')
                    if isinstance(target,str) and target:
                        resolved=urljoin(base_url,target)
                        item.setdefault('targets',[]).append(resolved)
                        links.append({'url':resolved,'label':(item.get('source') or {}).get('label',''),'event':item['event'],'evidence':item['evidence']+' '+action})
            for key,child in list(value.items())[:100]:
                if key!='actions' or isinstance(child,list):find_actions(child,item)
    for entry in metadata[:30]:walk(entry.get('data'),entry.get('source','Axure'))
    if visited>18000:unknown.append({'status':'needs_review','reason':'Axure metadata traversal limit reached'})
    return interactions[:600],links[:1000],unknown


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self,*args):pass
    def translate_path(self,path):
        candidate=Path(super().translate_path(path)).resolve()
        root=Path(self.directory).resolve()
        return str(candidate) if candidate.is_relative_to(root) else str(root/'__forge_denied__')


def capture_prototype(url,out,width,height,options,collect):
    from playwright.sync_api import sync_playwright
    directory_server=None
    if isinstance(url,str) and Path(url).is_dir():
        root=Path(url).resolve()
        entry=next((name for name in ('start.html','index.html') if (root/name).is_file()),None)
        if not entry:raise ValueError('原型目录缺少 start.html 或 index.html')
        directory_server=ThreadingHTTPServer(('127.0.0.1',0),partial(_QuietHandler,directory=str(root)))
        threading.Thread(target=directory_server.serve_forever,daemon=True).start()
        url='http://127.0.0.1:%s/%s'%(directory_server.server_address[1],entry)
    policy=NetworkPolicy(url)
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    assets={};blocked=set();metadata=[];asset_size=[0];meta_size=[0]
    prototype={'version':'forge-prototype-1.0','access':'prototype','source':url,'pages':[],'edges':[],'unknown':[],'warnings':[]}
    if directory_server:prototype['sourceKind']='axure-directory'
    if 'projectContext' in options:prototype['projectContext']=options['projectContext']
    try:
        with sync_playwright() as pw:
            browser=None
            for channel in ('chrome','msedge',None):
                try:browser=pw.chromium.launch(channel=channel,headless=not options['login']);break
                except Exception:continue
            if browser is None:raise RuntimeError('未找到可启动的 Chrome/Edge/Chromium')
            context=browser.new_context(viewport={'width':width,'height':height},service_workers='block',accept_downloads=False)
            def route_request(route):
                if policy.allows(route.request.url):route.continue_()
                else:blocked.add(urlsplit(route.request.url).netloc[:120]);route.abort()
            context.route('**/*',route_request)
            if hasattr(context,'route_web_socket'):context.route_web_socket('**/*',lambda ws:ws.close())
            page=context.new_page()
            def save_response(response):
                try:
                    mime=response.headers.get('content-type','').split(';')[0]
                    if policy.same_origin(response.url) and ('.js' in urlsplit(response.url).path or 'javascript' in mime) and len(metadata)<30 and meta_size[0]<8*1024*1024:
                        if int(response.headers.get('content-length','0'))>3*1024*1024:return
                        text=response.text()
                        meta_size[0]+=len(text)
                        metadata.extend(parse_axure_source(text,response.url))
                    if response.url not in assets and len(assets)<40 and asset_size[0]<8*1024*1024 and mime in ('image/png','image/jpeg','image/webp','image/gif','font/woff','font/woff2'):
                        if int(response.headers.get('content-length','0'))>2*1024*1024:return
                        body=response.body()
                        if len(body)>2*1024*1024 or asset_size[0]+len(body)>8*1024*1024:return
                        assets[response.url]={'mime':mime,'data':'data:'+mime+';base64,'+base64.b64encode(body).decode('ascii')};asset_size[0]+=len(body)
                except Exception:pass
            context.on('response',save_response)
            try:
                response=page.goto(url,wait_until='domcontentloaded',timeout=35000)
                if response and response.status>=400:raise ValueError('目标网页返回 HTTP %s'%response.status)
                if options['login']:
                    ready=[False]
                    page.expose_function('__forgeLoginReady',lambda:ready.__setitem__(0,True))
                    page.add_init_script(LOGIN_CONTROL)
                    deadline=time.monotonic()+options['loginTimeout']
                    while not ready[0] and time.monotonic()<deadline:
                        if page.is_closed():raise ValueError('登录浏览器已关闭')
                        try:page.evaluate(LOGIN_CONTROL)
                        except Exception:pass
                        page.wait_for_timeout(250)
                    if not ready[0]:raise ValueError('等待手动登录超时，请重试并在浏览器中点击开始采集')
                    page.evaluate("document.getElementById('__forge_capture_control')?.remove()")
                pending=deque([page.url]);seen=set();main_data=None
                while pending and len(prototype['pages'])<options['maxPages']:
                    target=pending.popleft()
                    canonical=canonical_key(target)
                    if canonical in seen or not policy.same_origin(target):continue
                    seen.add(canonical)
                    if len(prototype['pages']) or canonical!=canonical_key(page.url):
                        metadata.clear();meta_size[0]=0
                        try:
                            response=page.goto(target,wait_until='domcontentloaded',timeout=25000)
                            if response and response.status>=400:raise ValueError('HTTP %s'%response.status)
                        except Exception as exc:
                            prototype['unknown'].append({'url':target,'status':'needs_review','reason':str(exc)[:180]});continue
                    if not policy.same_origin(page.url):
                        prototype['unknown'].append({'url':target,'status':'needs_review','reason':'Redirect left authorized origin'});continue
                    page.wait_for_timeout(900)
                    page.evaluate("document.getElementById('__forge_capture_control')?.remove()")
                    frames=[frame for frame in page.frames if policy.same_origin(frame.url)]
                    for frame in frames:
                        try:frame.evaluate("document.getElementById('__forge_capture_control')?.remove()")
                        except Exception:pass
                    # Axure player shells expose prototype in mainFrame; choose actual content first.
                    preferred=[f for f in frames if f!=page.main_frame and (f.name.lower() in ('mainframe','main') or '/start.html' in page.url)]
                    primary=preferred[0] if preferred else page.main_frame
                    seen.add(canonical_key(primary.url))
                    page.wait_for_timeout(200)
                    for i in range(1,4):
                        primary.evaluate('(i)=>scrollTo(0,Math.min(document.documentElement.scrollHeight,innerHeight*i))',i)
                        page.wait_for_timeout(100)
                    primary.evaluate('scrollTo(0,0)')
                    data=primary.evaluate(collect)
                    if not data['nodes']:
                        prototype['unknown'].append({'url':target,'status':'needs_review','reason':'No visible content'});continue
                    index=len(prototype['pages'])+1
                    data_file='page-data.json' if index==1 else 'page-%s.json'%index
                    shot='screenshot.png' if index==1 else 'page-%s.png'%index
                    # Keep all same-origin frames as bounded extra documents, not cross-origin data.
                    frame_data=[];dom_interactions=[];all_meta=list(metadata)
                    for frame in frames[:12]:
                        try:
                            dom_interactions.extend(frame.evaluate(DOM_INTERACTIONS))
                            globals_data=frame.evaluate(GLOBAL_METADATA)
                            if len(json.dumps(globals_data))<2*1024*1024:all_meta.extend(globals_data)
                            if frame!=primary:
                                child=frame.evaluate(collect)
                                if child['nodes']:frame_data.append({'url':frame.url,'name':frame.name,'data':child})
                        except Exception:prototype['unknown'].append({'url':frame.url,'status':'needs_review','reason':'Frame extraction unavailable'})
                    declared,links,unknown=extract_axure(all_meta,primary.url)
                    interactions=[];interaction_keys=set()
                    for interaction in (dom_interactions+declared)[:1200]:
                        key=json.dumps(interaction,sort_keys=True,ensure_ascii=False)
                        if key not in interaction_keys:interaction_keys.add(key);interactions.append(interaction)
                    if primary!=page.main_frame:
                        page.screenshot(path=str(out/shot),clip={'x':0,'y':0,'width':width,'height':min(12000,max(height,page.evaluate('document.documentElement.scrollHeight')))},animations='disabled',timeout=15000)
                    else:
                        page.screenshot(path=str(out/shot),clip={'x':0,'y':0,'width':width,'height':min(12000,max(height,data['document']['height']))},animations='disabled',timeout=15000)
                    record={'id':'page-%s'%index,'url':primary.url,'entryUrl':page.url,'title':data['title'],'file':data_file,'screenshot':shot,'status':'observed','interactions':interactions}
                    prototype['pages'].append(record);prototype['unknown'].extend(unknown)
                    data.update({'assets':dict(assets),'capturedAt':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'warnings':[],
                                 'frames':frame_data,'interactions':interactions,'prototypePage':record['id']})
                    if index==1:main_data=data
                    (out/data_file).write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
                    candidates=links+[{'url':v['target'],'label':v.get('label',''),'event':v['event'],'evidence':v['evidence']} for v in dom_interactions if v['kind']=='navigate']
                    for item in candidates[:1500]:
                        destination=item['url']
                        if not policy.same_origin(destination):continue
                        if urlsplit(destination).path.lower().endswith(('.js','.css','.png','.jpg','.pdf','.zip','.svg','.woff','.woff2')):continue
                        edge={'from':primary.url,'to':destination,'event':item.get('event','navigate'),'label':item.get('label',''),'evidence':item['evidence'],'status':'declared'}
                        if edge not in prototype['edges'] and len(prototype['edges'])<2000:prototype['edges'].append(edge)
                        # Ordinary fragments only change an anchor, including when entered
                        # from a hash-routed page; they are not a separate page to visit.
                        fragment=urlsplit(destination).fragment
                        if fragment and not fragment.startswith(('/', '!/')):continue
                        # Route hashes identify SPA pages; plain anchors remain same-page state.
                        key=canonical_key(destination)
                        if key not in seen and len(pending)<1500:pending.append(destination)
                if main_data is None:raise ValueError('没有可采集的可见原型页面')
                if pending:prototype['warnings'].append('已达到页面数量限制，仍有声明目标未访问。')
                if blocked:prototype['warnings'].append('已阻止其他私网源或不支持协议的请求；单点登录需同源或公开认证地址。')
                prototype['unknown'].append({'status':'needs_review','reason':'未执行任意业务按钮、提交或自定义脚本；声明交互不等于行为已验证。'})
                prototype['unknown']=prototype['unknown'][:300]
                prototype['warnings'].append('仅采集当前可见状态；跨源 iframe、Canvas、条件分支和完整后端业务需人工核对。')
                (out/'prototype.json').write_text(json.dumps(prototype,ensure_ascii=False,indent=2),encoding='utf-8')
                main_data['prototype']=prototype
                main_data['assets']=assets
                main_data['warnings']=prototype['warnings']
                (out/'page-data.json').write_text(json.dumps(main_data,ensure_ascii=False),encoding='utf-8')
                theme={'version':'forge-theme-1.0','source':main_data['url'],'viewport':main_data['viewport'],'tokens':main_data['tokens']}
                (out/'theme.json').write_text(json.dumps(theme,ensure_ascii=False,indent=2),encoding='utf-8')
            finally:context.close();browser.close()
    finally:
        if directory_server:directory_server.shutdown();directory_server.server_close()
    return main_data
