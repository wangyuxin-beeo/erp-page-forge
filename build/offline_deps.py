"""Install bundled wheels without pip or network access.

The returned ``path`` must precede existing dependencies in sys.path. Existing
DEPS contents are never removed. Each manifest/runtime has an isolated immutable
installation, staged beside DEPS and committed with an atomic rename.
"""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / 'runtime-wheels'
_THREAD_LOCK = threading.Lock()
LOCK_TIMEOUT = 60.0
LOCK_STALE = 600.0
MAX_EXTRACTED = 600 * 1024 * 1024


def _runtime():
    version = '%s.%s' % sys.version_info[:2]
    if sys.platform != 'win32' or platform.machine().lower() not in ('amd64', 'x86_64') or sys.maxsize <= 2**32:
        raise ValueError('离线依赖仅支持 Windows x64。')
    if platform.python_implementation() != 'CPython' or version not in ('3.10','3.11','3.12','3.13','3.14'):
        raise ValueError('离线依赖需要 CPython 3.10–3.14。')
    import sysconfig
    if sysconfig.get_config_var('Py_GIL_DISABLED') or (hasattr(sys, '_is_gil_enabled') and not sys._is_gil_enabled()):
        raise ValueError('离线依赖不支持 free-threaded Python；请使用常规 GIL 版本。')
    return version, 'cp%s%s' % sys.version_info[:2]


def _read_bundle():
    manifest_path = BUNDLE / 'manifest.json'
    if not manifest_path.is_file():
        raise ValueError('未找到离线依赖包 runtime-wheels/manifest.json；已有依赖可继续使用。')
    raw = manifest_path.read_bytes()
    if len(raw) > 1024 * 1024:
        raise ValueError('离线依赖清单过大。')
    manifest = json.loads(raw)
    if not isinstance(manifest, dict) or manifest.get('version') != 1 or manifest.get('platform') != 'win_amd64':
        raise ValueError('离线依赖清单版本或平台不支持。')
    if not isinstance(manifest.get('files'), list) or not isinstance(manifest.get('python'), list):
        raise ValueError('离线依赖清单缺少 files/python 列表。')
    seen = set()
    for entry in manifest['files']:
        if not isinstance(entry, dict):
            raise ValueError('离线依赖文件记录不正确。')
        name, digest = entry.get('name'), entry.get('sha256')
        if not isinstance(name, str) or '/' in name or '\\' in name or ':' in name or not name.endswith('.whl') or name.startswith('.') or name in seen:
            raise ValueError('离线 wheel 文件名不合法或重复。')
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-fA-F]{64}', digest):
            raise ValueError('离线 wheel 缺少有效 SHA256。')
        seen.add(name)
    return manifest, hashlib.sha256(raw).hexdigest()


def _select(manifest, version, abi):
    if version not in manifest['python']:
        raise ValueError('离线包未声明支持 Python ' + version)
    selected = {}
    for entry in manifest['files']:
        # Wheel names may include an optional build number; tags are the last 3 components.
        parts = entry['name'][:-4].split('-')
        if len(parts) not in (5, 6):
            continue
        package = parts[0].lower().replace('-', '_')
        py, binary, target = parts[-3:]
        valid = ((package == 'playwright' and (py, binary, target) == ('py3','none','win_amd64')) or
                 (package in ('pyee','typing_extensions') and (py,binary,target) == ('py3','none','any')) or
                 (package == 'greenlet' and (py,binary,target) == (abi,abi,'win_amd64')))
        if valid:
            if package in selected:
                raise ValueError('离线包存在多个匹配的 '+package+' wheel。')
            selected[package] = entry
    missing = {'playwright','pyee','typing_extensions','greenlet'} - selected.keys()
    if missing:
        raise ValueError('离线包缺少匹配 Python '+version+' 的 wheel：'+', '.join(sorted(missing)))
    return list(selected.values())


