"""Bounded, read-only extraction of a project's frontend stack and theme sources.
Never executes project code or reads environment files / credentials.
"""
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

SKIP={'node_modules','.git','dist','build','coverage','.next','.nuxt','vendor','.pnpm','.output'}
THEME=re.compile(r'theme|token|variable|global|tailwind|design|palette|colors?|app\.(?:css|scss|less)$',re.I)
STYLE={'.css','.scss','.less','.sass','.json','.js','.ts','.mjs'}


def read_project(raw):
    if not isinstance(raw,str) or not raw.strip():
        return None
    root=Path(raw).expanduser()
    if not root.is_absolute() or not root.is_dir():
        raise ValueError('项目目录须为本机存在的绝对目录')
    root=root.resolve()
    package=root/'package.json'
    if not package.is_file() or package.is_symlink():
        raise ValueError('项目根目录需要 package.json；请选择前端项目根目录')
    if package.stat().st_size>256*1024:raise ValueError('package.json 超过读取上限')
    manifest=json.loads(package.read_text(encoding='utf-8-sig'))
    deps={**manifest.get('dependencies',{}),**manifest.get('devDependencies',{})}
    framework='vue' if 'vue' in deps else ('react' if 'react' in deps else 'html')
    libraries={k:v for k,v in deps.items() if k in {'vue','react','react-dom','next','nuxt','vue-router','react-router-dom','antd','ant-design-vue','element-plus','@arco-design/web-react','@arco-design/web-vue','@mui/material','tailwindcss','vite','typescript','pinia','@reduxjs/toolkit'}}
    sources=[];visited=0;total=0;warnings=[]
    # Do not recursively follow symlinks; only bounded source directories.
    def walk(directory,depth=0):
        nonlocal visited,total
        if depth>5 or visited>=3500 or len(sources)>=18 or total>=65000:return
        try:children=sorted(directory.iterdir(),key=lambda p:p.name)
        except OSError:return
        for p in children:
            visited+=1
            if visited>3500:return
            if p.is_symlink() or p.name.startswith('.') or p.name in SKIP:continue
            if p.is_dir():
                walk(p,depth+1)
                continue
            relative=p.relative_to(root)
            in_theme_dir=any(part.lower() in {'theme','themes','styles','style','tokens','design-tokens'} for part in relative.parts[:-1])
            if p.suffix.lower() not in STYLE or not (THEME.search(p.name) or in_theme_dir):continue
            if any(s['path']==relative.as_posix() for s in sources):continue
            if p.stat().st_size>128*1024:continue
            text=p.read_text(encoding='utf-8',errors='replace')
            # Never include files containing credential assignments, even if theme-named.
            if re.search(r'(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*[\"\'][^\"\']+',text,re.I):
                warnings.append('跳过可能包含凭据的主题文件：'+p.relative_to(root).as_posix());continue
            excerpt=text[:min(10000,65000-total)];total+=len(excerpt)
            sources.append({'path':p.relative_to(root).as_posix(),'content':excerpt,'truncated':len(excerpt)<len(text)})
            if len(sources)>=18 or total>=65000:return
    for directory in ('src','styles','theme','themes','assets'):
        if (root/directory).is_dir():walk(root/directory)
    for name in ('tailwind.config.js','tailwind.config.ts','tailwind.config.mjs'):
        p=root/name
        if p.is_file() and not p.is_symlink() and p.stat().st_size<32000:
            text=p.read_text(encoding='utf-8')
            if re.search(r'(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*[\"\'][^\"\']+',text,re.I):
                warnings.append('跳过可能包含凭据的主题文件：'+name);continue
            if len(sources)>=18 or total>=65000:break
            excerpt=text[:min(10000,65000-total)];total+=len(excerpt)
            sources.append({'path':name,'content':excerpt,'truncated':len(excerpt)<len(text)})
    if not sources:warnings.append('没有发现可读取的主题文件；将使用原型主题候选，生成后须核对项目规范。')
    variables=[]
    for source in sources:
        for name,value in re.findall(r'(--[\w-]+|\$[\w-]+|@[\w-]+)\s*:\s*([^;{}\n]+)',source['content']):
            if len(variables)<160:variables.append({'name':name,'value':value.strip(),'file':source['path']})
    return {'version':'forge-project-1.0','framework':framework,'typescript':'typescript' in deps,
            'libraries':libraries,'files':[s['path'] for s in sources],'sources':sources,
            'variables':variables,'warnings':warnings,
            'routing':'vue-router' if 'vue-router' in deps else ('react-router-dom' if 'react-router-dom' in deps else None)}


def decode_files(raw):
    """Validate a model-generated file bundle. Never write into the source project."""
    if not isinstance(raw,str) or len(raw)>2*1024*1024:raise ValueError('项目代码输出为空或超过 2MB')
    match=re.search(r'```(?:json)?\s*([\s\S]*?)```',raw,re.I)
    obj=json.loads(match[1] if match else raw)
    files=obj.get('files')
    if not isinstance(files,list) or not 1<=len(files)<=30:raise ValueError('模型需返回包含 1–30 个文件的 files 列表')
    seen=set();out=[]
    for f in files:
        if not isinstance(f,dict):raise ValueError('代码文件格式不正确')
        name=f.get('path');text=f.get('content')
        if not isinstance(name,str) or not isinstance(text,str):raise ValueError('代码文件需要 path/content')
        p=PurePosixPath(name)
        if '\\' in name or ':' in name or not name or p.is_absolute() or any(part in {'.','..'} or part.startswith('.') for part in p.parts):raise ValueError('代码文件路径不合法')
        if name in seen or p.suffix.lower() not in {'.vue','.tsx','.jsx','.ts','.js','.css','.scss','.less','.json','.md','.html'}:raise ValueError('代码文件重复或类型不支持')
        if p.name in {'package.json','package-lock.json','tsconfig.json','vite.config.ts','vite.config.js'}:raise ValueError('生成物不可覆盖项目依赖或构建配置')
        seen.add(name);out.append({'path':name,'content':text})
    return out


def write_bundle(directory,files,context,prototype):
    """One zip is the deliverable; code is intentionally never executed or installed."""
    with zipfile.ZipFile(Path(directory)/'project-code.zip','w',zipfile.ZIP_DEFLATED) as z:
        for f in files:z.writestr(f['path'],f['content'])
        z.writestr('forge/project-context.json',json.dumps(context,ensure_ascii=False,indent=2))
        z.writestr('forge/prototype.json',json.dumps(prototype,ensure_ascii=False,indent=2))
        z.writestr('forge/INTEGRATION.md','生成代码尚未在目标项目编译或运行。先检查主题引用、路由注册、组件API及交互，再并入项目。\n未提供接口契约时仅使用模拟数据，不连接原型系统业务接口。\n文件均为新增建议，请勿直接覆盖现有项目文件。\n')
