#!/usr/bin/env python3
"""First-party public webpage capture. Runs Playwright in an isolated subprocess.
Install optional dependency: python build/web_capture.py --install
"""
import argparse
import base64
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
DEPS = Path(os.environ.get('FORGE_DATA_DIR', str(HERE))) / 'web-deps'
sys.path.insert(0, str(HERE / 'web-deps'))
sys.path.insert(0, str(DEPS))
INSTALL_ADVICE = '离线版会自动准备包内采集依赖，无需联网；请确认本机已安装 Chrome 或 Edge，并点击“重新检查”。'


def prepare_dependencies():
    from offline_deps import ensure_offline
    result=ensure_offline(DEPS)
    if result.get('available') and result.get('path'):
        path=str(result['path'])
        if path not in sys.path:sys.path.insert(0,path)
    return result


def validate_url(url):
    if not isinstance(url, str) or len(url) > 4096:
        raise ValueError('请输入有效的公开网页网址')
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('仅支持不含用户名和密码的 http/https 公开网址')
    if p.port not in (None, 80, 443):
        raise ValueError('公开网址采集仅支持 80/443 端口')
    try:
        addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError('无法解析网址域名') from exc
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('当前入口只采集公开网页，不访问本机或内网地址')
    return url


def status():
    # Directory presence does not prove native dependencies match this Python.
    offline=prepare_dependencies()
    paths=([str(offline['path'])] if offline.get('available') and offline.get('path') else [])+[str(DEPS),str(HERE/'web-deps')]
    probe = 'import sys; sys.path[:0] = ' + repr(paths) + '; from playwright.sync_api import sync_playwright'
    try:
        result = subprocess.run([sys.executable, '-c', probe], capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=15,
                                env=dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8'))
        if result.returncode == 0:
            return {'available': True, 'advice': ''}
        lines = (result.stderr or result.stdout).strip().splitlines()
        detail = lines[-1][:300] if lines else '依赖导入失败'
    except (OSError, subprocess.TimeoutExpired) as exc:
        detail = type(exc).__name__
    return {'available': False, 'advice': '采集依赖不可用（Python %s）：%s。%s' %
            (sys.version.split()[0], detail, offline.get('advice') or INSTALL_ADVICE)}


# Deliberately select computed presentation properties; never read form values,
# cookies, storage, scripts or event handlers from the target.
COLLECT = r"""() => {
 const props = ['display','position','width','height','padding','margin','gap','grid-template-columns','flex-direction','align-items','justify-content','color','background-color','background-image','font-family','font-size','font-weight','line-height','letter-spacing','border','border-radius','box-shadow','text-align','overflow'];
 const nodes=[], styles=[], pool=new Map(), counts={colors:{},fontFamilies:{},fontSizes:{},spacing:{},radii:{},shadows:{}};
 const count=(k,v)=>{if(v && !['none','normal','0px','rgba(0, 0, 0, 0)'].includes(v)) counts[k][v]=(counts[k][v]||0)+1};
 let visited=0, truncated=false;
 function walk(el,parent){
  if(++visited>12000 || nodes.length>=2500){truncated=true;return}
  if(['SCRIPT','STYLE','LINK','META','NOSCRIPT','IFRAME','OBJECT','EMBED'].includes(el.tagName)) return;
  const c=getComputedStyle(el),r=el.getBoundingClientRect();
  if(c.display==='none'||c.visibility==='hidden'||!r.width||!r.height)return;
  const st={};props.forEach(k=>st[k]=c.getPropertyValue(k));
  const key=JSON.stringify(st);let sid=pool.get(key);if(sid===undefined){sid=styles.length;pool.set(key,sid);styles.push(st)}
  const id=nodes.length;
  const direct=[...el.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent.trim()).filter(Boolean).join(' ').slice(0,1000);
  const node={id,parent,tag:el.tagName.toLowerCase(),text:direct,style:sid,bbox:[Math.round(r.x+scrollX),Math.round(r.y+scrollY),Math.round(r.width),Math.round(r.height)]};
  if(el.id)node.domId=el.id.slice(0,200);
  for(const [key,attr] of [['name','name'],['label','data-label'],['type','data-type'],['role','role']]){const value=el.getAttribute(attr);if(value)node[key]=value.slice(0,200)}
  if(!node.type&&el.getAttribute('type'))node.type=el.getAttribute('type').slice(0,100);
  node.interactive=el.matches('a[href],button,input,select,textarea,[role="button"],[role="tab"],[aria-controls],[data-target]');
  if(el.tagName==='IMG'){node.src=el.currentSrc||el.src;node.alt=(el.alt||'').slice(0,200)}
  if(el.tagName==='A'){node.href=el.href}
  if(el.tagName==='SVG'){node.svg=el.outerHTML.slice(0,12000)}
  nodes.push(node);
  count('colors',c.color);count('colors',c.backgroundColor);count('fontFamilies',c.fontFamily);count('fontSizes',c.fontSize);
  ['paddingTop','paddingRight','paddingBottom','paddingLeft','rowGap','columnGap'].forEach(k=>count('spacing',c[k]));count('radii',c.borderRadius);count('shadows',c.boxShadow);
  if(el.tagName!=='SVG')for(const child of el.children)walk(child,id);
 }
 if(document.body)walk(document.body,null);
 const tokens={};Object.entries(counts).forEach(([k,v])=>tokens[k]=Object.entries(v).sort((a,b)=>b[1]-a[1]).slice(0,32).map(([value,count])=>({value,count})));
 return {version:'forge-web-1.0',title:document.title,url:location.href,viewport:{width:innerWidth,height:innerHeight},document:{width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight},nodes,styles,tokens,truncated};
}"""


def capture_worker(url, out, width=1280, height=800, options=None):
    prepare_dependencies()
    # Browser artifacts belong to the writable plugin data root, not global TEMP.
    browser_temp=DEPS.parent/'browser-temp'
    browser_temp.mkdir(parents=True,exist_ok=True)
    os.environ['TEMP']=str(browser_temp);os.environ['TMP']=str(browser_temp)
    from prototype_capture import normalize_options, validate_target, capture_prototype
    options=normalize_options(options)
    if options['access']=='prototype':
        return capture_prototype(validate_target(url,options),out,width,height,options,COLLECT)
    from playwright.sync_api import sync_playwright
    validate_url(url)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    blocked, assets, budget = set(), {}, [0]
    with sync_playwright() as pw:
        browser = None
        for channel in ('chrome', 'msedge', None):
            try:
                browser = pw.chromium.launch(channel=channel, headless=True)
                break
            except Exception:
                continue
        if browser is None:
            raise RuntimeError('未找到可启动的 Chrome/Edge/Chromium；可执行 python -m playwright install chromium')
        context = browser.new_context(viewport={'width': width, 'height': height}, service_workers='block', accept_downloads=False)
        checked = {}
        def route_request(route):
            target = route.request.url
            scheme = urlsplit(target).scheme
            if scheme in ('data', 'blob'):
                return route.continue_()
            host = urlsplit(target).netloc
            try:
                if host not in checked:
                    validate_url(target)
                    checked[host] = True
                elif scheme not in ('http', 'https'):
                    raise ValueError('scheme')
                route.continue_()
            except Exception:
                blocked.add(host[:120])
                route.abort()
        context.route('**/*', route_request)
        page = context.new_page()
        if hasattr(page, 'route_web_socket'):
            page.route_web_socket('**/*', lambda ws: ws.close())
        def save_asset(response):
            try:
                mime = response.headers.get('content-type', '').split(';')[0]
                if response.url in assets or len(assets)>=40 or budget[0]>=8*1024*1024:
                    return
                if mime not in ('image/png','image/jpeg','image/webp','image/gif','font/woff','font/woff2'):
                    return
                if int(response.headers.get('content-length','0')) > 2*1024*1024:
                    return
                body = response.body()
                if len(body)>2*1024*1024 or budget[0]+len(body)>8*1024*1024:
                    return
                budget[0]+=len(body)
                assets[response.url]={'mime':mime,'data':'data:'+mime+';base64,'+base64.b64encode(body).decode('ascii')}
            except Exception:
                pass
        page.on('response', save_asset)
        try:
            response=page.goto(url, wait_until='domcontentloaded', timeout=35000)
            if response and response.status>=400:
                raise ValueError('目标网页返回 HTTP %s' % response.status)
            page.wait_for_timeout(1200)
            # Trigger a bounded amount of lazy content, then return to the top.
            for i in range(1, 5):
                page.evaluate('(i)=>scrollTo(0,Math.min(document.documentElement.scrollHeight,innerHeight*i))',i)
                page.wait_for_timeout(180)
            page.evaluate('scrollTo(0,0)')
            page.wait_for_timeout(300)
            validate_url(page.url)
            data=page.evaluate(COLLECT)
            if not data['nodes']:
                raise ValueError('网页没有可采集的可见内容')
            shot_height=min(12000,max(height,data['document']['height']))
            page.screenshot(path=str(out/'screenshot.png'),clip={'x':0,'y':0,'width':width,'height':shot_height}, animations='disabled',timeout=20000)
            data['assets']=assets
            data['capturedAt']=time.strftime('%Y-%m-%dT%H:%M:%S%z')
            data['warnings']=[]
            if blocked: data['warnings'].append('已阻止非公开或不支持的资源地址，部分内容可能缺失。')
            if data['truncated']: data['warnings'].append('DOM 已达到采集上限，数据已截断。')
            if data['document']['height']>12000: data['warnings'].append('截图仅保留前 12000 像素。')
            data['warnings'].append('采集当前桌面视口；跨域 iframe、Canvas 内容和完整业务交互可能无法还原。')
            (out/'page-data.json').write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
            theme={'version':'forge-theme-1.0','source':data['url'],'viewport':data['viewport'],'tokens':data['tokens']}
            (out/'theme.json').write_text(json.dumps(theme,ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            context.close()
            browser.close()


def capture(url,out,width,height,options=None):
    from prototype_capture import normalize_options
    normalized=normalize_options(options)
    env=dict(os.environ,PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
    args=[sys.executable,str(Path(__file__).resolve()),'--worker',url,'--out',str(out),'--width',str(width),'--height',str(height)]
    if options is not None:args+=['--options',json.dumps(normalized,ensure_ascii=False)]
    timeout=110 if normalized['access']=='public' else 45+60*normalized['maxPages']+(normalized['loginTimeout'] if normalized['login'] else 0)
    result=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',env=env,timeout=timeout)
    if result.returncode:
        message=(result.stderr or result.stdout or '采集失败').strip().splitlines()[-1]
        raise RuntimeError(message[:500])
    return json.loads((Path(out)/'page-data.json').read_text(encoding='utf-8'))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install',action='store_true')
    parser.add_argument('--worker')
    parser.add_argument('--out')
    parser.add_argument('--width',type=int,default=1280)
    parser.add_argument('--height',type=int,default=800)
    parser.add_argument('--options',default='{}',help='JSON: access, maxPages, login, loginTimeout')
    args=parser.parse_args()
    if args.install:
        result=prepare_dependencies()
        print(json.dumps(result,ensure_ascii=False))
        if not result.get('available'):sys.exit(1)
    elif args.worker:
        capture_worker(args.worker,args.out,args.width,args.height,json.loads(args.options))
    else:
        print(json.dumps(status(),ensure_ascii=False))