def _probe(path):
    # -I ignores user site/PYTHONPATH; explicit package path always wins.
    script = 'import sys;sys.path.insert(0,sys.argv[1]);import playwright.sync_api'
    try:
        result = subprocess.run([sys.executable,'-I','-c',script,str(path)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                timeout=30, text=True, encoding='utf-8', errors='replace')
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _verify(entry):
    p = BUNDLE / entry['name']
    if p.is_symlink() or not p.is_file():
        raise ValueError('离线 wheel 不存在或为链接：'+entry['name'])
    h = hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    if h.hexdigest() != entry['sha256'].lower():
        raise ValueError('离线 wheel SHA256 校验失败：'+entry['name'])
    return p


def _extract(wheel, dest, total):
    with zipfile.ZipFile(wheel) as z:
        for member in z.infolist():
            name = member.orig_filename
            p = PurePosixPath(name)
            if not name or '\\' in name or ':' in name or '\x00' in name or p.is_absolute() or any(x in ('.','..') or x.endswith((' ','.')) or re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',x,re.I) for x in name.rstrip('/').split('/')):
                raise ValueError('wheel 含不安全路径：'+name)
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise ValueError('wheel 不允许链接或特殊文件。')
            # purelib/platlib data spread is supported; scripts and installation hooks are not executed.
            parts = list(p.parts)
            if parts and parts[0].endswith('.data'):
                # Headers/data/scripts remain inert inside their original .data tree;
                # only importable purelib/platlib entries are spread to package root.
                if len(parts) >= 2 and parts[1] in ('purelib','platlib'):
                    if len(parts) == 2:
                        if member.is_dir():continue
                        raise ValueError('wheel 安装数据目录不正确：'+name)
                    parts = parts[2:]
            target = dest.joinpath(*parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if target.exists():
                raise ValueError('wheel 文件冲突：'+name)
            total[0] += member.file_size
            if total[0] > MAX_EXTRACTED:
                raise ValueError('离线依赖解压体积超过限制。')
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(member) as src, target.open('xb') as dst:
                shutil.copyfileobj(src, dst)


@contextmanager
def _process_lock(path):
    deadline = time.monotonic() + LOCK_TIMEOUT
    token = uuid.uuid4().hex
    owned = False
    try:
        while True:
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump({'token':token,'pid':os.getpid(),'created':time.time()}, f)
                owned = True
                break
            except FileExistsError:
                try:
                    age = time.time() - path.stat().st_mtime
                    if age > LOCK_STALE:
                        # Expired locks are moved atomically; only remove our claimed stale file.
                        stale = path.with_name(path.name+'.stale-'+uuid.uuid4().hex)
                        path.replace(stale)
                        stale.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    raise ValueError('离线依赖正在被其他进程安装，等待超时，请稍后重试。')
                time.sleep(.1)
        yield
    finally:
        if owned:
            try:
                if json.loads(path.read_text(encoding='utf-8')).get('token') == token:
                    path.unlink()
            except (OSError, ValueError):
                pass


def bundle_status():
    """Validate manifest/runtime and selected file hashes, without installing."""
    try:
        version, abi = _runtime()
        manifest, digest = _read_bundle()
        selected = _select(manifest, version, abi)
        for entry in selected:
            _verify(entry)
        return {'available':True,'advice':'','version':1,'python':version,
                'manifestHash':digest,'files':[e['name'] for e in selected]}
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as exc:
        return {'available':False,'advice':str(exc)}


def ensure_offline(deps: Path):
    """Return {installed, available, advice, path}; never touches unrelated DEPS files.

    installed=True means this call extracted and verified a new installation.
    available=True means the returned isolated path passed a real import probe.
    Missing bundles return False without judging/removing pre-existing dependencies.
    """
    result = {'installed':False,'available':False,'advice':'','path':None}
    staging = None
    try:
        version, abi = _runtime()
        manifest, digest = _read_bundle()
        selected = _select(manifest, version, abi)
        deps = Path(deps).absolute()
        parent = deps.parent
        parent.mkdir(parents=True, exist_ok=True)
        if deps.is_symlink():
            raise ValueError('离线依赖目标目录不能是符号链接。')
        dest = deps / ('offline-'+digest[:24]+'-'+abi)
        marker = dest / '.offline-manifest.json'
        if not _THREAD_LOCK.acquire(timeout=LOCK_TIMEOUT):
            raise ValueError('离线依赖正在安装，线程等待超时，请稍后重试。')
        try:
            with _process_lock(parent / ('.'+deps.name+'.offline.lock')):
                # Recheck hash even for cached installs: corrupt bundles cannot silently pass.
                wheels = [_verify(e) for e in selected]
                if dest.is_symlink():
                    raise ValueError('离线依赖版本目录不能是符号链接。')
                if marker.is_file():
                    cached = json.loads(marker.read_text(encoding='utf-8'))
                    if cached.get('manifestHash') == digest and _probe(dest):
                        return dict(result, available=True, path=str(dest))
                staging = Path(tempfile.mkdtemp(prefix='.'+deps.name+'-offline-',dir=str(parent)))
                total = [0]
                for wheel in wheels:
                    _extract(wheel, staging, total)
                if not _probe(staging):
                    raise ValueError('离线依赖导入验证失败：当前 Python 无法导入 playwright.sync_api。')
                (staging/'.offline-manifest.json').write_text(json.dumps({'manifestHash':digest,'python':version}),encoding='utf-8')
                deps.mkdir(exist_ok=True)
                backup = None
                if dest.exists():
                    backup = parent / ('.'+deps.name+'-backup-'+uuid.uuid4().hex)
                    dest.replace(backup)
                try:
                    staging.replace(dest)
                    staging = None
                except BaseException:
                    if backup is not None:
                        backup.replace(dest)
                    raise
                if backup is not None:
                    shutil.rmtree(backup,ignore_errors=True)
                return dict(result,installed=True,available=True,path=str(dest))
        finally:
            _THREAD_LOCK.release()
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as exc:
        return dict(result,advice=str(exc))
    finally:
        if staging is not None:
            shutil.rmtree(staging,ignore_errors=True)
