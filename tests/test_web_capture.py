"""Web capture contract and safety tests; browser and model are mocked.

Run: python -m unittest discover -s tests -p test_web_capture.py -v
These checks do NOT verify real browser capture or actual model visual fidelity.
"""
import base64
import copy
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'build'))
import studio_store
import web_capture
import web_routes

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
ASSET_URL = 'https://public.example/logo.png'
ASSET_DATA = 'data:image/png;base64,' + base64.b64encode(PNG).decode()
FIXTURE = {
    'version': 'forge-web-1.0', 'title': '<Reference & dashboard>',
    'url': 'https://public.example/page',
    'viewport': {'width': 1280, 'height': 800},
    'document': {'width': 1280, 'height': 1800},
    'nodes': [{'id': 0, 'parent': None, 'tag': 'body', 'style': 0, 'text': 'Dashboard', 'bbox': [0, 0, 1280, 1800]}],
    'styles': [{'color': 'rgb(22, 22, 31)', 'font-size': '14px'}],
    'tokens': {'colors': [{'value': '#5b5bd6', 'count': 4}]},
    'assets': {ASSET_URL: {'mime': 'image/png', 'data': ASSET_DATA}},
    'truncated': False, 'warnings': ['Fixture capture; not a live webpage.'],
}


def dns(*addresses):
    return [(socket.AF_INET6 if ':' in address else socket.AF_INET,
             socket.SOCK_STREAM, 6, '', (address, 443)) for address in addresses]


class URLPolicyTests(unittest.TestCase):
    def test_public_http_and_https(self):
        with patch.object(web_capture.socket, 'getaddrinfo', return_value=dns('8.8.8.8')) as resolver:
            for url in ('https://public.example/page', 'http://public.example:80/page', 'https://public.example:443/'):
                self.assertEqual(web_capture.validate_url(url), url)
            self.assertEqual(resolver.call_count, 3)

    def test_private_reserved_loopback_and_mixed_dns_rejected(self):
        for addresses in [('127.0.0.1',), ('10.0.0.1',), ('172.16.0.1',), ('192.168.1.2',),
                          ('169.254.169.254',), ('0.0.0.0',), ('::1',), ('fc00::1',),
                          ('fe80::1',), ('8.8.8.8', '10.0.0.1'), ()]:
            with self.subTest(addresses=addresses), patch.object(web_capture.socket, 'getaddrinfo', return_value=dns(*addresses)):
                with self.assertRaises(ValueError):
                    web_capture.validate_url('https://public.example/')

    def test_credentials_ports_and_schemes_rejected_before_dns(self):
        with patch.object(web_capture.socket, 'getaddrinfo') as resolver:
            for url in ('https://user:secret@public.example/', 'https://user@public.example/',
                        'https://public.example:8080/', 'https://public.example:9222/',
                        'file:///etc/passwd', 'javascript:alert(1)', 'ftp://public.example/', '', None):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    web_capture.validate_url(url)
            resolver.assert_not_called()

    def test_dns_failure_returns_validation_error(self):
        with patch.object(web_capture.socket, 'getaddrinfo', side_effect=socket.gaierror('fixture DNS error')):
            with self.assertRaisesRegex(ValueError, '无法解析'):
                web_capture.validate_url('https://public.example/')

    def test_optional_dependency_status(self):
        failed=subprocess.CompletedProcess([],1,'','ImportError: incompatible native dependency')
        offline={'available':False,'advice':'离线依赖不匹配'}
        with patch.object(web_capture,'prepare_dependencies',return_value=offline), patch.object(web_capture.subprocess,'run',return_value=failed):
            self.assertFalse(web_capture.status()['available'])
            self.assertIn('离线依赖不匹配', web_capture.status()['advice'])
        success=subprocess.CompletedProcess([],0,'','')
        with patch.object(web_capture,'prepare_dependencies',return_value={'available':True,'path':'offline-deps'}), patch.object(web_capture.subprocess,'run',return_value=success) as probe:
            self.assertTrue(web_capture.status()['available'])
            self.assertIn('offline-deps',probe.call_args.args[0][2])


class IsolatedRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='forge-web-tests-', dir=ROOT / 'tests')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'FORGE_DATA_DIR': str(self.root)}).start()
        out = self.root / 'studio-out'
        for name, value in {'DATA_ROOT': str(self.root), 'OUT': str(out),
                            'WS_FILE': str(out / 'workspaces.json'), 'LOG_FILE': str(out / 'runs.jsonl')}.items():
            patch.object(studio_store, name, value).start()
        patch.object(web_capture, 'DEPS', self.root / 'web-deps').start()
        patch.object(web_capture, 'status', return_value={'available': True, 'advice': ''}).start()
        patch.object(web_capture.socket, 'getaddrinfo', return_value=dns('8.8.8.8')).start()
        _, ws = studio_store.create_workspace('Isolated web fixture')
        self.workspace = ws['id']

    @staticmethod
    def fixture_capture(url, out, width, height):
        data = copy.deepcopy(FIXTURE)
        data['url'] = url
        data['viewport'] = {'width': width, 'height': height}
        path = Path(out)
        (path / 'screenshot.png').write_bytes(PNG)
        (path / 'page-data.json').write_text(json.dumps(data), encoding='utf-8')
        (path / 'theme.json').write_text(json.dumps({'version': 'forge-theme-1.0', 'tokens': data['tokens']}), encoding='utf-8')
        return data

    def capture_fixture(self):
        with patch.object(web_capture, 'capture', side_effect=self.fixture_capture) as browser:
            status, result = web_routes.capture({'url': FIXTURE['url'], 'workspace': self.workspace})
        self.assertEqual(status, 200, result)
        browser.assert_called_once()
        return result

    def test_capture_contract_exports_archive_and_history(self):
        result = self.capture_fixture()
        rid = result['run']
        self.assertTrue(result['ok'])
        self.assertEqual(result['preview'], '/out/' + rid + '/screenshot.png')
        self.assertEqual(result['data'], '/api/runs/' + rid + '/file/page-data.json')
        self.assertEqual(result['theme'], '/api/runs/' + rid + '/file/theme.json')
        self.assertEqual(result['bundle'], '/api/runs/' + rid + '/zip?all=1')
        self.assertEqual(result['summary'], {'nodes': 1, 'assets': 1, 'truncated': False, 'pages': 1, 'interactions': 0})
        directory, workspace = studio_store.find_run(rid)
        self.assertEqual(workspace, self.workspace)
        self.assertTrue(Path(directory).is_relative_to(self.root))
        landing = Path(directory, 'page.html').read_text(encoding='utf-8')
        self.assertIn('&lt;Reference &amp; dashboard&gt;', landing)
        self.assertNotIn('<Reference', landing)
        files = {item['name']: item for item in studio_store.artifacts(rid)}
        for name in ('page-data.json', 'theme.json', 'screenshot.png'):
            self.assertTrue(files[name]['deliverable'])
            self.assertEqual(files[name]['download'], '/api/runs/' + rid + '/file/' + name)
        blob, name = studio_store.zip_run(rid, deliverable_only=False)
        self.assertEqual(name, rid + '.zip')
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            self.assertEqual(json.loads(archive.read(rid + '/page-data.json'))['assets'][ASSET_URL]['data'], ASSET_DATA)
            self.assertEqual(archive.read(rid + '/screenshot.png'), PNG)
            self.assertIn(rid + '/theme.json', archive.namelist())
        entry = studio_store._read_log()[-1]
        self.assertEqual(entry['source'], 'web-capture')
        self.assertFalse(entry['accepted'])
        self.assertEqual(entry['acceptanceStatus'], 'needs_review')

    def test_capture_error_archived_and_lock_released(self):
        with patch.object(web_capture, 'capture', side_effect=RuntimeError('fixture browser failure')):
            status, body = web_routes.capture({'url': FIXTURE['url'], 'workspace': self.workspace})
        self.assertEqual(status, 502)
        self.assertIn('fixture browser failure', body['error'])
        self.assertFalse(web_routes.CAPTURE_LOCK.locked())
        entry = studio_store._read_log()[-1]
        self.assertFalse(entry['ok'])
        self.assertEqual(entry['workspace'], self.workspace)
        self.assertIn('fixture browser failure', entry['error'])

    def test_capture_bad_payload_and_missing_dependency(self):
        with patch.object(web_capture, 'capture') as browser:
            for payload in ({'url': 'https://user:secret@public.example'},
                            {'url': FIXTURE['url'], 'width': 200},
                            {'url': FIXTURE['url'], 'height': 2000},
                            {'url': FIXTURE['url'], 'workspace': 'missing-workspace'}):
                with self.subTest(payload=payload):
                    status, body = web_routes.capture(payload)
                    self.assertEqual(status, 400)
                    self.assertIn('error', body)
            browser.assert_not_called()
        with patch.object(web_capture, 'status', return_value={'available': False}):
            self.assertEqual(web_routes.capture({'url': FIXTURE['url']})[0], 503)

    def test_capture_busy_rejected(self):
        web_routes.CAPTURE_LOCK.acquire()
        try:
            self.assertEqual(web_routes.capture({'url': FIXTURE['url']})[0], 409)
        finally:
            web_routes.CAPTURE_LOCK.release()

    def test_worker_subprocess_error_and_timeout_propagate(self):
        with patch.object(web_capture.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'Traceback\nfixture worker error')):
            with self.assertRaisesRegex(RuntimeError, 'fixture worker error'):
                web_capture.capture(FIXTURE['url'], self.root, 1280, 800)
        with patch.object(web_capture.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixture', 110)):
            with self.assertRaises(subprocess.TimeoutExpired):
                web_capture.capture(FIXTURE['url'], self.root, 1280, 800)

    def test_restore_mock_chat_uses_current_conf_and_waits_for_acceptance(self):
        source = self.capture_fixture()
        conf = {'model': 'fixture-current-model', 'base_url': 'fixture-model-bridge'}
        html = '<html><head><style>body{color:#123}</style></head><body><main>Fixture restored</main><script>alert(1)</script><img src="' + ASSET_URL + '"></body></html>'
        mock_chat = Mock(return_value='```html\n' + html + '\n```')
        status, result = web_routes.restore({'run': source['run'], 'hint': 'Change title'}, conf, mock_chat)
        self.assertEqual(status, 200, result)
        mock_chat.assert_called_once()
        passed_conf, image, prompt = mock_chat.call_args.args
        self.assertIs(passed_conf, conf)
        self.assertTrue(image.startswith('data:image/png;base64,'))
        self.assertIn('Change title', prompt)
        self.assertIn(FIXTURE['url'], prompt)
        self.assertIn('Dashboard', prompt)
        self.assertIn('不可信', prompt)
        rid = result['run']
        self.assertNotEqual(rid, source['run'])
        self.assertEqual(result['preview'], '/out/' + rid + '/page.html')
        self.assertEqual(result['report'], '/out/' + rid + '/report.html')
        self.assertEqual(result['acceptanceStatus'], 'needs_review')
        directory, workspace = studio_store.find_run(rid)
        self.assertEqual(workspace, self.workspace)
        page = Path(directory, 'page.html').read_text(encoding='utf-8')
        self.assertNotIn('<script', page)
        self.assertIn(ASSET_DATA, page)
        acceptance = json.loads(Path(directory, 'acceptance.json').read_text(encoding='utf-8'))
        self.assertEqual(acceptance['status'], 'needs_review')
        self.assertEqual(acceptance['passed'], 0)
        entry = studio_store._read_log()[-1]
        self.assertFalse(entry['accepted'])
        self.assertEqual(entry['source'], 'web-restore')
        self.assertIn('不表示', Path(directory, 'report.html').read_text(encoding='utf-8'))

    def test_restore_missing_run_model_and_invalid_hint(self):
        mock_chat = Mock()
        self.assertEqual(web_routes.restore({'run': 'missing'}, None, mock_chat)[0], 503)
        self.assertEqual(web_routes.restore({'run': 'missing'}, {'model': 'fixture'}, mock_chat)[0], 404)
        self.assertEqual(web_routes.restore({}, {'model': 'fixture'}, mock_chat)[0], 400)
        source = self.capture_fixture()
        for hint in (42, 'x' * 4001):
            self.assertEqual(web_routes.restore({'run': source['run'], 'hint': hint}, {'model': 'fixture'}, mock_chat)[0], 400)
        mock_chat.assert_not_called()

    def test_restore_model_failure_invalid_output_and_lock_release(self):
        source = self.capture_fixture()
        for mock_chat in (Mock(side_effect=RuntimeError('fixture model failed')), Mock(return_value='not HTML')):
            with self.subTest(chat=mock_chat):
                status, body = web_routes.restore({'run': source['run']}, {'model': 'fixture'}, mock_chat)
                self.assertEqual(status, 502)
                self.assertIn('error', body)
                self.assertFalse(web_routes.RESTORE_LOCK.locked())


class SanitizerTests(unittest.TestCase):
    def test_active_html_external_attributes_removed_local_assets_inlined(self):
        html = '''<html><head><link href="https://evil.example/a.css" rel="stylesheet"><base href="https://evil.example"><meta http-equiv="refresh" content="0;url=https://evil.example"></head><body onload="evil()"><script>alert(1)</script><iframe srcdoc="evil"></iframe><object data="https://evil.example"></object><a href="javascript:evil()" onclick="evil()">bad</a><a href="#ok">local</a><img src="https://evil.example/track.png" onerror="evil()"><img src="ASSET"><form action="https://evil.example"><input formaction="https://evil.example"></form></body></html>'''.replace('ASSET', ASSET_URL)
        clean = web_routes.sanitize(html, FIXTURE['assets'])
        for fragment in ('<script', '<iframe', '<object', '<link', '<base', '<form', 'onload=', 'onclick=', 'onerror=', 'formaction=', 'srcdoc=', 'javascript:', 'https://evil.example'):
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, clean)
        self.assertIn(ASSET_DATA, clean)
        self.assertIn('href="#ok"', clean)
        self.assertIn('Content-Security-Policy', clean)
        self.assertIn("script-src 'none'", clean)
        self.assertIn("connect-src 'none'", clean)

    def test_external_css_load_references_removed(self):
        clean = web_routes.sanitize('<html><head><style>@import "https://evil.example/a.css"; div{background:url(https://evil.example/track.png)}</style></head><body><div style="background:url(https://evil.example/inline.png)">text</div></body></html>', {})
        self.assertNotIn('https://evil.example', clean, 'External CSS loads must be removed, not only blocked by CSP')


if __name__ == '__main__':
    unittest.main()
