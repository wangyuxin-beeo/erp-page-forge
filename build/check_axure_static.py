"""Regression tests: self-contained static Axure import; no Node or JS execution."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import axure
from axure_static import extract, read_call, safe_page, StaticDataError

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / 'build' / 'axure' / 'sample-prototype'
TEST_TMP = ROOT / 'build' / 'test-axure-tmp'
TEST_TMP.mkdir(exist_ok=True)


class AxureStaticTests(unittest.TestCase):
    def test_sample_extract_and_generate(self):
        import forge
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            with patch.object(axure, 'find_node', side_effect=AssertionError('Node must not be used')):
                ok, out, err = axure.run_extract(str(SAMPLE), tmp)
            self.assertTrue(ok, err)
            ex = axure.load_extraction(tmp, 'inout-list')
            spec = axure.build_spec('inout-list', ex, str(SAMPLE))
            self.assertEqual(len(spec['fields']), 9)
            self.assertEqual(len(spec['actions']), 4)
            self.assertEqual(len(spec['statusMachine']['states']), 5)
            self.assertEqual(spec['page']['name'], '出入库单据管理')
            self.assertEqual(spec['nav'][0]['group'], '仓储中心')
            html, _, _ = forge.build_from_spec(spec)
            self.assertIn('单据号', html)
            self.assertIn('演示用虚构数据', html)

    def test_js_data_literal_syntax(self):
        obj = read_call("/* fake $axure.loadDocument(bad()) */ $axure.loadDocument({unquoted:'中文', list:[true,null,0xff,], escaped:'a\\x62\\u0063',});", 'loadDocument')
        self.assertEqual(obj, {'unquoted':'中文','list':[True,None,255],'escaped':'abc'})

    def test_never_evaluates_code(self):
        for value in ['{a:process.exit()}', '{a:(function(){return 1})()}', '{...other}', '{a:foo}', '{a:`template`}', '{a:1+2}']:
            with self.subTest(value=value):
                with self.assertRaises(StaticDataError):
                    read_call('$axure.loadDocument('+value+');', 'loadDocument')

    def test_traversal_rejected(self):
        for value in ['../outside', '/absolute', '%2e%2e/outside', 'C:/file', 'a/../../b']:
            with self.assertRaises(StaticDataError): safe_page(value)

    def test_explicit_page_and_no_advanced(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            extract(str(SAMPLE), tmp, ['inout-list'], False)
            ex = axure.load_extraction(tmp, 'inout-list')
            self.assertEqual(list(ex['notes']), ['page'])
            self.assertFalse(ex['interactions'])

    def test_unknown_syntax_returns_diagnostic_without_legacy(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root = Path(tmp) / 'input'; (root/'data').mkdir(parents=True)
            (root/'data/document.js').write_text('$axure.loadDocument({sitemap:getData()});', encoding='utf-8')
            with patch.object(axure, 'find_node', side_effect=AssertionError('No implicit JS fallback')):
                ok, _, err = axure.run_extract(str(root), str(Path(tmp)/'output'))
            self.assertFalse(ok)
            self.assertIn('--parser legacy', err)


if __name__ == '__main__': unittest.main(verbosity=2)
