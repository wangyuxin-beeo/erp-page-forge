"""Focused integration edge regressions discovered during independent audit."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'build'))
import project_context
import web_routes


class IntegrationAuditEdges(unittest.TestCase):
    def test_theme_directory_index_and_variable_formats_are_read(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'src/theme').mkdir(parents=True)
            (root/'package.json').write_text('{"dependencies":{"vue":"3"}}')
            theme=':root{--brand: #13579b;}\n$spacing: 8px;\n@radius: 4px;'
            (root/'src/theme/index.scss').write_text(theme)
            context=project_context.read_project(temp)
            self.assertIn('src/theme/index.scss',context['files'])
            self.assertEqual({v['name'] for v in context['variables']},{'--brand','$spacing','@radius'})
            (root/'src/theme/variables.scss').write_text(theme)
            context=project_context.read_project(temp)
            self.assertEqual({v['name'] for v in context['variables']},{'--brand','$spacing','@radius'})
            self.assertIn('src/theme/index.scss',context['files'])

    def test_root_tailwind_and_source_theme_both_filter_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'src').mkdir()
            (root/'package.json').write_text('{"dependencies":{"react":"19"}}')
            content='const api_key = "AUDIT_SENTINEL_CREDENTIAL"; export default {theme:{}};'
            (root/'src/theme.ts').write_text(content)
            (root/'tailwind.config.ts').write_text(content)
            context=project_context.read_project(temp)
            self.assertNotIn('src/theme.ts',context['files'])
            self.assertNotIn('AUDIT_SENTINEL_CREDENTIAL',json.dumps(context))
            self.assertNotIn('tailwind.config.ts',context['files'])

    def test_secondary_page_style_table_is_sent_to_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            main={'title':'one','url':'http://prototype/one','viewport':{'width':1280,'height':800},'document':{'width':1280,'height':800},'tokens':{},'truncated':False,'nodes':[{'id':0,'style':0,'text':'PAGE_ONE'}],'styles':[{'background-color':'#111111'}],'assets':{}}
            second={'title':'two','url':'http://prototype/two','tokens':{},'nodes':[{'id':0,'style':0,'text':'PAGE_TWO'}],'styles':[{'background-color':'rgb(13, 57, 91)','grid-template-columns':'213px 807px'}]}
            main['frames']=[{'url':'http://prototype/frame-one','name':'erpContent','data':{'nodes':[{'id':0,'style':0,'text':'FRAME_ONE_AUDIT'}],'styles':[{'color':'FRAME_ONE_STYLE'}]}}]
            second['frames']=[{'url':'http://prototype/frame-two','name':'erpContent','data':{'nodes':[{'id':0,'style':0,'text':'FRAME_TWO_AUDIT'}],'styles':[{'color':'FRAME_TWO_STYLE'}]}}]
            proto={'pages':[{'file':'page-data.json','screenshot':'screenshot.png','interactions':[]},{'file':'page-2.json','screenshot':'page-2.png','interactions':[]}],'edges':[]}
            for name,data in [('page-data.json',main),('page-2.json',second),('prototype.json',proto),('project-context.json',{'framework':'vue'})]:
                (root/name).write_text(json.dumps(data))
            (root/'screenshot.png').write_bytes(b'FIRST_SCREENSHOT')
            (root/'page-2.png').write_bytes(b'SECOND_SCREENSHOT')
            with patch.object(web_routes.studio_store,'find_run',return_value=(temp,'default')),patch.object(web_routes,'_restore_project',return_value=(200,{'ok':True})) as generate:
                code,_=web_routes.restore({'run':'run-audit','mode':'project'},{'model':'mock'},lambda *args:'')
                self.assertEqual(code,200)
                context=generate.call_args.args[5];image=generate.call_args.args[6]
                self.assertIn('PAGE_TWO',context)
                self.assertIn('rgb(13, 57, 91)',context)
                self.assertIn('213px 807px',context)
                for marker in ('FRAME_ONE_AUDIT','FRAME_ONE_STYLE','FRAME_TWO_AUDIT','FRAME_TWO_STYLE'):
                    self.assertIn(marker,context)
                self.assertEqual(image,'data:image/png;base64,RklSU1RfU0NSRUVOU0hPVA==')

    def test_hash_route_identity_preserves_spa_routes_but_not_anchors(self):
        from prototype_capture import canonical_key
        base='http://127.0.0.1:32767/index.html'
        self.assertEqual(canonical_key(base+'#section'),base)
        self.assertEqual(canonical_key(base+'#/detail'),base+'#/detail')
        self.assertEqual(canonical_key(base+'#!/detail'),base+'#!/detail')
        self.assertNotEqual(canonical_key(base+'#/home'),canonical_key(base+'#/detail'))
        self.assertEqual(canonical_key(base+'?page=2#section'),base+'?page=2')

    def test_project_read_permission_failure_returns_structured_error(self):
        with patch.object(web_routes.web_capture,'status',return_value={'available':True}),patch.object(web_routes.web_capture,'validate_url',return_value='https://example.com'),patch.object(web_routes.project_context,'read_project',side_effect=PermissionError('denied theme file')):
            code,body=web_routes.capture({'url':'https://example.com','projectPath':'C:/project'})
            self.assertGreaterEqual(code,400)
            self.assertLess(code,500)
            self.assertTrue(body.get('error'))


if __name__=='__main__':unittest.main(verbosity=2)
