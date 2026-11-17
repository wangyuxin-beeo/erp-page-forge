#!/usr/bin/env python3
"""Real-browser check of A6 difference resolution, isolated generated artifacts."""
import html
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "build"))
import forge
from verify_react_render import find_browser, run_browser

out = ROOT / "plugin/test-results/statement-resolution"
out.mkdir(parents=True, exist_ok=True)
spec = {"specVersion": "1.0", "page": {"name": "对账差异处理测试", "module": "对账"},
        "entity": {"recipeKey": "statement"}, "archetype": "A6", "fields": []}
page, model, completed = forge.build_from_spec(spec)
script = r"""<script>
(async function () {
  var checks = [];
  function check(name, value) { checks.push({name:name,pass:!!value}); }
  function tick() { return new Promise(function(r){setTimeout(r,0);}); }
  try {
    var M=window.__MODEL__,key=document.querySelector('#partner').value+'|'+document.querySelector('#period').value;
    var S=M.sets[key], money=JSON.stringify(S.lines.map(function(l){return [l.receivable,l.received];}));
    check('initial resolution hidden',document.querySelector('#__resolve').style.display==='none');
    document.querySelector('#__confirm').click();
    document.querySelector('#__modal [data-act="ok"]').click();await tick();
    check('marking difference requires memo',S.status==='待确认');
    document.querySelector('#__modal textarea').value='核对发现演示在途款项';
    document.querySelector('#__modal [data-act="ok"]').click();await tick();
    check('marking difference changes status',S.status==='有差异');
    check('resolution becomes visible',document.querySelector('#__resolve').style.display!=='none');
    document.querySelector('#__resolve').click();
    document.querySelector('#__modal [data-act="ok"]').click();await tick();
    check('resolution requires memo',S.status==='有差异');
    document.querySelector('#__modal textarea').value='已完成线下核实与对方确认';
    document.querySelector('#__modal [data-act="ok"]').click();await tick();
    check('resolved state confirmed',S.status==='已确认');
    check('resolution memo retained',S.resolutionMemo==='已完成线下核实与对方确认');
    check('difference cleared',S._diff===0&&S.lines.every(function(l){return l.diff===0;}));
    check('financial amounts unchanged',money===JSON.stringify(S.lines.map(function(l){return [l.receivable,l.received];})));
    check('identity and state visible',document.querySelector('#pageSub').textContent.includes(S.code)&&document.querySelector('#pageSub').textContent.includes('确认状态'));
    check('resolution hidden after completion',document.querySelector('#__resolve').style.display==='none');
  } catch(e) {checks.push({name:'browser execution',pass:false,error:String(e)});}
  var pre=document.createElement('pre');pre.id='resolution-test-result';pre.textContent=JSON.stringify(checks);document.body.appendChild(pre);
})();
</script>"""
page = page.replace("</body>", script + "</body>")
path = out / "probe.html"
path.write_text(page, encoding="utf-8")
dom, stderr = run_browser(find_browser(), path.as_uri(), (1440, 1100), dump=True)
match = re.search(r'<pre id="resolution-test-result">(.*?)</pre>', dom, re.S)
if not match:
    raise SystemExit("Browser returned no test results: " + stderr[-1500:])
checks = json.loads(html.unescape(match.group(1)))
(out / "results.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
for item in checks:
    print(("PASS " if item["pass"] else "FAIL ") + item["name"])
sys.exit(1 if not all(c["pass"] for c in checks) else 0)
