"""Offline regression for the independent screenshot pipeline. No remote calls."""
import copy
import json
import os
import zipfile
import io
import screenshot_render as render
import studio_api as api
import studio_store as store

HERE = os.path.dirname(__file__)
spec = json.load(open(os.path.join(HERE, 'vision', 'screenshot-samples', 'form-two-tables.json'), encoding='utf-8'))
checks = []
def check(name, value):
    checks.append(bool(value))
    print(('PASS ' if value else 'FAIL ') + name)

check('standalone contract', not render.validate(spec)[0] and spec['kind'] == 'screenshot')
check('reject old archetype extract', bool(render.validate({'archetype': 'A1'})[0]))
check('reject empty elements', bool(render.validate({'kind':'screenshot','canvas':{'w':100,'h':100},'elements':[]})[0]))
html = render.render(spec)
check('deterministic output', html == render.render(copy.deepcopy(spec)))
check('preview scales full canvas', 'function fitCanvas()' in html and "cv.style.transform='scale('" in html)
ctl = {'id':'probe','type':'select','bbox':[10,40,120,30],'label':'字段名称','value':'原图值','options':['原图值']}
check('select shows original value', '<option selected>原图值</option>' in render.render_control(ctl))
check('field label is visible', "data-label-for='probe'" in render.render_element(ctl))
check('buttons do not force blue', '.ctl.btn{background:inherit;color:inherit;' in html)
check('two real tables', html.count("<table class='ss-table'>") == 2)
blank = copy.deepcopy(spec)
flat = []
render.collect(blank['elements'], flat)
table = next(e for e in flat if e['type'] == 'table')
table['table']['columns'][0] = {'text':'', 'width':45}
check('blank header is valid', not render.validate(blank)[0])
check('blank header keeps width', '<col style="width:45px">' in render.render(blank) and '<th></th>' in render.render(blank))
# S1：confidence / source 可追溯字段的校验（新增，向后兼容）
ok_conf = copy.deepcopy(spec)
render.collect(ok_conf['elements'], [])  # noop warm-up
flat = []
render.collect(ok_conf['elements'], flat)
flat[0]['confidence'] = 0.9
flat[0]['source'] = 'vlm'
check('confidence/source accepted', not render.validate(ok_conf)[0])
bad_conf = copy.deepcopy(spec)
flat = []
render.collect(bad_conf['elements'], flat)
flat[0]['confidence'] = 1.5
check('confidence out of range rejected', bool(render.validate(bad_conf)[0]))
bad_src = copy.deepcopy(spec)
flat = []
render.collect(bad_src['elements'], flat)
flat[0]['source'] = 'gpt'
check('illegal source rejected', bool(render.validate(bad_src)[0]))
bad_bbox = copy.deepcopy(spec)
flat = []
render.collect(bad_bbox['elements'], flat)
flat[0]['bbox'] = [10, 20, 30]  # 3 元，缺 h
check('bbox not 4-tuple rejected', bool(render.validate(bad_bbox)[0]))
check('legacy contract (no confidence/source) still valid', not render.validate(spec)[0])
# S1：结构化 uncertain 条目渲染成人话，而非 Python dict 字符串
sunc = copy.deepcopy(spec)
sunc['uncertain'] = [{'elementId': 'e1', 'field': 'bbox', 'why': '边界模糊'}]
sunc_html = render.render(sunc)
check('structured uncertain renders readable', 'e1 · bbox' in sunc_html and "{'elementId'" not in sunc_html)
# S2：few-shot 样例（出库单转译 + 简单表单）存在且通过 validate/render
fewshot_path = os.path.join(HERE, 'vision', 'screenshot-samples', 'outorder-fewshot.json')
check('fewshot sample exists', os.path.exists(fewshot_path))
fewshot = json.load(open(fewshot_path, encoding='utf-8'))
check('fewshot sample validates', not render.validate(fewshot)[0] and fewshot['kind'] == 'screenshot')
fewshot_html = render.render(fewshot)
check('fewshot sample renders', 'ss-table' in fewshot_html and '出库单管理' in fewshot_html)
check('fewshot sample keeps original terms', '作業类型' in fewshot_html and '极素值引用' in fewshot_html)
probe = copy.deepcopy(spec)
probe['elements'][0]['text'] = '<img src=x onerror=alert(1)>'
probe['elements'][0]['style'] = {'color': 'red; background:url(https://evil.invalid)', 'bg': '\" onmouseover=\"alert(1)'}
safe = render.render(probe)
check('text escaped', '&lt;img' in safe and '<img src=x' not in safe)
check('style injection blocked', 'evil.invalid' not in safe and 'onmouseover=' not in safe)
code, result = api.run_screenshot_pipeline(spec)
check('render and hard checks pass', code == 200 and result['summary']['hardFailed'] == 0)
code, saved = api.save_template(result['run'], '截图独立回归样例')
check('template saved', code == 200)
code, parsed = api.parse_command('', saved.get('id'))
check('template keeps screenshot structure', code == 200 and parsed.get('screenshot') and parsed['spec']['elements'] == spec['elements'])
code, reused = api.forge_from_command({'templateId':saved.get('id'), 'command':''})
check('template reuse separate renderer', code == 200 and reused.get('archetype') == 'screenshot')
check('ZIP declares standalone spec', 'screenshot.spec.json' in store.DELIVERABLE)
page = os.path.join(store.find_run(result['run'])[0], 'page.html')
print('DEMO ' + page)
print('REPORT ' + os.path.join(os.path.dirname(page), 'report.html'))
print('RESULT %d/%d' % (sum(checks), len(checks)))
raise SystemExit(0 if all(checks) else 1)
