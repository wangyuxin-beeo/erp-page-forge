import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'build'))
import project_context

class ProjectContextTests(unittest.TestCase):
    def test_project_theme_and_stack_skip_secrets_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);(p/'src').mkdir();(p/'src/styles').mkdir()
            (p/'package.json').write_text(json.dumps({'dependencies':{'vue':'3','element-plus':'2'},'devDependencies':{'typescript':'5'}}))
            (p/'src/styles/variables.scss').write_text(':root{--brand:#123456;}\n$space: 8px;')
            (p/'src/styles/theme-secret.ts').write_text('const api_key = "should-not-be-read";')
            (p/'.env').write_text('PASSWORD=not-read')
            c=project_context.read_project(str(p))
            self.assertEqual(c['framework'],'vue');self.assertTrue(c['typescript'])
            self.assertEqual(c['files'],['src/styles/variables.scss'])
            self.assertIn('--brand',[v['name'] for v in c['variables']])
            self.assertNotIn('should-not-be-read',json.dumps(c))
            self.assertTrue(c['warnings'])
    def test_bundle_paths_and_files_are_reviewable(self):
        for path in ('../bad.js','C:/bad.js','/bad.js','src\\bad.js','.env','package.json','src/a.exe'):
            with self.subTest(path=path),self.assertRaises(ValueError):
                project_context.decode_files(json.dumps({'files':[{'path':path,'content':'bad'}]}))
        with tempfile.TemporaryDirectory() as temp:
            files=project_context.decode_files('```json\n'+json.dumps({'files':[{'path':'src/Prototype.vue','content':'<template>Hello</template>'}]})+'\n```')
            project_context.write_bundle(temp,files,{'framework':'vue'},{'pages':[]})
            with zipfile.ZipFile(Path(temp)/'project-code.zip') as z:
                self.assertIn('src/Prototype.vue',z.namelist())
                self.assertIn('forge/INTEGRATION.md',z.namelist())
    def test_project_generation_carries_theme_and_requires_review(self):
        with tempfile.TemporaryDirectory() as temp:
            import studio_store,studio_api,project_generate
            out=Path(temp)/'source';out.mkdir()
            context={'framework':'react','sources':[{'path':'src/theme.css','content':':root{--brand:#123456}'}]}
            (out/'project-context.json').write_text(json.dumps(context),encoding='utf-8')
            prompts=[]
            def chat(conf,image,prompt):
                prompts.append(prompt)
                return json.dumps({'files':[{'path':'src/Prototype.tsx','content':'export default function Page(){return <button>Open</button>}'},{'path':'INTEGRATION.md','content':'Build before merging.'}]})
            target=Path(temp)/'result';target.mkdir()
            with patch.object(studio_store,'new_run',return_value=('run-test',str(target),'default')),patch.object(studio_api,'record') as record:
                code,body=project_generate.generate(str(out),'default','run-source',{'title':'Demo','url':'http://prototype.local/'},{'pages':[]},'click->dialog','data:image/png;base64,x','',{'model':'mock'},chat)
                self.assertEqual(code,200);self.assertEqual(body['generationMode'],'project')
                self.assertEqual(body['acceptanceStatus'],'needs_review')
                self.assertIn('--brand',prompts[0]);self.assertIn('click->dialog',prompts[0])
                self.assertTrue((target/'project-code.zip').is_file())
                self.assertEqual(record.call_args.kwargs['summary']['unsupported'],3)

if __name__=='__main__':unittest.main()
