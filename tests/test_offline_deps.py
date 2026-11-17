"""Offline-only fake wheel tests: no pip, network, browser or real wheel download."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'build'))
import offline_deps as od


class OfflineDepsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.bundle=self.root/'runtime-wheels';self.bundle.mkdir()
        self.deps=self.root/'deps';self.deps.mkdir()
        (self.deps/'user.txt').write_text('untouched')
        self.bundle_patch=patch.object(od,'BUNDLE',self.bundle);self.bundle_patch.start()
        self.runtime=patch.object(od,'_runtime',return_value=('3.12','cp312'));self.runtime.start()
        self.files=[]
        self.wheel('playwright-1.0-py3-none-win_amd64.whl',{'playwright/__init__.py':'','playwright/sync_api/__init__.py':'MARKER = 1'})
        self.wheel('pyee-1.0-py3-none-any.whl',{'pyee/__init__.py':''})
        self.wheel('typing_extensions-1.0-py3-none-any.whl',{'typing_extensions.py':''})
        self.wheel('greenlet-1.0-cp312-cp312-win_amd64.whl',{'greenlet/__init__.py':''})
        self.manifest()
    def tearDown(self):
        self.runtime.stop();self.bundle_patch.stop();self.temp.cleanup()
    def wheel(self,name,contents):
        path=self.bundle/name
        with zipfile.ZipFile(path,'w') as z:
            for key,value in contents.items():z.writestr(key,value)
        self.files.append({'name':name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        return path
    def manifest(self):
        (self.bundle/'manifest.json').write_text(json.dumps({'version':1,'platform':'win_amd64','files':self.files,'python':['3.10','3.11','3.12','3.13','3.14']}))
    def test_real_subprocess_import_install_cache_and_user_preservation(self):
        result=od.ensure_offline(self.deps)
        self.assertTrue(result['available'],result);self.assertTrue(result['installed'])
        dest=Path(result['path']);self.assertTrue(dest.is_relative_to(self.deps))
        self.assertTrue((dest/'playwright/sync_api/__init__.py').is_file())
        self.assertEqual((self.deps/'user.txt').read_text(),'untouched')
        cached=od.ensure_offline(self.deps)
        self.assertTrue(cached['available']);self.assertFalse(cached['installed']);self.assertEqual(cached['path'],result['path'])
        self.assertFalse(list(self.root.glob('.deps-offline-*')))
        self.assertFalse((self.root/'.deps.offline.lock').exists())
    def test_bundle_status_and_no_bundle_leave_existing_files(self):
        self.assertTrue(od.bundle_status()['available'])
        (self.bundle/'manifest.json').unlink()
        result=od.ensure_offline(self.deps)
        self.assertFalse(result['available']);self.assertFalse(result['installed']);self.assertTrue(result['advice'])
        self.assertTrue((self.deps/'user.txt').exists())
    def test_corrupt_hash_prevents_extraction_even_cached(self):
        first=od.ensure_offline(self.deps);self.assertTrue(first['available'])
        (self.bundle/self.files[0]['name']).write_bytes(b'corrupt')
        result=od.ensure_offline(self.deps)
        self.assertFalse(result['available']);self.assertIn('SHA256',result['advice'])
        self.assertTrue(Path(first['path']).exists())
    def test_path_traversal_rejected_and_staging_cleaned(self):
        for name in ('../escaped.py','/absolute.py','C:/evil.py','a\\evil.py','a/../../evil.py'):
            with self.subTest(name=name):
                path=self.bundle/self.files[0]['name']
                with zipfile.ZipFile(path,'w') as z:z.writestr(name.replace('\\','/'),'bad')
                if '\\' in name:
                    path.write_bytes(path.read_bytes().replace(name.replace('\\','/').encode(),name.encode()))
                self.files[0]['sha256']=hashlib.sha256(path.read_bytes()).hexdigest();self.manifest()
                result=od.ensure_offline(self.deps)
                self.assertFalse(result['available']);self.assertIn('路径',result['advice'])
                self.assertFalse((self.root/'escaped.py').exists())
                self.assertFalse(list(self.root.glob('.deps-offline-*')))
    def test_symlink_in_wheel_rejected(self):
        p=self.bundle/self.files[0]['name']
        with zipfile.ZipFile(p,'w') as z:
            entry=zipfile.ZipInfo('link');entry.create_system=3;entry.external_attr=(stat.S_IFLNK|0o777)<<16
            z.writestr(entry,'../outside')
        self.files[0]['sha256']=hashlib.sha256(p.read_bytes()).hexdigest();self.manifest()
        result=od.ensure_offline(self.deps)
        self.assertFalse(result['available']);self.assertIn('链接',result['advice'])
    def test_python_abi_selection_excludes_free_threaded(self):
        manifest,_=od._read_bundle()
        for minor in range(10,15):
            abi='cp3'+str(minor)
            wheels=[e for e in self.files if not e['name'].startswith('greenlet')]
            wheels += [{'name':'greenlet-1.0-'+abi+'-'+abi+'-win_amd64.whl','sha256':'0'*64},
                       {'name':'greenlet-1.0-'+abi+'-'+abi+'t-win_amd64.whl','sha256':'0'*64}]
            manifest['files']=wheels
            selected=od._select(manifest,'3.'+str(minor),abi)
            green=[e['name'] for e in selected if e['name'].startswith('greenlet')]
            self.assertEqual(green,['greenlet-1.0-'+abi+'-'+abi+'-win_amd64.whl'])
        manifest['files']=[e for e in self.files if not e['name'].startswith('greenlet')]
        with self.assertRaises(ValueError):od._select(manifest,'3.12','cp312')
    def test_unsupported_runtime_clear_advice(self):
        with patch.object(od,'_runtime',side_effect=ValueError('仅支持 Windows x64')):
            self.assertIn('Windows x64',od.ensure_offline(self.deps)['advice'])
    def test_invalid_manifest_names_and_duplicate_selection(self):
        self.files[0]['name']='../outside.whl';self.manifest()
        self.assertFalse(od.bundle_status()['available'])
        self.files[0]['name']='playwright-1.0-py3-none-win_amd64.whl'
        self.files.append(dict(self.files[0],name='playwright-2.0-py3-none-win_amd64.whl'))
        self.manifest()
        self.assertIn('多个',od.ensure_offline(self.deps)['advice'])
    def test_probe_failure_does_not_replace_existing_or_leave_staging(self):
        with patch.object(od,'_probe',return_value=False):
            result=od.ensure_offline(self.deps)
        self.assertFalse(result['available']);self.assertIn('导入',result['advice'])
        self.assertEqual(list(self.deps.iterdir()),[self.deps/'user.txt'])
        self.assertFalse(list(self.root.glob('.deps-offline-*')))
    def test_expired_lock_recovers_and_current_lock_times_out(self):
        lock=self.root/'.deps.offline.lock';lock.write_text('{}')
        os.utime(lock,(time.time()-1000,time.time()-1000))
        self.assertTrue(od.ensure_offline(self.deps)['available'])
        lock.write_text('{}')
        with patch.object(od,'LOCK_TIMEOUT',.01):
            result=od.ensure_offline(self.deps)
        self.assertFalse(result['available']);self.assertIn('超时',result['advice']);self.assertTrue(lock.exists())
    def test_failed_commit_restores_previous_version(self):
        first=od.ensure_offline(self.deps);dest=Path(first['path'])
        real_replace=Path.replace
        def replace(p,target):
            if p.name.startswith('.deps-offline-'):raise OSError('simulated atomic rename failure')
            return real_replace(p,target)
        with patch.object(od,'_probe',side_effect=[False,True]),patch.object(Path,'replace',replace):
            failed=od.ensure_offline(self.deps)
        self.assertFalse(failed['available']);self.assertTrue(dest.exists())
        self.assertTrue((dest/'playwright/sync_api/__init__.py').exists())
        self.assertEqual((self.deps/'user.txt').read_text(),'untouched')
    def test_thread_serialization(self):
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            results=list(pool.map(lambda _:od.ensure_offline(self.deps),range(3)))
        self.assertTrue(all(r['available'] for r in results),results)
        self.assertEqual(sum(r['installed'] for r in results),1)

if __name__=='__main__':unittest.main()
